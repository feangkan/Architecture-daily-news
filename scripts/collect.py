#!/usr/bin/env python3
"""Daily architecture news collector.

1. Fetch RSS/Atom feeds listed in config/sources.json
2. Drop items already seen, score the rest against config/profile.json
3. Store every new item in data/items/YYYY-MM.jsonl, with its score and tags
4. Optionally ask Claude (ANTHROPIC_API_KEY) for summaries, suggestions and evaluation
5. Write digests/YYYY/YYYY-MM-DD.md + digests/latest.md + out/notify.txt
6. Optionally push a phone notification (ntfy / Telegram)

Python standard library only, except the optional `anthropic` package.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import email.utils
import hashlib
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"
DATA = ROOT / "data"
DIGESTS = ROOT / "digests"
OUT = ROOT / "out"
UA = "Mozilla/5.0 (compatible; ArchitectureDailyNews/1.0; +https://github.com/feangkan/Architecture-daily-news)"

ATOM = "{http://www.w3.org/2005/Atom}"
MEDIA = "{http://search.yahoo.com/mrss/}"
DC = "{http://purl.org/dc/elements/1.1/}"


# ---------------------------------------------------------------- fetching
def fetch(url: str, timeout: int = 25) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def clean_text(s: str | None, limit: int = 500) -> str:
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:limit] + ("…" if len(s) > limit else "")


def parse_date(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    s = s.strip()
    try:
        d = email.utils.parsedate_to_datetime(s)
    except (TypeError, ValueError):
        d = None
    if d is None:
        try:
            d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.astimezone(dt.timezone.utc)


def parse_feed(raw: bytes, source: dict) -> list[dict]:
    root = ET.fromstring(raw)
    items: list[dict] = []
    # RSS 2.0
    for it in root.iter("item"):
        link = (it.findtext("link") or it.findtext("guid") or "").strip()
        items.append({
            "title": clean_text(it.findtext("title"), 300),
            "link": link,
            "summary": clean_text(it.findtext("description") or it.findtext("{http://purl.org/rss/1.0/modules/content/}encoded")),
            "published": parse_date(it.findtext("pubDate") or it.findtext(f"{DC}date")),
            "publisher": clean_text(it.findtext("source"), 80),
        })
    # Atom (incl. arXiv)
    for en in root.iter(f"{ATOM}entry"):
        link = ""
        for l in en.findall(f"{ATOM}link"):
            if l.get("rel", "alternate") == "alternate":
                link = l.get("href", "")
                break
        if not link and en.find(f"{ATOM}link") is not None:
            link = en.find(f"{ATOM}link").get("href", "")
        items.append({
            "title": clean_text(en.findtext(f"{ATOM}title"), 300),
            "link": link.strip(),
            "summary": clean_text(en.findtext(f"{ATOM}summary") or en.findtext(f"{ATOM}content")),
            "published": parse_date(en.findtext(f"{ATOM}published") or en.findtext(f"{ATOM}updated")),
            "publisher": "",
        })
    for i in items:
        i["source"] = source["name"]
        i["source_type"] = source.get("type", "general")
    return [i for i in items if i["title"] and i["link"]]


def fetch_all(sources: list[dict]) -> tuple[list[dict], list[str]]:
    errors: list[str] = []

    def one(src: dict) -> list[dict]:
        try:
            return parse_feed(fetch(src["url"]), src)
        except Exception as e:  # noqa: BLE001 - one bad feed must not stop the run
            errors.append(f"{src['name']}: {type(e).__name__}: {str(e)[:120]}")
            return []

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(one, sources))
    return [i for batch in results for i in batch], errors


# ---------------------------------------------------------------- scoring
def item_id(item: dict) -> str:
    key = re.sub(r"[?#].*$", "", item["link"]) if "news.google.com" not in item["link"] else item["title"].lower()
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def norm_title(t: str) -> str:
    t = re.sub(r"\s[-–|]\s[^-–|]{2,40}$", "", t)  # drop " - Publisher" suffix (Google News)
    return re.sub(r"[^a-z0-9 ]", "", t.lower())[:90]


def score_item(item: dict, profile: dict) -> None:
    title = " " + item["title"].lower() + " "
    body = " " + item["summary"].lower() + " "
    tags: dict[str, float] = {}
    for key, topic in profile["topics"].items():
        hits = 0.0
        for kw in topic["keywords"]:
            k = kw.lower()
            pat = r"(?<![a-z])" + re.escape(k.strip()) + r"(?![a-z])" if k.strip() == k else re.escape(k)
            if re.search(pat, title):
                hits += 2
            elif re.search(pat, body):
                hits += 1
        if item["source_type"] == key:
            hits += 1.5
        if hits:
            tags[key] = min(hits, 6) * topic["weight"]
    # jobs: boost if a preferred location or role appears
    text = title + body
    if "jobs" in tags or item["source_type"] == "jobs":
        if any(loc.lower() in text for loc in profile.get("job_locations", [])):
            tags["jobs"] = tags.get("jobs", 0) + 6
        if any(r.lower() in text for r in profile.get("job_roles", [])):
            tags["jobs"] = tags.get("jobs", 0) + 3
    penalty = 10 if any(n in text for n in profile.get("negative_keywords", [])) else 0
    # reward items that touch several of the core topics (cross-over = more relevant to the profile)
    core = [k for k in tags if profile["topics"][k]["weight"] >= 3 and k != "jobs"]
    bonus = 4 * max(0, len(core) - 1)
    item["tags"] = sorted(tags, key=tags.get, reverse=True)
    item["score"] = round(sum(tags.values()) + bonus - penalty, 1)
    item["importance"] = "high" if item["score"] >= 24 else "medium" if item["score"] >= 12 else "low"


SECTIONS = [
    ("jobs", "💼 Hiring & careers"),
    ("agentic", "🤖 Agentic / AI architecture"),
    ("computational", "🧮 Computational design"),
    ("fabrication", "🦾 Digital fabrication & robotics"),
    ("sustainable", "🌱 Sustainability & low carbon"),
    ("ecology", "🌿 Ecology & landscape"),
    ("resilience", "🌊 Climate resilience"),
    ("retrofit", "🏚️ Retrofit & adaptive reuse"),
    ("research", "🔬 New research"),
    ("tools", "🛠️ Tools for architects"),
    ("trends", "🌏 Global trends"),
    ("general", "📰 General update"),
]


def section_of(item: dict) -> str:
    return item["tags"][0] if item["tags"] else "general"


# ---------------------------------------------------------------- storage
def load_seen() -> dict:
    p = DATA / "seen.json"
    return json.loads(p.read_text()) if p.exists() else {}


def save_seen(seen: dict, today: dt.date) -> None:
    cutoff = (today - dt.timedelta(days=90)).isoformat()
    seen = {k: v for k, v in seen.items() if v >= cutoff}
    (DATA / "seen.json").write_text(json.dumps(seen, sort_keys=True, indent=0))


def store_items(items: list[dict], today: dt.date) -> None:
    d = DATA / "items"
    d.mkdir(parents=True, exist_ok=True)
    with (d / f"{today:%Y-%m}.jsonl").open("a", encoding="utf-8") as f:
        for i in items:
            row = {k: (v.isoformat() if isinstance(v, dt.datetime) else v) for k, v in i.items()}
            row["collected"] = today.isoformat()
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def update_topic_stats(items: list[dict], today: dt.date, profile: dict) -> dict[str, str]:
    """Append today's per-topic counts and return a 7-day vs previous-7-day trend arrow per topic."""
    p = DATA / "topic_counts.csv"
    topics = list(profile["topics"])
    rows = []
    if p.exists():
        with p.open() as f:
            rows = [r for r in csv.DictReader(f) if r["date"] != today.isoformat()]
    counts = {t: sum(1 for i in items if t in i["tags"]) for t in topics}
    rows.append({"date": today.isoformat(), **{t: str(counts[t]) for t in topics}})
    with p.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", *topics])
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "0") for k in ["date", *topics]})
    last7 = rows[-7:]
    prev7 = rows[-14:-7]
    trend = {}
    for t in topics:
        a = sum(int(r.get(t, 0) or 0) for r in last7)
        b = sum(int(r.get(t, 0) or 0) for r in prev7)
        if len(prev7) < 3:
            trend[t] = f"{a}"
        else:
            arrow = "↑" if a > b * 1.25 else "↓" if a < b * 0.8 else "→"
            trend[t] = f"{a} {arrow} (prev {b})"
    return trend


