#!/usr/bin/env python3
"""
عامل الرندر — بيشتغل جوه GitHub Actions، من غير سيرفر ومن غير كارت.

بياخد ملف payload.json فيه السكريبت وروابط اللقطات، وبيطلّع فيديو Short
مقاس 1080x1920 بصوت.

الخطوات:
  1. يحوّل السكريبت كله لصوت واحد متصل بـ edge-tts (مجاني، من غير مفتاح)
  2. ينزّل اللقطات ويقصّها قطع قصيرة بحركة، وبيحتفظ بصوتها الأصلي (جمهور، ملعب...)
  3. يركّب الكل: فيديو + صوت الخلفية الأصلي خافت + التعليق الصوتي فوقه
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
AMBIENCE_VOLUME = 0.22       # حجم صوت الخلفية الأصلي (جمهور/ملعب) تحت التعليق
DEFAULT_VOICE = "en-US-AndrewNeural"
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


def has_audio(path):
    """هل الفيديو ده فيه مسار صوت أصلًا؟ لقطات الأرشيف مش كلها بتيجي بصوت."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    return bool(out.stdout.strip())


def download(url, dest):
    print(f"+ download {url} -> {dest}", flush=True)
    req = urllib.request.Request(url, headers={"User-Agent": "render-worker/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)
    return dest


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


def prepare_segments(urls, target_total, outdir):
    """
    ينزّل اللقطات ويقطّعها قطع قصيرة بحركة، بدل لقطة واحدة طويلة ساكنة.

    القطع السريعة (كل ثانيتين تقريبًا) هي اللي بتمسك المشاهد في الشورتس.
    لو اللقطات أقل من عدد القطع المطلوبة، بنرجع نستخدمها تاني بس من مكان
    مختلف جوه اللقطة، فالمشهد ما يتكررش بنفس الشكل.

    كل قطعة بتحتفظ بصوتها الأصلي (جمهور، ضوضاء ملعب، أمبيانس عام) بدل ما
    نمسحه بالكامل — ده اللي بيدّي إحساس إن الفيديو "حي" مش صامت. اللقطات
    اللي مالهاش صوت أصلًا بناخد لها سكتة بدل ما نسيب القطعة من غير مسار
    صوت خالص، عشان كل القطع تتلزق مع بعض بشكل موحّد بعدين.
    """
    sources = []
    for i, url in enumerate(urls):
        raw = outdir / f"raw_{i:03d}.mp4"
        try:
            download(url, raw)
            sources.append((raw, duration_of(raw), has_audio(raw)))
        except Exception as exc:
            print(f"! اللقطة دي مش راضية تنزل، هعدّيها: {exc}", flush=True)

    if not sources:
        raise RuntimeError("مفيش ولا لقطة اشتغلت — مش هينفع نركّب فيديو")

    needed = max(1, math.ceil(target_total / SEGMENT_SECONDS))
    segments = []

    for n in range(needed):
        src, src_duration, src_has_audio = sources[n % len(sources)]
        lap = n // len(sources)
        start = min(lap * SEGMENT_SECONDS, max(0.0, src_duration - SEGMENT_SECONDS))
        out = outdir / f"seg_{n:03d}.mp4"
        try:
            if src_has_audio:
                cmd = ["ffmpeg", "-y", "-ss", f"{start:.2f}", "-t", f"{SEGMENT_SECONDS:.2f}",
                       "-i", str(src), "-vf", motion_filter(n),
                       "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                       "-pix_fmt", "yuv420p",
                       "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
                       str(out)]
            else:
                # مفيش صوت في المصدر — بنحط سكتة بنفس المدة عشان القطعة
                # تفضل متوافقة مع القطع التانية اللي فيها صوت.
                cmd = ["ffmpeg", "-y", "-ss", f"{start:.2f}", "-t", f"{SEGMENT_SECONDS:.2f}",
                       "-i", str(src),
                       "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                       "-t", f"{SEGMENT_SECONDS:.2f}",
                       "-vf", motion_filter(n), "-map", "0:v", "-map", "1:a",
                       "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                       "-pix_fmt", "yuv420p",
                       "-c:a", "aac", "-b:a", "128k",
                       "-shortest", str(out)]
            run(cmd)
            segments.append(out)
        except subprocess.CalledProcessError as exc:
            print(f"! القطعة دي فشلت، هعدّيها: {exc}", flush=True)

    if not segments:
        raise RuntimeError("مفيش ولا قطعة اتعملت — مش هينفع نركّب فيديو")
    return segments


def concat_video(clips, target_total, outfile):
    """يلزق اللقطات (بفيديوها وصوتها) ويكررها لو مش مغطية مدة الصوت."""
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


def compose(video, voice_audio, outfile):
    """
    التركيب النهائي: فيديو + صوت خلفيته الأصلي (خافت) + التعليق الصوتي فوقه.

    مكناش بنستخدم غير صوت التعليق ومسحنا صوت اللقطات خالص، فالفيديو كان
    حاسس إنه فاضي. دلوقتي بنخفّض صوت الخلفية ونمزجه مع التعليق بدل ما
    نستبدله بالكامل.
    """
    total = duration_of(voice_audio)
    run([
        "ffmpeg", "-y", "-i", str(video), "-i", str(voice_audio),
        "-filter_complex",
        f"[0:a]volume={AMBIENCE_VOLUME}[amb];"
        f"[amb][1:a]amix=inputs=2:duration=longest:dropout_transition=0[aout]",
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
    voice_audio = synthesize(lines, voice, WORK / "voice.mp3")
    total = duration_of(voice_audio)
    print(f"    مدة التعليق: {total:.1f} ثانية")

    # الشورتس اللي بتكمّل للآخر بتكون في حدود ٤٠ ثانية، و١٨٠ هي حد يوتيوب نفسه.
    # مبنوقفش الرندر، بس بنسيب أثر في اللوج عشان نعرف السكريبت طوّل.
    if total > 180:
        print("! التعليق أطول من ١٨٠ ثانية، ده مش Short خالص. قصّر السكريبت.")
    elif total > 50:
        print(f"! التعليق {total:.0f} ثانية — أطول من المستهدف (٣٠-٤٠). الاحتفاظ هيقل.")

    print("==> بجهّز اللقطات وبقطّعها بحركة، مع صوتها الأصلي")
    segments = prepare_segments(clips, total, video_dir)
    ambient = concat_video(segments, total, WORK / "ambient.mp4")

    print("==> التركيب النهائي")
    final = compose(ambient, voice_audio, Path("output.mp4"))

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
