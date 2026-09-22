#!/usr/bin/env python3
"""
عامل الرندر — بيشتغل جوه GitHub Actions، من غير سيرفر ومن غير كارت.

بياخد ملف payload.json فيه السكريبت وروابط اللقطات، وبيطلّع فيديو Short
مقاس 1080x1920 بصوت.

الخطوات:
  1. يحوّل السكريبت كله لصوت واحد متصل بـ edge-tts (مجاني، من غير مفتاح)
  2. ينزّل اللقطات ويقصّها قطع قصيرة بحركة (فيديو بس، من غير صوت)
  3. يجهّز مسار صوت خلفية واحد متصل (من صوت أحد اللقطات الحقيقي، أو
     أمبيانس صناعي هادي لو مفيش ولا لقطة فيها صوت أصلًا)
  4. يركّب الكل: فيديو + خلفية خافتة + التعليق الصوتي فوقها
"""

import json
import math
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

WORK = Path("work")
W, H = 1080, 1920
FPS = 30
SEGMENT_SECONDS = 2.5        # طول كل قطعة — قطع سريعة عشان الشورت ميبقاش ساكن
ZOOM_MAX = 1.18              # أقصى تقريب، خفيف عشان ميبانش مصطنع
ZOOM_SPEED = 0.0012          # مقدار الزوم لكل كادر
MUSIC_VOLUME = 0.40          # حجم الموسيقى قبل ما تتخفض تحت الكلام
DEFAULT_VOICE = "en-US-AndrewNeural"

# موسيقى الخلفية بنولّدها بنفسنا (مش تراك جاهز) عشان صفر مخاطرة حقوق نشر على
# يوتيوب. تتابع أكوردات Am–F–C–G (لوب ١٢ ثانية) بيتكرر لحد ما يغطي الفيديو،
# نغمات جيبية مع طبقة مزاحة بسيطة (detune) عشان دفء، وفلتر وصدى خفيف.
MUSIC_CHORDS = [
    [110.00, 220.00, 261.63, 329.63],   # Am
    [87.31, 174.61, 220.00, 261.63],    # F
    [130.81, 261.63, 329.63, 392.00],   # C
    [98.00, 196.00, 246.94, 293.66],    # G
]
MUSIC_CHORD_SECONDS = 3.0
# أصوات edge-tts شكلها دايمًا "xx-XX-NameNeural" (زي en-US-AndrewNeural).
# الـ AI بيتخيّل أحيانًا أسماء أصوات من مزوّدين تانيين (زي "alloy" بتاعة OpenAI)،
# فبنرفض أي حاجة مش شكل edge-tts ونرجع للافتراضي بدل ما نوقع الرندر كله.
EDGE_VOICE_RE = re.compile(r"^[a-z]{2}-[A-Z]{2}-\w+Neural$")


def normalize_voice(voice):
    if voice and EDGE_VOICE_RE.match(voice):
        return voice
    print(f"! صوت غير معروف لـ edge-tts: {voice!r} — هستخدم {DEFAULT_VOICE}", flush=True)
    return DEFAULT_VOICE


# ---------------------------------------------------------------- أدوات

