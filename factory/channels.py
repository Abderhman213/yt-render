"""
إعدادات القنوات السبعة — منقولة من وركفلوهات n8n (المصنع + استقبال النتيجة).

كل قناة ليها: الشيت والتاب، المواعيد بتوقيتها المحلي، موديلات Gemini ودرجات
حرارتها، الأصوات المسموحة، طريقة جلب اللقطات، وقواعد بوابة الجودة. البرومبتات
نفسها في factory/prompts/<القناة>/ (ideas / script / gate / long).

الاختلافات بين القنوات مقصودة ومنقولة زي ما هي من n8n — متوحّدهاش من غير سبب.
"""

EN_VOICES = ["en-US-AndrewNeural", "en-US-BrianNeural", "en-GB-RyanNeural"]
NICHE_VOICES = ["en-US-AndrewNeural", "en-US-BrianNeural", "en-US-ChristopherNeural"]
ES_VOICES = ["es-ES-AlvaroNeural", "es-ES-ElviraNeural", "es-ES-XimenaNeural"]

NY = "America/New_York"

# أمثلة الـ JSON اللي كانت في عقد "شكل ..." (Structured Output Parser) في n8n.
_NICHE_IDEAS_EXAMPLE = (
    '{"topics":[{"topic":"a short internal label","title":"a curiosity-driven YouTube Shorts title '
    'under 60 characters that teases the answer without giving it away","description":"2-3 sentence '
    'video description ending with #Shorts","tags":["%(tag)s","tag2","tag3"],"search_query":"2-3 word '
    'stock footage term","voice":"en-US-AndrewNeural"}]}'
)
_NICHE_SCRIPT_EXAMPLE = (
    '{"title":"title under 60 characters","description":"2-3 sentence description ending with #Shorts",'
    '"tags":["%(tag)s","tag2"],"search_query":"2-3 word stock footage term","voice":"en-US-AndrewNeural",'
    '"mood":"%(mood)s","lines":["First sentence.","Second sentence."],"moments":[{"text":"%(card)s",'
    '"subtext":"factual detail","color":"%(color)s"}]}'
)
_NICHE_LONG_EXAMPLE = (
    '{"title":"title under 60 characters","description":"2-4 sentence description","tags":["%(tag)s",'
    '"tag2"],"search_query":"2-3 word stock footage term","voice":"en-US-AndrewNeural","mood":"%(lmood)s",'
    '"lines":["First sentence.","Second sentence."],"moments":[{"text":"%(card)s","subtext":"factual detail",'
    '"color":"%(color)s"}]}'
)
_GATE_EXAMPLE = '{"score":82,"is_original":true,"policy_risk":"low","reasons":"short explanation"}'


