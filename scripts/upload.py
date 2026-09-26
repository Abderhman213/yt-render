#!/usr/bin/env python3
"""
يرفع الفيديو على يوتيوب.

بيقرا بيانات الدخول من متغيرات البيئة (أسرار المستودع):
  YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN

بيطبع رقم الفيديو في مخرجات الـ Action علشان n8n يسجّله في الجدول.
"""

import json
import os
import random
import re
import sys
import time
from pathlib import Path

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

SHORTS_MAX_SECONDS = 180
MAX_TITLE_HASHTAGS = 5
MAX_UPLOAD_RETRIES = 5
RETRIABLE_STATUS_CODES = {500, 502, 503, 504}


def hashtag(tag):
    """'human biology' -> '#HumanBiology'. None لو التاج فاضي/رموز بس."""
    words = re.findall(r"[A-Za-z0-9]+", tag)
    return "#" + "".join(w.capitalize() for w in words) if words else None


def credentials():
    missing = [k for k in ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN")
               if not os.environ.get(k)]
    if missing:
        raise SystemExit("ناقص أسرار: " + ", ".join(missing))

    return Credentials(
        token=None,
        refresh_token=os.environ["YT_REFRESH_TOKEN"],
        client_id=os.environ["YT_CLIENT_ID"],
        client_secret=os.environ["YT_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
    )


def upload_with_retry(request):
    """
    بيرفع الفيديو تشنك تشنك، وبيعيد المحاولة لو حصل عطل مؤقت (شبكة أو
    خطأ 5xx من يوتيوب) بدل ما يفشل التشغيلة كلها على أول عطل عابر.
    resumable=True يخلي next_chunk() يكمل من نفس النقطة بعد كل محاولة.
    """
    response = None
    retry = 0
    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                print(f"... رفع {int(status.progress() * 100)}%", flush=True)
        except HttpError as exc:
            if exc.resp.status not in RETRIABLE_STATUS_CODES or retry >= MAX_UPLOAD_RETRIES:
                raise
            retry += 1
            wait = min(2 ** retry + random.random(), 60)
            print(f"! خطأ رفع مؤقت ({exc.resp.status})، محاولة {retry}/{MAX_UPLOAD_RETRIES} بعد {wait:.0f} ثانية",
                  flush=True)
            time.sleep(wait)
        except (ConnectionError, TimeoutError, OSError) as exc:
            if retry >= MAX_UPLOAD_RETRIES:
                raise
            retry += 1
            wait = min(2 ** retry + random.random(), 60)
            print(f"! انقطاع شبكة أثناء الرفع، محاولة {retry}/{MAX_UPLOAD_RETRIES} بعد {wait:.0f} ثانية: {exc}",
                  flush=True)
            time.sleep(wait)
    return response


def main():
    meta = json.loads(Path("output.json").read_text(encoding="utf-8"))
    video = Path(meta["file"])
    if not video.exists():
        raise SystemExit(f"مفيش ملف فيديو: {video}")

    title = meta["title"][:100]
    description = meta["description"][:4900]
    # الرندر بيحدّد اللغة من الصوت. لو ناقصة لأي سبب، الإنجليزي هو الافتراضي.
    language = meta.get("language") or "en"

    # سطر هاشتاجات من التاجات نفسها، بالإضافة لـ #Shorts (بيساعد يوتيوب يصنّفه صح)
    tags_line = " ".join(
        h for h in (hashtag(t) for t in meta.get("tags", [])[:MAX_TITLE_HASHTAGS]) if h
    )
    # الفيديو الطويل الأفقي (format=long) عمره ما يكون Short، فمبنحطلوش #Shorts.
    is_short = meta.get("format", "short") != "long" and meta["duration"] <= SHORTS_MAX_SECONDS
    if is_short and "#Shorts" not in tags_line:
        tags_line = f"{tags_line} #Shorts".strip()
    if tags_line:
        description = f"{description}\n\n{tags_line}"

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": meta.get("tags", [])[:15],
            "categoryId": str(meta.get("category_id", "27")),
            "defaultLanguage": language,
            "defaultAudioLanguage": language,
        },
        "status": {
            "privacyStatus": meta.get("privacy", "public"),
            "selfDeclaredMadeForKids": False,
        },
    }

    youtube = build("youtube", "v3", credentials=credentials())
    media = MediaFileUpload(str(video), chunksize=-1, resumable=True,
                            mimetype="video/mp4")

    request = youtube.videos().insert(
        part="snippet,status", body=body, media_body=media
    )

    response = upload_with_retry(request)

    video_id = response["id"]
    url = f"https://www.youtube.com/watch?v={video_id}"
    print(f"==> اترفع: {url}")

    # نخرّج النتيجة علشان n8n يقراها
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"video_id={video_id}\n")
            f.write(f"video_url={url}\n")

    Path("uploaded.json").write_text(
        json.dumps(
            {"id": meta.get("id", ""), "video_id": video_id, "url": url, "title": title},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
