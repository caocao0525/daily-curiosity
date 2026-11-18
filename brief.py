"""Generate a short, source-linked daily Korean briefing.

Inputs are abstracts / headlines, not full text: the LLM is told to avoid
unsupported claims and to distinguish preprints from peer-reviewed publications.
"""
import datetime as dt
import html
import json
import os
from pathlib import Path
import re
import sys
import time
import xml.etree.ElementTree as ET
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import feedparser
import requests

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
SITE = ROOT / "site"
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "DailyCuriosity/0.1 (personal academic briefing; contact: configure yourself)"})


def fetch(url, params=None):
    response = SESSION.get(url, params=params, timeout=35)
    response.raise_for_status()
    return response.text


def tidy(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()


def parsed_date(item):
    for key in ("published_parsed", "updated_parsed"):
        stamp = item.get(key)
        if stamp:
            return dt.datetime(*stamp[:6], tzinfo=dt.timezone.utc)
    return None


def arxiv(topic, cap):
    url = "https://export.arxiv.org/api/query"
    params = {"search_query": topic["query"], "start": 0, "max_results": cap, "sortBy": "submittedDate", "sortOrder": "descending"}
    feed = feedparser.parse(fetch(url, params))
    output = []
    for entry in feed.entries:
        link = entry.get("id", entry.get("link", ""))
        output.append({"title": tidy(entry.get("title")), "url": link.replace("http://", "https://"),
                       "summary": tidy(entry.get("summary"))[:2500], "published": iso_date(parsed_date(entry)),
                       "source": "arXiv preprint", "topic": topic["name"]})
    return output


def pubmed(topic, cap):
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
    params = {"db": "pubmed", "term": topic["query"], "retmax": cap, "sort": "pub date", "retmode": "json", "tool": "daily_curiosity"}
    ids = SESSION.get(base + "esearch.fcgi", params=params, timeout=35)
    ids.raise_for_status()
    pmids = ids.json().get("esearchresult", {}).get("idlist", [])
    if not pmids:
        return []
    # ESummary gives citation metadata but not necessarily abstracts.
    # Never pretend this is a full paper summary.
    params = {"db": "pubmed", "id": ",".join(pmids), "retmode": "json", "tool": "daily_curiosity"}
    response = SESSION.get(base + "esummary.fcgi", params=params, timeout=35)
    response.raise_for_status()
    entries = response.json().get("result", {})
    abstracts = {}
    online_dates = {}
    try:
        xml_response = SESSION.get(base + "efetch.fcgi", params={"db": "pubmed", "id": ",".join(pmids), "retmode": "xml", "tool": "daily_curiosity"}, timeout=35)
        xml_response.raise_for_status()
        root = ET.fromstring(xml_response.content)
        for article in root.findall(".//PubmedArticle"):
            node = article.find(".//MedlineCitation/PMID")
            if node is None or not node.text:
                continue
            sections = []
            for elem in article.findall(".//Article/Abstract/AbstractText"):
                prefix = elem.attrib.get("Label", "")
                segment = "".join(elem.itertext()).strip()
                if segment:
                    sections.append((prefix + ": " if prefix else "") + segment)
            abstracts[node.text] = " ".join(sections)[:3000]
            # An online release can precede the journal issue/eCollection date.
            dates = [n for n in article.findall(".//Article/ArticleDate") if n.attrib.get("DateType", "").lower() == "electronic"]
            dates += [n for n in article.findall(".//PubmedData/History/PubMedPubDate") if n.attrib.get("PubStatus", "").lower() in ("epublish", "aheadofprint")]
            for date_node in dates:
                y, m, d = (date_node.findtext(k, "") for k in ("Year", "Month", "Day"))
                try:
                    online_dates[node.text] = dt.date(int(y), int(m), int(d)).isoformat()
                    break
                except (ValueError, TypeError):
                    pass
    except (requests.RequestException, ET.ParseError) as exc:
        print(f"WARNING: PubMed abstracts unavailable: {exc}", file=sys.stderr)
    output = []
    for pmid in pmids:
        record = entries.get(pmid, {})
        if not record:
            continue
        journal = record.get("fulljournalname", "PubMed")
        output.append({"title": tidy(record.get("title", "")), "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                       "summary": (f"Journal: {journal}. Abstract: {abstracts[pmid]}" if abstracts.get(pmid) else f"Journal: {journal}. Title/metadata only; abstract unavailable."),
                       "published": online_dates.get(pmid, record.get("pubdate", "")), "source": "PubMed citation", "topic": topic["name"]})
    return output


def google_news(topic, cap):
    url = "https://news.google.com/rss/search?" + urlencode({"q": topic["query"], "hl": "en-US", "gl": "US", "ceid": "US:en"})
    feed = feedparser.parse(fetch(url))
    return [{"title": tidy(e.get("title")), "url": e.get("link", ""),
             "summary": tidy(e.get("summary"))[:1200], "published": iso_date(parsed_date(e)),
             "source": "News headline / RSS snippet", "topic": topic["name"]} for e in feed.entries[:cap]]


def iso_date(value):
    return value.date().isoformat() if value else ""


def age_ok(value, days):
    try:
        published = dt.date.fromisoformat(value[:10])
        today = dt.datetime.now(ZoneInfo(CONFIG["timezone"])).date()
        return today - dt.timedelta(days=days) <= published <= today
    except (ValueError, TypeError):
        # Unknown publication date is not evidence of recency.
        return False


def collect():
    out = []
    for group, topics in (("research", CONFIG["research_topics"]), ("general", CONFIG["general_topics"])):
        for topic in topics:
            try:
                cap = CONFIG["max_candidates_per_source"]
                items = {"arxiv": arxiv, "pubmed": pubmed, "google_news_rss": google_news}[topic["source"]](topic, cap)
                for item in items:
                    if item["title"] and item["url"] and age_ok(item["published"], CONFIG["max_days_old"]):
                        item["group"] = group
                        out.append(item)
                print(f"Fetched {len(items)} from {topic['name']}")
            except (requests.RequestException, ValueError, KeyError) as exc:
                print(f"WARNING: failed {topic['name']}: {exc}", file=sys.stderr)
            time.sleep(3 if topic["source"] == "arxiv" else 0.4)
    unique = {}
    for item in out:
        unique.setdefault(item["url"], item)
    return list(unique.values())


def choose_and_summarize(candidates, seen):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Set OPENAI_API_KEY in your environment / GitHub repository secrets.")
    available = [item for item in candidates if item["url"] not in seen]
    if not available:
        return []
    # Minimize prompt size and prevent unrelated data from entering the prompt.
    payload = [{"id": i, **{k: v for k, v in x.items() if k in ("title", "summary", "published", "source", "topic", "group")}}
               for i, x in enumerate(available)]
    system = """You are a rigorous scientific editor preparing an English-first daily reading digest for a computational genomics researcher interested in neuroscience, evolutionary psychology, physics, mathematics, and fine art. Select up to 3 directly relevant research stories and 3 genuinely interesting general stories, never padding with unrelated material. For research, demand substantial relevance to computational genomics, epigenomics, chromatin, regulatory elements, biological foundation models, or broadly transferable ML methods. Reject unrelated clinical imaging, medical diagnoses, nanomaterials and generic biomedical items even if the search engine returned them. Aim for subject diversity among general stories. Use ONLY supplied titles, abstracts, RSS snippets and metadata, never invent methods or results. Label preprints and uncertainties, especially in psychology and news. Remember publication online and the future journal issue/eCollection date can differ. Write clear concise English primarily and a short Korean supplementary takeaway.
Return JSON object with key items, an array of {id, headline, summary, why, korean, evidence_note}. Headline: concise descriptive English. Summary: 2-3 short specific English sentences explaining problem, approach, findings where source allows. Why: one English sentence on its actual significance, no empty hype. Korean: one short Korean sentence summarizing the idea, NOT a lengthy translation. Evidence_note: short English caution if warranted, else empty. No links in JSON."""
    user = {"target_total": CONFIG["max_items"], "research_target": CONFIG["research_target"], "general_target": CONFIG["general_target"], "candidates": payload}
    request = {"model": CONFIG["model"], "temperature": 0.2,
               "response_format": {"type": "json_object"},
               "messages": [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(user, ensure_ascii=False)}]}
    response = SESSION.post("https://api.openai.com/v1/chat/completions", json=request,
                            headers={"Authorization": f"Bearer {api_key}"}, timeout=120)
    if not response.ok:
        raise RuntimeError(f"LLM API returned HTTP {response.status_code}: {response.text[:400]}")
    result = json.loads(response.json()["choices"][0]["message"]["content"])
    selected = []
    chosen = set()
    group_counts = {"research": 0, "general": 0}
    group_targets = {"research": CONFIG["research_target"], "general": CONFIG["general_target"]}
    for row in result.get("items", []):
        try:
            idx = int(row["id"])
        except (KeyError, ValueError, TypeError):
            continue
        if idx not in range(len(available)) or idx in chosen:
            continue
        original = available[idx]
        group = original["group"]
        if group_counts[group] >= group_targets[group]:
            continue
        chosen.add(idx)
        group_counts[group] += 1
        selected.append({**original, "headline": str(row.get("headline", original["title"]))[:180],
                         "brief": str(row.get("summary", ""))[:1000], "why": str(row.get("why", ""))[:500],
                         "korean": str(row.get("korean", ""))[:350],
                         "evidence_note": str(row.get("evidence_note", ""))[:120]})
        if len(selected) >= CONFIG["max_items"]:
            break
    return selected


