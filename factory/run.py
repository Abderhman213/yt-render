#!/usr/bin/env python3
"""
المصنع — بديل وركفلوهات n8n "مصنع ..." السبعة.

  python -m factory.run --channel bw --mode short
  python -m factory.run --channel es --mode long

short: قراءة جدول المحتوى → (لو الأفكار قليلة: توليد 20 فكرة وإضافتها والوقوف)
       → سكريبت → بوابة الجودة → فحص التكرار والدرجة → لقطات → تشغيل render.yml
       → تحديث الصف → إشعار. لو اترفض: تحديث الصف وإشعار ومحاولة تانية (لحد 3).
long:  سكريبت طويل → لقطات → تشغيل render.yml → تسجيل صف → إشعار.

--dry-run: بيعمل كل خطوات Gemini و Pexels ويطبع الـ payload، من غير ما يكتب في
الشيت أو يبعت تليجرام أو يشغّل الرندر.
--privacy: بيغيّر privacy في الـ payload (مفيد لأول تجربة: private).
"""

import argparse
import json
import os
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from factory import services  # noqa: E402
from factory.channels import CHANNELS  # noqa: E402

PROMPTS = Path(__file__).resolve().parent / "prompts"
MOODS = ["upbeat", "calm", "dark", "epic", "electronic"]
MAX_ATTEMPTS = 3
PASS_SCORE = 80


def js_json(v):
    """JSON.stringify بتاع جافاسكربت (من غير مسافات، ومن غير escape للعربي/الإسباني)."""
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))


def prompt(ch, name, **values):
    text = (PROMPTS / ch["key"] / f"{name}.txt").read_text(encoding="utf-8")
    for k, v in values.items():
        text = text.replace("{{" + k + "}}", str(v))
    return text


def ask(ch, stage, text):
    return services.gemini_json(ch["models"][stage], ch["systems"][stage], text,
                                ch["examples"][stage], ch["temps"][stage])


def prefixed(ch, text):
    return f"{ch['label']} — {text}" if ch["label"] else text


