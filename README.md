# ☀️ The Daily Curiosity — 10-minute personalized reading digest

Personal academic + curiosity briefing, written in Korean and generated daily.

## Topics & output

- ~3 research items: computational genomics, epigenomics, biological foundation models, AI/ML methods
- ~3 curiosity items: neuroscience/evolutionary psychology, physics, mathematics, fine art
- LLM chooses **up to 6** stories, not necessarily 6. Short concrete summaries, source links, caveats.
- Sources in this MVP: arXiv (abstracts), PubMed (abstracts where available; otherwise citation metadata), Google News RSS (headlines/snippets; art news).
- Output: `site/index.html` and `data/latest.json`.

## Setup (GitHub Pages)

1. Create a **new GitHub repository**, upload **all the files including `.github/workflows/daily.yml`**.
2. In repository **Settings → Secrets and variables → Actions → New repository secret**, set `OPENAI_API_KEY` (your API key). API usage is billed separately from ChatGPT plans.
3. In **Settings → Actions → General → Workflow permissions**, allow **Read and write permissions** if your repository's policy requires it. The workflow commits read-history to the repo.
4. In **Settings → Pages → Build and deployment**, set **Source = GitHub Actions**.
5. Open **Actions → Daily Curiosity → Run workflow**. When it completes, your site will be published at the GitHub Pages URL listed under Settings → Pages.
6. Thereafter it runs daily around **7:17 AM America/New_York**. GitHub scheduled workflows may be delayed or occasionally skipped; you can always trigger manually.

**Privacy:** GitHub Pages hosts public content. Avoid adding private notes to the generated pages or committing secrets. Use a private repository if you want to hide source/config/history; published GitHub Pages site access may still be public depending on account/plan.

## Change interests or reading budget

Edit `config.json`: change `research_topics`, `general_topics`, `max_items`, `research_target`, `general_target`, or `max_days_old`. ArXiv query syntax is different from PubMed's. The model name is also configurable. `OPENAI_API_KEY` must never be added to the file.

## Local run

```bash
python -m pip install -r requirements.txt
export OPENAI_API_KEY='your-api-key'
python brief.py
python -m http.server 8000 -d site
```

Open http://localhost:8000/. No constantly running server is needed for GitHub Pages.

## Known limitations / next improvements

- This is a **starter MVP**, not a production-grade crawler. Coverage is incomplete: Some PubMed records lack abstracts, and those records cannot support full method/result summaries; art news snippets can be shallow. The model is instructed not to fabricate missing details.
- To improve scientific summaries, next add trusted publisher feeds and optionally full-text retrieval where permitted.
- arXiv and PubMed searches have different relevance/freshness behaviors. Some days fewer items will appear. Google News links may redirect.
- AI selection and summaries can contain mistakes. Check original publications before making scientific claims.
- The workflow keeps ~60 days of seen URLs, preventing short-term repeats; the current website only shows the latest briefing, without a long-term archive.
- External requests and API usage depend on network access and third-party uptime. No actual live source-fetch or OpenAI call was made while packaging this project.