def _niche(key, name, emoji, sheet_id, hours, long_hour, tag, color, *,
           script_temp=0.7, mood_default="cinematic", strict_risk=False,
           script_mood="upbeat", long_mood="epic", card="SHORT PUNCHY LINE",
           ideas_extra="", script_system=None, long_system=None, ideas_system=None,
           gate_system=None):
    fmt = {"tag": tag, "color": color, "mood": script_mood, "lmood": long_mood, "card": card}
    return {
        "key": key,
        "render_channel": key,
        "name": name,
        "label": f"{emoji} {name}",
        "tz": NY,
        "hours": hours,
        "long_day": 5,  # Saturday (Python weekday: Monday=0)
        "long_hour": long_hour,
        "sheet_id": sheet_id,
        "sheet_tab": "Untitled",
        "models": {"ideas": "gemini-3.1-flash-lite", "script": "gemini-3.1-flash-lite",
                   "gate": "gemini-3.1-flash-lite", "long": "gemini-3.1-flash-lite"},
        "temps": {"ideas": 0.9, "script": script_temp, "gate": 0.1, "long": script_temp},
        "systems": {
            "ideas": ideas_system or f'You are a YouTube Shorts content strategist for the US English channel "{name}". You always return strictly valid JSON matching the requested schema, with no extra commentary.',
            "script": script_system or f'You are a professional YouTube Shorts scriptwriter for the US English channel "{name}". You always return strictly valid JSON matching the requested schema, with no extra commentary.',
            "gate": gate_system or "You are a strict YouTube content policy reviewer. You always return strictly valid JSON matching the requested schema, with no extra commentary.",
            "long": long_system or f'You are a professional long-form YouTube documentary scriptwriter for the US English channel "{name}". You always return strictly valid JSON matching the requested schema, with no extra commentary.',
        },
        "examples": {
            "ideas": _NICHE_IDEAS_EXAMPLE % fmt,
            "script": _NICHE_SCRIPT_EXAMPLE % fmt,
            "gate": _GATE_EXAMPLE,
            "long": _NICHE_LONG_EXAMPLE % fmt,
        },
        "voices": NICHE_VOICES,
        "min_ideas": 3,
        "published_statuses": ["published", "ok", "rendering"],
        "dup_check": True,
        "strict_risk": strict_risk,          # Cold File: أي خطر غير low بيترفض
        "min_lines": 0,
        "reject_marks_row": True,            # الصف المرفوض بياخد status=rejected
        "short_clips": {"source": "pexels", "query": "script", "per_page": 20, "pages": 3,
                        "orientation": "portrait", "count": 12, "pick": "hd"},
        "short_moments": 2,
        "short_mood_default": mood_default,
        "long_clips": {"source": "pexels", "orientation": "landscape", "per_page": 60, "pages": 3,
                       "count": 40, "pick": "hd_landscape"},
        "long_moments": 4,
        "long_mood_default": mood_default,
        "long_format": "long",
        "long_needs_titles": True,
        "long_row": {"status": "rendering", "with_topic": True},
        "messages": {
            "ideas": f"{emoji} {name}: جهّزت 20 فكرة فيديو جديدة في الجدول، وأول واحدة هتتنفذ في التشغيلة الجاية.{ideas_extra}",
            "long_started": f"{emoji} {name} — فيديو السبت الطويل، بدأ الرندر: {{title}}",
        },
    }