def run(cmd, **kw):
    """يشغّل أمر ويقع بصوت عالي لو فشل."""
    print("+", " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run(cmd, check=True, **kw)


def duration_of(path):
    """مدة ملف صوت أو فيديو بالثواني."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def mean_db(path):
    """متوسط مستوى الصوت بالديسيبل، أو None لو مقدرناش نقيسه."""
    out = subprocess.run(
        ["ffmpeg", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    found = re.search(r"mean_volume:\s*(-?[\d.]+) dB", out.stderr)
    return float(found.group(1)) if found else None


def download(url, dest):
    print(f"+ download {url} -> {dest}", flush=True)
    req = urllib.request.Request(url, headers={"User-Agent": "render-worker/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)
    return dest


def fetch_sources(urls, outdir):
    """ينزّل كل اللقطات مرة واحدة، ويرجّع مسارها ومدتها."""
    sources = []
    for i, url in enumerate(urls):
        raw = outdir / f"raw_{i:03d}.mp4"
        try:
            download(url, raw)
            sources.append((raw, duration_of(raw)))
        except Exception as exc:
            print(f"! اللقطة دي مش راضية تنزل، هعدّيها: {exc}", flush=True)

    if not sources:
        raise RuntimeError("مفيش ولا لقطة اشتغلت — مش هينفع نركّب فيديو")
    return sources


# ---------------------------------------------------------------- مراحل

def synthesize(lines, voice, outfile):
    """
    السكريبت كله في نداء واحد لـ edge-tts، مش جملة جملة.

    كنا بنولّد كل جملة في ملف لوحدها ونلزقهم بسكتة ثابتة بينهم، وده كان
    بيطلّع الكلام مقطّع وميكانيكي لأن كل جملة بتتقفل وتتفتح من الصفر.
    نداء واحد بالسكريبت كامل بيسيب لـ edge-tts نفسه يتحكم في نبرة ووقفات
    طبيعية بين الجمل، فالنتيجة بتبقى صوت متصل بدل قطع ملزوقة.
    """
    text = " ".join(lines)
    run(["edge-tts", "--voice", voice, "--text", text, "--write-media", str(outfile)])
    return outfile


SILENCE_NOISE = "-30dB"      # أي حاجة أهدى من كده تتحسب سكتة
SILENCE_MIN_GAP = 0.35       # مش بنلمس سكتة أقصر من كده — دي وقفة طبيعية جوه الجملة
SILENCE_TARGET_GAP = 0.18    # أي سكتة أطول من الحد بنقصّها للمقدار ده


def detect_silences(path, noise=SILENCE_NOISE, min_gap=SILENCE_MIN_GAP):
    """يرجّع لستة (بداية, نهاية) للسكتات الأطول من الحد الأدنى في ملف الصوت."""
    out = subprocess.run(
        ["ffmpeg", "-i", str(path), "-af", f"silencedetect=noise={noise}:d={min_gap}",
         "-f", "null", "-"],
        capture_output=True, text=True,
    )
    starts = [float(m) for m in re.findall(r"silence_start:\s*([\d.]+)", out.stderr)]
    ends = [float(m) for m in re.findall(r"silence_end:\s*([\d.]+)", out.stderr)]
    return list(zip(starts, ends))


def tighten_pauses(voice_path, outfile, target=SILENCE_TARGET_GAP):
    """
    يقصّر السكتات الطويلة بين الجمل من غير ما يلزق الكلام ببعضه.

    edge-tts بيحط وقفة طبيعية بعد كل نقطة (بتوصل لثانية أحيانًا)، وده
    اللي بيحس المستخدم بيه إنه "مقطّع". بنكتشف أي سكتة أطول من الحد
    ونقصّها لمدة ثابتة أقصر بدل ما نمسحها خالص — مسحها بالكامل بيخلي
    الجمل تتلزق ببعض وتبقى غير مفهومة.
    """
    total = duration_of(voice_path)
    silences = detect_silences(voice_path)
    if not silences:
        shutil.copy(voice_path, outfile)
        return outfile

    cuts = []
    cursor = 0.0
    for s_start, s_end in silences:
        if s_start > cursor:
            cuts.append((cursor, s_start))
        cuts.append((s_start, min(s_start + target, s_end)))
        cursor = s_end
    if cursor < total:
        cuts.append((cursor, total))

    parts_dir = outfile.parent / "pause_parts"
    parts_dir.mkdir(exist_ok=True)
    parts = []
    for i, (start, end) in enumerate(cuts):
        if end - start <= 0.01:
            continue
        part = parts_dir / f"part_{i:03d}.m4a"
        run(["ffmpeg", "-y", "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(voice_path),
             "-c:a", "aac", "-b:a", "128k", str(part)])
        parts.append(part)

    listing = parts_dir / "list.txt"
    with open(listing, "w", encoding="utf-8") as f:
        for p in parts:
            f.write(f"file '{p.resolve()}'\n")
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
         "-c:a", "aac", "-b:a", "128k", str(outfile)])
    return outfile


def motion_filter(index):
    """
    فلتر الحركة: زوم بطيء داخل/خارج بالتبادل على كل قطعة.

    اللقطة الثابتة بتخلي المشاهد يسيب الشورت بسرعة، فبنضيف حركة مستمرة.
    بنكبّر الصورة الأول قبل الـ zoompan عشان الحركة تطلع ناعمة —
    من غير كده الـ zoompan بيقرّب الإزاحة لأقرب بكسل فالصورة بترجف.
    """
    frames_per_second_step = ZOOM_SPEED
    if index % 2 == 0:
        zoom = f"min(1+{frames_per_second_step}*on,{ZOOM_MAX})"
    else:
        zoom = f"max({ZOOM_MAX}-{frames_per_second_step}*on,1)"

    return (
        f"scale={int(W * 1.5)}:{int(H * 1.5)}:force_original_aspect_ratio=increase,"
        f"crop={int(W * 1.5)}:{int(H * 1.5)},"
        f"zoompan=z='{zoom}':d=1:"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"s={W}x{H}:fps={FPS},setsar=1"
    )


def prepare_segments(sources, target_total, outdir):
    """
    يقطّع اللقطات قطع قصيرة بحركة، بدل لقطة واحدة طويلة ساكنة.

    القطع السريعة (كل ثانيتين تقريبًا) هي اللي بتمسك المشاهد في الشورتس.
    لو اللقطات أقل من عدد القطع المطلوبة، بنرجع نستخدمها تاني بس من مكان
    مختلف جوه اللقطة، فالمشهد ما يتكررش بنفس الشكل.

    القطع دي فيديو بس من غير صوت — صوت الخلفية بقى مسار منفصل
    (build_music) بدل ما يتلزق مع كل قطعة، عشان لزق قطع فيها صوت
    وقطع من غيره مع بعض بـ "-c copy" كان بيطلّع Non-monotonic DTS
    (خلل حقيقي في التوقيت بيسمع كـ"طقطقة" في الصوت).
    """
    needed = max(1, math.ceil(target_total / SEGMENT_SECONDS))
    segments = []

    for n in range(needed):
        src, src_duration = sources[n % len(sources)]
        lap = n // len(sources)
        start = min(lap * SEGMENT_SECONDS, max(0.0, src_duration - SEGMENT_SECONDS))
        out = outdir / f"seg_{n:03d}.mp4"
        try:
            run(["ffmpeg", "-y", "-ss", f"{start:.2f}", "-t", f"{SEGMENT_SECONDS:.2f}",
                 "-i", str(src), "-an", "-vf", motion_filter(n),
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                 "-pix_fmt", "yuv420p", str(out)])
            segments.append(out)
        except subprocess.CalledProcessError as exc:
            print(f"! القطعة دي فشلت، هعدّيها: {exc}", flush=True)

    if not segments:
        raise RuntimeError("مفيش ولا قطعة اتعملت — مش هينفع نركّب فيديو")
    return segments


def _music_chord(freqs, outfile):
    """أكورد واحد: نغماته الجيبية + طبقة مزاحة بسيطة، مع فيد ولفلتر دفء."""
    inputs = []
    for f in freqs:
        inputs += ["-f", "lavfi", "-t", f"{MUSIC_CHORD_SECONDS}",
                   "-i", f"sine=frequency={f}:sample_rate=44100"]
        inputs += ["-f", "lavfi", "-t", f"{MUSIC_CHORD_SECONDS}",
                   "-i", f"sine=frequency={f * 1.004:.3f}:sample_rate=44100"]
    n = len(freqs) * 2
    labels = "".join(f"[{i}:a]" for i in range(n))
    filt = (f"amix=inputs={n}:duration=first:normalize=0,volume={1.0 / n:.3f},"
            f"afade=t=in:st=0:d=0.5,afade=t=out:st={MUSIC_CHORD_SECONDS - 0.6}:d=0.6,"
            f"lowpass=f=2200")
    run(["ffmpeg", "-y", *inputs, "-filter_complex", f"{labels}{filt}[a]",
         "-map", "[a]", "-c:a", "pcm_s16le", str(outfile)])
    return outfile


def build_music(target_total, outfile, outdir):
    """
    موسيقى خلفية متصلة تغطي الفيديو كله — بنولّدها بنفسنا بالكامل.

    بنبني لوب ١٢ ثانية (تتابع أكوردات Am–F–C–G) وبنكرّره لحد ما يغطي مدة
    التعليق. توليدها محليًا معناه صفر مخاطرة حقوق نشر — ولا تراك خارجي ممكن
    يجيب ضربة على القناة أو يوقّف الأرباح، وثابتة على كل الفيديوهات.
    """
    parts = []
    for i, freqs in enumerate(MUSIC_CHORDS):
        parts.append(_music_chord(freqs, outdir / f"chord_{i}.wav"))

    listing = outdir / "music_list.txt"
    with open(listing, "w", encoding="utf-8") as f:
        for p in parts:
            f.write(f"file '{p.resolve()}'\n")

    # مزيج النغمات بيطلع خافت (mean حوالي -36dB)، فبنرفعه لمستوى ثابت (+16dB)
    # مع limiter يمنع أي تكسير — عشان بعد ما نخفّضه تحت الكلام يفضل مسموع.
    loop = outdir / "music_loop.m4a"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
         "-af", "aecho=0.8:0.9:70:0.3,tremolo=f=0.15:d=0.3,lowpass=f=2600,"
                "acompressor=threshold=-18dB:ratio=3,volume=16dB,alimiter=limit=0.9",
         "-c:a", "aac", "-b:a", "160k", str(loop)])

    loop_seconds = MUSIC_CHORD_SECONDS * len(MUSIC_CHORDS)
    loops = max(0, math.ceil(target_total / loop_seconds) - 1)
    run(["ffmpeg", "-y", "-stream_loop", str(loops), "-i", str(loop),
         "-t", f"{target_total:.2f}", "-c:a", "aac", "-b:a", "160k", str(outfile)])
    print("    موسيقى الخلفية: مولّدة محليًا (Am–F–C–G)", flush=True)
    return outfile


def concat_video(clips, target_total, outfile):
    """يلزق اللقطات (فيديو بس) ويكررها لو مش مغطية مدة الصوت."""
    total = sum(duration_of(c) for c in clips)
    sequence = list(clips)
    while total < target_total:
        sequence.extend(clips)
        total += sum(duration_of(c) for c in clips)

    listing = WORK / "video_list.txt"
    with open(listing, "w", encoding="utf-8") as f:
        for c in sequence:
            f.write(f"file '{c.resolve()}'\n")

    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
         "-c", "copy", str(outfile)])
    return outfile


def compose(video, music_audio, voice_audio, outfile):
    """
    التركيب النهائي: فيديو + موسيقى خلفية بتنخفض تحت الكلام + التعليق فوقه.

    الموسيقى بتتخفض أوتوماتيك وقت ما الكلام بيشتغل (sidechaincompress) وبترجع
    وقت السكتات، فالكلام دايمًا أوضح منها — بدل ما نسيب حجم ثابت بيزاحم الصوت.
    """
    total = duration_of(voice_audio)
    run([
        "ffmpeg", "-y", "-i", str(video), "-i", str(music_audio), "-i", str(voice_audio),
        "-filter_complex",
        # نقسم الكلام لنسختين: واحدة تتحكّم في خفض الموسيقى، وواحدة تتحطّ فوقها
        # normalize=0 مهم: من غيره amix بيقسم كل مدخل على 2 فيخفّض الكلام نفسه.
        # الموسيقى مخفوضة ومكتومة تحت الكلام، وفي الآخر limiter يمنع أي تكسير.
        f"[2:a]asplit=2[vkey][vmix];"
        f"[1:a]volume={MUSIC_VOLUME}[bg];"
        f"[bg][vkey]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=300[duck];"
        f"[duck][vmix]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[mix];"
        f"[mix]alimiter=limit=0.95[aout]",
        "-map", "0:v", "-map", "[aout]",
        "-t", f"{total:.2f}",
        "-c:v", "libx264", "-preset", "medium", "-crf", "21",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(outfile),
    ])
    return outfile


# ---------------------------------------------------------------- main

def main():
    payload_path = Path(sys.argv[1] if len(sys.argv) > 1 else "payload.json")
    payload = json.loads(payload_path.read_text(encoding="utf-8"))

    lines = [l for l in payload["lines"] if l and l.strip()]
    if not lines:
        raise SystemExit("السكريبت فاضي")

    voice = normalize_voice(payload.get("voice", DEFAULT_VOICE))
    clips = payload.get("clips", [])
    if not clips:
        raise SystemExit("مفيش لقطات في الـ payload")

    WORK.mkdir(exist_ok=True)
    video_dir = WORK / "video"
    video_dir.mkdir(exist_ok=True)

    print("==> بحوّل السكريبت لصوت متصل")
    voice_raw = synthesize(lines, voice, WORK / "voice_raw.mp3")
    print("==> بقصّر السكتات الطويلة بين الجمل")
    voice_audio = tighten_pauses(voice_raw, WORK / "voice.m4a")
    total = duration_of(voice_audio)
    print(f"    مدة التعليق: {total:.1f} ثانية")

    # الشورتس اللي بتكمّل للآخر بتكون في حدود ٤٠ ثانية، و١٨٠ هي حد يوتيوب نفسه.
    # مبنوقفش الرندر، بس بنسيب أثر في اللوج عشان نعرف السكريبت طوّل.
    if total > 180:
        print("! التعليق أطول من ١٨٠ ثانية، ده مش Short خالص. قصّر السكريبت.")
    elif total > 50:
        print(f"! التعليق {total:.0f} ثانية — أطول من المستهدف (٣٠-٤٠). الاحتفاظ هيقل.")

    print("==> بنزّل اللقطات")
    sources = fetch_sources(clips, video_dir)

    print("==> بقطّع اللقطات بحركة")
    segments = prepare_segments(sources, total, video_dir)
    silent = concat_video(segments, total, WORK / "silent.mp4")

    print("==> بجهّز موسيقى الخلفية")
    music_dir = WORK / "music"
    music_dir.mkdir(exist_ok=True)
    music = build_music(total, WORK / "music.m4a", music_dir)

    print("==> التركيب النهائي")
    final = compose(silent, music, voice_audio, Path("output.mp4"))

    meta = {
        "id": payload.get("id", ""),
        "file": str(final),
        "duration": duration_of(final),
        # يوتيوب بيوزّع الفيديو حسب لغته، فبنقولهاله صراحةً بدل ما يخمّن.
        # الصوت نفسه هو أدق مصدر للغة: en-US-... يعني إنجليزي، es-ES-... إسباني.
        "language": voice.split("-")[0],
        "title": payload.get("title", ""),
        "description": payload.get("description", ""),
        "tags": payload.get("tags", []),
        "privacy": payload.get("privacy", "public"),
    }
    Path("output.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
    print(f"==> تمام: {final} ({meta['duration']:.1f} ثانية)")


if __name__ == "__main__":
    main()
