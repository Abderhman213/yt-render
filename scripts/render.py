#!/usr/bin/env python3
"""
عامل الرندر — بيشتغل جوه GitHub Actions، من غير سيرفر ومن غير كارت.

بياخد ملف payload.json فيه السكريبت وروابط اللقطات، وبيطلّع فيديو Short
مقاس 1080x1920 بصوت.

الخطوات:
  1. يحوّل كل جملة لصوت لوحدها بـ edge-tts  (مجاني، من غير مفتاح)
  2. يلزق ملفات الصوت مع سكتة بسيطة بينهم
  3. ينزّل اللقطات ويوحّد مقاسها على 1080x1920
  4. يركّب الكل: فيديو + صوت
"""

import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

WORK = Path("work")
W, H = 1080, 1920
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


def download(url, dest):
    print(f"+ download {url} -> {dest}", flush=True)
    req = urllib.request.Request(url, headers={"User-Agent": "render-worker/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)
    return dest


# ---------------------------------------------------------------- مراحل

def synthesize(lines, voice, outdir):
    """جملة جملة، علشان نقدر نحط سكتة بينهم."""
    parts = []
    for i, line in enumerate(lines):
        part = outdir / f"line_{i:03d}.mp3"
        run(["edge-tts", "--voice", voice, "--text", line, "--write-media", str(part)])
        parts.append(part)
    return parts


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


def compose(video, voice_audio, outfile):
    """التركيب النهائي: صورة + تعليق صوتي."""
    total = duration_of(voice_audio)
    run([
        "ffmpeg", "-y", "-i", str(video), "-i", str(voice_audio),
        "-map", "0:v", "-map", "1:a",
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
    audio_dir = WORK / "audio"
    video_dir = WORK / "video"
    audio_dir.mkdir(exist_ok=True)
    video_dir.mkdir(exist_ok=True)

    print("==> بحوّل السكريبت لصوت")
    parts = synthesize(lines, voice, audio_dir)

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
    final = compose(silent, voice_audio, Path("output.mp4"))

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
