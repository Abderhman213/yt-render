"""
عملاء الخدمات الخارجية اللي كانت n8n بتكلّمها: Google Sheets و Gemini و
Telegram و Pexels و Wikimedia Commons و GitHub (لتشغيل render.yml).

كل حاجة هنا بتاخد بياناتها من متغيرات البيئة (أسرار GitHub):
  GEMINI_API_KEY, PEXELS_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID (اختياري)
  GOOGLE_SERVICE_ACCOUNT_JSON  — أو —  GOOGLE_CLIENT_ID + GOOGLE_CLIENT_SECRET + GOOGLE_REFRESH_TOKEN
  GITHUB_TOKEN, GITHUB_REPOSITORY (بيتحطوا تلقائي جوه GitHub Actions)
"""

import html
import json
import os
import random
import re
import time
import urllib.parse

import requests

from factory.channels import DEFAULT_TELEGRAM_CHAT_ID

TIMEOUT = 60


def _retry(fn, tries=3, wait=5, what="request"):
    """زي retryOnFail في n8n: 3 محاولات بينهم 5 ثواني."""
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            last = exc
            print(f"! {what} فشل (محاولة {i + 1}/{tries}): {exc}", flush=True)
            if i < tries - 1:
                time.sleep(wait)
    raise last


# ------------------------------------------------------------ Google Sheets

class Sheet:
    """جدول محتوى واحد: الصف الأول عناوين الأعمدة، والمطابقة دايمًا بعمود id."""

    SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

    def __init__(self, sheet_id, tab):
        self.sheet_id = sheet_id
        self.tab = tab
        self._creds = self._credentials()
        self._headers = None

    @classmethod
    def _credentials(cls):
        sa = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
        if sa:
            from google.oauth2 import service_account
            return service_account.Credentials.from_service_account_info(json.loads(sa), scopes=cls.SCOPES)
        refresh = os.environ.get("GOOGLE_REFRESH_TOKEN", "").strip()
        if refresh:
            from google.oauth2 import credentials as user_creds
            return user_creds.Credentials(
                None,
                refresh_token=refresh,
                client_id=os.environ["GOOGLE_CLIENT_ID"],
                client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
                token_uri="https://oauth2.googleapis.com/token",
                scopes=cls.SCOPES,
            )
        raise SystemExit("مفيش بيانات دخول لـ Google Sheets: حط GOOGLE_SERVICE_ACCOUNT_JSON "
                         "أو GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET/GOOGLE_REFRESH_TOKEN")

    def _auth_headers(self):
        from google.auth.transport.requests import Request

        if not self._creds.valid:
            self._creds.refresh(Request())
        return {"Authorization": f"Bearer {self._creds.token}"}

    def _url(self, suffix):
        return f"https://sheets.googleapis.com/v4/spreadsheets/{self.sheet_id}/values/{suffix}"

    def _range(self, a1=""):
        if not self.tab:
            # من غير اسم تاب: Google بيستخدم أول تاب في الشيت
            return urllib.parse.quote(a1 or "A:ZZ", safe="")
        name = "'" + self.tab.replace("'", "''") + "'"
        return urllib.parse.quote(name + ("!" + a1 if a1 else ""), safe="")

    def _values(self):
        def go():
            r = requests.get(self._url(self._range()), headers=self._auth_headers(), timeout=TIMEOUT)
            r.raise_for_status()
            return r.json().get("values", [])
        return _retry(go, what="قراءة الشيت")

    def rows(self):
        """كل الصفوف كـ dicts (زي عقدة قراءة Google Sheets في n8n)."""
        values = self._values()
        if not values:
            self._headers = []
            return []
        self._headers = [str(h).strip() for h in values[0]]
        out = []
        for i, raw in enumerate(values[1:], start=2):
            row = {h: (raw[j] if j < len(raw) else "") for j, h in enumerate(self._headers) if h}
            row["_row"] = i
            out.append(row)
        return out

    @property
    def headers(self):
        if self._headers is None:
            self.rows()
        return self._headers

    def append(self, records):
        """يضيف صفوف جديدة. الأعمدة اللي مش موجودة في الشيت بتتجاهل (autoMapInputData)."""
        if not records:
            return
        headers = self.headers
        if not headers:
            # شيت فاضي خالص: نكتب العناوين من أول صف
            headers = list(records[0].keys())
            self._put("A1", [headers])
            self._headers = headers
        body = {"values": [[_cell(rec.get(h, "")) for h in headers] for rec in records]}

        def go():
            r = requests.post(
                self._url(self._range("A1") + ":append"),
                params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"},
                headers=self._auth_headers(), json=body, timeout=TIMEOUT)
            r.raise_for_status()
        _retry(go, what="إضافة صفوف للشيت")

    def update(self, row_id, fields):
        """يحدّث الصف اللي عمود id بتاعه = row_id، في الأعمدة الموجودة بس."""
        row_id = str(row_id)
        rows = self.rows()
        match = next((r for r in rows if str(r.get("id", "")).strip() == row_id), None)
        if not match:
            print(f"! مفيش صف بـ id={row_id} في الشيت — مش هحدّث حاجة", flush=True)
            return False
        for col, val in fields.items():
            if col == "id" or col not in self._headers:
                continue
            letter = _col_letter(self._headers.index(col))
            self._put(f"{letter}{match['_row']}", [[_cell(val)]])
        return True

    def _put(self, a1, values):
        def go():
            r = requests.put(self._url(self._range(a1)), params={"valueInputOption": "RAW"},
                             headers=self._auth_headers(), json={"values": values}, timeout=TIMEOUT)
            r.raise_for_status()
        _retry(go, what="تحديث الشيت")


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, (list, dict)):
        return json.dumps(v, ensure_ascii=False)
    return v