# ---------------------------------------------------------------- AI layer
AI_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "overview": {"type": "string"},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "summary": {"type": "string"},
                    "why_it_matters": {"type": "string"},
                    "importance": {"type": "string", "enum": ["high", "medium", "low"]},
                },
                "required": ["id", "summary", "why_it_matters", "importance"],
                "additionalProperties": False,
            },
        },
        "suggestions": {"type": "array", "items": {"type": "string"}},
        "evaluation": {"type": "array", "items": {"type": "string"}},
        "skills_to_watch": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["headline", "overview", "items", "suggestions", "evaluation", "skills_to_watch"],
    "additionalProperties": False,
}

AI_SYSTEM = """You are the morning news editor for an early-career architect (Master of Architecture level) whose interests are:
computational design, agentic / AI-driven architecture, digital fabrication and robotics, sustainability and low carbon,
ecology, climate resilience, retrofit and adaptive reuse, new research, and tools for architects. They are also watching
the hiring market (preferred locations: {locations}).

You receive today's candidate news items as JSON. Work only from what the items say; never invent facts, numbers,
firms or job openings. If an item's text is too thin to judge, say so briefly.

Return:
- headline: the single most important development today, one sentence.
- overview: 3-4 sentences on what today's news says overall.
- items: for the 12-18 most relevant items only, keyed by their id: a 1-2 sentence plain summary, one sentence on why it
  matters for this person specifically (career, skills, portfolio, research), and your importance rating.
- suggestions: 3-5 concrete actions for this week (e.g. a skill to try, a firm to look at, a paper to read, a portfolio
  angle), each tied to an item.
- evaluation: 3-5 critical notes: source credibility, hype vs substance, what is missing or one-sided, signal strength.
- skills_to_watch: 2-4 tools or skills appearing in today's news that are worth learning.
Use British/Australian spelling. Be concise and specific."""


