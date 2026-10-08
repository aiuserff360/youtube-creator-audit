"""Single-channel YouTube audit.

Fetches a channel's profile and all public videos through the YouTube Data
API v3, classifies each video into a language/industry bucket, computes
metrics for the last 10 / 20 / 30 / lifetime windows and writes:

    output/<slug>_videos.csv
    output/<slug>_summary.json
    output/<slug>_scorecard.md

Usage:
    python src/yt_channel_audit.py                      # Mr Review Wala
    python src/yt_channel_audit.py UCxxxx... | @handle  # another channel
    python src/yt_channel_audit.py --refresh            # ignore the cache
"""

import argparse
import csv
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median

import requests
from dotenv import load_dotenv

from keywords import BUCKETS, FALLBACK_BUCKET, FORMAT_BUCKET

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
OUTPUT_DIR = ROOT / "output"
API_BASE = "https://www.googleapis.com/youtube/v3"
DEFAULT_CHANNEL = "UCflqFc2XQPyTiC4Fb7SfKgA"  # Mr Review Wala
SHORT_MAX_SEC = 180  # YouTube's Shorts limit since October 2024

WINDOWS = [("last_10", 10), ("last_20", 20), ("last_30", 30), ("lifetime", None)]

OBSERVED, CALCULATED, INFERRED, NOT_AVAILABLE = (
    "OBSERVED", "CALCULATED", "INFERRED", "NOT AVAILABLE")
TAG_ICON = {OBSERVED: "🟢", CALCULATED: "🔵", INFERRED: "🟠", NOT_AVAILABLE: "⚪"}

VIDEO_FIELDS = [
    "video_id", "title", "published", "duration_sec", "is_short", "views",
    "likes", "comments", "language_bucket", "matched_keyword", "matched_in",
    "also_matched", "tags", "description", "url",
]

FIELD_TAGS = {
    # profile
    "channel_id": OBSERVED, "title": OBSERVED, "handle": OBSERVED,
    "country": OBSERVED, "created": OBSERVED, "subscribers": OBSERVED,
    "total_views": OBSERVED, "video_count": OBSERVED,
    # per-video
    "video_id": OBSERVED, "published": OBSERVED, "duration_sec": OBSERVED,
    "views": OBSERVED, "likes": OBSERVED, "comments": OBSERVED,
    "tags": OBSERVED, "description": OBSERVED, "url": OBSERVED,
    "is_short": INFERRED, "language_bucket": INFERRED,
    "matched_keyword": INFERRED, "matched_in": INFERRED,
    "also_matched": INFERRED,
    # window metrics
    "video_count_in_window": CALCULATED, "median_views": CALCULATED,
    "avg_views": CALCULATED, "median_likes": CALCULATED,
    "median_comments": CALCULATED, "engagement_per_view_pct": CALCULATED,
    "views_to_subscriber_ratio": CALCULATED, "uploads_per_month": CALCULATED,
    "consistency_pct": CALCULATED, "viral_rate_pct": CALCULATED,
    "shorts_pct": CALCULATED, "telugu_share_pct": CALCULATED,
    "telugu_median_views": CALCULATED, "telugu_engagement_pct": CALCULATED,
    "telugu_affinity_index": CALCULATED, "language_mix": CALCULATED,
    # private YouTube Analytics
    "demographics": NOT_AVAILABLE, "watch_time": NOT_AVAILABLE,
    "impressions": NOT_AVAILABLE, "ctr": NOT_AVAILABLE,
    "traffic_sources": NOT_AVAILABLE,
}

# Calculated metrics whose inputs include an inferred field.
DEPENDS_ON_INFERRED = {
    "shorts_pct": "is_short",
    "telugu_share_pct": "language_bucket",
    "telugu_median_views": "language_bucket",
    "telugu_engagement_pct": "language_bucket",
    "telugu_affinity_index": "language_bucket",
    "language_mix": "language_bucket",
}

