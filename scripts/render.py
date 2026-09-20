#!/usr/bin/env python3
"""
عامل الرندر — بيشتغل جوه GitHub Actions، من غير سيرفر ومن غير كارت.

بياخد ملف payload.json فيه السكريبت وروابط اللقطات، وبيطلّع فيديو Short
مقاس 1080x1920 بصوت وترجمة محروقة.

الخطوات:
  1. يحوّل كل جملة لصوت لوحدها بـ edge-tts  (مجاني، من غير مفتاح)
  2. يقيس مدة كل ملف صوت بـ ffprobe ويبني منها ملف الترجمة
  3. ينزّل اللقطات ويوحّد مقاسها على 1080x1920
  4. يركّب الكل: فيديو + صوت + موسيقى خلفية + ترجمة محروقة
"""

import json
import os
import random
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

WORK = Path("work")
W, H = 1080, 1920
MUSIC_VOLUME = 0.12          # الموسيقى تحت التعليق الصوتي
GAP_BETWEEN_LINES = 0.25     # سكتة بسيطة بين الجمل، بالثواني
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


def srt_timestamp(seconds):
    """0.0 -> 00:00:00,000"""
    if seconds < 0:
        seconds = 0.0
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_srt(lines, durations, gap=GAP_BETWEEN_LINES):
    """
    يبني ملف ترجمة من الجمل ومدة كل واحدة.

    بنعرف التوقيت بالظبط لأننا إحنا اللي حوّلنا كل جملة لصوت لوحدها —
    فمفيش حاجة تتخمّن ومفيش حاجة للتعرّف الآلي على الكلام.
    """
    if len(lines) != len(durations):
        raise ValueError("عدد الجمل مش مطابق لعدد المدد")

    blocks = []
    cursor = 0.0
    for i, (text, dur) in enumerate(zip(lines, durations), start=1):
        start, end = cursor, cursor + dur
        blocks.append(
            f"{i}\n{srt_timestamp(start)} --> {srt_timestamp(end)}\n{text.strip()}\n"
        )
        cursor = end + gap
    return "\n".join(blocks)


def download(url, dest):
    print(f"+ download {url} -> {dest}", flush=True)
    req = urllib.request.Request(url, headers={"User-Agent": "render-worker/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)
    return dest


# ---------------------------------------------------------------- مراحل

def synthesize(lines, voice, outdir):
    """جملة جملة، علشان نعرف مدة كل واحدة بالظبط."""
    parts, durations = [], []
    for i, line in enumerate(lines):
        part = outdir / f"line_{i:03d}.mp3"
        run(["edge-tts", "--voice", voice, "--text", line, "--write-media", str(part)])
        parts.append(part)
        durations.append(duration_of(part))
    return parts, durations


def join_audio(parts, gap, outfile):
    """يلزق ملفات الصوت مع سكتة بينهم."""
    silence = WORK / "gap.mp3"
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
         "-t", str(gap), "-q:a", "9", str(silence)])

    listing = WORK / "audio_list.txt"
    with open(listing, "w", encoding="utf-8") as f:
        for i, p in enumerate(parts):
            f.write(f"file '{p.resolve()}'\n")
            if i < len(parts) - 1:
                f.write(f"file '{silence.resolve()}'\n")

    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
         "-c:a", "libmp3lame", "-q:a", "2", str(outfile)])
    return outfile


def normalize_clips(urls, target_total, outdir):
    """
    ينزّل كل لقطة ويحوّلها لمقاس Short، ويقصّها لطول متساوي
    بحيث مجموعهم يغطي مدة الصوت.
    """
    per_clip = max(2.5, target_total / max(1, len(urls)))
    normalized = []

    for i, url in enumerate(urls):
        raw = outdir / f"raw_{i:03d}.mp4"
        try:
            download(url, raw)
        except Exception as exc:
            print(f"! اللقطة دي مش راضية تنزل، هعدّيها: {exc}", flush=True)
            continue

        out = outdir / f"clip_{i:03d}.mp4"
        # نكبّر ونقص من النص علشان نملا الإطار العمودي من غير ما الصورة تتمطّ
        vf = (
            f"scale={W}:{H}:force_original_aspect_ratio=increase,"
            f"crop={W}:{H},fps=30,setsar=1"
        )
        try:
            run(["ffmpeg", "-y", "-t", f"{per_clip:.2f}", "-i", str(raw),
                 "-an", "-vf", vf, "-c:v", "libx264", "-preset", "veryfast",
                 "-crf", "23", "-pix_fmt", "yuv420p", str(out)])
            normalized.append(out)
        except subprocess.CalledProcessError as exc:
            print(f"! اللقطة دي فشلت في المعالجة، هعدّيها: {exc}", flush=True)

    if not normalized:
        raise RuntimeError("مفيش ولا لقطة اشتغلت — مش هينفع نركّب فيديو")
    return normalized


