# ShortsAI — visual world

Surface: `templates/index.html`. Mode: **Operate**. The tool disappears into the task.

## The decision that drives everything

**The interface is monochrome so the content is the only colour on screen.**

This surface has to show 15 caption styles — yellow, cyan, magenta, orange, mint,
hot pink — over live video, and the user's job is to *judge* those colours. Any
branded accent competes with the thing being judged and corrupts the decision. So
the chrome is neutral from black to bone, and the only saturated pixels are the
user's footage, their captions and their logo.

The one exception is semantic state (ready / working / failed), which must read
instantly and is never decorative.

## Tokens

Layered near-black, very slightly warm, so it sits calmly next to warm video:

| Role | Value |
|---|---|
| `--bg` page | `#0B0B0C` |
| `--surface` panel | `#131315` |
| `--surface-2` raised control | `#1B1B1E` |
| `--surface-3` hover | `#232327` |
| `--line` hairline | `#2A2A2E` |
| `--line-strong` | `#3A3A40` |
| `--text` | `#F4F3F0` |
| `--text-2` | `#A3A29E` |
| `--text-3` | `#6E6D6A` |
| `--ok` | `#4ADE80` |
| `--warn` | `#FBBF24` |
| `--err` | `#F87171` |

Selection is white, not a hue: a selected control gets `--text` border plus a 6%
white fill. The primary action is solid bone on black — the only filled button on
the page, so "Generate" is never ambiguous.

## Type

One family (system UI stack — permitted and correct for Operate; no display face in
a tool). Fixed rem scale, ratio ~1.15: `11 / 12 / 13 / 15 / 18 / 24 / 32px`.
Numerals are tabular everywhere a value changes (durations, counts, scores).

## Layout

Three zones on desktop: a **section rail** (jump between the five steps, each showing
its current setting), the **stage** (one section at a time — the "divide the sections"
requirement), and a **sticky 9:16 preview** that never scrolls away, because every
caption and overlay decision is judged against it.

Below 1100px the preview docks under the stage; below 720px the rail becomes a
horizontal scroller. Responsive behaviour is structural, never fluid type.

## Motion

150–200ms, ease-out, state only. One authored moment: the stage cross-fades and
lifts 4px when you change section. Everything else is a state transition. All of it
respects `prefers-reduced-motion`.

## Refused here

No cards-of-icon-plus-heading scaffolds, no eyebrows above headings, no gradient
text, no glass, no emoji icons (all icons are authored SVG on one 1.5px stroke), no
coloured left-borders, no progress rings standing in for content.
