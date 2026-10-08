"""Discover creators who get views on Telugu film reviews.

Works backwards from films: for each film in films.txt, ask YouTube for the
most-viewed "<film> review" videos (search.list, 100 quota units per call),
collect the channels behind them, then look up each channel's size and rank
them by how their Telugu-film reviews performed.

    python src/discover.py --dry-run          # show planned calls and quota cost, spend nothing
    python src/discover.py                    # run (asks for confirmation first)
    python src/discover.py --budget 2500      # stop before spending more than this many units

Outputs: output/discovery/candidates.csv (one row per channel, ranked) and
output/discovery/review_videos.csv (every matched video). Search responses are
cached under data/raw/_search/, so re-runs are free.
"""

import argparse
import csv
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parent))

from yt_channel_audit import (OUTPUT_DIR, RAW_DIR, ROOT, ApiError, QuotaExceeded,  # noqa: E402
                              YouTubeClient, load_api_key, parse_duration, to_int)

SEARCH_COST = 100
FILMS_FILE = ROOT / "films.txt"
OUT_DIR = OUTPUT_DIR / "discovery"

# Two searches per film: the most-viewed reviews anywhere, and the most relevant
# ones for Telugu-language viewers (surfaces Telugu-speaking creators).
QUERY_VARIANTS = [
    {"order": "viewCount"},
    {"order": "relevance", "relevanceLanguage": "te"},
]


def read_films():
    if not FILMS_FILE.exists():
        sys.exit("films.txt not found. Put one Telugu film title per line in the project folder.")
    return [l.strip() for l in FILMS_FILE.read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.startswith("#")]


def search_cache(params):
    key = hashlib.sha1(json.dumps(params, sort_keys=True).encode()).hexdigest()[:16]
    return RAW_DIR / "_search" / f"{key}.json"


def planned_searches(films, since):
    for film in films:
        for variant in QUERY_VARIANTS:
            params = {"part": "snippet", "type": "video", "q": f"{film} review", "maxResults": 50,
                      "regionCode": "IN", "publishedAfter": since, **variant}
            yield film, params


