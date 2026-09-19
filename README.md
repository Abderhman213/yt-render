# عامل الرندر — بديل السيرفر

المستودع ده بيقوم بدور السيرفر: ffmpeg والتعليق الصوتي والرفع على يوتيوب،
كلها بتشتغل على GitHub Actions. من غير كارت ومن غير VPS.

الحساب المجاني بياخد **٢٠٠٠ دقيقة شهريًا** على المستودعات الخاصة، وحد الصرف
عليه **صفر افتراضيًا** — يعني لو الدقايق خلصت الشغل بيقف، مش بيتحاسب عليك.
الفيديو الواحد بياخد حوالي ٣ لـ ٥ دقايق، يعني تقريبًا ٤٠٠ فيديو في الشهر.

---

## ١. اعمل المستودع

على GitHub: **New repository** → الاسم `yt-render` → **Private** → Create.

ارفع الملفات دي جواه بنفس الترتيب:

```
.github/workflows/render.yml
scripts/render.py
scripts/upload.py
assets/music/          ← حط هنا مقاطع الموسيقى
```

---

## ٢. هات توكن يوتيوب (من المتصفح، من غير ما تثبّت حاجة)

من [Google Cloud Console](https://console.cloud.google.com) — **مش محتاج كارت**:

1. أنشئ مشروع جديد
2. `APIs & Services → Library` → فعّل **YouTube Data API v3**
3. `OAuth consent screen` → External → ضيف الـ scope `.../auth/youtube.upload`
   → ضيف إيميلك في Test users → **اضغط PUBLISH APP**
4. `Credentials → Create Credentials → OAuth client ID` → **Web application**
   → في Authorized redirect URIs حط:
   `https://developers.google.com/oauthplayground`
   → احفظ الـ Client ID والـ Secret

بعدين من [OAuth Playground](https://developers.google.com/oauthplayground):

1. اضغط الترس فوق على اليمين → علّم **Use your own OAuth credentials**
   → حط الـ Client ID والـ Secret
2. في الخانة الشمال، في `Step 1`، الزق الـ scope ده:
   `https://www.googleapis.com/auth/youtube.upload`
3. اضغط **Authorize APIs** ووافق بحساب القناة
4. في `Step 2` اضغط **Exchange authorization code for tokens**
5. انسخ الـ **Refresh token**

> لو سبت التطبيق في وضع Testing من غير Publish، التوكن ده هيبطل بعد فترة
> قصيرة والرفع هيقف من غير رسالة خطأ واضحة.

---

## ٣. حط الأسرار في المستودع

من `Settings → Secrets and variables → Actions → New repository secret`:

| الاسم | القيمة |
|---|---|
| `YT_CLIENT_ID` | من الخطوة ٢ |
| `YT_CLIENT_SECRET` | من الخطوة ٢ |
| `YT_REFRESH_TOKEN` | من OAuth Playground |
| `N8N_CALLBACK_URL` | عنوان الـ webhook بتاع n8n (اختياري) |

---

## ٤. الموسيقى

نزّل ٥ لـ ١٠ مقاطع من مكتبة يوتيوب الصوتية (من YouTube Studio → Audio Library،
وفلتر على اللي مش محتاج نسب) وحطهم في `assets/music/`.
السكربت بيختار واحد عشوائي كل مرة، والموسيقى بتنزل تحت صوت التعليق.

استخدام مكتبة يوتيوب نفسها معناه صفر مشاكل حقوق.

---

## ٥. جرّبه يدوي

من تبويب **Actions** → `render-and-upload` → **Run workflow**، والزق في خانة
الـ payload:

```json
{
  "id": "row-123",
  "title": "Test upload",
  "description": "Testing the pipeline.",
  "tags": ["test"],
  "privacy": "private",
  "voice": "en-US-AndrewNeural",
  "lines": [
    "This is the first line of the test.",
    "And this is the second one.",
    "If you can read this, the pipeline works."
  ],
  "clips": [
    "https://videos.pexels.com/video-files/example-1.mp4",
    "https://videos.pexels.com/video-files/example-2.mp4"
  ]
}
```

خلّي `privacy` على `private` في أول تجربة، واتفرّج على الناتج قبل ما تخليه `public`.

---

## شكل الـ payload

| الحقل | إيه هو |
|---|---|
| `id` | معرّف صف الفكرة في جدول n8n. بيرجع كما هو في الإبلاغ النهائي علشان n8n يعرف يحدّث الصف الصح. |
| `lines` | السكريبت مقسّم جمل. كل جملة بتبقى سطر ترجمة لوحدها. |
| `clips` | روابط فيديو مباشرة من Pexels. بتتقص وتتوزّع على مدة الصوت. |
| `voice` | صوت edge-tts، مثلاً `en-US-AndrewNeural` أو `en-GB-RyanNeural` |
| `privacy` | `public` أو `private` أو `unlisted` |

التوقيت بتاع الترجمة بيتحسب من مدة كل جملة بعد تحويلها لصوت — يعني مظبوط
بالظبط، من غير تعرّف آلي على الكلام ومن غير تخمين.