def category_icon(topic):
    """A compact visual cue, with a fallback for future categories."""
    name = (topic or "").lower()
    if any(word in name for word in ("genom", "epigenom", "chromatin", "foundation model")):
        return "🧬"
    if "neuroscien" in name or "psycholog" in name or "cognit" in name:
        return "🧠"
    if "art" in name or "museum" in name:
        return "🎨"
    if "physic" in name or "quantum" in name:
        return "⚛️"
    if "math" in name:
        return "∑"
    if "ai / ml" in name or "machine learning" in name:
        return "🤖"
    return "📚"


STYLE = """
:root{color-scheme:light dark}
*{box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"Noto Sans KR",sans-serif;max-width:800px;margin:0 auto;padding:30px 18px 80px;line-height:1.65;background:#fafaf8;color:#242726}
a{color:#23665c}header{padding-bottom:18px;border-bottom:1px solid #d8dedc}
h1{font-size:2.1rem;margin:0}header p,.meta,.caution,.muted{color:#68736e}
.subtitle{margin:2px 0 12px}
nav{display:flex;gap:10px;margin:18px 0 4px}
nav a{text-decoration:none;border:1px solid #cbd7d0;padding:6px 17px;border-radius:999px;font-size:.9rem}
nav a.active{background:#23665c;color:#fff;border-color:#23665c}
.card{background:white;border:1px solid #e4e8e5;border-radius:14px;padding:21px;margin:17px 0}
.card h2{font-size:1.14rem;line-height:1.4;margin:9px 0}
.meta{display:flex;gap:12px;justify-content:space-between;font-size:.78rem;flex-wrap:wrap}
.why{border-left:3px solid #84a69b;padding-left:12px}
.ko{font-size:.9rem;color:#68736e;background:#f2f5f3;border-radius:8px;padding:10px 13px;margin:10px 0}
.caution{font-size:.78rem}
footer{font-size:.8rem;color:#68736e;margin-top:40px}
.filters{display:flex;gap:10px;flex-wrap:wrap;margin:22px 0}
.filters input{font:inherit;color:inherit;background:transparent;border:1px solid #a9b6ae;border-radius:8px;padding:10px;min-width:0}
.filters input[type=search]{flex:2 1 230px}.filters input[type=date]{flex:1 1 170px}.filters button{font:inherit;border:1px solid #a9b6ae;color:inherit;background:transparent;border-radius:8px;padding:9px 12px;cursor:pointer}.filters button:hover{border-color:#23665c}
.date-heading{margin:28px 0 8px;font-size:1rem}
.archive-count{font-size:.85rem;color:#68736e}
@media(prefers-color-scheme:dark){
body{background:#151a18;color:#e8efec}
.card{background:#202724;border-color:#38473f}
.ko{background:#26312b;color:#c6d7cd}
header{border-color:#38473f}a{color:#9bd8c0}
nav a{border-color:#52685c}
nav a.active{background:#316e5e;border-color:#316e5e}
.filters input{border-color:#52685c}
}
"""


