---
name: yt-pipeline
description: Operate and modify the yt-render YouTube Shorts pipeline (n8n → GitHub Actions render/upload) for the two channels "DID YOU KNOW THIS ?" (English) and "Pasión Liguera" (Spanish). Use whenever asked to change video content/topics, background music, voice, clip sourcing, upload behavior, or to debug a failed render/upload run in this repo.
---

# yt-render pipeline

Two YouTube Shorts channels share this one repo and one render codepath.

## Architecture

- `render.yml` (GitHub Actions) takes a `payload` JSON string + `channel` input (empty = English, `es` = Spanish). Secrets are indexed dynamically per channel (`YT_*` vs `YT_ES_*`), separate Actions concurrency group per channel.
- n8n generates content (topic → script/metadata → Pexels clip search → render payload → dispatch `render.yml` → upload) and is the source of truth for prompts/topics/guardrails. n8n workflows:
  - English "المصنع — توليد ونشر": `QqhrZncobmYLYSKE`
  - Spanish "المصنع الإسباني — كورة": `TNpLJWN9Zowz70Ii`
- `scripts/render.py` does the actual video assembly (voice, clips, music, captions). `scripts/upload.py` uploads to YouTube, honors `meta.get("privacy", "public")`.
- `assets/music/` holds the real background-music tracks + `manifest.json` (filename → mood tags) + `SOURCES.md` (licenses). `.github/workflows/fetch-music.yml` is the reusable workflow that downloads new CC0/public-domain tracks from archive.org and commits them (the dev sandbox can't reach archive.org directly, so this must run on an Actions runner).

## Critical rules

1. **n8n `update_workflow` only edits the draft.** You MUST call `publish_workflow` after every change or production keeps running the old version. This is the single most common mistake — always publish after updating.
2. **`update_workflow` operation format**: `{"type": "updateNodeParameters", "nodeName": "<exact node name>", "parameters": {...}}`. There is no `nodeId`/`changes` form — it will be rejected.
3. **Never trigger `execute_workflow` with `executionMode: "production"`.** Claude Code's auto-mode classifier blocks this (`[Production Deploy]`) and it should not be bypassed — it burns a real content-queue item and publishes a real video. Prefer waiting for the scheduled run, or ask the user to trigger it manually in the n8n UI, then read logs/results afterward.
4. **Repeated production test runs burn real uploads and the daily YouTube upload cap** (`uploadLimitExceeded`, separate from API quota). Verify via job logs of an already-triggered run before dispatching another. When a fresh end-to-end test is genuinely needed, do it once — set `privacy: "private"` (or `"unlisted"`) in the test payload, not `"public"`.
5. **Don't republish an n8n workflow repeatedly in one day** — it can reset/disrupt the schedule trigger (misfirePolicy "skip" can silently drop a slot). Batch edits, publish once.
6. **Music tracks must be CC0 / public-domain-dedication**, verified via archive.org's `metadata/<id>` endpoint's `licenseurl` field. Accept `*/publicdomain/zero/1.0/*` and `*/licenses/publicdomain/*`; reject `*/publicdomain/mark/1.0/*` (that's just a label, not a dedication) and anything else.

## Known gotchas (see also `assets/music/SOURCES.md` for full source list)

- **YAML block-scalar indentation**: embedding multi-line Python/shell inside a `run: |` block with inconsistent indentation silently truncates the block, breaking the whole workflow's parsing (including `on:` triggers) with no obvious error at the point of failure. Diagnose with `python3 -c "import yaml; yaml.safe_load(open(path))"` locally. Fix: write scripts via `printf '%s\n' 'line1' 'line2' ... > script.py` (every YAML line at consistent indentation) instead of inline heredocs.
- **ffmpeg `-vn` is required** when processing source mp3s that carry embedded cover-art image streams — without it, ffmpeg tries to transcode the image as video and fails with "Could not write header".
- **This dev sandbox** cannot reach archive.org, YouTube, GitHub Actions log blob storage, the GitHub secrets API, or edge-tts's own endpoint (speech.platform.bing.com) — all work fine from GitHub Actions runners. Never try to set/verify GitHub secrets directly (no tool exists for it; the user must do it). Use `mcp__github__get_job_logs` for run logs, not curl.
- **Google's `commondatastorage.googleapis.com` sample bucket now 403s.** Use `download.samplelib.com`, MDN interactive-examples, or w3schools sample URLs for test payload clips.
- `get_job_logs` output can exceed tool token limits — read the saved file with Python string filtering (search for `error`/`!`/status keywords) rather than the `Read` tool's line-offset chunking.

## Background music system (mood-aware, as of 2026-09-22)

`render.py`'s `build_music(target_total, outfile, outdir, mood=None)`:
- Picks a track from `assets/music/` whose `manifest.json` tags include `mood` (falls back to full-pool random if no mood given or no match).
- `MUSIC_VOLUME = 0.25` (kept low, clearly under narration — the user explicitly asked for this twice).
- Normalizes the chosen track with `loudnorm=I=-19:TP=-1.5:LRA=11`, loops it to voice length, `-vn` on both ffmpeg calls.
- Falls back to an old AI-generated chord-progression bed (`_build_generated_music`) ONLY if `assets/music/` itself is missing — the user explicitly rejected generated music as the default ("هات تراكات جاهزة").
- `mood` is generated by the AI script-writer prompt in both n8n workflows (allowed values: `upbeat|calm|dark|epic|electronic`, default `cinematic`) and passed end-to-end through the render payload.
- To add more tracks: extend `fetch-music.yml` with new archive.org CC0 sources (license-verify each `licenseurl` before trusting it), then add entries to `assets/music/manifest.json` with mood tags, dispatch the workflow, and `git pull`.

## Content/topic guardrails

- **English channel**: science/curiosity + debate-driving US topics. No partisan politics/elections/named politicians/religion/race-gender-sexuality-blame/medical misinfo/recent tragedies/conspiracy-as-fact.
- **Spanish channel**: La Liga only, documented history — NOT match highlights (no footage rights). Favors documented controversies (referee scandals, brawls, federation sanctions, bitter transfers) alongside records — officially-documented only, never rumour or living-player-private-life claims.
- Shared script rules: 6-9 sentences (~30-40s), hook under 12 words, no sign-off, title under 60 chars, gate score ≥80.
- Motion: 2.5s segments, 12 Pexels clips/video, randomized search page (1-4) and rotating search-query phrasings to avoid clip repetition.

## Workflow for making a change

1. Read the relevant n8n node(s) via `get_workflow_details` before editing — don't guess node names.
2. Apply the change with `update_workflow` (draft-only).
3. **`publish_workflow` immediately** — do not batch this step away.
4. If it touches `render.py`/ffmpeg logic: `py_compile` locally, test the specific logic path with synthetic data (real TTS/archive.org are unreachable from the sandbox), commit, push, and (if it needs live verification) dispatch the relevant Actions workflow and poll for completion rather than assuming success.
5. Update `/tmp/claude/memory/team/silo/youtube-shorts-pipeline-tuning.md` and `-gotchas.md` if the change is architecturally significant or a new gotcha was discovered.