def _col_letter(idx):
    s = ""
    idx += 1
    while idx:
        idx, rem = divmod(idx - 1, 26)
        s = chr(65 + rem) + s
    return s


# ------------------------------------------------------------ Gemini

def gemini_json(model, system, prompt, example, temperature):
    """
    بديل عقدة AI Agent + Structured Output Parser: بيطلب JSON بس، وبيدّي الموديل
    نفس مثال الشكل اللي كان في n8n. 3 محاولات للطلب كله، و5 للـ HTTP نفسه.
    """
    key = os.environ["GEMINI_API_KEY"]
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    full_prompt = (
        prompt.rstrip()
        + "\n\nOutput format: respond with a single JSON object that follows exactly the structure "
          "of this example (same keys and value types), and nothing else:\n"
        + example
    )
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": full_prompt}]}],
        "generationConfig": {"temperature": temperature, "responseMimeType": "application/json"},
    }

    def http():
        # 429 = حد الطلبات في الدقيقة (القنوات بتشتغل مع بعض). بنستنى المدة اللي
        # Gemini بيقولها (retryDelay) أو نضاعف الانتظار، بدل ما نخبط فيه كل 5 ثواني.
        wait = 8
        for i in range(7):
            r = requests.post(url, params={"key": key}, json=body, timeout=180)
            if r.status_code not in (429, 500, 502, 503, 504):
                r.raise_for_status()
                return r.json()
            delay = wait
            m = re.search(r'"retryDelay":\s*"(\d+)', r.text)
            if m:
                delay = max(delay, int(m.group(1)) + 1)
            print(f"! Gemini HTTP {r.status_code} — هستنى {delay} ثانية (محاولة {i + 1}/7)", flush=True)
            time.sleep(min(delay, 65))
            wait = min(wait * 2, 60)
        raise RuntimeError(f"Gemini HTTP {r.status_code} بعد 7 محاولات: {r.text[:200]}")

    def once():
        data = http()
        parts = data["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts).strip()
        return _parse_json(text)

    return _retry(once, tries=3, wait=5, what="تحليل رد Gemini")


def _parse_json(text):
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start:end + 1])
        raise


# ------------------------------------------------------------ Telegram

def telegram(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print(f"(تليجرام مش متظبط) {text}", flush=True)
        return
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip() or DEFAULT_TELEGRAM_CHAT_ID
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat, "text": html.escape(text, quote=False),
                                "parse_mode": "HTML"}, timeout=30)
        r.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        # إشعار فاشل مش سبب نوقّع التشغيلة
        print(f"! إشعار تليجرام فشل: {exc}", flush=True)


# ------------------------------------------------------------ Pexels / Commons