CAVEATS = [
    "Subscriber counts are rounded by YouTube above 1,000.",
    "Deleted and private videos are absent, so 'lifetime' means 'currently public'.",
    "Hidden likes and disabled comments appear as null, not zero.",
    "Language buckets come from keyword rules on title, description and tags; "
    "they are an interpretation, not a fact.",
    "is_short is a heuristic (duration of 3 minutes or less, YouTube's Shorts "
    "limit); the API does not say whether a video is a Short.",
]


class ApiError(Exception):
    pass


class QuotaExceeded(ApiError):
    pass


class YouTubeClient:
    """Thin YouTube Data API client with an on-disk JSON cache."""

    def __init__(self, api_key, refresh=False):
        self.session = requests.Session()
        # Header rather than ?key= so the key never appears in URLs or errors.
        self.session.headers["X-goog-api-key"] = api_key
        self.refresh = refresh
        self.units_used = 0
        self.cache_hits = 0

    def get(self, endpoint, params, cache_path, cost=1):
        if cache_path.exists() and not self.refresh:
            self.cache_hits += 1
            return json.loads(cache_path.read_text(encoding="utf-8"))
        resp = self.session.get(f"{API_BASE}/{endpoint}", params=params, timeout=30)
        self.units_used += cost  # 1 unit for list endpoints, 100 for search.list
        if resp.status_code != 200:
            raise self._error(resp, params)
        data = resp.json()
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                              encoding="utf-8")
        return data

    @staticmethod
    def _error(resp, params):
        try:
            err = resp.json().get("error", {})
        except ValueError:
            err = {}
        message = err.get("message", resp.text[:300])
        reasons = {e.get("reason") for e in err.get("errors", [])}
        if resp.status_code == 403 and reasons & {"quotaExceeded", "dailyLimitExceeded"}:
            return QuotaExceeded(message)
        if resp.status_code == 403 and (
                "accessNotConfigured" in reasons
                or "not been used in project" in message
                or "is disabled" in message):
            return ApiError(
                "YouTube Data API v3 is not enabled on the Google Cloud project "
                "that owns this key. Open console.cloud.google.com → APIs & "
                "Services → Library → YouTube Data API v3 → Enable, then re-run.")
        if resp.status_code == 403 and "blocked" in message.lower():
            return ApiError(
                "The API key's restrictions block this request. In Google Cloud "
                "→ Credentials, make sure the key allows YouTube Data API v3.")
        if resp.status_code == 400 and "API key not valid" in message:
            return ApiError(
                "The API key was rejected. Check that .env contains the full key "
                "as YOUTUBE_API_KEY=... with no spaces or quotes.")
        if resp.status_code == 400 and "forHandle" in params:
            return ApiError(
                f"YouTube rejected the handle '{params['forHandle']}'. Please give "
                "the exact @handle from the channel URL, or the UC… channel ID.")
        return ApiError(f"HTTP {resp.status_code}: {message}")


def load_api_key():
    load_dotenv(ROOT / ".env")
    key = (os.environ.get("YOUTUBE_API_KEY") or "").strip().strip("\"'")
    if not key:
        sys.exit(
            "No API key found. Open the .env file in the project folder and add "
            "a line:\n  YOUTUBE_API_KEY=your-key-here\nthen run this again.")
    return key


def parse_duration(iso):
    """ISO-8601 duration (PT1H2M3S) to seconds; P0D (live/premiere) gives 0."""
    m = re.fullmatch(
        r"P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", iso or "")
    if not m:
        return 0
    weeks, days, hours, minutes, seconds = (int(g) if g else 0 for g in m.groups())
    return ((weeks * 7 + days) * 24 + hours) * 3600 + minutes * 60 + seconds


_KEYWORD_PATTERNS = [
    (bucket, [(kw, re.compile(r"(?<!\w)" + re.escape(kw) + r"(?!\w)")) for kw in kws])
    for bucket, kws in BUCKETS
]


