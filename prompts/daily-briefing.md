# Daily briefing prompt (Claude Code scheduled task, ~09:00 Melbourne)

This is the instruction the Claude Code scheduled task runs every morning. Edit it here for reference;
the live copy is stored in the scheduled task (claude.ai/code → scheduled tasks).

You are producing today's Architecture Daily briefing for the owner of the repository
feangkan/Architecture-daily-news (an early-career architect, M.Arch level).

1. Get the repo: if /home/user/Architecture-daily-news (or a checkout of it) is not present, attach it with
   add_repo (owner feangkan, repo Architecture-daily-news, access push) and clone it.
   Check out branch `claude/architect-news-automation-ks6va8` (the default branch) and pull.
2. Read config/profile.json (interests, weights, job locations/roles) and digests/latest.md plus the newest
   data/items/*.jsonl rows (today's GitHub Actions feed collection, if present). Don't repeat items already
   in yesterday's digest.
3. Research with WebSearch (send searches in parallel, last 48 hours where possible):
   hiring/jobs for architects and computational designers in the profile's locations; agentic / AI architecture;
   computational design; digital fabrication & robotics; sustainability / mass timber / embodied carbon;
   ecology & landscape; climate resilience; retrofit & adaptive reuse; new research papers; tools & software
   releases; global trends, awards, competitions and events.
4. Write digests/YYYY/YYYY-MM-DD-claude.md (today's date, Melbourne time) in this structure:
   Headline · Top 5 (each: link, 1–2 sentence summary, "why it matters" for this person) · Suggestions
   (3–5 concrete actions this week) · Evaluation (credibility, hype vs substance, gaps, signal strength) ·
   Skills/tools to watch · sections per topic with ★★★/★★☆/★☆☆ importance and a reference link on every item.
   Never invent facts, firms, numbers or job openings; mark older items "(background)". British/Australian spelling.
5. Append one JSON line per item to data/briefings.jsonl:
   {"date","title","url","source","topics":[...],"importance","why"}.
6. Commit ("Claude briefing YYYY-MM-DD") and push to `claude/architect-news-automation-ks6va8`
   (retry network errors up to 4 times). If pushing is refused, say so; still finish step 7.
7. Final message (this becomes the phone notification): headline, the Top 5 as one line each with link,
   the single most useful suggestion, and the number of hiring items. Keep it under 150 words.