CHANNELS = {
    # القناة الإنجليزية الأصلية — "المصنع — توليد ونشر" (QqhrZncobmYLYSKE)
    "en": {
        "key": "en",
        "render_channel": "",
        "name": "English",
        "label": "",
        "tz": NY,
        "hours": [2, 12, 17, 22],
        "long_day": 5,
        "long_hour": 23,
        "sheet_id": "1YWHU7FPtVv4_CgO6Fz9tx7DjscZSOe0T_sxzGEMiJ0E",
        "sheet_tab": "queue",
        "models": {"ideas": "gemini-3.5-flash-lite", "script": "gemini-3.5-flash-lite",
                   "gate": "gemini-3.5-flash-lite", "long": "gemini-3.5-flash-lite"},
        "temps": {"ideas": 0.9, "script": 0.7, "gate": 0.1, "long": 0.7},
        "systems": {
            "ideas": "You are a YouTube Shorts content strategist for a US audience. You specialise in topics that drive debate in the comments while staying strictly inside YouTube's advertiser-friendly guidelines. You always return strictly valid JSON matching the requested schema, with no extra commentary.",
            "script": "You are a professional YouTube Shorts scriptwriter for a US audience, writing debate-driving factual content that stays strictly inside YouTube's advertiser-friendly guidelines. You always return strictly valid JSON matching the requested schema, with no extra commentary before or after it.",
            "gate": "You are a strict YouTube content policy reviewer. You distinguish between debate-driving factual content (acceptable) and advertiser-unsafe content (not acceptable). You always return strictly valid JSON matching the requested schema, with no extra commentary.",
            "long": "You are a professional long-form YouTube documentary scriptwriter for a US audience, writing debate-driving factual content that stays strictly inside YouTube's advertiser-friendly guidelines. You always return strictly valid JSON matching the requested schema, with no extra commentary before or after it.",
        },
        "examples": {
            "ideas": '{"topics":[{"topic":"a short internal label","title":"a curiosity-driven YouTube Shorts title under 60 characters that teases the fact without giving away the answer","description":"2-3 sentence video description ending with #Shorts","tags":["tag1","tag2","tag3"],"search_query":"2-3 word stock footage search term in English","voice":"en-US-AndrewNeural"}]}',
            "script": '{"title":"a curiosity-driven YouTube Shorts title under 60 characters that teases the fact without giving away the answer","description":"a 2-3 sentence description ending with #Shorts","tags":["tag1","tag2","tag3"],"search_query":"2-3 word English stock footage search term","voice":"en-US-AndrewNeural","mood":"upbeat","lines":["First sentence of narration.","Second sentence.","Third sentence."]}',
            "gate": '{"score":82,"is_original":true,"policy_risk":"low","reasons":"Uses stock footage but the narration adds original explanation and analysis."}',
            "long": '{"title":"Title under 60 characters","description":"2-4 sentence description","tags":["tag1","tag2"],"search_query":"2-3 word English stock footage search term","voice":"en-US-AndrewNeural","mood":"dark","lines":["First sentence.","Second sentence.","..."],"moments":[{"text":"1 IN 3 NEVER TOLD","subtext":"Declassified 1977 report","color":"1F6FEB"}]}',
        },
        "voices": EN_VOICES,
        "min_ideas": 8,
        "published_statuses": ["published"],
        "dup_check": True,
        "strict_risk": False,
        "min_lines": 0,
        # في n8n الصف المرفوض كان بيتحدّث بالدرجة بس ويفضل idea، فالمحاولة
        # الجاية بتعيد كتابة نفس الموضوع. سايبينه زي ما هو.
        "reject_marks_row": False,
        "short_clips": {"source": "pexels", "query": "script", "per_page": 80, "pages": 3,
                        "orientation": "portrait", "count": 12, "pick": "hd"},
        "short_moments": 0,
        "short_mood_default": "cinematic",
        "long_clips": {"source": "pexels", "orientation": "portrait", "per_page": 80, "pages": 3,
                       "count": 30, "pick": "hd"},
        "long_moments": 2,
        "long_mood_default": "cinematic",
        "long_format": None,
        "long_needs_titles": False,
        "long_row": {"status": "rendering-long", "with_topic": False},
        "messages": {
            "ideas": "جهّزت 20 فكرة فيديو جديدة في الجدول. هعالج أول واحدة في أقرب تشغيلة (الجدول شغال 4 مرات يوميًا).",
            "long_started": "القناة الأمريكية — فيديو طويل السبت، بدأ الرندر: {title}",
        },
    },

    # "المصنع الإسباني — كورة" (TNpLJWN9Zowz70Ii) — توقيت مدريد
    "es": {
        "key": "es",
        "render_channel": "es",
        "name": "كورة",
        "label": "القناة الإسبانية (كورة)",
        "tz": "Europe/Madrid",
        "hours": [13, 15, 19, 21],
        "long_day": 5,
        "long_hour": 18,
        "sheet_id": "1bwJMgjmzemb2nTgaYuUb-vzcWDzStojuMYvtqxbWmw8",
        "sheet_tab": "queue",
        "models": {"ideas": "gemini-3.1-flash-lite", "script": "gemini-3.1-flash-lite",
                   "gate": "gemini-3.1-flash-lite", "long": "gemini-3.1-flash-lite"},
        "temps": {"ideas": 0.9, "script": 0.7, "gate": 0.1, "long": 0.7},
        "systems": {
            "ideas": "You are a YouTube Shorts content strategist for a Spanish-language football history channel aimed at viewers in Spain. You always return strictly valid JSON matching the requested schema, with no extra commentary.",
            "script": "You are a professional YouTube Shorts scriptwriter writing in Spain Spanish for a football history channel in Spain. You always return strictly valid JSON matching the requested schema, with no extra commentary before or after it.",
            "gate": "You are a strict YouTube content policy reviewer working on Spanish-language football content. You always return strictly valid JSON matching the requested schema, with no extra commentary.",
            "long": "You are a professional long-form YouTube documentary scriptwriter writing in Spain Spanish for a football history channel. You always return strictly valid JSON matching the requested schema, with no extra commentary before or after it.",
        },
        "examples": {
            "ideas": '{"topics":[{"topic":"a short internal label in Spanish","title":"a curiosity-driven YouTube Shorts title in Spain Spanish, under 60 characters, that teases the fact without giving away the answer","description":"2-3 sentence video description in Spain Spanish ending with #Shorts","tags":["etiqueta1","etiqueta2","etiqueta3"],"image_queries":["Athletic Club Bilbao","San Mames stadium","La Liga football"],"voice":"es-ES-AlvaroNeural"}]}',
            "script": '{"title":"a curiosity-driven YouTube Shorts title in Spain Spanish, under 60 characters, that teases the fact without giving away the answer","description":"a 2-3 sentence description in Spain Spanish ending with #Shorts","tags":["etiqueta1","etiqueta2","etiqueta3"],"image_queries":["Athletic Club Bilbao","San Mames stadium","La Liga football"],"voice":"es-ES-AlvaroNeural","mood":"dark","lines":["Primera frase de la narración.","Segunda frase.","Tercera frase."],"moments":[{"text":"¡GOL DE BATA!","subtext":"Athletic 12-1 Barcelona · 1931","color":"A50044"}]}',
            "gate": '{"score":82,"is_original":true,"policy_risk":"low","reasons":"Uses stock footage but the Spanish narration adds original explanation and analysis."}',
            "long": '{"title":"Título en español, menos de 60 caracteres","description":"Descripción de 2 a 4 frases en español","tags":["laliga","futbol"],"image_queries":["Athletic Club Bilbao","San Mames stadium","La Liga trophy","La Liga football"],"voice":"es-ES-AlvaroNeural","mood":"epic","lines":["Primera frase.","Segunda frase.","..."],"moments":[{"text":"¡GOL DE BATA!","subtext":"Athletic 12-1 Barcelona · 1931","color":"A50044"}]}',
        },
        "voices": ES_VOICES,
        "min_ideas": 3,
        "published_statuses": ["published", "ok"],
        "dup_check": False,                  # فحص التكرار اتشال في n8n للقناة دي
        "strict_risk": False,
        "min_lines": 5,
        "reject_marks_row": False,
        "laliga_only": True,
        "blocked_ids": [6],
        "priority_ids": [19],
        "verified_topics": {
            19: "Athletic Club 12-1 FC Barcelona, La Liga matchday 10, 8 February 1931, San Mames (Bilbao). It is still the biggest win in the history of La Liga. VERIFIED FACTS - use ONLY these, add no other numbers, names or dates: Athletic were coached by the Englishman Frederick Pentland; Barcelona by James Bellamy. Attendance was over 18,000. Athletic led 6-1 at half-time. Agustin Sauto, nicknamed Bata, scored 7 of the 12 goals (minutes 2, 8, 24, 37, 57, 60 and 68). The other Athletic goals: Gorostiza, Lafuente, Garizurieta, Iraragorri and one own goal by Sastre of Barcelona. Barcelona's only goal was scored by Goiburu in the 10th minute. Do NOT call it a Copa del Rey match: it was a league match.",
        },
        "short_clips": {"source": "pexels", "query": "random",
                        "queries": ["football stadium", "soccer match", "soccer ball", "football fans cheering",
                                    "soccer players", "football pitch", "soccer stadium night", "football goal"],
                        "per_page": 40, "pages": 4, "orientation": "portrait", "size": "medium",
                        "count": 12, "pick": "near1080", "min_duration": 3},
        "short_moments": 2,
        "short_mood_default": "epic",
        "long_clips": {"source": "commons", "count": 40},
        "long_moments": 4,
        "long_mood_default": "cinematic",
        "long_format": None,
        "long_needs_titles": False,
        "long_row": {"status": "rendering-long", "with_topic": True},
        "messages": {
            "ideas": "القناة الإسبانية (كورة): جهّزت 20 فكرة فيديو جديدة في الجدول. هعالج أول واحدة في أقرب تشغيلة (الجدول شغال 4 مرات يوميًا).",
            "long_started": "القناة الإسبانية — فيديو طويل السبت، بدأ الرندر: {title}",
        },
    },

    "bw": _niche("bw", "Brand Battles Daily", "🥤⚔️", "1XLOdp3fGppehccXDf47PqSbOaE3AtvqWo_XOmH6DiTk",
                 [8, 12, 17, 20], 11, "brandwars", "EF4444"),
    "gen": _niche("gen", "Boomer vs Zoomer", "👴📱", "1iSNcRVhEr5Vz7I7OJfq93pFzd4cQ4RPYY26O8zBnNOQ",
                  [12, 17, 20, 22], 11, "generations", "8B5CF6"),
    "states": _niche("states", "50 States Showdown", "🇺🇸🗺️", "1dBSbP8Kx6BPxC9dZqakC6MLIJutWfL9aha6ngDiDqT0",
                     [11, 13, 18, 21], 12, "states", "2563EB"),
    "college": _niche("college", "Hate Week Daily", "🏈🎓", "1QkZGzJ7rFDgN1y68P-4hsDB7HWMdO2Kt3kb4YUWvFBs",
                      [10, 15, 19, 21], 9, "collegefootball", "B91C1C"),
    "crime": _niche("crime", "Cold File", "🗂️🔎", "1DvfO3fCymAM7Li88NXxtJpmG8gEXyZTtmqvC-XBfC4s",
                    [19, 21, 22, 23], 20, "unsolved", "991B1B",
                    script_temp=0.6, mood_default="dark", strict_risk=True,
                    script_mood="dark", long_mood="dark", card="SHORT LINE",
                    ideas_extra=" راجع الأفكار لو حابب تمسح أي قضية مش مريحة.",
                    ideas_system='You are a YouTube Shorts content strategist for the US English true-mystery channel "Cold File". You always return strictly valid JSON matching the requested schema, with no extra commentary.',
                    script_system='You are a careful, fact-checked true-mystery scriptwriter for the US English channel "Cold File". You always return strictly valid JSON matching the requested schema, with no extra commentary.',
                    gate_system="You are a strict YouTube content policy and defamation-risk reviewer. You always return strictly valid JSON matching the requested schema, with no extra commentary.",
                    long_system='You are a careful, fact-checked long-form true-mystery documentary scriptwriter for the US English channel "Cold File". You always return strictly valid JSON matching the requested schema, with no extra commentary.'),
}