def classify(title, description, tags):
    """Return (language_bucket, matched_keyword, matched_in, also_matched).

    1. An industry keyword in the title wins outright (priority order).
    2. Otherwise the industry mentioned most often in description + tags wins;
       ties go to the earlier bucket. Creators stuff these fields with
       keywords for several industries, so a single mention is weak evidence.
    3. OTT/Other is a format, not an industry, so it only applies when no
       industry matched anywhere.
    also_matched lists the other industries that appeared, to flag weak labels.
    """
    title = title.lower()
    rest = "\n".join([description, " ".join(tags)]).lower()
    industries = [bp for bp in _KEYWORD_PATTERNS if bp[0] != FORMAT_BUCKET]

    counts = {}
    for bucket, patterns in industries:
        hits = {kw: len(p.findall(rest)) for kw, p in patterns}
        hits = {kw: n for kw, n in hits.items() if n}
        if hits:
            counts[bucket] = hits

    for bucket, patterns in industries:
        for keyword, pattern in patterns:
            if pattern.search(title):
                return bucket, keyword, "title", "|".join(b for b in counts if b != bucket)

    if counts:
        winner = max(counts, key=lambda b: sum(counts[b].values()))
        keyword = max(counts[winner], key=counts[winner].get)
        return winner, keyword, "description/tags", "|".join(
            b for b in counts if b != winner)

    for bucket, patterns in _KEYWORD_PATTERNS:
        if bucket != FORMAT_BUCKET:
            continue
        for keyword, pattern in patterns:
            if pattern.search(title):
                return bucket, keyword, "title", ""
            if pattern.search(rest):
                return bucket, keyword, "description/tags", ""
    return FALLBACK_BUCKET, "", "", ""


def slugify(name):
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "channel"


def to_int(value):
    return int(value) if value is not None else None


# ── fetch ────────────────────────────────────────────────────────────────────

def fetch_channel(client, identifier):
    parts = "snippet,statistics,contentDetails"
    if re.fullmatch(r"UC[\w-]{22}", identifier):
        params = {"part": parts, "id": identifier}
        cache = RAW_DIR / identifier / "channel.json"
    else:
        handle = identifier if identifier.startswith("@") else "@" + identifier
        params = {"part": parts, "forHandle": handle}
        cache = RAW_DIR / "_handles" / f"{handle[1:].lower()}.json"
    data = client.get("channels", params, cache)
    items = data.get("items") or []
    if not items:
        cache.unlink(missing_ok=True)  # don't cache a miss (typos, renamed handles)
        raise ApiError(
            f"No channel found for '{identifier}'. Please check the UC… ID or "
            "the exact @handle from the channel URL.")
    return items[0]


def fetch_video_ids(client, channel_id, uploads_playlist):
    ids, token, page = [], None, 0
    while True:
        params = {"part": "contentDetails", "playlistId": uploads_playlist,
                  "maxResults": 50}
        if token:
            params["pageToken"] = token
        cache = RAW_DIR / channel_id / f"playlist_items_{page:04d}.json"
        try:
            data = client.get("playlistItems", params, cache)
        except QuotaExceeded:
            raise
        except ApiError as e:
            if "404" in str(e) or "playlistNotFound" in str(e):
                return []  # channel exists but has no uploads playlist
            raise
        ids += [it["contentDetails"]["videoId"] for it in data.get("items", [])]
        token = data.get("nextPageToken")
        page += 1
        if not token:
            return ids


def fetch_videos(client, channel_id, video_ids):
    items = []
    for n, start in enumerate(range(0, len(video_ids), 50)):
        batch = video_ids[start:start + 50]
        params = {"part": "snippet,statistics,contentDetails", "id": ",".join(batch),
                  "maxResults": 50}
        cache = RAW_DIR / channel_id / f"videos_{n:04d}.json"
        items += client.get("videos", params, cache).get("items", [])
    return items


# ── transform ────────────────────────────────────────────────────────────────

