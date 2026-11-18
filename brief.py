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


def render(issue):
    def esc(value):
        return html.escape(str(value or ""), quote=True)
    cards = []
    for item in issue["items"]:
        cards.append(f'''<article class="card"><div class="meta"><span>{esc(item['topic'])}</span><span>{esc(item['source'])} · {esc(item['published'])}</span></div>
<h2>{esc(item['headline'])}</h2><p>{esc(item['brief'])}</p><p class="why"><b>Why it matters</b> {esc(item['why'])}</p>
{f'<p class="ko"><b>한국어 요약</b> {esc(item.get("korean", ""))}</p>' if item.get("korean") else ""}
{f'<p class="caution">{esc(item["evidence_note"])}</p>' if item['evidence_note'] else ''}
<a href="{esc(item['url'])}" target="_blank" rel="noopener noreferrer">Read original ↗</a></article>''')
    content = '\n'.join(cards) if cards else '<p>오늘은 선정된 소식이 없어요. 새로운 자료가 없거나 출처 수집에 문제가 있었을 수 있습니다.</p>'
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(CONFIG['title'])} · {esc(issue['date'])}</title><style>
:root{{color-scheme:light dark}}body{{font-family:-apple-system,BlinkMacSystemFont,"Noto Sans KR",sans-serif;max-width:760px;margin:0 auto;padding:32px 18px 80px;line-height:1.65;background:#fafaf8;color:#242726}}
a{{color:#23665c}}header{{padding-bottom:18px;border-bottom:1px solid #d8dedc}}h1{{font-size:2.1rem;margin:0}}header p,.meta,.caution{{color:#68736e}}.subtitle{{margin:2px 0 12px}}.card{{background:white;border:1px solid #e4e8e5;border-radius:14px;padding:21px;margin:17px 0}}.card h2{{font-size:1.18rem;line-height:1.4;margin:11px 0}}.meta{{display:flex;gap:12px;justify-content:space-between;font-size:.78rem;flex-wrap:wrap}}.why{{border-left:3px solid #84a69b;padding-left:12px}}.ko{{font-size:.9rem;color:#68736e;background:#f2f5f3;border-radius:8px;padding:12px 14px}}.caution{{font-size:.78rem}}footer{{font-size:.8rem;color:#68736e;margin-top:40px}}@media(prefers-color-scheme:dark){{body{{background:#151a18;color:#e8efec}}.card{{background:#202724;border-color:#38473f}}.ko{{background:#26312b;color:#c6d7cd}}header{{border-color:#38473f}}a{{color:#9bd8c0}}}}
</style></head><body><header><h1>☀️ {esc(CONFIG['title'])}</h1><p class="subtitle">Your 10-minute knowledge briefing · {esc(issue['date'])}</p><p>Research × Curiosity · {len(issue['items'])} stories</p></header>
<main>{content}</main><footer>Summaries are generated from abstracts, metadata and RSS excerpts. Verify important claims in original sources.</footer></body></html>'''


def main():
    DATA.mkdir(exist_ok=True)
    SITE.mkdir(exist_ok=True)
    now = dt.datetime.now(ZoneInfo(CONFIG["timezone"]))
    today = now.date().isoformat()
    seen_file = DATA / "seen.json"
    seen = json.loads(seen_file.read_text(encoding="utf-8")) if seen_file.exists() else {}
    # Keep recent history only; storage remains bounded.
    seen = {url: day for url, day in seen.items() if day >= (now.date() - dt.timedelta(days=60)).isoformat()}
    candidate_items = collect()
    print(f"Total unique candidates: {len(candidate_items)}")
    selected = choose_and_summarize(candidate_items, seen)
    issue = {"date": today, "items": selected}
    (DATA / "latest.json").write_text(json.dumps(issue, ensure_ascii=False, indent=2), encoding="utf-8")
    (SITE / "index.html").write_text(render(issue), encoding="utf-8")
    for item in selected:
        seen[item["url"]] = today
    seen_file.write_text(json.dumps(seen, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Published {len(selected)} cards on {today}")


if __name__ == "__main__":
    main()
