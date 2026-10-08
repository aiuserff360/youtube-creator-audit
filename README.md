# YouTube creator audit for Telugu-cinema campaigns

Audits YouTube movie-review channels through the official **YouTube Data API v3**
and answers one question: is this creator a good fit for promoting a Telugu film?
It pulls a channel's public profile and every public video, labels each video by
film industry, computes reach and engagement over recent windows, and renders a
dashboard with a rule-based verdict for a stated goal.

Every figure carries a trust tag so nothing inferred is passed off as fact:

| Tag | Meaning | Examples |
|---|---|---|
| OBSERVED | straight from YouTube | subscribers, views, likes, comments, dates, durations |
| CALCULATED | arithmetic on observed values | median views, engagement rate, uploads per month |
| INFERRED | our interpretation, can be wrong | industry label, Shorts flag, the verdict |
| NOT AVAILABLE | private to the creator | audience age, gender, location, watch time |

## What is deployed

| What | Where | Notes |
|---|---|---|
| Source | https://github.com/aiuserff360/youtube-creator-audit | this repo |
| Live app (any creator) | https://youtube-creator-audit.onrender.com | Render free tier, password-protected, auto-redeploys on push to `main` |
| Static page (audited creators only) | https://aiuserff360.github.io/youtube-creator-audit/ | GitHub Pages serving `docs/index.html` |

## Repository layout

```
CLAUDE.md                    full project brief and the decisions taken so far (read this first)
README.md
requirements.txt             requests, python-dotenv (nothing else)
render.yaml                  one-click Render deployment
channels.txt                 channel IDs / handles to audit in batch (one per line)
films.txt                    Telugu films that discover.py searches for
src/
  yt_channel_audit.py        the pipeline: fetch → classify → metrics → CSV/JSON/scorecard
  keywords.py                keyword lists per industry bucket; extend these
  dashboard_template.html    the dashboard page (vanilla JS, no build step, inline SVG charts)
  dashboard_data.py          loads output/<slug>/ files for the dashboard
  serve.py                   HTTP server: GET /api/audit?channel=… runs the pipeline, serves the page
  export_dashboard.py        writes self-contained dashboard HTML files and docs/index.html
  discover.py                finds candidate creators from "<film> review" searches (uses search.list; asks first)
docs/index.html              generated; the static all-creators page
data/raw/<channel_id>/       generated; cached raw API responses (git-ignored)
output/<channel_slug>/       generated; CSV, summary JSON, scorecard, dashboard HTML (git-ignored)
```

## Setup

Requirements: Python 3.10+ (3.12 recommended), a Google account.

1. Clone the repo.
2. Create a virtual environment and install the two dependencies:
   ```
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```
3. Get a YouTube Data API key (free, no billing): Google Cloud Console → new project →
   APIs & Services → Library → enable **YouTube Data API v3** → Credentials →
   Create credentials → API key. Restrict the key to YouTube Data API v3.
4. Put it in a `.env` file at the repo root (git-ignored, never commit it):
   ```
   YOUTUBE_API_KEY=AIza...
   ```

## Usage

```
.venv/bin/python src/yt_channel_audit.py @FilmiIndian        # audit one channel (handle or UC… ID)
.venv/bin/python src/yt_channel_audit.py @FilmiIndian --refresh   # ignore the cache, re-fetch
.venv/bin/python src/serve.py                                # dashboard at http://127.0.0.1:8765
.venv/bin/python src/export_dashboard.py                     # rebuild output/*/…_dashboard.html and docs/index.html
```

An audit prints the four windows to the terminal and writes to `output/<slug>/`:
`<slug>_videos.csv` (one row per video), `<slug>_summary.json` (profile, windows,
field tags), `<slug>_scorecard.md`. Raw API responses are cached under `data/raw/`,
so re-running a channel costs no quota until you pass `--refresh`.

Quota: every call costs 1 unit; an audit costs about 1 + 2 × ceil(videos / 50)
(a 2,500-video channel ≈ 101 units) against a free daily limit of 10,000.
`search.list` (100 units per call) is deliberately never used.

## Finding creators to audit

```
.venv/bin/python src/discover.py --dry-run   # plan and quota cost, spends nothing
.venv/bin/python src/discover.py             # runs after a y/N confirmation
```

