"""
publish_kit.py — everything a person needs to actually POST a clip.

For each finished clip this produces a "publish kit":

    • TWO sets of at least 5 titles each. For funny/relatable clips that means
      bro-format memes ("bro: you're lucky" / "me:") and a second meme template
      set (POV / nobody / when you…). For serious clips the same two slots hold
      authority hooks and curiosity hooks instead — the goal is always a heading
      that is attractive and engaging, never a flat description.
    • One short on-screen title (<= 6 words) — this is the one burned into the video.
    • A ready-to-paste caption, hashtags, and a first comment.
    • Posting guidelines: best time to post, and per-platform notes.

The kit is written next to each clip as `<clip>_POST.txt` so it travels inside the
downloaded ZIP, and is returned as structured data for the in-app copy panel.

Like the rest of the pipeline this degrades rather than fails: if no chat provider
is reachable, every clip still gets a usable fallback kit built from its transcript.
"""

import json
import os
import re

import providers

# Clips per LLM call. Each clip's kit is large (10+ titles, caption, tags, notes),
# so batches are small — a bigger batch gets truncated mid-JSON and we lose the lot.
_KIT_BATCH = 5

# Transcript budget per clip inside the prompt.
_EXCERPT_CHARS = 700

# Hashtag hygiene: too few looks lazy, too many reads as spam and platforms
# de-prioritise it.
_MIN_TAGS, _MAX_TAGS = 5, 12

_ONSCREEN_MAX_WORDS = 6


def _loads(raw: str):
    """Parse a JSON object out of an LLM reply, tolerating code fences and prose."""
    if not raw:
        return None
    txt = re.sub(r"```[a-zA-Z]*", "", raw).replace("```", "").strip()
    try:
        return json.loads(txt)
    except (ValueError, TypeError):
        pass
    for open_c, close_c in (("{", "}"), ("[", "]")):
        i, j = txt.find(open_c), txt.rfind(close_c)
        if 0 <= i < j:
            try:
                return json.loads(txt[i:j + 1])
            except (ValueError, TypeError):
                continue
    return None


def _excerpt(text: str, limit: int = _EXCERPT_CHARS) -> str:
    text = " ".join((text or "").split())
    return text[:limit] + ("…" if len(text) > limit else "")


def _clean_tag(tag: str) -> str:
    """Normalise one hashtag to '#word' form, or '' if nothing usable is left."""
    t = re.sub(r"[^0-9A-Za-z_ऀ-ॿ]", "", str(tag or ""))
    return f"#{t}" if t else ""


def _clean_tags(tags) -> list:
    if not isinstance(tags, list):
        tags = re.split(r"[\s,]+", str(tags or ""))
    out, seen = [], set()
    for t in tags:
        c = _clean_tag(t)
        key = c.lower()
        if c and key not in seen:
            seen.add(key)
            out.append(c)
    return out[:_MAX_TAGS]


def _clean_titles(titles, want: int) -> list:
    """Strip numbering/quotes from a title list and drop empties. Never pads with
    filler — a short honest list beats five variations of the same line."""
    out = []
    if not isinstance(titles, list):
        titles = [titles] if titles else []
    for t in titles:
        s = str(t or "").strip()
        s = re.sub(r"^\s*\d+[\.\)]\s*", "", s)          # "1. " / "2) "
        s = s.strip().strip('"').strip("'").strip()
        if s and s not in out:
            out.append(s)
    return out[:max(want, len(out))]


def _shorten_onscreen(text: str) -> str:
    """The burned-in title has to fit the frame — clamp it to a few words."""
    words = " ".join(str(text or "").split()).strip('"').split()
    return " ".join(words[:_ONSCREEN_MAX_WORDS])


def _fallback_kit(clip: dict) -> dict:
    """A usable kit with no LLM: the clip's own opening line becomes the title and
    caption, so the .txt is never empty and the user still has something to paste."""
    opening = _excerpt(clip.get("opening", "") or clip.get("transcript", ""), 90)
    onscreen = _shorten_onscreen(opening) or f"Clip {clip['index']}"
    return {
        "index": clip["index"],
        "tone": "unknown",
        "title_sets": [
            {"label": "From the clip", "titles": [opening] if opening else []},
        ],
        "onscreen": onscreen,
        "caption": opening,
        "hashtags": _clean_tags(["shorts", "reels", "viral", "trending", "fyp"]),
        "first_comment": "",
        "best_time": "",
        "platform_notes": "",
        "generated": False,
    }


def _clip_block(clip: dict, local_idx: int) -> str:
    dur = float(clip.get("duration") or 0.0)
    lines = [f"CLIP {local_idx}  ({dur:.0f}s)"]
    if clip.get("opening"):
        lines.append(f"  OPENS WITH: {_excerpt(clip['opening'], 160)}")
    lines.append(f"  TRANSCRIPT: {_excerpt(clip.get('transcript', '')) or '(no speech detected)'}")
    return "\n".join(lines)


