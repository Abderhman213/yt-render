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

القنوات التانية ليها نفس التلاتة ببادئة مختلفة: `YT_ES_*`, `YT_BW_*`, `YT_GEN_*`,
`YT_STATES_*`, `YT_COLLEGE_*`, `YT_CRIME_*`.

### أسرار المصنع (factory.yml — بديل n8n)

| الاسم | القيمة |
|---|---|
| `GEMINI_API_KEY` | مفتاح Google AI Studio (نفس اللي كان في n8n) |
| `PEXELS_API_KEY` | مفتاح Pexels |
| `PIXABAY_API_KEY` | مفتاح Pixabay (اختياري — من غيره بيكتفي بـ Pexels و Commons) |
| `TELEGRAM_BOT_TOKEN` | توكن البوت من BotFather |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | ملف JSON بتاع Service Account كامل. **لازم تعمل Share لكل شيت من السبعة لإيميل الـ service account (Editor).** |

بديل الـ service account: `GOOGLE_CLIENT_ID` + `GOOGLE_CLIENT_SECRET` + `GOOGLE_REFRESH_TOKEN`
بنفس طريقة OAuth Playground في الخطوة ٢، بس بالـ scope
`https://www.googleapis.com/auth/spreadsheets`.

اختياري: متغير (Variable مش Secret) اسمه `TELEGRAM_CHAT_ID` لو عايز الإشعارات تروح
لمحادثة غير الافتراضية.

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
| `id` | معرّف صف الفكرة في جدول القناة. بيرجع كما هو في الإبلاغ النهائي (`factory/report.py`) علشان يحدّث الصف الصح. |
| `lines` | السكريبت مقسّم جمل. كل جملة بتبقى سطر ترجمة لوحدها. |
| `clips` | روابط فيديو مباشرة من Pexels. بتتقص وتتوزّع على مدة الصوت. |
| `voice` | صوت edge-tts، مثلاً `en-US-AndrewNeural` أو `en-GB-RyanNeural` |
| `privacy` | `public` أو `private` أو `unlisted` |

التوقيت بتاع الترجمة بيتحسب من مدة كل جملة بعد تحويلها لصوت — يعني مظبوط
بالظبط، من غير تعرّف آلي على الكلام ومن غير تخمين.

---

## المصنع (بديل n8n)

`factory.yml` بيصحى كل ساعة، و`factory/plan.py` بيشغّل القنوات اللي ميعادها
دلوقتي حسب توقيتها المحلي (نيويورك، ومدريد للقناة الإسبانية). كل تشغيلة:

1. تقرا جدول القناة. لو الأفكار قليلة، تولّد 20 فكرة بـ Gemini وتضيفها وتقف.
2. تكتب السكريبت، وتعدّيه على بوابة الجودة (درجة 80 أو أكتر، ومن غير تكرار أو خطر).
3. تجيب لقطات من تلات مصادر مع بعض: Wikimedia Commons (حوالي التلت، صور حقيقية للي بيتحكي عنه)، و Pexels و Pixabay (لقطات عامة). لو مصدر وقع، الباقيين بيكمّلوا.
4. تشغّل `render.yml`، وفي آخره `factory/report.py` بيحدّث الصف ويبعت تليجرام.

| الملف | فيه إيه |
|---|---|
| `factory/channels.py` | الشيتات، المواعيد، الموديلات، الأصوات، وقواعد كل قناة |
| `factory/prompts/<قناة>/` | البرومبتات: `ideas`, `script`, `gate`, `long` |
| `factory/run.py` | خط الإنتاج نفسه |

تشغيل يدوي: **Actions → factory → Run workflow**. اختار القناة (أو `all`) والنوع،
وعلّم `dry_run` لتجربة من غير ما يكتب في الشيت أو ينشر، أو اختار `privacy=private`
لأول فيديو حقيقي.