class Run:
    def __init__(self, ch, dry_run=False, privacy=None):
        self.ch = ch
        self.dry = dry_run
        self.privacy = privacy
        try:
            self.sheet = services.Sheet(ch["sheet_id"], ch["sheet_tab"])
        except SystemExit:
            if not dry_run:
                raise
            # تجربة جافة من غير بيانات Google: نكمّل بطابور فاضي
            print("! مفيش بيانات دخول للشيت — التجربة الجافة هتفترض إن الطابور فاضي", flush=True)
            self.sheet = None

    # ---------------------------------------------------------- side effects

    def notify(self, text):
        if self.dry:
            print(f"[dry-run] تليجرام: {text}")
        else:
            services.telegram(text)

    def sheet_update(self, row_id, fields):
        if self.dry:
            print(f"[dry-run] تحديث الصف {row_id}: {fields}")
        else:
            self.sheet.update(row_id, fields)

    def sheet_append(self, records):
        if self.dry:
            print(f"[dry-run] إضافة {len(records)} صف للشيت")
        else:
            self.sheet.append(records)

    def dispatch(self, payload):
        if self.privacy:
            payload["privacy"] = self.privacy
        out = Path(os.environ.get("PAYLOAD_OUT", "payload.json"))
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        if self.dry:
            print(f"[dry-run] payload جاهز في {out} ({len(payload['lines'])} جملة، {len(payload['clips'])} لقطة)")
        else:
            services.dispatch_render(payload, self.ch["render_channel"])
            print(f"==> اتشغّل render.yml (channel={self.ch['render_channel'] or 'en'})")

    def rows(self):
        return self.sheet.rows() if self.sheet else []

    # ---------------------------------------------------------- short

    def short(self):
        ch = self.ch
        for attempt in range(1, MAX_ATTEMPTS + 1):
            print(f"==> محاولة {attempt}/{MAX_ATTEMPTS}", flush=True)
            pick = self.pick_next(self.rows())

            if pick["need_topics"]:
                self.generate_ideas(pick)
                return

            nxt = pick["next"]
            print(f"==> الفكرة: #{nxt.get('id')} {nxt.get('title')}", flush=True)
            content = ask(ch, "script", prompt(
                ch, "script", TOPIC=nxt.get("topic", ""), TITLE=nxt.get("title", ""),
                SEARCH_QUERY=nxt.get("search_query", "")))
            gate = ask(ch, "gate", prompt(
                ch, "gate", TITLE=content.get("title", ""), DESCRIPTION=content.get("description", ""),
                LINES=js_json(content.get("lines", []))))
            verdict = self.check(content, gate, pick["published_titles"])
            print(f"    درجة: {verdict['score']} — {verdict['reason']}", flush=True)

            if verdict["passed"]:
                clips = self.short_clips(content)
                payload = self.payload(nxt["id"], content, clips, ch["short_moments"], ch["short_mood_default"])
                self.dispatch(payload)
                self.sheet_update(nxt["id"], {"status": "rendering", "title": content.get("title", ""),
                                              "score": verdict["score"]})
                self.notify(prefixed(ch, f"بدأ رندر: {content.get('title')} (درجة الجودة: {verdict['score']})"))
                return

            fields = {"score": verdict["score"]}
            if ch["reject_marks_row"]:
                fields.update({"status": "rejected", "notes": verdict["reason"]})
            self.sheet_update(nxt["id"], fields)
            self.notify(prefixed(ch, f"اترفض: {content.get('title')}\nالسبب: {verdict['reason']}"))
            if self.dry:
                return

        self.notify(prefixed(ch, "استنفدنا محاولات هذا التشغيل من غير موضوع مؤهل للنشر. هنجرب تاني في التشغيلة الجاية."))

    def pick_next(self, rows):
        """اختيار الفكرة التالية."""
        ch = self.ch
        norm = lambda s: str(s or "").strip().lower()  # noqa: E731
        ideas = [r for r in rows if norm(r.get("status")) == "idea"]
        published = [r for r in rows if norm(r.get("status")) in ch["published_statuses"]]
        published_titles = [t for t in (norm(r.get("title")) for r in published) if t]
        max_id = 0
        for r in rows:
            try:
                max_id = max(max_id, int(str(r.get("id", "")).strip()))
            except ValueError:
                pass
        base = {"published_titles": published_titles, "max_id": max_id}

        if ch.get("laliga_only"):
            laliga = re.compile(r"(la ?liga|\bliga\b)")

            def ok(r):
                blob = norm(r.get("tags")) + " " + norm(r.get("title")) + " " + norm(r.get("topic"))
                return bool(laliga.search(blob)) and _int(r.get("id")) not in ch["blocked_ids"]
            ideas = [r for r in ideas if ok(r)]

        if len(ideas) < ch["min_ideas"]:
            return {**base, "need_topics": True}

        prio = ch.get("priority_ids", [])
        ideas.sort(key=lambda r: (0 if _int(r.get("id")) in prio else 1, _int(r.get("id")) or 0))
        nxt = dict(ideas[0])
        vt = ch.get("verified_topics", {}).get(_int(nxt.get("id")))
        if vt:
            nxt["topic"] = vt
        return {**base, "need_topics": False, "next": nxt}

    def generate_ideas(self, pick):
        ch = self.ch
        print("==> الطابور محتاج أفكار — بولّد 20 فكرة جديدة", flush=True)
        data = ask(ch, "ideas", prompt(ch, "ideas", PUBLISHED_TITLES=js_json(pick["published_titles"])))
        topics = data.get("topics") or (data.get("output") or {}).get("topics") or []
        rows = []
        for i, t in enumerate(topics):
            voice = t.get("voice") if t.get("voice") in ch["voices"] else ch["voices"][0]
            if ch["key"] == "en":
                voice = t.get("voice") or "en-US-AndrewNeural"
            if ch.get("laliga_only"):
                queries = t.get("image_queries") or ([t["search_query"]] if t.get("search_query") else ["La Liga football"])
                search = "|".join(queries)
            else:
                search = t.get("search_query", "")
            rows.append({
                "id": str(pick["max_id"] + i + 1), "topic": t.get("topic", ""), "status": "idea",
                "title": t.get("title", ""), "description": t.get("description", ""),
                "tags": ",".join(t.get("tags") or []), "search_query": search, "voice": voice,
                "score": "", "notes": "", "video_id": "", "video_url": "", "published_at": "",
            })
        self.sheet_append(rows)
        print(f"    اتضاف {len(rows)} فكرة", flush=True)
        self.notify(ch["messages"]["ideas"])

    def check(self, content, gate, published_titles):
        """فحص التكرار والدرجة."""
        ch = self.ch
        score = _num(gate.get("score"))
        risk = str(gate.get("policy_risk") or "").lower()
        risk_ok = risk == "low" if ch["strict_risk"] else risk != "high"
        original = gate.get("is_original") is not False

        duplicate, best = False, 0.0
        if ch["dup_check"]:
            words = lambda s: [w for w in re.sub(r"[^a-z0-9 ]", "", str(s or "").lower()).split(" ") if len(w) > 3]  # noqa: E731
            new = words(content.get("title"))
            for pub in published_titles:
                old = words(pub)
                if not old or not new:
                    continue
                overlap = sum(1 for w in new if w in old) / min(len(new), len(old))
                best = max(best, overlap)
                if overlap >= 0.6:
                    duplicate = True

        lines = content.get("lines")
        has_lines = isinstance(lines, list) and len(lines) >= ch["min_lines"]
        passed = score >= PASS_SCORE and original and risk_ok and not duplicate and has_lines

        if duplicate:
            reason = f"duplicate title (overlap {best:.2f})"
        elif score < PASS_SCORE:
            reason = f"low quality score: {score:g}"
        elif not risk_ok:
            reason = (f"policy risk {gate.get('policy_risk')}: " if ch["strict_risk"] else "high policy risk: ") + str(gate.get("reasons"))
        elif not has_lines:
            reason = "script too short"
        elif not original:
            reason = "not original enough" if ch["key"] == "es" else "not original"
        else:
            reason = "OK"
        return {"passed": passed, "reason": reason, "score": score}

    def short_clips(self, content):
        cfg = self.ch["short_clips"]
        if cfg["query"] == "random":
            query = random.choice(cfg["queries"])
        else:
            query = content.get("search_query", "")
        videos = services.pexels_videos(query, cfg["orientation"], cfg["per_page"],
                                        random.randint(1, cfg["pages"]), cfg.get("size"))
        return pick_pexels(videos, cfg)

    # ---------------------------------------------------------- long

    def long(self):
        ch = self.ch
        values = {"NOW": datetime.now(ZoneInfo(ch["tz"])).isoformat(timespec="milliseconds")}
        if ch["long_needs_titles"]:
            titles = [str(r["title"]) for r in self.rows() if r.get("title")]
            values["PUBLISHED_TITLES"] = js_json(titles)
        content = ask(ch, "long", prompt(ch, "long", **values))
        print(f"==> فيديو طويل: {content.get('title')} ({len(content.get('lines') or [])} جملة)", flush=True)

        cfg = ch["long_clips"]
        if cfg["source"] == "commons":
            clips = pick_commons(services.commons_media(content.get("image_queries") or ["La Liga football"]),
                                 cfg["count"])
        else:
            videos = services.pexels_videos(content.get("search_query", ""), cfg["orientation"],
                                            cfg["per_page"], random.randint(1, cfg["pages"]))
            clips = pick_pexels(videos, cfg)

        row_id = f"long-{int(time.time() * 1000)}"
        payload = self.payload(row_id, content, clips, ch["long_moments"], ch["long_mood_default"])
        if ch["long_format"]:
            payload["format"] = ch["long_format"]
        self.dispatch(payload)

        record = {"id": row_id, "status": ch["long_row"]["status"], "title": content.get("title", ""),
                  "notes": "فيديو طويل أسبوعي (السبت)"}
        if ch["long_row"]["with_topic"]:
            record["topic"] = content.get("title", "")
        self.sheet_append([record])
        self.notify(ch["messages"]["long_started"].format(title=content.get("title")))

    # ---------------------------------------------------------- payload

    def payload(self, row_id, content, clips, max_moments, mood_default):
        ch = self.ch
        voice = content.get("voice")
        if voice not in ch["voices"]:
            voice = ch["voices"][0]
        mood = content.get("mood") if content.get("mood") in MOODS else mood_default
        payload = {
            "id": row_id,
            "title": content.get("title"),
            "description": content.get("description"),
            "tags": content.get("tags") or [],
            "privacy": "public",
            "voice": voice,
            "mood": mood,
            "lines": content.get("lines") or [],
            "clips": clips,
        }
        if max_moments:
            moments = content.get("moments") if isinstance(content.get("moments"), list) else []
            payload["moments"] = [m for m in moments[:max_moments] if isinstance(m, dict) and m.get("text")]
        return payload


