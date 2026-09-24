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
import random
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
MUSIC_VOLUME = 0.25          # حجم الموسيقى قبل ما تتخفض تحت الكلام — خُفِّض بطلب المستخدم
DEFAULT_VOICE = "en-US-AndrewNeural"

# تراكات موسيقى خلفية حقيقية وجاهزة (Public Domain / CC0، راجع
# assets/music/SOURCES.md للمصدر والرخصة) — دي الاختيار الأساسي بطلب المستخدم
# صراحة ("هات تراكات جاهزة"). بنعمل loudnorm لأي تراك نختاره عشان مستوى الصوت
# يبقى ثابت مهما اختلف التراك الأصلي، فتوازن sidechaincompress في compose()
# يفضل شغال صح.
MUSIC_DIR = Path("assets/music")
MUSIC_MANIFEST = MUSIC_DIR / "manifest.json"
# مزاج افتراضي لو الـ payload مبعتش mood أو المزاج المطلوب مش موجود له تراك.
DEFAULT_MOOD = "cinematic"

# لو مفيش تراكات حقيقية (بيئة تجريبية من غير assets/music مثلاً)، بنرجع
# لموسيقى بنولّدها بنفسنا كـ fallback عشان الرندر ميقعش. تتابع أكوردات
# Am–F–C–G (لوب ١٢ ثانية) بيتكرر لحد ما يغطي الفيديو، نغمات جيبية مع طبقة
# مزاحة بسيطة (detune) عشان دفء، وفلتر وصدى خفيف.
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


IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")