def ai_digest(items: list[dict], profile: dict) -> dict | None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
    except ImportError:
        print("anthropic package not installed; skipping AI layer", file=sys.stderr)
        return None
    payload = [
        {"id": i["id"], "title": i["title"], "source": i["source"], "tags": i["tags"], "score": i["score"], "text": i["summary"][:400]}
        for i in items[: profile.get("max_items_for_ai", 60)]
    ]
    model = os.environ.get("CLAUDE_MODEL", "claude-opus-5-5")
    client = anthropic.Anthropic()
    kwargs = dict(
        model=model,
        max_tokens=16000,
        system=AI_SYSTEM.format(locations=", ".join(profile.get("job_locations", []))),
        messages=[{"role": "user", "content": "Today's items:\n" + json.dumps(payload, ensure_ascii=False)}],
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": AI_SCHEMA}},
    )
    try:
        try:
            # Server-side fallback: if the main model declines, the API retries on a suitable fallback model.
            resp = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
        except anthropic.BadRequestError:
            resp = client.messages.create(**kwargs)
        if resp.stop_reason == "refusal":
            print("AI layer: model declined the request", file=sys.stderr)
            return None
        text = next(b.text for b in resp.content if b.type == "text")
        return json.loads(text)
    except anthropic.RateLimitError as e:
        print(f"AI layer rate limited: {e}", file=sys.stderr)
    except anthropic.APIStatusError as e:
        print(f"AI layer API error {e.status_code}: {e.message}", file=sys.stderr)
    except anthropic.APIConnectionError as e:
        print(f"AI layer connection error: {e}", file=sys.stderr)
    except (StopIteration, json.JSONDecodeError) as e:
        print(f"AI layer returned unexpected output: {e}", file=sys.stderr)
    return None


def rule_based_notes(items: list[dict], trend: dict[str, str], profile: dict) -> dict:
    """Fallback 'suggestion' and 'evaluation' when no API key is set."""
    top = items[:5]
    by_topic: dict[str, int] = {}
    for i in items:
        for t in i["tags"]:
            by_topic[t] = by_topic.get(t, 0) + 1
    busiest = sorted(by_topic, key=by_topic.get, reverse=True)[:3]
    labels = {k: v["label"] for k, v in profile["topics"].items()}
    sugg = [f"Read first: [{i['title']}]({i['link']}) ({i['source']})" for i in top[:3]]
    jobs = [i for i in items if "jobs" in i["tags"]]
    if jobs:
        sugg.append(f"Check the hiring section: {len(jobs)} item(s) mention jobs or careers.")
    ev = [
        "Busiest topics today: " + ", ".join(f"{labels.get(t, t)} ({by_topic[t]})" for t in busiest) if busiest else "Quiet news day.",
        "Ranking is keyword-based. Add an ANTHROPIC_API_KEY secret to get AI summaries and a critical evaluation.",
    ]
    return {"headline": top[0]["title"] if top else "No new items today", "overview": "", "items": [], "suggestions": sugg, "evaluation": ev, "skills_to_watch": []}


