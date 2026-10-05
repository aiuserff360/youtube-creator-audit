# YouTube creator audit for Telugu-cinema campaigns

Audits YouTube movie-review channels with the official YouTube Data API v3 and
answers one question: is this creator a good fit for promoting a Telugu film?

Every figure carries a trust tag: **OBSERVED** (straight from YouTube),
**CALCULATED** (arithmetic on observed values), **INFERRED** (our interpretation)
or **NOT AVAILABLE** (private to the creator, such as audience age and location).

## Dashboard

`docs/index.html` is a self-contained page covering the creators audited so far.
Pick a creator, say what you are trying to do (film, audience, minimum reach),
and it gives a rule-based verdict plus the full dashboard.

## Run it yourself

```
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
echo "YOUTUBE_API_KEY=your-key" > .env
.venv/bin/python src/yt_channel_audit.py @SomeChannel   # audit one channel
.venv/bin/python src/serve.py                           # live dashboard, type any channel
.venv/bin/python src/export_dashboard.py                # rebuild docs/index.html
```

The API key stays in `.env` and is never written into any page.
