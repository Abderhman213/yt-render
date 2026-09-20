#!/usr/bin/env python3
"""
أداة تشغّلها مرة واحدة بس على جهازك (مش على GitHub) عشان تجيب YT_REFRESH_TOKEN.

الاستخدام:
  1. نزّل ملف client_secret.json من Google Cloud Console (OAuth Client ID
     نوع "Desktop app") وحطه جنب السكريبت ده.
  2. ثبّت المكتبة المطلوبة:
       pip install google-auth-oauthlib
  3. شغّل:
       python get_refresh_token.py
  4. هيفتحلك المتصفح، سجّل دخول بحساب يوتيوب اللي عايز ترفع عليه وادوس Allow.
  5. السكريبت هيطبعلك التلات قيم اللي محتاج تحطهم في GitHub secrets:
     YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN
"""

import json
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
CLIENT_SECRET_FILE = "client_secret.json"


def main():
    if not Path(CLIENT_SECRET_FILE).exists():
        raise SystemExit(
            f"مفيش ملف {CLIENT_SECRET_FILE}. نزّله من Google Cloud Console "
            "(APIs & Services > Credentials > OAuth Client ID > Desktop app) "
            "وحطه جنب السكريبت ده."
        )

    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
    creds = flow.run_local_server(port=0)

    client_config = json.loads(Path(CLIENT_SECRET_FILE).read_text(encoding="utf-8"))
    installed = client_config.get("installed", client_config.get("web", {}))

    print("\n==> خلصنا. انسخ القيم دي وحطها في GitHub secrets:\n")
    print(f"YT_CLIENT_ID={installed.get('client_id', creds.client_id)}")
    print(f"YT_CLIENT_SECRET={installed.get('client_secret', creds.client_secret)}")
    print(f"YT_REFRESH_TOKEN={creds.refresh_token}")


if __name__ == "__main__":
    main()
