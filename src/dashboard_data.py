"""Shared helpers for the dashboard: slim video rows and load audited channels."""

import csv
import json

from yt_channel_audit import OUTPUT_DIR

# Columns the dashboard needs; description and tags are dropped to keep pages small.
DASHBOARD_FIELDS = ["video_id", "title", "published", "duration_sec", "is_short", "views",
                    "likes", "comments", "language_bucket", "matched_keyword", "matched_in",
                    "also_matched", "url"]
_INTS = {"duration_sec", "views", "likes", "comments"}


def slim_videos(videos):
    return [{k: v[k] for k in DASHBOARD_FIELDS} for v in videos]


def discovery_info():
    """channel_id → how it showed up in discover.py's film-review searches."""
    path = OUTPUT_DIR / "discovery" / "candidates.csv"
    if not path.exists():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as f:
        return {r["channel_id"]: {"films_covered": int(r["films_covered"] or 0),
                                  "review_videos_found": int(r["review_videos_found"] or 0),
                                  "median_review_views": int(r["median_review_views"] or 0) if r["median_review_views"] else None,
                                  "films": r["films"]} for r in csv.DictReader(f)}


SITE_RECENT, SITE_TOP = 400, 100


def slim_for_site(data, films=()):
    """Keep each channel's recent + top videos (+ any naming a tracked film) so a multi-channel page stays small."""
    videos = sorted(data["videos"], key=lambda v: v["published"] or "", reverse=True)
    keep = {v["video_id"] for v in videos[:SITE_RECENT]}
    keep |= {v["video_id"] for v in sorted(videos, key=lambda v: v["views"] or 0, reverse=True)[:SITE_TOP]}
    keep |= {v["video_id"] for v in videos if any(f in v["title"].lower() for f in films)}
    slim = [v for v in videos if v["video_id"] in keep]
    capped = len(slim) < len(videos)
    return dict(data, videos=slim, videos_capped=capped, videos_total=len(videos),
                site_note=(f"This shared page holds the {SITE_RECENT} most recent and {SITE_TOP} most-viewed of {len(videos)} videos, "
                           "plus any video naming a tracked film; the film check and top-10 cover those." if capped else ""))


def tracked_films():
    path = OUTPUT_DIR.parent / "films.txt"
    if not path.exists():
        return []
    return [l.strip().lower() for l in path.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]


def load_channel(slug, discovery=None):
    """Read a channel's saved summary + videos from output/<slug>/."""
    folder = OUTPUT_DIR / slug
    summary = json.loads((folder / f"{slug}_summary.json").read_text(encoding="utf-8"))
    videos = []
    with (folder / f"{slug}_videos.csv").open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            v = {k: row.get(k, "") for k in DASHBOARD_FIELDS}
            for k in _INTS:
                v[k] = int(v[k]) if v[k] != "" else None
            v["is_short"] = v["is_short"] == "True"
            videos.append(v)
    disc = (discovery if discovery is not None else discovery_info()).get(summary["profile"]["channel_id"])
    return {"summary": summary, "videos": videos, "audience": summary.get("audience_signals"), "discovery": disc}


def audited_channels():
    """Slugs of every channel with a summary file, newest audit first."""
    found = []
    if OUTPUT_DIR.exists():
        for folder in OUTPUT_DIR.iterdir():
            f = folder / f"{folder.name}_summary.json"
            if f.exists():
                s = json.loads(f.read_text(encoding="utf-8"))
                found.append((s["generated_at"], folder.name, s["profile"]))
    found.sort(reverse=True)
    return [{"slug": slug, "id": p["channel_id"], "title": p["title"], "audited": ts}
            for ts, slug, p in found]
