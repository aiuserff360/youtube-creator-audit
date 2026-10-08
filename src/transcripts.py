"""Fetch YouTube captions for review videos (unofficial library, local only).

    python src/transcripts.py --film Mirai          # videos matching a film in output/discovery/review_videos.csv
    python src/transcripts.py --ids a1b2c3,d4e5f6   # specific video IDs
    python src/transcripts.py --channel filmi_indian --telugu-only   # a channel's Telugu-labelled videos

Uses youtube-transcript-api, which reads the captions YouTube already shows
(creator-uploaded or auto-generated). Not the official API, no quota, but
YouTube blocks it from cloud servers, so run it on a laptop. Every video gets a
transcript_status; failures never stop the run. Results are cached as
data/transcripts/<video_id>.json. Everything downstream is INFERRED.
"""

import argparse
import csv
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from yt_channel_audit import OUTPUT_DIR, ROOT  # noqa: E402

TRANSCRIPT_DIR = ROOT / "data" / "transcripts"
PREFERRED_LANGS = ["te", "hi", "en", "ta", "ml", "kn"]  # Telugu first, then Hindi, English
PAUSE_SEC = 1.0  # be gentle; YouTube rate-limits aggressive clients


def video_ids_for(args):
    """Return [(video_id, title, channel_title)] from the chosen source."""
    if args.ids:
        return [(v.strip(), "", "") for v in args.ids.split(",") if v.strip()]
    if args.channel:
        path = OUTPUT_DIR / args.channel / f"{args.channel}_videos.csv"
        with path.open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        if args.telugu_only:
            rows = [r for r in rows if r["language_bucket"] == "Telugu"]
        return [(r["video_id"], r["title"], args.channel) for r in rows]
    path = OUTPUT_DIR / "discovery" / "review_videos.csv"
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = [r for r in csv.DictReader(f) if args.film.lower() in r["films"].lower()]
    return [(r["video_id"], r["title"], r["channel_title"]) for r in rows]


def fetch_one(api, video_id):
    """Pick the best available caption track and return a result dict."""
    from youtube_transcript_api._errors import (CouldNotRetrieveTranscript, IpBlocked,
                                                RequestBlocked)
    base = {"video_id": video_id, "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    try:
        tracks = list(api.list(video_id))
    except (IpBlocked, RequestBlocked):
        raise
    except CouldNotRetrieveTranscript as e:
        return dict(base, transcript_status=type(e).__name__, text="", language=None, is_generated=None)
    except Exception as e:  # noqa: BLE001 - never let one video stop the run
        return dict(base, transcript_status=f"error:{type(e).__name__}", text="", language=None, is_generated=None)
    if not tracks:
        return dict(base, transcript_status="NoTranscriptFound", text="", language=None, is_generated=None)

    def rank(t):  # manual captions beat auto-generated; preferred languages beat others
        lang = t.language_code.split("-")[0]
        return (t.is_generated, PREFERRED_LANGS.index(lang) if lang in PREFERRED_LANGS else 99)
    track = sorted(tracks, key=rank)[0]
    try:
        fetched = track.fetch()
    except (IpBlocked, RequestBlocked):
        raise
    except Exception as e:  # noqa: BLE001
        return dict(base, transcript_status=f"fetch_error:{type(e).__name__}", text="",
                    language=track.language_code, is_generated=track.is_generated)
    segments = fetched.to_raw_data()
    text = " ".join(s["text"].replace("\n", " ").strip() for s in segments)
    return dict(base, transcript_status="ok", text=text, language=track.language_code,
                is_generated=track.is_generated, n_segments=len(segments),
                duration_covered_sec=round(segments[-1]["start"] + segments[-1]["duration"]) if segments else 0,
                available_languages=[t.language_code for t in tracks])


def main():
    ap = argparse.ArgumentParser(description="Fetch captions for review videos.")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--film", help="film name to match in output/discovery/review_videos.csv")
    src.add_argument("--ids", help="comma-separated video IDs")
    src.add_argument("--channel", help="channel slug under output/")
    ap.add_argument("--telugu-only", action="store_true", help="with --channel: only Telugu-labelled videos")
    ap.add_argument("--limit", type=int, default=0, help="stop after N videos (0 = all)")
    ap.add_argument("--refresh", action="store_true", help="re-fetch even if cached")
    args = ap.parse_args()

    from youtube_transcript_api import YouTubeTranscriptApi
    from youtube_transcript_api._errors import IpBlocked, RequestBlocked
    api = YouTubeTranscriptApi()
    TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)

    todo = video_ids_for(args)
    if args.limit:
        todo = todo[:args.limit]
    statuses, fetched_now = {}, 0
    for i, (vid, title, channel) in enumerate(todo, 1):
        cache = TRANSCRIPT_DIR / f"{vid}.json"
        if cache.exists() and not args.refresh:
            result = json.loads(cache.read_text(encoding="utf-8"))
        else:
            try:
                result = fetch_one(api, vid)
            except (IpBlocked, RequestBlocked) as e:
                print(f"\nYouTube is blocking this machine ({type(e).__name__}). Stopping; {fetched_now} fetched "
                      "this run. Wait an hour or so and re-run; finished videos are cached.")
                break
            result.update(title=title, channel_title=channel)
            if not result["transcript_status"].startswith(("error:", "fetch_error:")):
                cache.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")  # don't cache transient failures
            fetched_now += 1
            time.sleep(PAUSE_SEC)
        statuses[result["transcript_status"]] = statuses.get(result["transcript_status"], 0) + 1
        mark = "ok " if result["transcript_status"] == "ok" else "-- "
        print(f"{i:>3}/{len(todo)} {mark}{(result.get('language') or ''):<6}{(result.get('title') or title)[:70]}")

    ok = statuses.get("ok", 0)
    print(f"\nTranscripts: {ok} of {len(todo)} available ({ok / len(todo) * 100:.0f}% coverage). "
          f"Status counts: {statuses}")
    print(f"Saved under data/transcripts/. Next: python src/classify_reviews.py --film <name>")


if __name__ == "__main__":
    main()
