#!/usr/bin/env python3
"""
يرفع الفيديو على يوتيوب.

بيقرا بيانات الدخول من متغيرات البيئة (أسرار المستودع):
  YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN

بيطبع رقم الفيديو في مخرجات الـ Action علشان n8n يسجّله في الجدول.
"""

import json
import os
import re
import sys
from pathlib import Path

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SHORTS_MAX_SECONDS = 180
MAX_TITLE_HASHTAGS = 5


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


def main():
    meta = json.loads(Path("output.json").read_text(encoding="utf-8"))
    video = Path(meta["file"])
    if not video.exists():
        raise SystemExit(f"مفيش ملف فيديو: {video}")

    title = meta["title"][:100]
    description = meta["description"][:4900]

    # سطر هاشتاجات من التاجات نفسها، بالإضافة لـ #Shorts (بيساعد يوتيوب يصنّفه صح)
    tags_line = " ".join(
        h for h in (hashtag(t) for t in meta.get("tags", [])[:MAX_TITLE_HASHTAGS]) if h
    )
    if meta["duration"] <= SHORTS_MAX_SECONDS and "#Shorts" not in tags_line:
        tags_line = f"{tags_line} #Shorts".strip()
    if tags_line:
        description = f"{description}\n\n{tags_line}"

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": meta.get("tags", [])[:15],
            "categoryId": str(meta.get("category_id", "27")),
            "defaultLanguage": "en",
            "defaultAudioLanguage": "en",
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

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"... رفع {int(status.progress() * 100)}%", flush=True)

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