def build_profile(channel):
    snippet, stats = channel["snippet"], channel.get("statistics", {})
    return {
        "channel_id": channel["id"],
        "title": snippet.get("title", ""),
        "handle": snippet.get("customUrl"),
        "country": snippet.get("country"),
        "created": snippet.get("publishedAt"),
        "subscribers": None if stats.get("hiddenSubscriberCount")
        else to_int(stats.get("subscriberCount")),
        "total_views": to_int(stats.get("viewCount")),
        "video_count": to_int(stats.get("videoCount")),
        "url": f"https://www.youtube.com/channel/{channel['id']}",
    }


def build_video_row(item):
    snippet, stats = item["snippet"], item.get("statistics", {})
    title = snippet.get("title", "")
    description = snippet.get("description", "")
    tags = snippet.get("tags", [])
    duration = parse_duration(item.get("contentDetails", {}).get("duration"))
    bucket, keyword, matched_in, also_matched = classify(title, description, tags)
    return {
        "video_id": item["id"],
        "title": title,
        "published": snippet.get("publishedAt"),
        "duration_sec": duration,
        "is_short": 0 < duration <= SHORT_MAX_SEC,
        "views": to_int(stats.get("viewCount")),
        "likes": to_int(stats.get("likeCount")),        # None when hidden
        "comments": to_int(stats.get("commentCount")),  # None when disabled
        "language_bucket": bucket,
        "matched_keyword": keyword,
        "matched_in": matched_in,
        "also_matched": also_matched,
        "tags": tags,
        "description": description[:500],
        "url": f"https://www.youtube.com/watch?v={item['id']}",
    }


# ── metrics ──────────────────────────────────────────────────────────────────

def _median(values):
    values = [v for v in values if v is not None]
    return median(values) if values else None


def _pct(part, whole):
    return round(part / whole * 100, 2) if whole else None


def _engagement_pct(videos):
    """Median of (likes + comments) / views × 100.

    Videos with hidden likes or zero views are skipped; disabled comments
    count as 0 comments.
    """
    rates = [(v["likes"] + (v["comments"] or 0)) / v["views"] * 100
             for v in videos if v["views"] and v["likes"] is not None]
    return round(median(rates), 3) if rates else None


def window_metrics(videos, subscribers):
    n = len(videos)
    views = [v["views"] for v in videos if v["views"] is not None]
    med_views = median(views) if views else None
    telugu = [v for v in videos if v["language_bucket"] == "Telugu"]
    telugu_med = _median(v["views"] for v in telugu)

    dates = sorted(datetime.fromisoformat(v["published"].replace("Z", "+00:00"))
                   for v in videos if v["published"])
    span_days = (dates[-1] - dates[0]).total_seconds() / 86400 if len(dates) > 1 else 0

    mix = {}
    for v in videos:
        mix[v["language_bucket"]] = mix.get(v["language_bucket"], 0) + 1

    return {
        "video_count_in_window": n,
        "median_views": med_views,
        "avg_views": round(mean(views), 1) if views else None,
        "median_likes": _median(v["likes"] for v in videos),
        "median_comments": _median(v["comments"] for v in videos),
        "engagement_per_view_pct": _engagement_pct(videos),
        "views_to_subscriber_ratio":
            round(med_views / subscribers, 4) if med_views is not None and subscribers else None,
        "uploads_per_month": round(n / (span_days / 30.44), 2) if span_days else None,
        "consistency_pct": _pct(
            sum(0.5 * med_views <= x <= 2 * med_views for x in views), len(views))
            if med_views is not None else None,
        "viral_rate_pct": _pct(sum(x > 2 * med_views for x in views), len(views))
            if med_views is not None else None,
        "shorts_pct": _pct(sum(v["is_short"] for v in videos), n),
        "telugu_share_pct": _pct(len(telugu), n),
        "telugu_median_views": telugu_med,
        "telugu_engagement_pct": _engagement_pct(telugu),
        "telugu_affinity_index":
            round(telugu_med / med_views, 3) if telugu_med is not None and med_views else None,
        "language_mix": dict(sorted(mix.items(), key=lambda kv: -kv[1])),
    }


