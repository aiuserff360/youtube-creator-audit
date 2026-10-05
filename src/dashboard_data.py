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


def load_channel(slug):
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
    return {"summary": summary, "videos": videos}


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