def main():
    ap = argparse.ArgumentParser(description="Find creators via Telugu film reviews.")
    ap.add_argument("--dry-run", action="store_true", help="show the plan and cost, spend nothing")
    ap.add_argument("--budget", type=int, default=4500, help="max quota units to spend (default 4500)")
    ap.add_argument("--months", type=int, default=24, help="only videos published in the last N months")
    ap.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    args = ap.parse_args()

    films = read_films()
    since = (datetime.now(timezone.utc) - timedelta(days=30.44 * args.months)).strftime("%Y-%m-%dT%H:%M:%SZ")
    plan = list(planned_searches(films, since))
    uncached = [p for _, p in plan if not search_cache(p).exists()]
    est = len(uncached) * SEARCH_COST + 2 * max(1, len(plan))  # searches + rough video/channel lookups
    print(f"{len(films)} films → {len(plan)} searches ({len(uncached)} not yet cached). "
          f"Estimated quota: ~{est} units of the 10,000 daily limit.")
    if args.dry_run:
        for film, p in plan:
            print(f"  {'cached ' if search_cache(p).exists() else '100 u  '} q=\"{p['q']}\" order={p['order']}"
                  + (f" lang={p['relevanceLanguage']}" if "relevanceLanguage" in p else ""))
        return
    if est > args.budget:
        sys.exit(f"Estimated cost {est} exceeds --budget {args.budget}. Trim films.txt or raise the budget.")
    if not args.yes and input("Proceed? [y/N] ").strip().lower() != "y":
        return

    client = YouTubeClient(load_api_key())
    videos_by_id, film_of_video = {}, {}
    try:
        for film, params in plan:
            if client.units_used + SEARCH_COST > args.budget and not search_cache(params).exists():
                print(f"Budget reached before searching for {film}; stopping searches here.")
                break
            data = client.get("search", params, search_cache(params), cost=SEARCH_COST)
            for it in data.get("items", []):
                vid = it.get("id", {}).get("videoId")
                if vid:
                    film_of_video.setdefault(vid, set()).add(film)
            print(f"  {film:<32} {params['order']:<10} {len(data.get('items', [])):>3} results   "
                  f"(quota so far {client.units_used})")

        ids = list(film_of_video)
        for n, start in enumerate(range(0, len(ids), 50)):
            batch = ids[start:start + 50]
            cache = RAW_DIR / "_search" / f"videos_{hashlib.sha1(','.join(batch).encode()).hexdigest()[:16]}.json"
            data = client.get("videos", {"part": "snippet,statistics,contentDetails", "id": ",".join(batch),
                                         "maxResults": 50}, cache)
            for it in data.get("items", []):
                videos_by_id[it["id"]] = it

        channels = {}
        for vid, it in videos_by_id.items():
            s, st = it["snippet"], it.get("statistics", {})
            ch = channels.setdefault(s["channelId"], {"channel_id": s["channelId"], "channel_title": s["channelTitle"],
                                                       "films": set(), "videos": []})
            ch["films"] |= film_of_video[vid]
            ch["videos"].append({"video_id": vid, "title": s["title"], "published": s["publishedAt"],
                                 "views": to_int(st.get("viewCount")), "likes": to_int(st.get("likeCount")),
                                 "comments": to_int(st.get("commentCount")),
                                 "duration_sec": parse_duration(it.get("contentDetails", {}).get("duration")),
                                 "films": "|".join(sorted(film_of_video[vid])),
                                 "url": f"https://www.youtube.com/watch?v={vid}"})

        cids = list(channels)
        for start in range(0, len(cids), 50):
            batch = cids[start:start + 50]
            cache = RAW_DIR / "_search" / f"channels_{hashlib.sha1(','.join(batch).encode()).hexdigest()[:16]}.json"
            data = client.get("channels", {"part": "snippet,statistics", "id": ",".join(batch), "maxResults": 50}, cache)
            for it in data.get("items", []):
                ch, st, sn = channels[it["id"]], it.get("statistics", {}), it["snippet"]
                ch.update(handle=sn.get("customUrl"), country=sn.get("country"),
                          subscribers=None if st.get("hiddenSubscriberCount") else to_int(st.get("subscriberCount")),
                          channel_videos=to_int(st.get("videoCount")), channel_views=to_int(st.get("viewCount")))
    except QuotaExceeded:
        print(f"Daily quota exceeded after {client.units_used} units. Searches so far are cached; re-run tomorrow.")
        return
    except ApiError as e:
        sys.exit(f"Stopped: {e}")
    finally:
        print(f"Quota used this run: {client.units_used} units ({client.cache_hits} responses from cache)")

    rows = []
    for ch in channels.values():
        views = [v["views"] for v in ch["videos"] if v["views"] is not None]
        best = max(ch["videos"], key=lambda v: v["views"] or 0)
        rows.append({
            "channel_title": ch["channel_title"], "handle": ch.get("handle"), "channel_id": ch["channel_id"],
            "country": ch.get("country"), "subscribers": ch.get("subscribers"),
            "films_covered": len(ch["films"]), "review_videos_found": len(ch["videos"]),
            "median_review_views": round(median(views)) if views else None,
            "total_review_views": sum(views), "best_video_views": best["views"], "best_video_title": best["title"],
            "best_video_url": best["url"], "films": "|".join(sorted(ch["films"])),
            "channel_videos": ch.get("channel_videos"), "url": f"https://www.youtube.com/channel/{ch['channel_id']}",
        })
    # Rank: breadth of Telugu coverage first, then typical views on those reviews.
    rows.sort(key=lambda r: (r["films_covered"], r["median_review_views"] or 0), reverse=True)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with (OUT_DIR / "candidates.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["channel_title"])
        w.writeheader(); w.writerows(rows)
    vrows = [dict(v, channel_title=ch["channel_title"], channel_id=ch["channel_id"])
             for ch in channels.values() for v in ch["videos"]]
    with (OUT_DIR / "review_videos.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(vrows[0].keys()) if vrows else ["video_id"])
        w.writeheader(); w.writerows(vrows)

    print(f"\n{len(rows)} candidate channels from {len(vrows)} review videos. Top 25 by films covered, then median views:\n")
    print(f"{'channel':<34}{'films':>6}{'videos':>8}{'median views':>14}{'subs':>12}  handle")
    for r in rows[:25]:
        print(f"{r['channel_title'][:33]:<34}{r['films_covered']:>6}{r['review_videos_found']:>8}"
              f"{(r['median_review_views'] or 0):>14,}{(r['subscribers'] or 0):>12,}  {r['handle'] or ''}")
    print(f"\nWrote output/discovery/candidates.csv and review_videos.csv. "
          f"Add chosen channel IDs or handles to channels.txt for a full audit.")


if __name__ == "__main__":
    main()