def download(url, dest):
    print(f"+ download {url} -> {dest}", flush=True)
    req = urllib.request.Request(url, headers={"User-Agent": "render-worker/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
        content_type = r.headers.get("Content-Type", "")
        shutil.copyfileobj(r, f)
    return content_type


def _is_image_url(url, content_type):
    if any(url.lower().split("?")[0].endswith(ext) for ext in IMAGE_EXTENSIONS):
        return True
    return content_type.startswith("image/")


def fetch_sources(urls, outdir):
    """
    ينزّل كل اللقطات مرة واحدة، ويرجّع مسارها ومدتها ونوعها (فيديو/صورة).

    بعض القنوات (زي الإسبانية) بقت تجيب صور تاريخية حقيقية موثقة بدل
    فيديوهات ستوك عشوائية — الصورة مالهاش "مدة" حقيقية، فبنديها مدة
    وهمية كبيرة عشان منطق التدوير على المصادر (lap) يشتغل عادي، وبنعلّم
    عليها is_image عشان prepare_segments يستخدم -loop 1 بدل -ss.
    """
    sources = []
    for i, url in enumerate(urls):
        # مبنعرفش النوع غير بعد التحميل (بعض الروابط من غير امتداد واضح)
        tmp = outdir / f"raw_{i:03d}.tmp"
        try:
            content_type = download(url, tmp)
            is_image = _is_image_url(url, content_type)
            raw = outdir / f"raw_{i:03d}{'.jpg' if is_image else '.mp4'}"
            tmp.rename(raw)
            dur = 9999.0 if is_image else duration_of(raw)
            sources.append((raw, dur, is_image))
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


TEXT_CARD_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def _drawtext_escape(s):
    """بيهرّب الحروف اللي بتكسر فلتر drawtext (：و\\ و%) ويبدّل التنصيصة بعلامة يونيكود عشان متكسرش الـ text='...'."""
    return (s or "").replace("\\", "\\\\").replace(":", "\\:").replace("%", "\\%").replace("'", "’")


def build_text_card(text, subtext, color, outfile, duration):
    """
    كارت نصي بخلفية لون الفريق — بديل آمن عن لقطة جول حقيقية غير مرخّصة.

    بيتعمل من غير زوم هنا؛ الزوم بيتحط بعدين لما prepare_segments يعامله
    كأي مصدر فيديو عادي (نفس motion_filter اللي بيتحط على أي لقطة تانية)،
    عشان منعملش زوم فوق زوم.
    """
    color_hex = color if color.startswith("0x") else "0x" + color.lstrip("#")
    filters = [
        f"drawtext=fontfile={TEXT_CARD_FONT}:text='{_drawtext_escape(text)}':"
        f"fontcolor=white:fontsize=84:x=(w-text_w)/2:y=(h-text_h)/2-100:"
        f"box=1:boxcolor=black@0.35:boxborderw=28"
    ]
    if subtext:
        filters.append(
            f"drawtext=fontfile={TEXT_CARD_FONT}:text='{_drawtext_escape(subtext)}':"
            f"fontcolor=white:fontsize=44:x=(w-text_w)/2:y=(h-text_h)/2+60:"
            f"box=1:boxcolor=black@0.35:boxborderw=18"
        )
    # لازم fps=FPS من الأول (مش الافتراضي 25 بتاع lavfi) عشان مدة الكارت
    # تطلع مضبوطة لما prepare_segments يقصّه بعدين بـ -ss/-t زي أي فيديو عادي.
    run(["ffmpeg", "-y", "-f", "lavfi",
         "-i", f"color=c={color_hex}:s={W}x{H}:d={duration + 0.5:.2f}:r={FPS}",
         "-vf", ",".join(filters), "-t", f"{duration + 0.5:.2f}",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-pix_fmt", "yuv420p", str(outfile)])
    return outfile


def build_moment_cards(moments, outdir):
    """
    بيحوّل payload['moments'] (لحظات موثقة زي جول/رقم قياسي) لمصادر فيديو
    جاهزة تتحط في نفس دورة اللقطات العادية — كارت لون بالنص، مش لقطة حقيقية.
    """
    cards = []
    for i, m in enumerate(moments or []):
        text = (m.get("text") or "").strip()
        if not text:
            continue
        subtext = (m.get("subtext") or "").strip()
        color = (m.get("color") or "1a1a2e").strip()
        out = outdir / f"card_{i:03d}.mp4"
        try:
            build_text_card(text, subtext, color, out, SEGMENT_SECONDS)
            cards.append((out, duration_of(out), False))
        except subprocess.CalledProcessError as exc:
            print(f"! كارت النص ده فشل، هعدّيه: {exc}", flush=True)
    return cards


CTA_MIN_SEGMENTS = 6   # لازم فيديو 15 ثانية+ (6 قطع) عشان نضيف كسرة اللايك/الجرس/الاشتراك من غير ما ناكل حاجة مهمة

# كل عنصر: (نص، نص فرعي، لون خلفية hex من غير #) — بترتيب اللقطات
# (لايك، جرس، اشتراك) عشان يتزامن مع صوت كل واحد فيهم في build_cta_sfx.
CTA_TEXTS = {
    "es": [
        ("DALE LIKE", "Ayuda más de lo que crees", "E02222"),
        ("ACTIVA LA CAMPANA", "Para no perderte el próximo", "F2A900"),
        ("SUSCRÍBETE YA", "Únete a miles más", "1F6FEB"),
    ],
    "en": [
        ("HIT LIKE", "It helps more than you think", "E02222"),
        ("TURN ON THE BELL", "Never miss the next one", "F2A900"),
        ("SUBSCRIBE NOW", "Join thousands of others", "1F6FEB"),
    ],
}


def _cta_texts(voice):
    lang = (voice or "").split("-")[0].lower()
    return CTA_TEXTS.get(lang, CTA_TEXTS["en"])


def build_cta_segment(text, subtext, color, index, outfile):
    """
    قطعة "اعمل لايك/فعّل الجرس/اشترك" — نفس أسلوب build_text_card بس
    بحركة زوم (motion_filter) محطوطة من الأول ومقصوصة بالظبط SEGMENT_SECONDS،
    عشان تتحط مباشرة في قائمة القطع الجاهزة (segments) من غير مرحلة تقطيع تانية.
    """
    color_hex = color if color.startswith("0x") else "0x" + color.lstrip("#")
    filters = [
        motion_filter(index),
        f"drawtext=fontfile={TEXT_CARD_FONT}:text='{_drawtext_escape(text)}':"
        f"fontcolor=white:fontsize=88:x=(w-text_w)/2:y=(h-text_h)/2-90:"
        f"box=1:boxcolor=black@0.35:boxborderw=28",
        f"drawtext=fontfile={TEXT_CARD_FONT}:text='{_drawtext_escape(subtext)}':"
        f"fontcolor=white:fontsize=42:x=(w-text_w)/2:y=(h-text_h)/2+70:"
        f"box=1:boxcolor=black@0.35:boxborderw=18",
    ]
    run(["ffmpeg", "-y", "-f", "lavfi",
         "-i", f"color=c={color_hex}:s={W}x{H}:d={SEGMENT_SECONDS + 0.3:.2f}:r={FPS}",
         "-vf", ",".join(filters), "-t", f"{SEGMENT_SECONDS:.2f}",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-pix_fmt", "yuv420p", str(outfile)])
    return outfile


def build_cta_segments(voice, start_index, outdir):
    """بيطلّع 3 قطع (لايك/جرس/اشتراك) بلغة الفيديو، جاهزين يحلّوا محل 3 قطع في نص الفيديو."""
    out = []
    for i, (text, subtext, color) in enumerate(_cta_texts(voice)):
        path = outdir / f"cta_{i:03d}.mp4"
        build_cta_segment(text, subtext, color, start_index + i, path)
        out.append(path)
    return out


def _sfx_like_pop(outfile):
    """صوت "بوب" صاعد قصير — بيتزامن مع كارت اللايك."""
    run(["ffmpeg", "-y", "-f", "lavfi",
         "-i", "aevalsrc=0.6*sin(2*PI*(600+900*t)*t):d=0.16:s=44100",
         "-af", "afade=t=in:st=0:d=0.02,afade=t=out:st=0.1:d=0.06",
         "-c:a", "pcm_s16le", str(outfile)])
    return outfile


def _sfx_bell(outfile):
    """رنة جرس (نغمتين متناغمتين + خفوت أسّي) — بتتزامن مع كارت الجرس."""
    run(["ffmpeg", "-y",
         "-f", "lavfi", "-i", "sine=frequency=1318.5:d=1.3:sample_rate=44100",
         "-f", "lavfi", "-i", "sine=frequency=2637:d=1.3:sample_rate=44100",
         "-filter_complex",
         "[0:a]volume=0.7[a];[1:a]volume=0.3[b];[a][b]amix=inputs=2:duration=first:normalize=0,"
         "afade=t=out:st=0.05:d=1.2:curve=exp",
         "-c:a", "pcm_s16le", str(outfile)])
    return outfile


def _sfx_click(outfile):
    """طقّة زرار قصيرة — بتتزامن مع كارت الاشتراك."""
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anoisesrc=d=0.06:c=white:sample_rate=44100",
         "-af", "highpass=f=1500,lowpass=f=6000,afade=t=out:st=0:d=0.06",
         "-c:a", "pcm_s16le", str(outfile)])
    return outfile


def build_cta_sfx(cta_start, total_duration, outdir):
    """
    تراك صوتي فيه بس ٣ أصوات قصيرة (بوب/جرس/طقّة) في توقيت ظهور كل كارت،
    بيتحط فوق الموسيقى والتعليق في compose() من غير ما يقطع أي حاجة منهم.

    apad بيخلّي كل مدخل غير محدود المدة عشان amix (duration=longest) يستناهم
    كلهم لحد آخر واحد بيخلص؛ لازم -t هنا وإلا ffmpeg هيفضل شغال للأبد.
    """
    pop, bell, click = outdir / "sfx_pop.wav", outdir / "sfx_bell.wav", outdir / "sfx_click.wav"
    _sfx_like_pop(pop)
    _sfx_bell(bell)
    _sfx_click(click)

    pop_ms = int((cta_start + 0.35) * 1000)
    bell_ms = int((cta_start + SEGMENT_SECONDS + 0.15) * 1000)
    click_ms = int((cta_start + 2 * SEGMENT_SECONDS + 0.35) * 1000)

    out = outdir / "cta_sfx.m4a"
    run(["ffmpeg", "-y", "-i", str(pop), "-i", str(bell), "-i", str(click),
         "-filter_complex",
         f"[0:a]adelay={pop_ms}|{pop_ms},apad[a0];"
         f"[1:a]adelay={bell_ms}|{bell_ms},apad[a1];"
         f"[2:a]adelay={click_ms}|{click_ms},apad[a2];"
         f"[a0][a1][a2]amix=inputs=3:duration=longest:normalize=0[mix]",
         "-map", "[mix]", "-t", f"{total_duration:.2f}",
         "-c:a", "aac", "-b:a", "192k", str(out)])
    return out


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
        src, src_duration, is_image = sources[n % len(sources)]
        out = outdir / f"seg_{n:03d}.mp4"
        try:
            if is_image:
                # صورة ثابتة — من غير -ss (مفيش حاجة نتقدّم فيها)، بنلفّها
                # لمدة القطعة وحركة الزوم (motion_filter) بتديها إحساس الحركة.
                cmd = ["ffmpeg", "-y", "-loop", "1", "-i", str(src),
                       "-t", f"{SEGMENT_SECONDS:.2f}", "-an", "-vf", motion_filter(n),
                       "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                       "-pix_fmt", "yuv420p", str(out)]
            else:
                lap = n // len(sources)
                start = min(lap * SEGMENT_SECONDS, max(0.0, src_duration - SEGMENT_SECONDS))
                cmd = ["ffmpeg", "-y", "-ss", f"{start:.2f}", "-t", f"{SEGMENT_SECONDS:.2f}",
                       "-i", str(src), "-an", "-vf", motion_filter(n),
                       "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                       "-pix_fmt", "yuv420p", str(out)]
            run(cmd)
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


def _load_manifest():
    """{اسم الملف: [مزاج, مزاج...]} من manifest.json، أو {} لو الملف مش موجود."""
    if not MUSIC_MANIFEST.is_file():
        return {}
    try:
        return json.loads(MUSIC_MANIFEST.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _pick_real_track(mood=None):
    """
    تراك من assets/music يناسب المزاج المطلوب، وعشوائي بين المرشّحين لو
    أكتر من واحد بيطابق — عشان نفس المزاج مايطلّعش نفس التراك كل مرة.

    لو مفيش مزاج متحدد أو مفيش تراك بيطابقه، بنرجع لأي تراك عشوائي بدل
    ما نوقف الرندر على تفصيلة تصنيف.
    """
    if not MUSIC_DIR.is_dir():
        return None
    tracks = sorted(MUSIC_DIR.glob("*.mp3"))
    if not tracks:
        return None

    manifest = _load_manifest()
    if mood:
        matching = [t for t in tracks if mood in manifest.get(t.name, [])]
        if matching:
            return random.choice(matching)

    return random.choice(tracks)


def build_music(target_total, outfile, outdir, mood=None):
    """
    موسيقى خلفية متصلة تغطي الفيديو كله.

    بنفضّل تراك حقيقي جاهز من assets/music (Public Domain / CC0 — راجع
    SOURCES.md) يناسب مزاج الموضوع (mood من الـ payload، زي upbeat/calm/
    dark/epic/electronic — راجع manifest.json)، وبنطبّعه (loudnorm) لمستوى
    ثابت مهما كان التراك الأصلي عالي أو واطي، عشان توازن sidechaincompress
    في compose() يفضل شغال زي ما اتكيّل. لو مفيش تراكات، بنرجع لموسيقى
    مولّدة محليًا كـ fallback.
    """
    track = _pick_real_track(mood)
    if track is None:
        print("    مفيش تراكات حقيقية في assets/music، هستخدم موسيقى مولّدة كـ fallback", flush=True)
        return _build_generated_music(target_total, outfile, outdir)

    # بعض التراكات فيها صورة غلاف مضمّنة (attached pic) بتتقرا كـ"فيديو" —
    # -vn يمنع ffmpeg يحاول يشفّرها مع الصوت في حاوية m4a وتفشل العملية.
    normalized = outdir / "track_normalized.m4a"
    run(["ffmpeg", "-y", "-i", str(track), "-vn",
         "-af", "loudnorm=I=-19:TP=-1.5:LRA=11",
         "-c:a", "aac", "-b:a", "192k", str(normalized)])

    track_seconds = duration_of(normalized)
    loops = max(0, math.ceil(target_total / track_seconds) - 1)
    run(["ffmpeg", "-y", "-stream_loop", str(loops), "-i", str(normalized), "-vn",
         "-t", f"{target_total:.2f}", "-c:a", "aac", "-b:a", "192k", str(outfile)])
    print(f"    موسيقى الخلفية: {track.name} (تراك حقيقي Public Domain)", flush=True)
    return outfile


def _build_generated_music(target_total, outfile, outdir):
    """
    Fallback: موسيقى خلفية بنولّدها بنفسنا بالكامل، لو مفيش تراكات جاهزة.

    بنبني لوب ١٢ ثانية (تتابع أكوردات Am–F–C–G) وبنكرّره لحد ما يغطي مدة
    التعليق. توليدها محليًا معناه صفر مخاطرة حقوق نشر.
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


def compose(video, music_audio, voice_audio, outfile, sfx_audio=None):
    """
    التركيب النهائي: فيديو + موسيقى خلفية بتنخفض تحت الكلام + التعليق فوقه
    (+ صوت كسرة اللايك/الجرس/الاشتراك لو موجودة).

    الموسيقى بتتخفض أوتوماتيك وقت ما الكلام بيشتغل (sidechaincompress) وبترجع
    وقت السكتات، فالكلام دايمًا أوضح منها — بدل ما نسيب حجم ثابت بيزاحم الصوت.
    """
    total = duration_of(voice_audio)
    inputs = ["-i", str(video), "-i", str(music_audio), "-i", str(voice_audio)]
    # نقسم الكلام لنسختين: واحدة تتحكّم في خفض الموسيقى، وواحدة تتحطّ فوقها
    # normalize=0 مهم: من غيره amix بيقسم كل مدخل على عدد المدخلات فيخفّض الكلام نفسه.
    # الموسيقى مخفوضة ومكتومة تحت الكلام، وفي الآخر limiter يمنع أي تكسير.
    graph = (
        f"[2:a]asplit=2[vkey][vmix];"
        f"[1:a]volume={MUSIC_VOLUME}[bg];"
        f"[bg][vkey]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=300[duck];"
    )
    if sfx_audio:
        inputs += ["-i", str(sfx_audio)]
        graph += (
            f"[3:a]volume=0.55[sfx];"
            f"[duck][vmix][sfx]amix=inputs=3:duration=first:dropout_transition=0:normalize=0[mix];"
        )
    else:
        graph += f"[duck][vmix]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[mix];"
    graph += "[mix]alimiter=limit=0.95[aout]"

    run([
        "ffmpeg", "-y", *inputs,
        "-filter_complex", graph,
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
    moments = payload.get("moments") or []
    if not clips and not moments:
        raise SystemExit("مفيش لقطات ولا مواقف نصية في الـ payload")

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

    if moments:
        print(f"==> بعمل {len(moments)} كارت نص للحظات الموثقة (بديل آمن عن لقطة أصلية)")
        sources.extend(build_moment_cards(moments, video_dir))

    print("==> بقطّع اللقطات بحركة")
    segments = prepare_segments(sources, total, video_dir)

    cta_sfx = None
    if len(segments) >= CTA_MIN_SEGMENTS:
        mid = len(segments) // 2
        print("==> بحط كسرة لايك/جرس/اشتراك في نص الفيديو")
        cta_segments = build_cta_segments(voice, mid, video_dir)
        segments[mid:mid + len(cta_segments)] = cta_segments
        cta_sfx = build_cta_sfx(mid * SEGMENT_SECONDS, total, video_dir)

    silent = concat_video(segments, total, WORK / "silent.mp4")

    print("==> بجهّز موسيقى الخلفية")
    music_dir = WORK / "music"
    music_dir.mkdir(exist_ok=True)
    mood = payload.get("mood") or DEFAULT_MOOD
    music = build_music(total, WORK / "music.m4a", music_dir, mood=mood)

    print("==> التركيب النهائي")
    final = compose(silent, music, voice_audio, Path("output.mp4"), sfx_audio=cta_sfx)

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