def esc(value):
    return html.escape(str(value or ""), quote=True)


def story_card(item, issue_date=None):
    topic = item.get("topic", "Other")
    source = item.get("source", "")
    published = item.get("published", "")
    note = item.get("evidence_note", "")
    korean = item.get("korean", "")
    date_attr = f' data-date="{esc(issue_date)}"' if issue_date else ""
    search_text = " ".join(str(item.get(k, "")) for k in (
        "headline", "brief", "why", "korean", "topic", "source"))
    search_attr = f' data-search="{esc(search_text.lower())}"' if issue_date else ""
    heading = esc(item.get("headline", item.get("title", "")))
    return (
        f'<article class="card"{date_attr}{search_attr}>'
        f'<div class="meta"><span>{category_icon(topic)} {esc(topic)}</span>'
        f'<span>{esc(source)} · {esc(published)}</span></div>'
        f'<h2>{heading}</h2>'
        + (f'<p class="ko" lang="ko">{esc(korean)}</p>' if korean else "")
        + f'<p>{esc(item.get("brief", ""))}</p>'
        + f'<p class="why"><b>Why it matters:</b> {esc(item.get("why", ""))}</p>'
        + (f'<p class="caution">{esc(note)}</p>' if note else "")
        + f'<a href="{esc(item.get("url", ""))}" target="_blank" rel="noopener noreferrer">Read original ↗</a>'
        + '</article>'
    )


