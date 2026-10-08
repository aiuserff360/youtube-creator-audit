"""Audience signals from a public comment sample (official API, commentThreads.list).

    python src/comments.py filmi_indian            # one audited channel (slug under output/)
    python src/comments.py --all                   # every audited channel
    python src/comments.py filmi_indian --telugu   # sample only Telugu-labelled videos

Samples up to 20 top comments on each of the channel's 10 most recent videos
that have comments (<= 200 comments, ~10 quota units per channel), caches the
raw responses under data/raw/<channel_id>/, and writes
output/<slug>/<slug>_audience.json, also merged into <slug>_summary.json as
"audience_signals". Everything here is INFERRED: the script a comment is
written in is a rough proxy for the viewer's language, not a demographic.
"""

import argparse
import csv
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from yt_channel_audit import (OUTPUT_DIR, RAW_DIR, ApiError, QuotaExceeded, YouTubeClient,  # noqa: E402
                              load_api_key)

VIDEOS_PER_CHANNEL, COMMENTS_PER_VIDEO = 10, 20

SCRIPTS = [  # Unicode blocks → script label
    ("Telugu", 0x0C00, 0x0C7F), ("Devanagari", 0x0900, 0x097F), ("Tamil", 0x0B80, 0x0BFF),
    ("Kannada", 0x0C80, 0x0CFF), ("Malayalam", 0x0D00, 0x0D7F), ("Gurmukhi", 0x0A00, 0x0A7F),
    ("Bengali", 0x0980, 0x09FF), ("Gujarati", 0x0A80, 0x0AFF),
]
# Common romanised words; a comment in Latin letters is tagged by whichever list it hits more.
ROMAN_TELUGU = {"chala", "bagundi", "bagundhi", "baga", "anna", "andi", "undi", "ledu", "ante", "kuda", "ela",
                "enti", "emi", "cheppu", "chusa", "chudali", "sir", "garu", "manchi", "ra", "bro", "thopu", "kada",
                "avunu", "nenu", "meeru", "cinema", "picha", "mama", "ayipoindi", "akka"}
ROMAN_HINDI = {"hai", "hain", "nahi", "nhi", "bhai", "kya", "acha", "achha", "accha", "bahut", "bohot", "yaar",
               "yar", "tha", "thi", "kar", "karo", "dekh", "dekho", "dekha", "mast", "sahi", "kyu", "kyun", "aur",
               "mujhe", "tum", "aap", "wala", "wali", "bhot", "matlab", "jhakaas", "bakwas", "bakwaas"}
FILM_WORDS = {"movie", "film", "cinema", "review", "climax", "interval", "scene", "scenes", "story", "hero", "heroine",
              "director", "villain", "acting", "screenplay", "bgm", "music", "twist", "ending", "part", "sequel",
              "blockbuster", "flop", "hit", "boring", "rating", "watch", "theatre", "theater", "ott", "trailer",
              "సినిమా", "మూవీ", "రివ్యూ", "फिल्म", "मूवी", "रिव्यू", "कहानी", "क्लाइमेक्स"}


def script_of(text):
    counts = Counter()
    for ch in text:
        o = ord(ch)
        if ch.isascii() and ch.isalpha():
            counts["Latin"] += 1
            continue
        for name, lo, hi in SCRIPTS:
            if lo <= o <= hi:
                counts[name] += 1
                break
    if not counts:
        return "Emoji/other"
    return counts.most_common(1)[0][0]


def roman_hint(text):
    words = set(re.findall(r"[a-z]+", text.lower()))
    te, hi = len(words & ROMAN_TELUGU), len(words & ROMAN_HINDI)
    if te == hi == 0:
        return "unclear"
    return "Telugu (romanised)" if te > hi else "Hindi (romanised)" if hi > te else "unclear"


def is_movie_specific(text, title_words):
    words = set(re.findall(r"\w+", text.lower()))
    return bool(words & FILM_WORDS) or bool(words & title_words)