# ── outputs ──────────────────────────────────────────────────────────────────

def fmt(value):
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        return f"{value:,.0f}" if abs(value) >= 1000 or float(value).is_integer() \
            else f"{value:,.2f}"
    return str(value)


def write_csv(path, videos):
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=VIDEO_FIELDS)
        writer.writeheader()
        for v in videos:
            row = dict(v, tags="|".join(v["tags"]))
            writer.writerow({k: "" if row[k] is None else row[k] for k in VIDEO_FIELDS})


def tag_label(field):
    tag = FIELD_TAGS[field]
    label = f"{TAG_ICON[tag]} {tag}"
    if field in DEPENDS_ON_INFERRED:
        label += f" (uses 🟠 {DEPENDS_ON_INFERRED[field]})"
    return label


def metric_table(windows, fields):
    names = [name for name, _ in WINDOWS]
    lines = ["| Metric | Last 10 | Last 20 | Last 30 | Lifetime | Tag |",
             "|---|---|---|---|---|---|"]
    for field in fields:
        cells = " | ".join(fmt(windows[name][field]) for name in names)
        lines.append(f"| {field} | {cells} | {tag_label(field)} |")
    return lines


def write_scorecard(path, profile, windows, generated_at):
    names = [name for name, _ in WINDOWS]
    buckets = list(windows["lifetime"]["language_mix"])
    out = [
        f"# Scorecard — {profile['title']}",
        "",
        f"Generated {generated_at}",
        "",
        "Tags: 🟢 OBSERVED (straight from the API) · 🔵 CALCULATED (arithmetic on "
        "observed values) · 🟠 INFERRED (our interpretation) · ⚪ NOT AVAILABLE "
        "(private YouTube Analytics)",
        "",
        "## PROFILE",
        "",
        "| Field | Value | Tag |",
        "|---|---|---|",
    ]
    for field in ["title", "handle", "channel_id", "country", "created",
                  "subscribers", "total_views", "video_count"]:
        out.append(f"| {field} | {fmt(profile[field])} | {tag_label(field)} |")
    out += [f"| url | {profile['url']} | {TAG_ICON[OBSERVED]} {OBSERVED} |", ""]

    out += ["## PERFORMANCE", ""]
    out += metric_table(windows, [
        "video_count_in_window", "median_views", "avg_views", "median_likes",
        "median_comments", "engagement_per_view_pct", "views_to_subscriber_ratio",
        "consistency_pct", "viral_rate_pct"])

    out += ["", "## CONTENT", ""]
    out += metric_table(windows, ["uploads_per_month", "shorts_pct"])
    out += ["", f"Language mix, videos per bucket — {tag_label('language_mix')}", "",
            "| Bucket | Last 10 | Last 20 | Last 30 | Lifetime |", "|---|---|---|---|---|"]
    for bucket in buckets:
        cells = " | ".join(str(windows[name]["language_mix"].get(bucket, 0))
                           for name in names)
        out.append(f"| {bucket} | {cells} |")

    out += ["", "## TELUGU PERFORMANCE", ""]
    out += metric_table(windows, [
        "telugu_share_pct", "telugu_median_views", "telugu_engagement_pct",
        "telugu_affinity_index"])
    out += ["", "telugu_affinity_index above 1 means Telugu videos out-perform the "
                "channel's typical video in that window."]

    na = f"{TAG_ICON[NOT_AVAILABLE]} {NOT_AVAILABLE}"
    out += [
        "", "## AUDIENCE SIGNALS", "",
        "| Signal | Value | Tag |", "|---|---|---|",
        f"| Comment language/script mix | not collected yet (phase 3) | {TAG_ICON[INFERRED]} {INFERRED} |",
        f"| Movie-specific vs generic comments | not collected yet (phase 3) | {TAG_ICON[INFERRED]} {INFERRED} |",
        f"| Repeat commenters | not collected yet (phase 3) | {TAG_ICON[INFERRED]} {INFERRED} |",
        f"| Audience demographics | — | {na} |",
        f"| Watch time | — | {na} |",
        f"| Impressions and CTR | — | {na} |",
        f"| Traffic sources | — | {na} |",
        "", "## Caveats", "",
    ]
    out += [f"- {c}" for c in CAVEATS]
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def print_summary(profile, windows):
    print(f"\n{profile['title']}  ({profile['channel_id']})")
    print(f"Subscribers: {fmt(profile['subscribers'])}   "
          f"Total views: {fmt(profile['total_views'])}   "
          f"Public videos: {fmt(profile['video_count'])}\n")
    names = [name for name, _ in WINDOWS]
    print(f"{'metric':<28}" + "".join(f"{n:>14}" for n in names))
    for field in windows["lifetime"]:
        if field == "language_mix":
            continue
        print(f"{field:<28}" + "".join(f"{fmt(windows[n][field]):>14}" for n in names))
    print("\nlanguage_mix")
    for name in names:
        mix = ", ".join(f"{b} {c}" for b, c in windows[name]["language_mix"].items())
        print(f"  {name:<10} {mix}")


