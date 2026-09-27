#!/usr/bin/env python3
"""
إبلاغ نتيجة الرندر — بديل وركفلوهات n8n "استقبال نتيجة الرندر" السبعة.

بيشتغل كآخر خطوة في render.yml (حتى لو الرفع فشل): بيقرا output.json و
uploaded.json، يحدّث صف الفكرة في جدول القناة (status=ok أو failed + رابط
الفيديو)، ويبعت إشعار تليجرام.

  python factory/report.py <channel>     (نفس قيمة input الـ channel في render.yml)
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from factory import services  # noqa: E402
from factory.channels import by_render_channel  # noqa: E402


def main():
    render_channel = sys.argv[1] if len(sys.argv) > 1 else ""
    ch = by_render_channel(render_channel)
    if not ch:
        print(f"! قناة مش معروفة: {render_channel!r} — مش هبلّغ")
        return

    meta = json.loads(Path("output.json").read_text(encoding="utf-8")) if Path("output.json").exists() else {}
    if not meta and Path("payload.json").exists():
        meta = json.loads(Path("payload.json").read_text(encoding="utf-8"))
    uploaded = json.loads(Path("uploaded.json").read_text(encoding="utf-8")) if Path("uploaded.json").exists() else {}

    status = "ok" if uploaded else "failed"
    row = {
        "status": status,
        "id": meta.get("id", uploaded.get("id", "")),
        "title": meta.get("title", uploaded.get("title", "")),
        "video_id": uploaded.get("video_id", ""),
        "video_url": uploaded.get("url", ""),
        "run": os.environ.get("GITHUB_RUN_ID", ""),
    }
    print(f"==> النتيجة: {row}")

    if row["id"]:
        try:
            services.Sheet(ch["sheet_id"], ch["sheet_tab"]).update(row["id"], row)
        except BaseException as exc:  # noqa: BLE001 — حتى أخطاء المكتبات الغريبة (زي panic في cryptography)
            # فشل تحديث الشيت مش سبب نفشّل التشغيلة كلها
            print(f"! تحديث الشيت فشل: {exc}")

    prefix = f"{ch['label']} — " if ch["label"] else ""
    if status == "ok":
        services.telegram(f"{prefix}اترفع: {row['title']}\n{row['video_url']}")
    else:
        services.telegram(f"{prefix}فشل رفع الفيديو (id: {row['id']}, run: {row['run']}). راجع سجلات GitHub Actions.")


if __name__ == "__main__":
    main()
