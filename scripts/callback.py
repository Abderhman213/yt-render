#!/usr/bin/env python3
"""
يبني جسم الإبلاغ لـ n8n ويبعته، سواء الرفع نجح أو فشل.

بياخد id والعنوان من output.json (بتتكتب دايمًا في مرحلة الرندر) حتى لو
الرفع فشل بعدها — علشان n8n يعرف يحدّث الصف الصح في الجدول.
"""

import json
import os
import sys
import urllib.request
from pathlib import Path

webhook = os.environ.get("N8N_CALLBACK_URL", "").strip()
if not webhook:
    print("مفيش عنوان رجوع لـ n8n، هعدّي الخطوة دي.")
    sys.exit(0)

meta = {}
if Path("output.json").exists():
    meta = json.loads(Path("output.json").read_text(encoding="utf-8"))

uploaded = {}
status = "failed"
if Path("uploaded.json").exists():
    uploaded = json.loads(Path("uploaded.json").read_text(encoding="utf-8"))
    status = "ok"

body = {
    "status": status,
    "run": os.environ.get("GITHUB_RUN_ID", ""),
    "result": {
        "id": meta.get("id", uploaded.get("id", "")),
        "title": meta.get("title", uploaded.get("title", "")),
        "video_id": uploaded.get("video_id", ""),
        "url": uploaded.get("url", ""),
    },
}

data = json.dumps(body).encode("utf-8")
req = urllib.request.Request(
    webhook, data=data, headers={"Content-Type": "application/json"}, method="POST"
)
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        print(f"==> اتبلّغ n8n: {r.status}")
except Exception as exc:
    # الإبلاغ فشل مش سبب إننا نفشّل التشغيلة كلها
    print(f"! الإبلاغ لـ n8n فشل، بس ده مش سبب نفشّل بيه: {exc}")
