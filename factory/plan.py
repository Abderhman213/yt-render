#!/usr/bin/env python3
"""
الجدولة — بديل عقد "جدولة 4 مرات يوميًا" و"جدولة الفيديو الطويل (السبت)".

cron في GitHub بيشتغل بتوقيت UTC ومبيعرفش التوقيت الصيفي، فـ factory.yml بيصحى
كل ساعة، والسكربت ده بيبص على الساعة المحلية لكل قناة (نيويورك / مدريد) ويطلّع
matrix بالقنوات اللي ميعادها دلوقتي. كده المواعيد تفضل مظبوطة صيف وشتا.

  python factory/plan.py                     → حسب الساعة الحالية
  python factory/plan.py --channel bw --mode short   → تشغيل يدوي
  python factory/plan.py --channel all --mode short  → كل القنوات
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from factory.channels import CHANNELS  # noqa: E402

# الـ cron على دقيقة 7 من كل ساعة، وGitHub ساعات بيأخّره. بنطرح الـ 7 دقايق
# فأي تأخير لحد ~50 دقيقة بيتحسب على نفس الساعة.
CRON_MINUTE = 7


def due(now_utc):
    jobs = []
    for key, ch in CHANNELS.items():
        if not ch.get("enabled", True):
            continue  # قناة لسه مش جاهزة (مفاتيح يوتيوب أو الشيت ناقصين)
        local = (now_utc - timedelta(minutes=CRON_MINUTE)).astimezone(ZoneInfo(ch["tz"]))
        if local.hour in ch["hours"]:
            jobs.append({"channel": key, "mode": "short"})
        if local.weekday() == ch["long_day"] and local.hour == ch["long_hour"]:
            jobs.append({"channel": key, "mode": "long"})
    return jobs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", default="")
    ap.add_argument("--mode", default="short")
    ap.add_argument("--now", help="ISO timestamp للتجربة")
    args = ap.parse_args()

    if args.channel:
        keys = sorted(CHANNELS) if args.channel == "all" else [args.channel]
        for k in keys:
            if k not in CHANNELS:
                raise SystemExit(f"قناة مش معروفة: {k}")
        jobs = [{"channel": k, "mode": args.mode} for k in keys]
    else:
        now = datetime.fromisoformat(args.now) if args.now else datetime.now(timezone.utc)
        jobs = due(now)

    print(f"==> {len(jobs)} تشغيلة: {jobs}")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"matrix={json.dumps({'include': jobs})}\n")
            f.write(f"count={len(jobs)}\n")


if __name__ == "__main__":
    main()
