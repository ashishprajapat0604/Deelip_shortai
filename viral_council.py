"""
viral_council.py — "which of these clips will actually get views?"

A three-persona council scores every candidate clip, then a judge merges the
votes into a single ranking. This runs TWO LLM calls for the WHOLE batch rather
than per clip: free-tier keys rate-limit fast, and the personas reason better
when they can compare clips side by side.

    Round 1 (1 call)   Hook Critic, Algorithm Analyst and Retention Editor each
                       score every clip 1-100 and say why.
    Round 2 (1 call)   The Judge reads all three score sheets and returns the
                       final ranking, a view-potential band, and the single
                       change that would most improve each clip.

Every stage degrades instead of failing. No chat provider, an unparseable reply,
or a reply that skips clips all fall back to the selector's own score, so a job
never dies in here — it just gets a less interesting ranking.
"""

import json
import re

import providers

# The council scores 1-100 so the judge has room to separate near-identical
# clips. The selector's own 1-10 score is rescaled onto the same axis whenever we
# fall back, so downstream code only ever sees one scale.
SCORE_MIN, SCORE_MAX = 1, 100

# How much of each clip's transcript the council sees. Three personas x N clips
# in one prompt overflows a free-tier context window quickly, and the opening
# lines carry the hook — which is the thing actually being judged.
_EXCERPT_CHARS = 420

# Clips per council call. Beyond this the reply starts getting truncated and the
# model quietly drops the last few clips, so we batch instead.
_COUNCIL_BATCH = 12

PERSONAS = (
    ("hook_critic", "Hook Critic"),
    ("algo_analyst", "Algorithm Analyst"),
    ("retention_editor", "Retention Editor"),
)

_PERSONA_BRIEF = """THE THREE JUDGES — score every clip as each of them, independently:

1. HOOK CRITIC — cares ONLY about the first 3 seconds. Does the opening line stop a
   thumb mid-scroll? Punish slow build-ups, throat-clearing, context-setting and
   anything that needs the viewer to be patient. Reward shock, conflict, a bold
   claim, a question the viewer needs answered, or a visible emotion.

2. ALGORITHM ANALYST — thinks in distribution signals. Will this earn comments,
   shares and saves? Is it re-watchable? Is the topic broad enough to escape a
   niche audience, or so niche it will die in a small test batch? Reward
   controversy, relatability and "I have to send this to someone" energy.

3. RETENTION EDITOR — cares about whether people finish it. Is there a reason to
   keep watching at second 5, 10, 20? Does it pay off, or does it fizzle? Punish
   repetition, rambling, and clips whose best moment is at the very start with
   nothing after it."""


def _loads(raw: str):
    """Parse a JSON object out of an LLM reply, tolerating code fences and prose."""
    if not raw:
        return None
    txt = re.sub(r"```[a-zA-Z]*", "", raw).replace("```", "").strip()
    try:
        return json.loads(txt)
    except (ValueError, TypeError):
        pass
    # The model wrapped the JSON in commentary — take the outermost bracket pair.
    for open_c, close_c in (("{", "}"), ("[", "]")):
        i, j = txt.find(open_c), txt.rfind(close_c)
        if 0 <= i < j:
            try:
                return json.loads(txt[i:j + 1])
            except (ValueError, TypeError):
                continue
    return None


def _clamp_score(value, default=50):
    try:
        return max(SCORE_MIN, min(SCORE_MAX, int(round(float(value)))))
    except (TypeError, ValueError):
        return default


def _excerpt(text: str, limit: int = _EXCERPT_CHARS) -> str:
    text = " ".join((text or "").split())
    return text[:limit] + ("…" if len(text) > limit else "")


def _clip_block(clip: dict, local_idx: int) -> str:
    """One numbered block describing a clip for the council prompt."""
    dur = float(clip.get("duration") or 0.0)
    opening = _excerpt(clip.get("opening", ""), 160)
    body = _excerpt(clip.get("transcript", ""))
    lines = [f"CLIP {local_idx}  ({dur:.0f}s long)"]
    if opening:
        lines.append(f"  FIRST WORDS: {opening}")
    if clip.get("hook_text"):
        lines.append(f"  3-SEC HOOK USED: {_excerpt(clip['hook_text'], 160)}")
    lines.append(f"  TRANSCRIPT: {body or '(no speech detected)'}")
    return "\n".join(lines)