# ---------------------------------------------------------------- rendering
def render(items: list[dict], ai: dict, trend: dict[str, str], errors: list[str], today: dt.date, profile: dict, used_ai: bool, n_fetched: int) -> str:
    labels = {k: v["label"] for k, v in profile["topics"].items()}
    ai_items = {x["id"]: x for x in ai.get("items", [])}
    stars = {"high": "★★★", "medium": "★★☆", "low": "★☆☆"}
    L = [f"# Architecture Daily — {today:%A %d %B %Y}", ""]
    L += [f"**Headline:** {ai['headline']}", ""]
    if ai.get("overview"):
        L += [ai["overview"], ""]
    L += [f"_{len(items)} new relevant items from {n_fetched} fetched · ranked for: computational · agentic · fabrication · sustainability · ecology · resilience · retrofit · research · tools · jobs · {'AI-summarised' if used_ai else 'keyword-ranked'}_", ""]

    top = items[:5]
    if top:
        L += ["## ⭐ Top 5 today", ""]
        for n, i in enumerate(top, 1):
            imp = ai_items.get(i["id"], {}).get("importance", i["importance"])
            L.append(f"{n}. {stars[imp]} **[{i['title']}]({i['link']})** — {i['source']}")
            if i["id"] in ai_items:
                L.append(f"   - {ai_items[i['id']]['summary']}")
                L.append(f"   - _Why it matters:_ {ai_items[i['id']]['why_it_matters']}")
        L.append("")

    if ai.get("suggestions"):
        L += ["## 💡 Suggestions", ""] + [f"- {s}" for s in ai["suggestions"]] + [""]
    if ai.get("evaluation"):
        L += ["## 🧭 Evaluation", ""] + [f"- {s}" for s in ai["evaluation"]] + [""]
    if ai.get("skills_to_watch"):
        L += ["## 🎯 Skills / tools to watch", ""] + [f"- {s}" for s in ai["skills_to_watch"]] + [""]

    rest = items[5:]
    for key, title in SECTIONS:
        sec = [i for i in rest if section_of(i) == key]
        if not sec:
            continue
        L += [f"## {title}", ""]
        for i in sec:
            imp = ai_items.get(i["id"], {}).get("importance", i["importance"])
            tags = ", ".join(labels.get(t, t) for t in i["tags"][:3])
            L.append(f"- {stars[imp]} [{i['title']}]({i['link']}) — {i['source']}  ")
            if i["id"] in ai_items:
                L.append(f"  {ai_items[i['id']]['summary']} _{ai_items[i['id']]['why_it_matters']}_  ")
            elif i["summary"]:
                L.append(f"  {i['summary'][:220]}  ")
            L.append(f"  <sub>{tags} · score {i['score']}</sub>")
        L.append("")

    L += ["## 📈 Topic trend (items in last 7 days)", "", "| Topic | Count |", "|---|---|"]
    L += [f"| {labels[t]} | {trend[t]} |" for t in profile["topics"]]
    L.append("")
    if errors:
        L += ["<details><summary>Feed errors (" + str(len(errors)) + ")</summary>", ""] + [f"- {e}" for e in errors] + ["", "</details>", ""]
    L += ["---", "_Data: every item is stored with its score and tags in `data/items/`. Tune ranking in `config/profile.json`, sources in `config/sources.json`._"]
    return "\n".join(L)


# ---------------------------------------------------------------- notify
def notify(text: str, title: str, url: str) -> None:
    topic = os.environ.get("NTFY_TOPIC")
    if topic:
        try:
            req = urllib.request.Request(
                f"https://ntfy.sh/{urllib.parse.quote(topic)}",
                data=text.encode("utf-8"),
                headers={"Title": title.encode("ascii", "ignore").decode(), "Click": url, "Tags": "building_construction", "Markdown": "yes"},
            )
            urllib.request.urlopen(req, timeout=20).read()
            print("ntfy notification sent")
        except Exception as e:  # noqa: BLE001
            print(f"ntfy failed: {e}", file=sys.stderr)
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if tok and chat:
        try:
            body = urllib.parse.urlencode({"chat_id": chat, "text": f"{title}\n\n{text}\n\n{url}", "disable_web_page_preview": "true"}).encode()
            urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage", data=body, timeout=20).read()
            print("telegram notification sent")
        except Exception as e:  # noqa: BLE001
            print(f"telegram failed: {e}", file=sys.stderr)