For every film in `films.txt` it fetches the most-viewed "<film> review" videos
(`search.list`, 100 units per call, two calls per film), collects the channels
behind them, looks up their size, and writes `output/discovery/candidates.csv`
ranked by how many of the films each channel reviewed and the median views of
those reviews. Pick channels from it into `channels.txt`.

## How the pipeline works

```
channel handle / ID
  → channels.list                      profile, subscriber count, uploads playlist ID
  → playlistItems.list (50 per page)   every public video ID
  → videos.list (50 per batch)         title, description, tags, duration, views, likes, comments
  → classify()                         industry bucket from keywords (see below)
  → window_metrics()                   last 10 / 20 / 30 uploads and lifetime
  → CSV + JSON + scorecard
```

Metric definitions are in `CLAUDE.md` → "Metrics". Windows are the most recent N
public uploads by publish date; "lifetime" means "currently public".

### Industry classification (the known weak spot)

`classify()` in `yt_channel_audit.py` uses the keyword lists in `keywords.py`:
an industry keyword in the title wins outright (buckets checked Telugu-first);
otherwise the industry mentioned most often in description + tags wins. Each video
records `matched_keyword`, `matched_in` (title or description/tags) and
`also_matched` so labels can be audited.

Known limitations found on the two pilot channels: creators stuff descriptions and
tags with keywords for several industries, titles often name no industry, and some
labels are simply wrong (e.g. the Telugu film *Mirai* is labelled Hindi/Bollywood
on Filmi Indian because its description mentions Bollywood stars). Treat every
Telugu metric as provisional. The planned fix is LLM-based identification of the
film and its language, validated against a hand-labelled sample of ~30 videos
before the numbers are trusted (see "Phases" in `CLAUDE.md`).

## Dashboard and verdict

`dashboard_template.html` is a single page with no dependencies. It works in two
modes:

- **Served** by `serve.py`: the form accepts any channel; the page calls
  `/api/audit?channel=…`, which runs the pipeline and returns `{summary, videos}`.
  If `DASHBOARD_PASSWORD` is set, `/api/*` requires the `X-Dashboard-Key` header;
  the page prompts for it once per session.
- **Static**: `export_dashboard.py` embeds the data as JSON in place of the
  `/*__AUDIT_DATA__*/` placeholder (an array of channels for `docs/index.html`).
  Only already-audited creators are available in this mode.

The question form (who are we researching / what are we trying to do) is kept in the
URL (`?channel=&lang=&goal=&films=&aud=&min=`) so a filled-in question is shareable.
The verdict in `answer()` is rule-based and labelled INFERRED: it scores reach
against the stated minimum, consistency, language match with the target audience,
Telugu share, Telugu affinity, and how the named films performed against the 30
videos around them. It is a shortlist aid, not a decision.

## Deployment

- **Render** (live app): the "Deploy to Render" button below reads `render.yaml`
  and asks for `YOUTUBE_API_KEY` and `DASHBOARD_PASSWORD`. `serve.py` binds to
  `0.0.0.0:$PORT` when `PORT` is set. The free tier sleeps after 15 minutes idle
  (first request then takes ~1 minute) and has an ephemeral disk, so the cache is
  lost on restart.

  [![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/aiuserff360/youtube-creator-audit)

- **GitHub Pages** (static page): serves `docs/` from `main`. After auditing new
  channels, run
  `LIVE_APP_URL=https://youtube-creator-audit.onrender.com .venv/bin/python src/export_dashboard.py`,
  commit `docs/index.html` and push.

To run your own copies, fork the repo and deploy from the fork; the GitHub Pages
and Render instances above belong to the `aiuserff360` account.

## Ground rules worth keeping

- Never embed the API key in a page or commit it. The key only ever lives in
  `.env` locally and as a Render secret.
- Public endpoints only (`channels.list`, `playlistItems.list`, `videos.list`,
  optionally `commentThreads.list`). No OAuth, no creator-account access.
- Audience demographics are NOT AVAILABLE from public data. Don't fabricate them;
  ask creators for YouTube Studio screenshots.
- Every output value carries one of the four tags.

## Roadmap (from `CLAUDE.md`)

1. Done: single-channel pipeline, dashboard, verdict, hosting.
2. `run_all.py` over `channels.txt` producing `output/comparison.csv`.
3. On request: LLM-based film/language classification with validation, comment
   sampling for audience-language signals, transcripts.
4. On request: scheduled monthly refresh.