def _fallback_verdict(clip: dict) -> dict:
    """Neutral verdict built from the selector's own 1-10 score. Used whenever the
    LLM is unavailable so the ranking is still ordered and the UI still populates."""
    base = _clamp_score(float(clip.get("score") or 5) * 10, 50)
    return {
        "index": clip["index"],
        "final_score": base,
        "view_band": "unrated",
        "verdict": "Ranked by the clip selector's own score — the AI council was unavailable.",
        "improve": "",
        "personas": {key: base for key, _label in PERSONAS},
        "persona_notes": {},
        "rated": False,
    }


def _round_one(batch: list, log) -> dict:
    """Three persona score sheets in one call. Returns {local_idx: {...}}."""
    blocks = "\n\n".join(_clip_block(c, i) for i, c in enumerate(batch))
    prompt = f"""You are a panel of three short-form video experts reviewing candidate clips
for Instagram Reels / YouTube Shorts. The ONLY goal is views.

{_PERSONA_BRIEF}

TASK:
Score EVERY clip below as all three judges. Scores are 1-100 where 50 is "average
clip that gets ignored", 80+ means "this could genuinely take off", and below 30
means "do not post this". Be harsh and SPREAD THE SCORES OUT — if every clip gets
the same number you have failed the task. Keep each note under 15 words.

Output ONLY valid JSON, one entry per clip, using the clip numbers shown:
{{"scores": [{{"clip": 0, "hook_critic": 72, "hook_note": "...",
  "algo_analyst": 65, "algo_note": "...",
  "retention_editor": 80, "retention_note": "..."}}]}}

CLIPS:
{blocks}"""

    raw = providers.chat(prompt, temperature=0.35, json_mode=True, log=log)
    parsed = _loads(raw)
    if not parsed:
        return {}
    rows = parsed.get("scores", parsed) if isinstance(parsed, dict) else parsed
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
        out[local] = {
            "personas": {
                "hook_critic": _clamp_score(row.get("hook_critic")),
                "algo_analyst": _clamp_score(row.get("algo_analyst")),
                "retention_editor": _clamp_score(row.get("retention_editor")),
            },
            "persona_notes": {
                "hook_critic": str(row.get("hook_note", "") or "").strip(),
                "algo_analyst": str(row.get("algo_note", "") or "").strip(),
                "retention_editor": str(row.get("retention_note", "") or "").strip(),
            },
        }
    return out


def _round_two(batch: list, sheets: dict, log) -> dict:
    """The judge merges the three score sheets. Returns {local_idx: {...}}."""
    if not sheets:
        return {}

    lines = []
    for local, sheet in sorted(sheets.items()):
        p, n = sheet["personas"], sheet["persona_notes"]
        lines.append(
            f"CLIP {local} ({float(batch[local].get('duration') or 0):.0f}s)\n"
            f"  Hook Critic       {p['hook_critic']:3d}  {n.get('hook_critic','')}\n"
            f"  Algorithm Analyst {p['algo_analyst']:3d}  {n.get('algo_analyst','')}\n"
            f"  Retention Editor  {p['retention_editor']:3d}  {n.get('retention_editor','')}\n"
            f"  OPENS WITH: {_excerpt(batch[local].get('opening', ''), 140)}"
        )
    sheet_text = "\n\n".join(lines)

    prompt = f"""You are the Head of Content deciding which of these clips to actually post.
Three judges have scored each clip. Their score sheets are below.

TASK:
For every clip, return a FINAL score of 1-100 for view potential. You may disagree
with the judges — the Hook Critic matters most, because a clip nobody watches past
second 3 cannot succeed no matter how good the rest is. Then:
  - "view_band": one of "low", "medium", "high", "breakout"
  - "verdict": under 20 words, why it will or will not get views
  - "improve": under 12 words, the ONE change that would most increase its views

Rank them honestly. Most clips are "low" or "medium"; "breakout" should be rare.

Output ONLY valid JSON:
{{"ranking": [{{"clip": 0, "final_score": 78, "view_band": "high",
  "verdict": "...", "improve": "..."}}]}}

SCORE SHEETS:
{sheet_text}"""

    raw = providers.chat(prompt, temperature=0.3, json_mode=True, log=log)
    parsed = _loads(raw)
    if not parsed:
        return {}
    rows = parsed.get("ranking", parsed) if isinstance(parsed, dict) else parsed
    if not isinstance(rows, list):
        return {}

    _BANDS = ("low", "medium", "high", "breakout")
    out = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            local = int(row.get("clip"))
        except (TypeError, ValueError):
            continue
        if local not in sheets:
            continue
        band = str(row.get("view_band", "") or "").strip().lower()
        out[local] = {
            "final_score": _clamp_score(row.get("final_score")),
            "view_band": band if band in _BANDS else "medium",
            "verdict": str(row.get("verdict", "") or "").strip(),
            "improve": str(row.get("improve", "") or "").strip(),
        }
    return out