def pexels_videos(query, orientation, per_page, page, size=None):
    params = {"query": query, "orientation": orientation, "per_page": per_page, "page": page}
    if size:
        params["size"] = size

    def go():
        r = requests.get("https://api.pexels.com/videos/search", params=params,
                         headers={"Authorization": os.environ["PEXELS_API_KEY"]}, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json().get("videos", [])
    return _retry(go, what="Pexels")


def pixabay_videos(query, orientation, count):
    """فيديوهات Pixabay. الـ API مبيفلترش بالاتجاه، فبنفلتر إحنا."""
    def go():
        r = requests.get("https://pixabay.com/api/videos/", timeout=TIMEOUT, params={
            "key": os.environ["PIXABAY_API_KEY"], "q": query[:100], "per_page": 80,
            "page": random.randint(1, 2), "safesearch": "true"})
        r.raise_for_status()
        return r.json().get("hits", [])
    links = []
    for hit in shuffle(_retry(go, what="Pixabay")):
        files = [f for f in (hit.get("videos") or {}).values() if f.get("url")]
        portrait = orientation == "portrait"
        files = [f for f in files if ((f.get("height") or 0) >= (f.get("width") or 0)) == portrait]
        if not files:
            continue
        target = 1080 if portrait else 1920
        hd = sorted([f for f in files if min(f["width"], f["height"]) >= min(target, 1080)], key=lambda f: f["width"])
        pick = (hd or sorted(files, key=lambda f: -f["width"]))[0]
        links.append(pick["url"])
        if len(links) >= count:
            break
    return links


def commons_media(image_queries):
    params = {
        "action": "query", "generator": "search",
        "gsrsearch": " OR ".join(image_queries) + " filetype:bitmap|video -intitle:svg -intitle:logo -intitle:crest -intitle:map",
        "gsrnamespace": "6", "gsrlimit": "100", "prop": "imageinfo",
        "iiprop": "url|mime|extmetadata", "iiurlwidth": "1280", "format": "json", "origin": "*",
    }

    def go():
        r = requests.get("https://commons.wikimedia.org/w/api.php", params=params, timeout=TIMEOUT,
                         headers={"User-Agent": "yt-render-factory/1.0 (GitHub Actions)"})
        r.raise_for_status()
        return list(((r.json().get("query") or {}).get("pages") or {}).values())
    return _retry(go, what="Wikimedia Commons")


# ------------------------------------------------------------ YouTube (قراءة بس)

def youtube_stats(video_ids):
    """
    مشاهدات/لايكات/كومنتات فيديوهات عامة بمفتاح API عادي (YOUTUBE_API_KEY).
    الفيديوهات الـ private مابترجعش. بيرجّع {} لو المفتاح مش موجود.
    """
    key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    ids = [v for v in dict.fromkeys(video_ids) if v]
    if not key or not ids:
        return {}
    out = {}
    for i in range(0, len(ids), 50):
        def go(chunk=ids[i:i + 50]):
            r = requests.get("https://www.googleapis.com/youtube/v3/videos", timeout=TIMEOUT, params={
                "part": "statistics,snippet", "id": ",".join(chunk), "key": key})
            r.raise_for_status()
            return r.json().get("items", [])
        for item in _retry(go, what="YouTube stats"):
            st = item.get("statistics") or {}
            out[item["id"]] = {
                "views": int(st.get("viewCount", 0)), "likes": int(st.get("likeCount", 0)),
                "comments": int(st.get("commentCount", 0)),
                "title": (item.get("snippet") or {}).get("title", ""),
                "published": (item.get("snippet") or {}).get("publishedAt", ""),
            }
    return out


# ------------------------------------------------------------ GitHub

def dispatch_render(payload, render_channel):
    """
    يشغّل render.yml بنفس الطريقة اللي n8n كان بيشغّله بيها. GITHUB_TOKEN مسموحله
    يعمل workflow_dispatch (ده الاستثناء الوحيد من قاعدة "التوكن مبيشغّلش وركفلو").
    """
    repo = os.environ["GITHUB_REPOSITORY"]
    ref = os.environ.get("RENDER_REF", "main")
    inputs = {"payload": json.dumps(payload, ensure_ascii=False)}
    if render_channel:
        inputs["channel"] = render_channel

    def go():
        r = requests.post(
            f"https://api.github.com/repos/{repo}/actions/workflows/render.yml/dispatches",
            headers={"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
                     "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28"},
            json={"ref": ref, "inputs": inputs}, timeout=TIMEOUT)
        if r.status_code >= 400:
            raise RuntimeError(f"GitHub dispatch {r.status_code}: {r.text[:300]}")
    _retry(go, what="تشغيل render.yml")


def shuffle(items):
    items = list(items)
    random.shuffle(items)
    return items
