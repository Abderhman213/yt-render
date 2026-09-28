#!/usr/bin/env python3
"""
فحص يومي لصلاحية الرفع (refresh token) بتاعة كل قناة.

بيجرب يطلع access token من الـ refresh token. لو جوجل رفض (الـ token انتهى
أو اتلغى)، بيبعت تنبيه على تليجرام فورًا — قبل ما فيديو يترندر ويقع في الرفع.
لو جوجل رجّع refresh_token_expires_in (ده بيحصل لما مشروع جوجل كلاود لسه على
وضع Testing)، بيحذّر إن الـ token هيموت بعد كام يوم.

  python factory/token_check.py <channel_key> <secret_prefix>
"""

import os
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from factory import services  # noqa: E402
from factory.channels import CHANNELS  # noqa: E402


def main():
    key, prefix = sys.argv[1], sys.argv[2]
    name = CHANNELS.get(key, {}).get("label") or ("القناة الإنجليزي" if key == "en" else key)
    secret = f"{prefix}_REFRESH_TOKEN"
    creds = [os.environ.get(v, "").strip() for v in ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN")]
    if not all(creds):
        services.telegram(f"⚠️ {name}: أسرار الرفع ناقصة في GitHub ({prefix}_CLIENT_ID / _CLIENT_SECRET / _REFRESH_TOKEN).")
        sys.exit(1)

    r = requests.post("https://oauth2.googleapis.com/token", timeout=30, data={
        "client_id": creds[0], "client_secret": creds[1],
        "refresh_token": creds[2], "grant_type": "refresh_token",
    })
    data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code != 200:
        err = data.get("error_description") or data.get("error") or r.text[:120]
        services.telegram(
            f"🔴 {name}: صلاحية الرفع على يوتيوب انتهت ({err}).\n"
            f"الفيديوهات هتترندر بس مش هتترفع لحد ما تجدد الـ refresh token وتحطه في السيكرت {secret}.\n"
            "وتأكد إن مشروع جوجل كلاود بتاعها على In production مش Testing عشان ما يتكررش كل أسبوع."
        )
        print(f"! {name}: {r.status_code} {err}")
        sys.exit(1)

    left = data.get("refresh_token_expires_in")
    if left:
        days = int(left) // 86400
        print(f"! {name}: الـ token مؤقت — فاضل {days} يوم")
        if days <= 3:
            services.telegram(
                f"🟠 {name}: صلاحية الرفع هتنتهي خلال {days} يوم لأن مشروع جوجل كلاود لسه على Testing.\n"
                f"دوس Publish app في Google Auth Platform ← Audience، وبعدين طلّع refresh token جديد وحطه في {secret}."
            )
    print(f"✓ {name}: صلاحية الرفع شغالة")


if __name__ == "__main__":
    main()