_PROMPT_HEADER = """You write titles and captions for Instagram Reels / YouTube Shorts that get views.
For each clip below, produce a full posting kit.

FIRST decide the clip's TONE: "funny" (relatable, absurd, meme-able, emotional,
argument, fail, roast) or "serious" (informative, motivational, news, teaching,
genuinely emotional story).

THEN write TWO SETS OF 5 TITLES EACH — 10 titles per clip, all different.

IF TONE IS "funny", the two sets are:
  SET A — "Bro-format memes", label it exactly "Bro-format meme". Two-line contrast
  memes in the Instagram meme voice. The setup and the punchline sit on separate
  lines, written with a real newline between them. Examples of the FORM:
      bro: you're so lucky
      me: *my luck*
    ---
      everyone: just be confident
      me, internally:
    ---
      him: it's not that deep
      also him:
  SET B — "POV / template meme", label it exactly "POV / template meme". Use VARIED
  popular templates, a different one per title: "POV: …", "nobody: …", "when you …",
  "that one friend who …", "me explaining …", "the way I …".

IF TONE IS "serious", the two sets are:
  SET A — label it exactly "Authority hook". Confident, specific, high-stakes
  statements. ("Nobody teaches this in school", "This one habit costs you years")
  SET B — label it exactly "Curiosity hook". Open a loop the viewer must close.
  ("You're doing this backwards", "The real reason nobody told you")

TITLE RULES (all sets):
- Every title must be ATTRACTIVE and ENGAGING. Never a flat summary of the clip.
- Match the clip's actual content — do not invent facts that are not in the transcript.
- Hindi or Hinglish clips: write titles in Hinglish (Roman script), not Devanagari.
- No hashtags inside titles. No numbering. Max 12 words per line.

ALSO for each clip:
- "onscreen": the single strongest title, rewritten to MAX 6 WORDS, one line, no
  emoji — this gets burned onto the video itself so it must be short.
- "caption": 1-2 sentences to paste in the post. Conversational, ends with a
  question or a call to comment.
- "hashtags": 5-10 hashtags, mixing broad reach and topic-specific ones.
- "first_comment": one line to pin as the first comment to drive replies.
- "best_time": when to post this for the Indian audience, with the reason, one line.
- "platform_notes": one line of practical advice specific to THIS clip (aspect,
  where to cut, what to put in the thumbnail, whether to loop it)."""


def _generate_batch(batch: list, log) -> dict:
    """Returns {local_idx: kit dict} for one batch of clips."""
    blocks = "\n\n".join(_clip_block(c, i) for i, c in enumerate(batch))
    prompt = f"""{_PROMPT_HEADER}

Output ONLY valid JSON. Use the clip numbers shown. Newlines inside a bro-format
title must be written as \\n:
{{"clips": [{{"clip": 0, "tone": "funny",
  "set_a_label": "Bro-format meme", "set_a": ["...", "...", "...", "...", "..."],
  "set_b_label": "POV / template meme", "set_b": ["...", "...", "...", "...", "..."],
  "onscreen": "max six words",
  "caption": "...", "hashtags": ["#..."], "first_comment": "...",
  "best_time": "...", "platform_notes": "..."}}]}}

CLIPS:
{blocks}"""

    raw = providers.chat(prompt, temperature=0.85, json_mode=True, log=log)
    parsed = _loads(raw)
    if not parsed:
        return {}
    rows = parsed.get("clips", parsed) if isinstance(parsed, dict) else parsed
    if not isinstance(rows, list):
        return {}

    out = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            local = int(row.get("clip"))
        except (TypeError, ValueError):
            continue
        if not 0 <= local < len(batch):
            continue

        tone = str(row.get("tone", "") or "").strip().lower()
        tone = tone if tone in ("funny", "serious") else "unknown"
        set_a = _clean_titles(row.get("set_a"), 5)
        set_b = _clean_titles(row.get("set_b"), 5)
        title_sets = []
        if set_a:
            title_sets.append({
                "label": str(row.get("set_a_label") or "Meme titles").strip(),
                "titles": set_a,
            })
        if set_b:
            title_sets.append({
                "label": str(row.get("set_b_label") or "Alternate titles").strip(),
                "titles": set_b,
            })
        if not title_sets:
            continue   # nothing usable — the caller falls back for this clip

        onscreen = _shorten_onscreen(row.get("onscreen") or set_a[0] if set_a else "")
        tags = _clean_tags(row.get("hashtags"))
        if len(tags) < _MIN_TAGS:
            # Top up with broad-reach tags rather than posting with two hashtags.
            tags = _clean_tags(tags + ["shorts", "reels", "viral", "trending", "fyp"])

        out[local] = {
            "index": batch[local]["index"],
            "tone": tone,
            "title_sets": title_sets,
            "onscreen": onscreen,
            "caption": str(row.get("caption", "") or "").strip(),
            "hashtags": tags,
            "first_comment": str(row.get("first_comment", "") or "").strip(),
            "best_time": str(row.get("best_time", "") or "").strip(),
            "platform_notes": str(row.get("platform_notes", "") or "").strip(),
            "generated": True,
        }
    return out