# الست قنوات الجداد — مقفولة (enabled=False) لحد ما قناة يوتيوب ومفاتيح
# YT_<PREFIX>_* بتوعها يتضافوا في GitHub. لما تجهز: خلي enabled=True.
# 3 شورتس يوميًا + فيديو طويل السبت، بتوقيت نيويورك.
# القنوات الجديدة اللي مفاتيح يوتيوب بتاعتها اتضافت واتجرّبت — الباقي مقفول.
LIVE_NEW_CHANNELS = {"goat", "ref", "money", "future", "hist"}

NEW_SHEETS = {'fc': '1nfEPxYbazXa1sHhctjMyxB0Vh54H9EpEFr8b-ju3G5I',
    'goat': '1xcdZi9P8fv6h0zKmEo73ALDMwBLCwu6sowA7TH6Vmrk',
    'ref': '1FZP-xjr0npUd43XTD7W77_XIlLlDKbGX1wyF-BlQvfo',
    'money': '1OT72JmTHYZnW9p32I8USI2rHv5qDBYhzUy7yVf3nHSM',
    'future': '1BW7hU58OpsHo7RyHccfbLtmqojMZzFoE1EY0alEZrTA',
    'hist': '1wJE6EqT2IUP0jv8nRUXPRHtfwU_f5nPwRZWAApVvnyU'}