# ---------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--feed-file", action="append", help="parse a local feed file instead of fetching (testing)")
    ap.add_argument("--no-notify", action="store_true")
    args = ap.parse_args()

    profile = json.loads((CONFIG / "profile.json").read_text())
    sources = json.loads((CONFIG / "sources.json").read_text())["feeds"]
    tz = ZoneInfo(profile.get("timezone", "UTC"))
    now = dt.datetime.now(tz)
    today = now.date()
    for d in (DATA, DIGESTS, OUT):
        d.mkdir(parents=True, exist_ok=True)

    if args.feed_file:
        raw_items, errors = [], []
        for f in args.feed_file:
            raw_items += parse_feed(Path(f).read_bytes(), {"name": Path(f).stem, "type": "general"})
    else:
        raw_items, errors = fetch_all(sources)
    n_fetched = len(raw_items)

    seen = load_seen()
    cutoff = now.astimezone(dt.timezone.utc) - dt.timedelta(hours=profile.get("lookback_hours", 36))
    fresh, titles = [], set()
    for i in raw_items:
        i["id"] = item_id(i)
        nt = norm_title(i["title"])
        if i["id"] in seen or nt in titles:
            continue
        if i["published"] and i["published"] < cutoff and not args.feed_file:
            continue
        titles.add(nt)
        score_item(i, profile)
        fresh.append(i)
    fresh.sort(key=lambda x: x["score"], reverse=True)
    relevant = [i for i in fresh if i["score"] > 0][: profile.get("max_items_in_digest", 30)]

    for i in fresh:
        seen[i["id"]] = today.isoformat()
    store_items(fresh, today)
    save_seen(seen, today)
    trend = update_topic_stats(fresh, today, profile)

    ai = ai_digest(relevant, profile) if relevant else None
    used_ai = ai is not None
    notes = ai or rule_based_notes(relevant, trend, profile)
    md = render(relevant, notes, trend, errors, today, profile, used_ai, n_fetched)

    ydir = DIGESTS / f"{today:%Y}"
    ydir.mkdir(exist_ok=True)
    path = ydir / f"{today:%Y-%m-%d}.md"
    path.write_text(md, encoding="utf-8")
    (DIGESTS / "latest.md").write_text(md, encoding="utf-8")
    write_index()

    repo = os.environ.get("GITHUB_REPOSITORY", "feangkan/Architecture-daily-news")
    branch = os.environ.get("GITHUB_REF_NAME", "main")
    url = f"https://github.com/{repo}/blob/{branch}/digests/{today:%Y}/{path.name}"
    short = [f"⭐ {notes['headline']}", ""] + [f"{n}. {i['title']} ({i['source']})" for n, i in enumerate(relevant[:5], 1)]
    jobs = sum(1 for i in relevant if "jobs" in i["tags"])
    short += ["", f"{len(relevant)} items · {jobs} hiring/career"]
    (OUT / "notify.txt").write_text("\n".join(short), encoding="utf-8")
    (OUT / "issue_title.txt").write_text(f"Architecture Daily — {today:%a %d %b %Y}", encoding="utf-8")
    (OUT / "digest_path.txt").write_text(str(path.relative_to(ROOT)), encoding="utf-8")
    if not args.no_notify:
        notify("\n".join(short), f"Architecture Daily {today:%d %b}", url)
    print(f"{n_fetched} fetched, {len(fresh)} new, {len(relevant)} in digest, AI={'yes' if used_ai else 'no'}, feed errors={len(errors)}")
    print(f"wrote {path.relative_to(ROOT)}")
    return 0


def write_index() -> None:
    files = sorted(DIGESTS.glob("*/*.md"), reverse=True)
    lines = ["# Digest archive", "", "Newest first. [Latest](latest.md)", ""]
    lines += [f"- [{f.stem}]({f.parent.name}/{f.name})" for f in files[:120]]
    (DIGESTS / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