def generate(clips: list, log) -> dict:
    """Build a publish kit for every clip.

    `clips` is a list of {index, duration, opening, transcript}.
    Returns {clip_index: kit}.
    """
    if not clips:
        return {}

    log.section("PUBLISH KIT (titles, caption, tags, posting guide)")

    if not providers.provider_status().get("chat_ready"):
        log.log("  No chat provider configured — writing fallback kits from the transcript.")
        return {c["index"]: _fallback_kit(c) for c in clips}

    kits = {}
    batches = [clips[i:i + _KIT_BATCH] for i in range(0, len(clips), _KIT_BATCH)]
    log.log(f"  {len(clips)} clip(s) in {len(batches)} batch(es) of up to {_KIT_BATCH}")

    for bi, batch in enumerate(batches):
        got = _generate_batch(batch, log)
        log.log(f"  Batch {bi + 1}/{len(batches)}: {len(got)}/{len(batch)} kit(s) generated")
        for local, clip in enumerate(batch):
            kits[clip["index"]] = got.get(local) or _fallback_kit(clip)

    made = sum(1 for k in kits.values() if k.get("generated"))
    titles = sum(len(s["titles"]) for k in kits.values() for s in k["title_sets"])
    log.log(f"  {made}/{len(kits)} kit(s) from the AI | {titles} title option(s) total")
    return kits


# ─────────────────────────────────────────────────────────────
# Rendering the kit to the .txt that ships beside each clip
# ─────────────────────────────────────────────────────────────

_RULE = "=" * 62


def render_txt(kit: dict, clip_meta: dict = None, verdict: dict = None) -> str:
    """Human-readable posting sheet for one clip."""
    meta = clip_meta or {}
    lines = [
        _RULE,
        f"  POSTING KIT — {meta.get('filename', 'clip %s' % kit.get('index'))}",
        _RULE,
        "",
    ]

    if meta.get("duration"):
        span = ""
        if meta.get("start") is not None and meta.get("end") is not None:
            span = f"   (cut from {meta['start']:.1f}s – {meta['end']:.1f}s of the source)"
        lines.append(f"Length : {float(meta['duration']):.0f}s{span}")
    if meta.get("hook_text"):
        lines.append(f"Hook   : opens on the clip's peak, then replays in full — "
                     f"\"{_excerpt(meta['hook_text'], 70)}\"")

    if verdict:
        lines += [
            f"Rank   : #{verdict.get('rank', '?')} of this batch "
            f"({verdict.get('final_score', '?')}/100, {verdict.get('view_band', 'unrated')} view potential)",
        ]
        if verdict.get("verdict"):
            lines.append(f"Why    : {verdict['verdict']}")
        if verdict.get("improve"):
            lines.append(f"Fix    : {verdict['improve']}")
    lines.append("")

    for tset in kit.get("title_sets", []):
        if not tset.get("titles"):
            continue
        lines += ["-" * 62, f"  {tset['label'].upper()}", "-" * 62]
        for i, t in enumerate(tset["titles"], start=1):
            # Bro-format titles are two lines; indent the continuation so the pair
            # still reads as one option in a plain text file.
            parts = str(t).split("\n")
            lines.append(f"  {i}. {parts[0]}")
            lines += [f"     {p}" for p in parts[1:] if p.strip()]
        lines.append("")

    if kit.get("onscreen"):
        lines += ["-" * 62, "  ON-SCREEN TITLE (burned into the video)", "-" * 62,
                  f"  {kit['onscreen']}", ""]

    if kit.get("caption"):
        lines += ["-" * 62, "  CAPTION", "-" * 62, f"  {kit['caption']}", ""]

    if kit.get("hashtags"):
        lines += ["-" * 62, "  HASHTAGS", "-" * 62,
                  "  " + " ".join(kit["hashtags"]), ""]

    if kit.get("first_comment"):
        lines += ["-" * 62, "  PIN THIS AS THE FIRST COMMENT", "-" * 62,
                  f"  {kit['first_comment']}", ""]

    guide = [(l, kit.get(k)) for l, k in
             (("Best time to post", "best_time"), ("Notes for this clip", "platform_notes"))
             if kit.get(k)]
    if guide:
        lines += ["-" * 62, "  POSTING GUIDELINES", "-" * 62]
        lines += [f"  {label}: {value}" for label, value in guide]
        lines.append("")

    if not kit.get("generated"):
        lines += ["", "(The AI writer was unavailable for this clip — these are "
                  "fallbacks taken", "from the clip's own transcript. Re-run to get "
                  "generated titles.)"]

    return "\n".join(lines).rstrip() + "\n"


def write_kit_file(kit: dict, clip_path: str, clip_meta: dict = None,
                   verdict: dict = None) -> str:
    """Write `<clip stem>_POST.txt` next to the rendered clip. Returns its path."""
    stem = os.path.splitext(os.path.basename(clip_path))[0]
    out_path = os.path.join(os.path.dirname(clip_path), f"{stem}_POST.txt")
    meta = dict(clip_meta or {})
    meta.setdefault("filename", os.path.basename(clip_path))
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(render_txt(kit, meta, verdict))
    return out_path