def page_shell(title, subheading, body, active="today", archived=False):
    prefix = "../" if archived else ""
    nav = (
        '<nav aria-label="Site navigation">'
        + f'<a href="{prefix}index.html" class="{"active" if active == "today" else ""}">Today</a>'
        + f'<a href="{prefix}archive.html" class="{"active" if active == "archive" else ""}">Archive</a>'
        + '</nav>'
    )
    # Static pages: no server, database, or JavaScript framework required.
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        + f'<title>{esc(title)}</title><style>{STYLE}</style></head><body>'
        + f'<header><h1>☀️ {esc(CONFIG["title"])}</h1>'
        + f'<p class="subtitle">{esc(subheading)}</p>{nav}</header>'
        + f'<main>{body}</main>'
        + '<footer>Automatically generated from source abstracts, metadata and RSS snippets. '
        'Please verify important findings in their original sources.</footer>'
        '</body></html>'
    )


def render(issue, archived=False):
    cards = "\n".join(story_card(item) for item in issue["items"])
    if not cards:
        cards = '<p>No suitable stories today. Sources may be temporarily unavailable.</p>'
    intro = f'<p class="muted">Research × Curiosity · {len(issue["items"])} stories</p>'
    return page_shell(
        f'{CONFIG["title"]} · {issue["date"]}',
        f'Your 10-minute knowledge briefing · {issue["date"]}',
        intro + cards,
        archived=archived,
    )