# ── pipeline ─────────────────────────────────────────────────────────────────

def audit_channel(client, identifier):
    """Run the full audit for one channel; returns (summary, videos) or None."""
    channel = fetch_channel(client, identifier)
    channel_id = channel["id"]
    # A handle lookup is cached under _handles; keep a copy with the channel too.
    channel_cache = RAW_DIR / channel_id / "channel.json"
    if not channel_cache.exists():
        channel_cache.parent.mkdir(parents=True, exist_ok=True)
        channel_cache.write_text(
            json.dumps({"items": [channel]}, ensure_ascii=False, indent=1),
            encoding="utf-8")

    profile = build_profile(channel)
    uploads = channel.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
    video_ids = fetch_video_ids(client, channel_id, uploads) if uploads else []
    if not video_ids:
        print(f"{profile['title']} ({channel_id}) was found but has no public "
              "uploads. Nothing to audit.")
        return None

    videos = [build_video_row(it) for it in fetch_videos(client, channel_id, video_ids)]
    videos.sort(key=lambda v: v["published"] or "", reverse=True)

    windows = {name: window_metrics(videos[:size] if size else videos,
                                    profile["subscribers"])
               for name, size in WINDOWS}

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    summary = {
        "generated_at": generated_at,
        "profile": profile,
        "windows": windows,
        "field_tags": FIELD_TAGS,
        "calculated_from_inferred": DEPENDS_ON_INFERRED,
        "caveats": CAVEATS,
    }

    slug = slugify(profile["title"])
    out_dir = OUTPUT_DIR / slug  # one folder per channel
    out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(out_dir / f"{slug}_videos.csv", videos)
    (out_dir / f"{slug}_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_scorecard(out_dir / f"{slug}_scorecard.md", profile, windows, generated_at)

    print_summary(profile, windows)
    print(f"\nWrote {slug}_videos.csv, {slug}_summary.json and {slug}_scorecard.md "
          f"to output/{slug}/")
    return summary, videos


def main():
    parser = argparse.ArgumentParser(description="Audit one YouTube channel.")
    parser.add_argument("channel", nargs="?", default=DEFAULT_CHANNEL,
                        help="UC… channel ID or @handle (default: Mr Review Wala)")
    parser.add_argument("--refresh", action="store_true",
                        help="ignore cached API responses and re-fetch")
    args = parser.parse_args()

    client = YouTubeClient(load_api_key(), refresh=args.refresh)
    try:
        audit_channel(client, args.channel)
    except QuotaExceeded:
        sys.exit(
            f"Daily YouTube quota exceeded after {client.units_used} calls this "
            "run. Everything fetched so far is cached; re-run after midnight "
            "Pacific time and it will resume from the cache.")
    except ApiError as e:
        sys.exit(f"Stopped: {e}")
    finally:
        print(f"Quota used this run: {client.units_used} units "
              f"({client.cache_hits} responses served from cache)")


if __name__ == "__main__":
    main()