def sample_channel(client, slug, telugu_only=False):
    folder = OUTPUT_DIR / slug
    summary = json.loads((folder / f"{slug}_summary.json").read_text(encoding="utf-8"))
    channel_id = summary["profile"]["channel_id"]
    with (folder / f"{slug}_videos.csv").open(encoding="utf-8-sig", newline="") as f:
        videos = list(csv.DictReader(f))
    videos = [v for v in videos if v["comments"] not in ("", "0")]
    if telugu_only:
        videos = [v for v in videos if v["language_bucket"] == "Telugu"]
    videos = videos[:VIDEOS_PER_CHANNEL]  # CSV is newest first

    comments, per_video_authors = [], []
    for v in videos:
        cache = RAW_DIR / channel_id / f"comments_{v['video_id']}.json"
        try:
            data = client.get("commentThreads", {"part": "snippet", "videoId": v["video_id"], "maxResults": COMMENTS_PER_VIDEO,
                                                 "order": "relevance", "textFormat": "plainText"}, cache)
        except QuotaExceeded:
            raise
        except ApiError as e:
            if "commentsDisabled" in str(e) or "403" in str(e):
                continue  # comments turned off since the audit
            raise
        title_words = {w for w in re.findall(r"\w+", v["title"].lower()) if len(w) > 3}
        authors = set()
        for it in data.get("items", []):
            s = it["snippet"]["topLevelComment"]["snippet"]
            text = s.get("textOriginal") or s.get("textDisplay") or ""
            author = (s.get("authorChannelId") or {}).get("value") or s.get("authorDisplayName")
            authors.add(author)
            comments.append({"video_id": v["video_id"], "video_bucket": v["language_bucket"], "author": author,
                             "likes": s.get("likeCount", 0), "script": script_of(text),
                             "roman_hint": roman_hint(text) if script_of(text) == "Latin" else None,
                             "movie_specific": is_movie_specific(text, title_words), "chars": len(text)})
        per_video_authors.append(authors)

    n = len(comments)
    if not n:
        return None
    script_mix = Counter(c["script"] for c in comments)
    roman = Counter(c["roman_hint"] for c in comments if c["roman_hint"])
    all_authors = Counter(a for s in per_video_authors for a in s)
    repeat = sum(1 for a, k in all_authors.items() if k >= 2)
    # Best single guess at the comment language: scripts, plus romanised hints folded in.
    lang = Counter()
    for c in comments:
        if c["script"] == "Telugu" or c["roman_hint"] == "Telugu (romanised)":
            lang["Telugu"] += 1
        elif c["script"] == "Devanagari" or c["roman_hint"] == "Hindi (romanised)":
            lang["Hindi"] += 1
        elif c["script"] in ("Tamil", "Kannada", "Malayalam", "Bengali", "Gujarati", "Gurmukhi"):
            lang[c["script"]] += 1
        elif c["script"] == "Latin":
            lang["English/unclear"] += 1
        else:
            lang["Emoji/other"] += 1
    pct = lambda k: round(k / n * 100, 1)  # noqa: E731
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tag": "INFERRED",
        "videos_sampled": len(per_video_authors), "comments_sampled": n,
        "telugu_videos_only": telugu_only,
        "script_mix_pct": {k: pct(v) for k, v in script_mix.most_common()},
        "romanised_hint_pct_of_latin": {k: round(v / max(1, script_mix.get("Latin", 0)) * 100, 1) for k, v in roman.most_common()},
        "comment_language_pct": {k: pct(v) for k, v in lang.most_common()},
        "telugu_comment_pct": pct(lang.get("Telugu", 0)),
        "hindi_comment_pct": pct(lang.get("Hindi", 0)),
        "movie_specific_pct": pct(sum(c["movie_specific"] for c in comments)),
        "unique_commenters": len(all_authors),
        "repeat_commenters": repeat,
        "repeat_commenter_pct": round(repeat / max(1, len(all_authors)) * 100, 1),
        "method": "Top comments (relevance order) on the most recent videos with comments; script detected from Unicode "
                  "blocks, romanised Telugu/Hindi from small word lists, movie-specific from film vocabulary or title words.",
    }


def main():
    ap = argparse.ArgumentParser(description="Sample comments for audience signals.")
    ap.add_argument("slug", nargs="?")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--telugu", action="store_true", help="only Telugu-labelled videos")
    args = ap.parse_args()
    slugs = [p.name for p in OUTPUT_DIR.iterdir() if (p / f"{p.name}_summary.json").exists()] if args.all else [args.slug]
    if not slugs or slugs == [None]:
        sys.exit("Give a channel slug (folder name under output/) or --all.")

    client = YouTubeClient(load_api_key())
    try:
        for slug in sorted(slugs):
            result = sample_channel(client, slug, args.telugu)
            if not result:
                print(f"{slug:<34} no comments available")
                continue
            folder = OUTPUT_DIR / slug
            (folder / f"{slug}_audience.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            sfile = folder / f"{slug}_summary.json"
            summary = json.loads(sfile.read_text(encoding="utf-8"))
            summary["audience_signals"] = result
            sfile.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"{slug:<34} {result['comments_sampled']:>4} comments  Telugu {result['telugu_comment_pct']:>5.1f}%  "
                  f"Hindi {result['hindi_comment_pct']:>5.1f}%  movie-specific {result['movie_specific_pct']:>5.1f}%  "
                  f"repeat {result['repeat_commenter_pct']:>4.1f}%")
    except QuotaExceeded:
        print("Daily quota exceeded; finished channels are saved. Re-run tomorrow.")
    finally:
        print(f"Quota used: {client.units_used} units ({client.cache_hits} from cache)")


if __name__ == "__main__":
    main()
