# ShortsAI — product context

> Inferred from the owner's brief on 2026-08-14 rather than a live interview: the
> owner asked to "follow your instincts". Assumptions are labelled below.

## What it is

A local desktop tool (runs at `localhost:8000`, FastAPI + one HTML page) that turns
one long video into many vertical short clips for Instagram Reels and YouTube Shorts.
It downloads or accepts a video, transcribes it, uses AI to find the moments most
likely to get views, cuts them to 9:16, burns captions, and writes the titles,
captions and hashtags needed to post them.

## Who uses it

One person: the owner and their client, a Hindi/Hinglish short-form creator. Not a
team, no accounts, no multi-tenancy. They batch-process a video, judge the output,
and post the winners. **Assumption:** primarily Indian audience, Hindi and Hinglish
source video, evening working hours.

## The one job

Get from "here is a 40-minute video" to "here are 15 clips ranked by which will
perform, each with a title and caption ready to paste" without touching an editor.

Views are the only success metric the owner cares about. Every AI decision in the
pipeline is scored against predicted views, not accuracy or completeness.

## What must be true

- **Nothing may hard-fail.** Every AI stage degrades to a fallback: a rate-limited
  provider costs quality, never the job. The renderer has a four-rung fallback ladder.
- **It runs on a modest machine.** CPU encoding is the norm; GPU is a bonus.
- **Keys are the only setup.** Groq is required, everything else optional.
- **What you preview is what renders.** The caption preview loads the same TTF that
  ffmpeg burns in, so a style choice is never a surprise.

## Constraints that shape the UI

- Long jobs (minutes to an hour). Progress must be legible and honest.
- 15 caption styles and 20 fonts — a real inventory to navigate, not a token choice.
- Three overlays (caption, headline, logo) are positioned by dragging on a 9:16 stage.
- Sequential mode is a genuinely different job (split the whole video into parts)
  and must not be tangled with highlight mode's controls.
