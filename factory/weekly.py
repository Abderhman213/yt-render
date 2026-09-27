#!/usr/bin/env python3
"""
التقرير الأسبوعي على تليجرام: لكل قناة شغالة، مشاهدات/لايكات/كومنتات
فيديوهات آخر 7 أيام، وأعلى 3 فيديوهات. محتاج YOUTUBE_API_KEY.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from factory import services  # noqa: E402
from factory.channels import CHANNELS  # noqa: E402

DAYS = 7


def channel_report(ch, since):
    rows = services.Sheet(ch["sheet_id"], ch["sheet_tab"]).rows()
    stats = services.youtube_stats([r.get("video_id") for r in rows if r.get("video_id")])
    recent = [v for v in stats.values()
              if v["published"] and datetime.fromisoformat(v["published"].replace("Z", "+00:00")) >= since]
    if not recent:
        return f"{ch['label'] or 'English'}: مفيش فيديوهات عامة الأسبوع ده."
    views = sum(v["views"] for v in recent)
    likes = sum(v["likes"] for v in recent)
    comments = sum(v["comments"] for v in recent)
    top = sorted(recent, key=lambda v: -v["views"])[:3]
    lines = [f"{ch['label'] or 'English'} — {len(recent)} فيديو | {views:,} مشاهدة | {likes:,} لايك | {comments:,} كومنت"]
    lines += [f"   {i}. {v['title']} — {v['views']:,}" for i, v in enumerate(top, 1)]
    return "\n".join(lines)


def main():
    since = datetime.now(timezone.utc) - timedelta(days=DAYS)
    parts = []
    for ch in CHANNELS.values():
        if not ch.get("enabled", True):
            continue
        try:
            parts.append(channel_report(ch, since))
        except BaseException as exc:  # noqa: BLE001 — قناة واحدة فاشلة ماتوقفش التقرير
            parts.append(f"{ch['label'] or 'English'}: فشل التقرير ({exc})")
    text = "📊 تقرير الأسبوع\n\n" + "\n\n".join(parts)
    print(text)
    # تليجرام حده 4096 حرف للرسالة
    for i in range(0, len(text), 3900):
        services.telegram(text[i:i + 3900])


if __name__ == "__main__":
    main()
