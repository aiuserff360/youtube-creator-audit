# CLAUDE.md — YouTube Telugu-Cinema Influencer Audit

## What this project is

Build and operate a pipeline that audits YouTube movie-review creators (starting with one channel, scaling to 50–100) using the **official YouTube Data API v3**, and produces an influencer scorecard focused on **Telugu-cinema content performance**. The user is evaluating creators for a Telugu film marketing campaign. Subscriber count matters far less than observed per-video reach and Telugu-specific affinity.

Work in plain English with the user. Ask before any step that costs quota heavily (search.list) or sends data anywhere outside this machine.

## Ground rules

- **Never hard-code or print the API key.** Read it from `.env` (`YOUTUBE_API_KEY=...`) via `python-dotenv` or `os.environ`. `.env` is in `.gitignore`. If the key is missing, stop and tell the user how to add it.
- **Use only public endpoints with an API key**: `channels.list`, `playlistItems.list`, `videos.list`, `commentThreads.list`. No OAuth needed. We do not have access to the creator's YouTube Analytics.
- **Avoid `search.list`** (100 quota units per call). Resolve channels with `channels.list` using `id=` (UC… IDs) or `forHandle=`. Daily quota is 10,000 units; a normal channel audit should cost under 50.
- **Transcripts are not available from the official API** for third-party channels. If asked, use the unofficial `youtube-transcript-api` library, run locally only, wrapped in try/except, and record a `transcript_status` per video. Never let transcript failures stop a run. Treat transcript coverage as partial.
- **Cache raw API responses** to `data/raw/<channel_id>/` as JSON so re-runs don't re-spend quota. Only re-fetch when the user asks for a refresh.
- Python 3.10+. Dependencies: `requests`, `python-dotenv`. Keep `pandas` optional. No notebooks; plain scripts in `src/`.
- Every output field must carry one of four tags (see "Field tagging"). Never present inferred values as observed.

## Starting point

If `yt_channel_audit.py` exists in this folder, start from it: it already implements the single-channel pipeline. Move it into `src/`, switch it to read the key from `.env`, and build from there. If it doesn't exist, write it from the spec below.

First channel to audit:
- **Mr Review Wala** — channel ID `UCflqFc2XQPyTiC4Fb7SfKgA`

Further channels will be added to `channels.txt` (one ID or @handle per line).

## Pipeline spec

```
Creator handle / channel ID
  → channels.list (snippet, statistics, contentDetails)        → PROFILE
  → uploads playlist ID → playlistItems.list, paginate 50/page → all public video IDs
  → videos.list in batches of 50 (snippet, statistics, contentDetails) → VIDEO DATA
  → classify language/industry from title + description + tags (+ transcript if present)
  → compute metrics for last 10 / 20 / 30 / lifetime windows
  → optional: commentThreads.list sample (max 200 comments/channel) → AUDIENCE SIGNALS
  → write outputs
```

### Per-video fields to capture
`video_id, title, published, duration_sec, is_short, views, likes, comments, language_bucket, matched_keyword, matched_in, also_matched, tags, description (first 500 chars), url`
- `likes` = null when hidden; `comments` = null when disabled. Don't convert null to 0 in the raw file.
- `is_short` = duration ≤ 180 s, YouTube's Shorts limit since October 2024 (heuristic; flag as INFERRED). Changed from 60 s on 2026-10-01 after the pilot channel's 61–180 s Shorts all counted as non-Shorts.
- Parse ISO-8601 durations (`PT1H2M3S`). Handle `P0D` (live/premiere) as 0.

### Metrics (compute for each window: last 10, last 20, last 30, lifetime)
| Metric | Definition |
|---|---|
| median_views | median of views |
| avg_views | mean of views |
| median_likes / median_comments | medians, ignoring nulls |
| engagement_per_view_pct | median of (likes + comments) / views × 100 |
| views_to_subscriber_ratio | median_views / subscribers |
| uploads_per_month | videos in window / (days between first and last publish / 30.44) |
| consistency_pct | % of videos with views between 0.5× and 2× window median |
| viral_rate_pct | % of videos with views > 2× window median |
| shorts_pct | % of videos that are Shorts |
| telugu_share_pct | Telugu-bucket videos / videos in window |
| telugu_median_views | median views of Telugu-bucket videos |
| telugu_engagement_pct | engagement_per_view on Telugu-bucket videos |
| telugu_affinity_index | telugu_median_views / median_views |
| language_mix | count per bucket |

Windows exist so old viral videos don't distort the picture of the creator today.

### Language / industry classification
Buckets: `Telugu, Tamil, Malayalam, Kannada, Hindi/Bollywood, Hollywood, Korean, Chinese, OTT/Other, Other/Unclear`.

Method: keyword rules on lowercased text, matched with word boundaries. An industry keyword in the title wins outright, checked in priority order (Telugu first). Otherwise the industry mentioned most often in description + tags wins, ties going to the earlier bucket. `OTT/Other` is a format, not an industry, and applies only when no industry matched. (Changed on 2026-10-01: the pilot showed creators stuff descriptions and tags with keywords for several industries, so first-match on the combined text mislabelled videos.) Record which keyword fired in `matched_keyword`, where it fired in `matched_in` (title or description/tags), and any other industries present in `also_matched`, so the user can audit it. Keyword lists live in `src/keywords.py`; the user will extend them. Starting Telugu list: telugu, tollywood, prabhas, allu arjun, jr ntr, ntr, mahesh babu, ram charan, pawan kalyan, chiranjeevi, rajamouli, vijay deverakonda, nani, balakrishna, ravi teja, nithiin, sukumar, trivikram, naga chaitanya, sandeep reddy vanga, venkatesh. Build comparable lists for the other buckets.