for _key, _name, _emoji, _hours, _long, _tag, _color in [
    ("fc", "Hot Take FC", "🔥⚽", [11, 16, 20], 10, "hottakefc", "DC2626"),
    ("goat", "GOAT Court", "🐐⚖️", [12, 17, 21], 12, "goat", "CA8A04"),
    ("ref", "Ref Robbery", "🟥📺", [10, 15, 19], 13, "var", "16A34A"),
    ("money", "Money Myths", "💸🧾", [8, 13, 18], 9, "moneymyths", "059669"),
    ("future", "Future Shock", "🤖⚡", [9, 14, 19], 14, "futureshock", "7C3AED"),
    ("hist", "History Hot Seat", "🏛️🔥", [10, 16, 21], 15, "history", "B45309"),
]:
    CHANNELS[_key] = _niche(_key, _name, _emoji, NEW_SHEETS[_key], _hours, _long, _tag, _color)
    CHANNELS[_key]["sheet_tab"] = None  # أول تاب في الشيت
    CHANNELS[_key]["enabled"] = _key in LIVE_NEW_CHANNELS

# الـ Cold File كان مثال الأفكار بتاعه بيقول "teases the mystery" بدل "teases the answer".
CHANNELS["crime"]["examples"]["ideas"] = CHANNELS["crime"]["examples"]["ideas"].replace(
    "teases the answer without giving it away", "teases the mystery without giving it away")

# معرّف محادثة تليجرام اللي كانت كل الإشعارات بتروحله في n8n (ممكن تغيّره بـ TELEGRAM_CHAT_ID).
DEFAULT_TELEGRAM_CHAT_ID = "975228445"


def by_render_channel(render_channel):
    """يرجّع إعدادات القناة من قيمة input الـ channel بتاعة render.yml."""
    for ch in CHANNELS.values():
        if ch["render_channel"] == (render_channel or ""):
            return ch
    return None
