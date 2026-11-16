# Architecture Daily News

A daily, automated news briefing for an architect working in **computational design, agentic / AI architecture,
digital fabrication, sustainability, ecology, resilience and retrofit**, plus **hiring**, **new research**,
**tools** and **global trends**.

It runs on **GitHub Actions** every morning (≈ 06:30 Melbourne), so no laptop needs to be on.

**→ [Today's digest](digests/latest.md) · [Archive](digests/README.md)**

## What it does each day
1. Fetches ~27 RSS/Atom feeds: ArchDaily, Dezeen, designboom, Archinect, ArchitectureAU, Architect's Newspaper,
   Parametric Architecture, AEC Magazine, Rhino news, MIT, *Automation in Construction*, *Building and Environment*,
   arXiv, Dezeen Jobs, and targeted Google News searches for each topic.
2. Drops duplicates and anything seen before, then **scores** each item against `config/profile.json`
   (topic keywords × weights, a bonus for preferred job locations and roles, a bonus for crossover between core topics).
3. **Stores every item** with its score, tags and importance in `data/items/YYYY-MM.jsonl`,
   and daily topic counts in `data/topic_counts.csv`, which gives the 7-day trend arrows.
4. Writes `digests/YYYY/YYYY-MM-DD.md`: Top 5 · **Suggestions** · **Evaluation** · Skills to watch · sections per topic,
   each item with its **reference link**.
   With an `ANTHROPIC_API_KEY` secret, Claude writes the summaries, "why it matters", suggestions and the critical evaluation.
   Without one, ranking and notes are keyword-based.
5. **Phone delivery:** posts the digest as a GitHub Issue (push notification in the GitHub mobile app),
   plus optional ntfy and Telegram pushes.

## Set-up on your phone (5 minutes)
1. **GitHub mobile app** (iOS/Android): sign in and make sure you are *watching* this repo
   (Watch → All activity). Every morning's digest arrives as an issue notification and opens as formatted markdown.
2. **Optional: instant push with ntfy (free, no account):** install the *ntfy* app, subscribe to a long random topic
   name (e.g. `arch-daily-k7q93x`), then in GitHub → Settings → Secrets and variables → Actions → *New repository secret*:
   `NTFY_TOPIC` = that name. Tapping the push opens the full digest.
3. **Optional: AI summaries:** add the secret `ANTHROPIC_API_KEY` (from console.anthropic.com).
   The default model is `claude-haiku-5-5` (cheapest, low effort); to change it, set an Actions *variable* `CLAUDE_MODEL`.
4. **Optional: Telegram:** secrets `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`.

Run it any time: **Actions → Architecture daily digest → Run workflow** (works from the GitHub mobile app too).

> Scheduled workflows only run from the repository's **default branch**. If the default branch is not the one
> holding `.github/workflows/daily-digest.yml`, merge it there (or switch the default in Settings → Branches).

## Tuning
| Want to… | Edit |
|---|---|
| Rank a topic higher or lower, add keywords | `config/profile.json` → `topics.<id>.weight / keywords` |
| Change job locations or roles | `config/profile.json` → `job_locations`, `job_roles` |
| Add or remove news sources | `config/sources.json` (any RSS/Atom URL; Google News search URLs work for any query) |
| Change the time | `.github/workflows/daily-digest.yml` → `cron` (UTC) |

## Run locally
```bash
python scripts/collect.py --no-notify
```
Optional: `pip install anthropic` and set `ANTHROPIC_API_KEY` for the AI layer.

## Layout
```
config/      profile.json (interests, weights) · sources.json (feeds)
scripts/     collect.py: fetch → score → store → summarise → render → notify
data/        items/YYYY-MM.jsonl (all scored items) · topic_counts.csv · seen.json
digests/     YYYY/YYYY-MM-DD.md · latest.md · README.md (archive index)
.github/     workflows/daily-digest.yml
```
