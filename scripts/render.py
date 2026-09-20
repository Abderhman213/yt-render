#!/usr/bin/env python3
"""
عامل الرندر — بيشتغل جوه GitHub Actions، من غير سيرفر ومن غير كارت.

بياخد ملف payload.json فيه السكريبت وروابط اللقطات، وبيطلّع فيديو Short
مقاس 1080x1920 بصوت وترجمة محروقة.

الخطوات:
  1. يحوّل كل جملة لصوت لوحدها بـ edge-tts  (مجاني، من غير مفتاح)
  2. يقيس مدة كل ملف صوت بـ ffprobe ويبني منها ملف الترجمة
  3. ينزّل اللقطات ويوحّد مقاسها على 1080x1920
  4. يركّب الكل: فيديو + صوت + ترجمة محروقة
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


def ass_timestamp(seconds):
    """0.0 -> 0:00:00.00"""
    if seconds < 0:
        seconds = 0.0
    cs = int(round(seconds * 100))
    h, cs = divmod(cs, 360_000)
    m, cs = divmod(cs, 6_000)
    s, cs = divmod(cs, 100)
    return f"{h:01d}:{m:02d}:{s:02d}.{cs:02d}"


ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,DejaVu Sans,58,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,4,1,2,60,60,380,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def build_ass(lines, durations, width, height, gap=GAP_BETWEEN_LINES):
    """
    يبني ملف ترجمة (ASS) من الجمل ومدة كل واحدة.

    بنعرف التوقيت بالظبط لأننا إحنا اللي حوّلنا كل جملة لصوت لوحدها —
    فمفيش حاجة تتخمّن ومفيش حاجة للتعرّف الآلي على الكلام.

    بنستخدم ASS بدل SRT وبنحدد PlayResX/PlayResY بمقاس الفيديو الحقيقي
    عشان الهوامش (MarginV) تتحسب بالبكسل الصح مباشرة. لو سبنا ffmpeg
    يحوّل SRT لـ ASS لوحده، بيفترض دقة قديمة (384x288) للحسابات الداخلية
    حتى لو ضفنا force_style/original_size — أي MarginV أكبر من الدقة
    الوهمية دي كان بيدفع الترجمة برّه الشاشة تمامًا (تختفي خالص).
    """
    if len(lines) != len(durations):
        raise ValueError("عدد الجمل مش مطابق لعدد المدد")

    events = []
    cursor = 0.0
    for text, dur in zip(lines, durations):
        start, end = cursor, cursor + dur
        clean = text.strip().replace("\n", " ")
        events.append(
            f"Dialogue: 0,{ass_timestamp(start)},{ass_timestamp(end)},Default,,0,0,0,,{clean}"
        )
        cursor = end + gap
    return ASS_HEADER.format(width=width, height=height) + "\n".join(events) + "\n"


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


def grab_thumbnail(video, first_line_duration, outfile):
    """
    ياخد كادر ثابت للغلاف من وقت الجملة الأولى.

    يوتيوب بيختار كادر الغلاف لوحده لو مبعتناش واحد، وساعات بيقع في السكتة
    اللي بين الجمل (GAP_BETWEEN_LINES) فيطلع الغلاف من غير ترجمة خالص.
    بناخد الكادر من نص الجملة الأولى عشان نضمن إن الترجمة ظاهرة فيه.
    """
    at = max(0.2, min(first_line_duration / 2, first_line_duration - 0.15))
    run(["ffmpeg", "-y", "-ss", f"{at:.2f}", "-i", str(video),
         "-frames:v", "1", "-update", "1", "-q:v", "2", str(outfile)])
    return outfile


def compose(video, voice_audio, ass_file, outfile):
    """التركيب النهائي: صورة + تعليق + ترجمة محروقة."""
    total = duration_of(voice_audio)

    # ملف الـ ASS نفسه فيه PlayResX/PlayResY بمقاس الفيديو الحقيقي، فمفيش
    # داعي لـ force_style أو original_size هنا — المقاسات والهوامش
    # محسوبة بالبكسل الصح جوه الملف مباشرة (راجع build_ass).
    subtitles = f"subtitles={ass_file}"
    # الترجمة بتتحرق على الصورة — أغلب مشاهدين الـ Shorts بيتفرجوا من غير صوت

    cmd = ["ffmpeg", "-y", "-i", str(video), "-i", str(voice_audio),
           "-vf", subtitles, "-map", "0:v", "-map", "1:a"]

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
    ass_file = WORK / "subs.ass"
    ass_file.write_text(build_ass(lines, durations, W, H), encoding="utf-8")

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
    final = compose(silent, voice_audio, ass_file, Path("output.mp4"))

    print("==> بجهّز كادر الغلاف")
    thumbnail = grab_thumbnail(final, durations[0], Path("thumbnail.jpg"))

    meta = {
        "id": payload.get("id", ""),
        "file": str(final),
        "thumbnail": str(thumbnail),
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