def convene(clips: list, log) -> list:
    """Run the council over `clips` and return one verdict per clip.

    `clips` is a list of {index, duration, score, opening, transcript, hook_text}.
    Returns verdicts sorted BEST FIRST, each carrying `rank` (1 = best):
        {index, rank, final_score, view_band, verdict, improve,
         personas{...}, persona_notes{...}, rated}
    """
    if not clips:
        return []

    log.section("VIRAL COUNCIL (3 personas + judge)")

    if not providers.provider_status().get("chat_ready"):
        log.log("  No chat provider configured — ranking by selector score only.")
        verdicts = [_fallback_verdict(c) for c in clips]
    else:
        verdicts = []
        batches = [clips[i:i + _COUNCIL_BATCH] for i in range(0, len(clips), _COUNCIL_BATCH)]
        log.log(f"  {len(clips)} clip(s) in {len(batches)} council batch(es) "
                f"(2 LLM calls per batch)")

        for bi, batch in enumerate(batches):
            log.log(f"  Batch {bi + 1}/{len(batches)}: round 1 — three personas scoring…")
            sheets = _round_one(batch, log)
            if not sheets:
                log.log("    Round 1 returned nothing usable — falling back for this batch.")
                verdicts.extend(_fallback_verdict(c) for c in batch)
                continue

            log.log(f"    Round 1 scored {len(sheets)}/{len(batch)} clip(s). "
                    f"Round 2 — judge deliberating…")
            final = _round_two(batch, sheets, log)
            if not final:
                log.log("    Judge returned nothing usable — averaging the three personas.")

            for local, clip in enumerate(batch):
                sheet = sheets.get(local)
                if not sheet:
                    verdicts.append(_fallback_verdict(clip))
                    continue
                decided = final.get(local)
                if not decided:
                    # No judge line for this clip: the mean of the three personas is
                    # a defensible stand-in and keeps the clip in the ranking.
                    mean = sum(sheet["personas"].values()) / 3.0
                    decided = {
                        "final_score": _clamp_score(mean),
                        "view_band": "medium",
                        "verdict": "Averaged from the three judges (no final ruling returned).",
                        "improve": "",
                    }
                verdicts.append({
                    "index": clip["index"],
                    "final_score": decided["final_score"],
                    "view_band": decided["view_band"],
                    "verdict": decided["verdict"],
                    "improve": decided["improve"],
                    "personas": sheet["personas"],
                    "persona_notes": sheet["persona_notes"],
                    "rated": True,
                })

    # Best first, then stamp the rank. Ties break on the original clip order so the
    # ranking is stable between runs on the same job.
    verdicts.sort(key=lambda v: (-v["final_score"], v["index"]))
    for rank, v in enumerate(verdicts, start=1):
        v["rank"] = rank

    rated = sum(1 for v in verdicts if v.get("rated"))
    log.log(f"\n  Council ranking ({rated}/{len(verdicts)} AI-rated):")
    for v in verdicts:
        log.log(f"    #{v['rank']}  clip {v['index']}  score={v['final_score']:3d}  "
                f"[{v['view_band']}]  {v['verdict'][:70]}")
    return verdicts