def concat_video(clips, target_total, outfile):
    """يلزق اللقطات ويكررها لو مش مغطية مدة الصوت."""
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


def pick_music(music_dir):
    if not music_dir.is_dir():
        return None
    tracks = sorted(
        p for p in music_dir.iterdir()
        if p.suffix.lower() in {".mp3", ".m4a", ".wav", ".ogg"}
    )
    return random.choice(tracks) if tracks else None


def compose(video, voice_audio, srt_file, music, outfile):
    """التركيب النهائي: صورة + تعليق + موسيقى + ترجمة محروقة."""
    total = duration_of(voice_audio)

    style = (
        "FontName=DejaVu Sans,FontSize=58,Bold=1,"
        "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
        "BorderStyle=1,Outline=4,Shadow=1,"
        "Alignment=2,MarginV=380,MarginL=60,MarginR=60"
    )
    # لازم نحدد PlayResX/Y يساووا مقاس الفيديو الحقيقي (1080x1920)، وإلا
    # libass بيفترض دقة قديمة (384x288) ويكبّر الخط والهامش أضعاف مضاعفة —
    # ده اللي كان بيخلي الترجمة تطلع عملاقة وتغطي أعلى الشاشة فوق واجهة يوتيوب.
    subtitles = (
        f"subtitles={srt_file}:force_style='{style}':original_size={W}x{H}"
    )
    # الترجمة بتتحرق على الصورة — أغلب مشاهدين الـ Shorts بيتفرجوا من غير صوت

    cmd = ["ffmpeg", "-y", "-i", str(video), "-i", str(voice_audio)]
    if music:
        cmd += ["-stream_loop", "-1", "-i", str(music)]
        filter_complex = (
            f"[0:v]{subtitles}[v];"
            f"[2:a]volume={MUSIC_VOLUME}[bg];"
            f"[1:a][bg]amix=inputs=2:duration=first:dropout_transition=0[a]"
        )
        cmd += ["-filter_complex", filter_complex, "-map", "[v]", "-map", "[a]"]
    else:
        cmd += ["-vf", subtitles, "-map", "0:v", "-map", "1:a"]

    cmd += [
        "-t", f"{total:.2f}",
        "-c:v", "libx264", "-preset", "medium", "-crf", "21",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(outfile),
    ]
    run(cmd)
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
    audio_dir = WORK / "audio"
    video_dir = WORK / "video"
    audio_dir.mkdir(exist_ok=True)
    video_dir.mkdir(exist_ok=True)

    print("==> بحوّل السكريبت لصوت")
    parts, durations = synthesize(lines, voice, audio_dir)

    print("==> ببني ملف الترجمة من مدة كل جملة")
    srt_file = WORK / "subs.srt"
    srt_file.write_text(build_srt(lines, durations), encoding="utf-8")

    print("==> بلزق الصوت")
    voice_audio = join_audio(parts, GAP_BETWEEN_LINES, WORK / "voice.mp3")
    total = duration_of(voice_audio)
    print(f"    مدة التعليق: {total:.1f} ثانية")

    if total > 175:
        print("! التعليق أطول من ١٧٥ ثانية، ده مش Short. قصّر السكريبت.")

    print("==> بجهّز اللقطات")
    normalized = normalize_clips(clips, total, video_dir)
    silent = concat_video(normalized, total, WORK / "silent.mp4")

    print("==> التركيب النهائي")
    music = pick_music(Path("assets/music"))
    if music:
        print(f"    موسيقى: {music.name}")
    final = compose(silent, voice_audio, srt_file, music, Path("output.mp4"))

    meta = {
        "id": payload.get("id", ""),
        "file": str(final),
        "duration": duration_of(final),
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