# ------------------------------------------------------------ clip pickers

def pick_pexels(videos, cfg):
    """نفس منطق عقد "تجهيز حمولة الرندر" في n8n."""
    clips, seen = [], set()
    for v in services.shuffle(videos):
        files = v.get("video_files") or []
        mode = cfg["pick"]
        if mode == "near1080":
            if (v.get("duration") or 0) < cfg.get("min_duration", 0):
                continue
            mp4 = [f for f in files if f.get("file_type") == "video/mp4" and f.get("link")]
            if not mp4:
                continue
            portrait = [f for f in mp4 if (f.get("height") or 0) >= (f.get("width") or 0)]
            pool = sorted(portrait or mp4, key=lambda f: abs((f.get("width") or 0) - 1080))
            link = pool[0]["link"]
        else:
            landscape = mode == "hd_landscape"
            target = 1920 if landscape else 1080
            oriented = [f for f in files if ((f.get("width") or 0) >= (f.get("height") or 0)) == landscape
                        or ((f.get("width") or 0) == (f.get("height") or 0))]
            hd = sorted([f for f in oriented if (f.get("width") or 0) >= target], key=lambda f: f.get("width") or 0)
            best = sorted(oriented, key=lambda f: -(f.get("width") or 0))
            chosen = (hd or best or files or [None])[0]
            link = chosen.get("link") if chosen else None
        if not link or link in seen:
            continue
        seen.add(link)
        clips.append(link)
        if len(clips) >= cfg["count"]:
            break
    return clips