def render_archive(issues):
    cards = []
    total = 0
    for issue in sorted(issues, key=lambda x: x["date"], reverse=True):
        date = issue["date"]
        if not issue.get("items"):
            continue
        cards.append(f'<section class="issue" data-date="{esc(date)}">'
                     f'<h2 class="date-heading"><a href="issues/{esc(date)}.html">{esc(date)}</a></h2>')
        for item in issue["items"]:
            cards.append(story_card(item, date))
            total += 1
        cards.append('</section>')
    body = (
        '<p class="muted">Browse previous editions, newest first.</p>'
        '<div class="filters">'
        '<input id="search" type="search" placeholder="Search keywords or topics…" aria-label="Search archive">'
        '<input id="date" type="date" aria-label="Filter by date">'
        '<button id="clear-date" type="button">Clear date</button>'
        '</div>'
        f'<p id="count" class="archive-count">{total} stories archived</p>'
        + "\n".join(cards)
        + '<p id="empty" hidden>No matching stories. Try another keyword or date.</p>'
        + """<script>
(function(){
 const search=document.getElementById('search'), date=document.getElementById('date');
 const count=document.getElementById('count'), empty=document.getElementById('empty');
 const sections=Array.from(document.querySelectorAll('.issue'));
 function update(){
   const q=search.value.trim().toLocaleLowerCase(), d=date.value;
   let n=0;
   sections.forEach(section=>{
     let matches=0;
     section.querySelectorAll('.card').forEach(card=>{
       const hit=(!q || card.dataset.search.includes(q)) &&
                 (!d || card.dataset.date===d);
       card.hidden=!hit;
       if(hit){matches++;n++;}
     });
     section.hidden=matches===0;
   });
   count.textContent=n+' matching stories';
   empty.hidden=n!==0;
 }
 search.addEventListener('input',update);
 date.addEventListener('input',update);
 document.getElementById('clear-date').addEventListener('click',()=>{date.value='';update();});
 update();
})();
</script>"""
    )
    return page_shell(
        f'Archive · {CONFIG["title"]}',
        'Past briefings · searchable by keyword and date',
        body, active="archive",
    )


def main():
    DATA.mkdir(exist_ok=True)
    SITE.mkdir(exist_ok=True)
    issue_dir = DATA / "issues"
    site_issue_dir = SITE / "issues"
    issue_dir.mkdir(exist_ok=True)
    site_issue_dir.mkdir(exist_ok=True)

    now = dt.datetime.now(ZoneInfo(CONFIG["timezone"]))
    today = now.date().isoformat()
    seen_file = DATA / "seen.json"
    seen = json.loads(seen_file.read_text(encoding="utf-8")) if seen_file.exists() else {}
    # Keep the deduplication window bounded; the historical issues remain preserved.
    seen = {url: day for url, day in seen.items()
            if day >= (now.date() - dt.timedelta(days=60)).isoformat()}

    # The first successful briefing of each date is the permanent edition.
    # Subsequent manual reruns rebuild pages without collecting or calling the API.
    daily_file = issue_dir / f"{today}.json"
    if daily_file.exists():
        issue = json.loads(daily_file.read_text(encoding="utf-8"))
        if issue.get("date") != today or not isinstance(issue.get("items"), list):
            raise ValueError(f"Invalid saved daily briefing: {daily_file}")
        selected = issue["items"]
        print(f"Reusing first briefing for {today}; no new API request.")
    else:
        candidates = collect()
        print(f"Total unique candidates: {len(candidates)}")
        selected = choose_and_summarize(candidates, seen)
        issue = {"date": today, "items": selected}
        daily_file.write_text(
            json.dumps(issue, ensure_ascii=False, indent=2), encoding="utf-8")
        for item in selected:
            seen[item["url"]] = today
        seen_file.write_text(
            json.dumps(seen, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Created first briefing for {today}.")

    (DATA / "latest.json").write_text(
        json.dumps(issue, ensure_ascii=False, indent=2), encoding="utf-8")

    historical = []
    for path in sorted(issue_dir.glob("????-??-??.json"), reverse=True):
        try:
            historical.append(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"WARNING: skipping corrupt archive {path.name}: {exc}", file=sys.stderr)

    (SITE / "index.html").write_text(render(issue), encoding="utf-8")
    (SITE / "archive.html").write_text(render_archive(historical), encoding="utf-8")
    for old_issue in historical:
        (site_issue_dir / f'{old_issue["date"]}.html').write_text(
            render(old_issue, archived=True), encoding="utf-8")

    print(f"Published {len(selected)} cards on {today}; {len(historical)} archived editions")


if __name__ == "__main__":
    main()