Later phase (only when the user asks): LLM-based classification of transcript/description for film discussed, language spoken, sentiment (positive/negative/mixed), review vs recommendation, sponsor/promo language. Always validate against a hand-labelled sample of ~30 videos before reporting those numbers.

### Audience signals (optional, phase 2)
From a comment sample: script/language mix (Latin vs Telugu vs Devanagari characters as a first proxy), share of movie-specific vs generic comments, repeat commenters. Tag all of it INFERRED.

## Field tagging (mandatory on every report)
- 🟢 **OBSERVED** — straight from the API (subs, views, likes, comments, dates, durations, titles, tags)
- 🔵 **CALCULATED** — arithmetic on observed values (all window metrics)
- 🟠 **INFERRED** — our interpretation (language bucket, is_short, sentiment, audience signals, anything transcript-based)
- ⚪ **NOT AVAILABLE** — private YouTube Analytics (demographics, watch time, impressions, CTR, traffic sources)

Known caveats to state in reports: subscriber counts are rounded by YouTube above 1,000; deleted/private videos are absent so "lifetime" means "currently public"; hidden likes and disabled comments appear as null.

## Outputs
One folder per channel, `output/<channel_slug>/` (the user asked for each creator's files to be kept separate, 2026-10-05):
- `output/<channel_slug>/<channel_slug>_videos.csv` — one row per video
- `output/<channel_slug>/<channel_slug>_summary.json` — profile + all windows + field tags + generated_at
- `output/<channel_slug>/<channel_slug>_scorecard.md` — human-readable scorecard with five sections: PROFILE, PERFORMANCE, CONTENT, TELUGU PERFORMANCE, AUDIENCE SIGNALS; each value tagged
- Boss-facing PDF reports also live in the channel's folder.
- When multiple channels exist: `output/comparison.csv` with one row per channel and the last-30 metrics side by side, sorted by telugu_affinity_index

Do not add "prepared by" or generator credits to any output.

## Folder layout
```
.
├── CLAUDE.md
├── .env                 # YOUTUBE_API_KEY=...  (never committed)
├── .gitignore
├── channels.txt
├── requirements.txt
├── src/
│   ├── yt_channel_audit.py   # single-channel pipeline
│   ├── keywords.py
│   ├── run_all.py            # loops channels.txt, caches, builds comparison.csv
│   ├── dashboard_template.html  # single-page dashboard; renders embedded JSON or talks to serve.py
│   ├── dashboard_data.py     # load output/<slug>/ files, slim video rows for the page
│   ├── serve.py              # local server on 127.0.0.1:8765: type a handle → dashboard (key stays in .env)
│   └── export_dashboard.py   # writes output/<slug>/<slug>_dashboard.html and output/site/index.html (all channels, picker)
├── data/raw/
└── output/<channel_slug>/
```

## Published page (2026-10-05, user asked for it)
- Repo: https://github.com/aiuserff360/youtube-creator-audit (public, account aiuserff360). `output/`, `data/raw/`, `.env`, `.venv/` are not committed.
- GitHub Pages serves `docs/index.html` from `main`: https://aiuserff360.github.io/youtube-creator-audit/
- Live app (any creator, password-protected, key held as a Render secret): https://youtube-creator-audit.onrender.com — deployed from `render.yaml` on Render's free tier (sleeps after 15 min idle; cache is wiped on restart). Render auto-redeploys on every push to `main`.
- To update the static page after auditing new channels: `LIVE_APP_URL=https://youtube-creator-audit.onrender.com python src/export_dashboard.py`, then commit `docs/index.html` and push. The page goes live about a minute after the push.
- The page's question form takes `?channel=&lang=&goal=&films=&aud=&min=` so a filled-in question can be shared as a link.

## Dashboard (added 2026-10-05)
The boss wanted a dashboard view. Rules that follow from the ground rules: never embed the API key in a web page, so live "type any channel" lookups only work through `serve.py` on this machine; shareable files are static exports with data embedded and only cover channels already audited. Publishing `output/site/` (e.g. GitHub Pages) sends data off this machine — ask first. Audience demographics stay a NOT AVAILABLE panel; never fabricate them even if a mock-up shows them.

## Phases
1. **Phase 1 (now):** single channel end to end. Run it, show the summary, open the CSV, let the user sanity-check the language buckets.
2. **Phase 2:** `run_all.py` over `channels.txt` with caching and `comparison.csv`.
3. **Phase 3 (on request):** transcripts, LLM classification, comment sampling.
4. **Phase 4 (on request):** scheduled monthly refresh.

## How to behave on errors
- `403` mentioning the API not enabled or "not been used in project": tell the user to enable YouTube Data API v3 on the Cloud project that owns the key.
- `403 quotaExceeded`: stop, report how much was done, resume after midnight Pacific using the cache.
- `400` on `forHandle`: ask the user for the exact handle from the channel URL or the UC… ID.
- Channel found but zero uploads: report it; don't guess.
- Transcript errors: log and continue.

## Definition of done for Phase 1
`python src/yt_channel_audit.py` runs without errors on Mr Review Wala, produces the three output files, the summary printed to the terminal shows all four windows, and at least 20 rows of the CSV have been spot-checked for sensible language buckets.
