"""Audit every channel in channels.txt and build output/comparison.csv.

    python src/run_all.py                    # all channels, cached where possible
    python src/run_all.py --max-videos 3000  # cap per channel (default 3000; 0 = no cap)
    python src/run_all.py --budget 5000      # stop before spending more than this (default 5000)

channels.txt: one UC… ID or @handle per line; '#' starts a comment.
comparison.csv: one row per channel, last-30 metrics side by side, sorted by
telugu_affinity_index (then telugu_share_pct). Channels whose audit failed are
listed in the terminal and skipped.
"""

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from yt_channel_audit import (OUTPUT_DIR, ROOT, ApiError, QuotaExceeded, YouTubeClient,  # noqa: E402
                              audit_channel, load_api_key)

COMPARE_FIELDS = ["median_views", "avg_views", "engagement_per_view_pct", "views_to_subscriber_ratio",
                  "uploads_per_month", "consistency_pct", "viral_rate_pct", "shorts_pct",
                  "telugu_share_pct", "telugu_median_views", "telugu_engagement_pct", "telugu_affinity_index"]


def read_channels():
    f = ROOT / "channels.txt"
    return [l.split("#")[0].strip() for l in f.read_text(encoding="utf-8").splitlines()
            if l.split("#")[0].strip()]


def build_comparison():
    rows = []
    for folder in OUTPUT_DIR.iterdir():
        f = folder / f"{folder.name}_summary.json"
        if not f.exists():
            continue
        s = json.loads(f.read_text(encoding="utf-8"))
        p, w = s["profile"], s["windows"]["last_30"]
        row = {"channel": p["title"], "handle": p.get("handle"), "channel_id": p["channel_id"],
               "subscribers": p.get("subscribers"), "public_videos": p.get("video_count"),
               "videos_fetched": s.get("videos_fetched"), "telugu_videos_last_30": w["language_mix"].get("Telugu", 0)}
        row.update({f"last30_{k}": w[k] for k in COMPARE_FIELDS})
        row.update({f"lifetime_{k}": s["windows"]["lifetime"][k] for k in ("telugu_share_pct", "telugu_affinity_index")})
        row["audited"] = s["generated_at"]
        rows.append(row)
    rows.sort(key=lambda r: ((r["last30_telugu_affinity_index"] or 0), (r["last30_telugu_share_pct"] or 0)), reverse=True)
    out = OUTPUT_DIR / "comparison.csv"
    with out.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return rows


def main():
    ap = argparse.ArgumentParser(description="Audit all channels in channels.txt.")
    ap.add_argument("--max-videos", type=int, default=3000)
    ap.add_argument("--budget", type=int, default=5000)
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    max_videos = args.max_videos or None

    client = YouTubeClient(load_api_key(), refresh=args.refresh)
    done, failed, stopped = [], [], False
    for ident in read_channels():
        if client.units_used >= args.budget:
            print(f"Budget of {args.budget} units reached; stopping before {ident}.")
            stopped = True
            break
        print(f"\n=== {ident} ===")
        try:
            result = audit_channel(client, ident, max_videos=max_videos)
            (done if result else failed).append(ident)
        except QuotaExceeded:
            print("Daily quota exceeded; everything fetched so far is cached. Re-run tomorrow to continue.")
            stopped = True
            break
        except ApiError as e:
            print(f"Skipped {ident}: {e}")
            failed.append(ident)

    rows = build_comparison()
    print(f"\nAudited {len(done)} channel(s), {len(failed)} failed, quota used {client.units_used} units"
          + (" (stopped early)" if stopped else ""))
    if failed:
        print("Failed:", ", ".join(failed))
    print(f"\noutput/comparison.csv — {len(rows)} channels, sorted by last-30 Telugu affinity:\n")
    print(f"{'channel':<30}{'subs':>12}{'med views':>11}{'eng%':>7}{'telugu%':>9}{'tel vids':>9}{'affinity':>10}")
    for r in rows:
        print(f"{r['channel'][:29]:<30}{(r['subscribers'] or 0):>12,}{(r['last30_median_views'] or 0):>11,}"
              f"{(r['last30_engagement_per_view_pct'] or 0):>7.2f}{(r['last30_telugu_share_pct'] or 0):>9.0f}"
              f"{r['telugu_videos_last_30']:>9}{(r['last30_telugu_affinity_index'] or 0):>10.2f}")


if __name__ == "__main__":
    main()