def pick_commons(pages, count):
    """فيديوهات Commons الأول، وبعدين الصور (مصغّرة لعرض 1280)."""
    videos, images = [], []
    for p in pages:
        info = (p.get("imageinfo") or [None])[0]
        if not info:
            continue
        mime = (info.get("mime") or "").lower()
        if mime.startswith("video/") or mime == "application/ogg":
            videos.append(info)
        elif mime.startswith("image/") and mime != "image/svg+xml":
            images.append(info)
    clips, seen = [], set()
    for info in services.shuffle(videos) + services.shuffle(images):
        is_image = (info.get("mime") or "").lower().startswith("image/")
        link = (is_image and info.get("thumburl")) or info.get("url")
        if not link or link in seen:
            continue
        seen.add(link)
        clips.append(link)
        if len(clips) >= count:
            break
    return clips


def _int(v):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0
    return int(f) if f.is_integer() else f


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", required=True, choices=sorted(CHANNELS))
    ap.add_argument("--mode", required=True, choices=["short", "long"])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--privacy", choices=["public", "private", "unlisted"])
    args = ap.parse_args()

    run = Run(CHANNELS[args.channel], dry_run=args.dry_run, privacy=args.privacy)
    print(f"==> {args.channel} / {args.mode}{' (dry-run)' if args.dry_run else ''}", flush=True)
    if args.mode == "short":
        run.short()
    else:
        run.long()


if __name__ == "__main__":
    main()
