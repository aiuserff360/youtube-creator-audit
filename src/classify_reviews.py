"""Classify review transcripts with Claude: sentiment, verdict, language, promo.

    python src/classify_reviews.py --film Mirai --dry-run   # what would run, rough cost, no API calls
    python src/classify_reviews.py --film Mirai --limit 30  # classify up to 30 transcripts
    python src/classify_reviews.py --channel filmi_indian   # all cached transcripts for a channel's videos

Reads data/transcripts/<video_id>.json (from transcripts.py), sends each
transcript to Claude with a fixed JSON schema, and writes
output/reviews/<name>_sentiment.csv (+ .json). Results are cached per video in
data/reviews/. Every field here is INFERRED and must be validated against a
hand-labelled sample (~30 videos) before the numbers are reported.

Needs ANTHROPIC_API_KEY in .env (same file as the YouTube key).
"""

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))

from yt_channel_audit import OUTPUT_DIR, ROOT, slugify  # noqa: E402
from transcripts import TRANSCRIPT_DIR  # noqa: E402

REVIEW_DIR = ROOT / "data" / "reviews"
MODEL = "claude-opus-5-5"
PRICE_IN, PRICE_OUT = 4.00, 20.00  # USD per million tokens, claude-opus-5-5 (Anthropic list price)

SCHEMA = {
    "type": "object",
    "properties": {
        "film_discussed": {"type": "string", "description": "Main film or series reviewed, as named; empty if none"},
        "is_review": {"type": "boolean", "description": "True if the video evaluates the film (not just news, trailer talk or a listicle)"},
        "language_spoken": {"type": "string", "enum": ["Telugu", "Hindi", "Tamil", "Malayalam", "Kannada", "English", "Mixed", "Other"]},
        "sentiment": {"type": "string", "enum": ["positive", "negative", "mixed", "not_a_review"]},
        "recommendation": {"type": "string", "enum": ["recommend", "skip", "conditional", "unclear"]},
        "verdict_quote": {"type": "string", "description": "The reviewer's own verdict, quoted or closely paraphrased, max 30 words, original language"},
        "key_praise": {"type": "array", "items": {"type": "string"}, "description": "Up to 3 short points"},
        "key_criticism": {"type": "array", "items": {"type": "string"}, "description": "Up to 3 short points"},
        "sponsor_or_promo": {"type": "boolean", "description": "True if the video contains paid-promotion, sponsor or affiliate language"},
        "sponsor_note": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1, "description": "How sure you are of the sentiment, given caption quality"},
    },
    "required": ["film_discussed", "is_review", "language_spoken", "sentiment", "recommendation", "verdict_quote",
                 "key_praise", "key_criticism", "sponsor_or_promo", "sponsor_note", "confidence"],
    "additionalProperties": False,
}

SYSTEM = """You analyse transcripts of Indian YouTube movie-review videos for a film-marketing team.
The transcript is usually an auto-generated caption track and may be in Telugu, Hindi, Hinglish, Tamil or
English, with recognition errors and no punctuation. Judge the reviewer's overall verdict on the film from
what they actually say; do not infer sentiment from the film's reputation. "mixed" means genuinely balanced
praise and criticism; a review that is mostly positive with small complaints is "positive". If the video is
not a review of a film (news, box-office talk, trailer reaction, listicle), set is_review=false and
sentiment="not_a_review". Keep quotes and points short. Answer only with the JSON object."""


def load_transcripts(args):
    rows = []
    if args.channel:
        path = OUTPUT_DIR / args.channel / f"{args.channel}_videos.csv"
        with path.open(encoding="utf-8-sig", newline="") as f:
            wanted = {r["video_id"]: r for r in csv.DictReader(f)}
    else:
        path = OUTPUT_DIR / "discovery" / "review_videos.csv"
        with path.open(encoding="utf-8-sig", newline="") as f:
            wanted = {r["video_id"]: r for r in csv.DictReader(f) if args.film.lower() in r["films"].lower()}
    for vid, meta in wanted.items():
        cache = TRANSCRIPT_DIR / f"{vid}.json"
        if not cache.exists():
            continue
        t = json.loads(cache.read_text(encoding="utf-8"))
        if t.get("transcript_status") == "ok" and t.get("text"):
            rows.append({"video_id": vid, "title": meta.get("title", ""), "channel_title": meta.get("channel_title", args.channel or ""),
                         "views": meta.get("views"), "url": meta.get("url"), "caption_language": t.get("language"),
                         "caption_generated": t.get("is_generated"), "transcript": t["text"]})
    return rows


def classify(client, row):
    import anthropic
    user = (f"Video title: {row['title']}\nChannel: {row['channel_title']}\n"
            f"Caption track language code: {row['caption_language']} (auto-generated: {row['caption_generated']})\n\n"
            f"Transcript:\n{row['transcript']}")
    try:
        resp = client.messages.create(
            model=MODEL, max_tokens=2000, system=SYSTEM,
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": user}],
        )
    except anthropic.AuthenticationError:
        sys.exit("Anthropic rejected the API key. Check ANTHROPIC_API_KEY in .env.")
    except anthropic.RateLimitError as e:
        return {"error": f"rate_limited: {e.message}"}, None
    except anthropic.APIStatusError as e:
        return {"error": f"api_error_{e.status_code}: {e.message}"}, None
    except anthropic.APIConnectionError:
        return {"error": "connection_error"}, None
    if resp.stop_reason == "refusal":
        return {"error": "refused"}, resp.usage
    text = next((b.text for b in resp.content if b.type == "text"), "")
    try:
        return json.loads(text), resp.usage
    except json.JSONDecodeError:
        return {"error": "bad_json", "raw": text[:200]}, resp.usage


def main():
    ap = argparse.ArgumentParser(description="Classify review transcripts with Claude.")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--film")
    src.add_argument("--channel")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--yes", action="store_true", help="skip the cost confirmation")
    ap.add_argument("--refresh", action="store_true", help="re-classify cached videos")
    args = ap.parse_args()

    rows = load_transcripts(args)
    if args.limit:
        rows = rows[:args.limit]
    if not rows:
        sys.exit("No transcripts found. Run src/transcripts.py first.")
    name = slugify(args.film or args.channel)
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    pending = [r for r in rows if args.refresh or not (REVIEW_DIR / f"{r['video_id']}.json").exists()]

    chars = sum(len(r["transcript"]) for r in pending)
    est_in = chars / 3.5 + 400 * len(pending)   # rough: Indic/romanised text runs ~3.5 chars per token
    est_out = 250 * len(pending)
    est_cost = est_in / 1e6 * PRICE_IN + est_out / 1e6 * PRICE_OUT
    print(f"{len(rows)} transcripts ({len(pending)} not yet classified). Model {MODEL}. "
          f"Rough estimate: {est_in / 1000:.0f}K input tokens, ~${est_cost:.2f}.")
    if args.dry_run:
        for r in rows:
            print(f"  {len(r['transcript']):>7} chars  {r['caption_language']:<6} {r['channel_title'][:24]:<25} {r['title'][:50]}")
        return

    load_dotenv(ROOT / ".env")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is missing. Add a line ANTHROPIC_API_KEY=sk-ant-... to .env and re-run.")
    if pending and not args.yes and input("Proceed? [y/N] ").strip().lower() != "y":
        return

    import anthropic
    client = anthropic.Anthropic()
    tokens_in = tokens_out = 0
    results = []
    for i, r in enumerate(rows, 1):
        cache = REVIEW_DIR / f"{r['video_id']}.json"
        if cache.exists() and not args.refresh:
            out = json.loads(cache.read_text(encoding="utf-8"))
        else:
            out, usage = classify(client, r)
            if usage:
                tokens_in += usage.input_tokens
                tokens_out += usage.output_tokens
            out = {k: v for k, v in r.items() if k != "transcript"} | out | {
                "classified_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "model": MODEL}
            if "error" not in out or not out["error"].startswith(("rate_limited", "connection")):
                cache.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        results.append(out)
        tag = out.get("sentiment", out.get("error", "?"))
        print(f"{i:>3}/{len(rows)} {tag:<13} {out['channel_title'][:24]:<25} {out['title'][:48]}")

    fields = ["video_id", "channel_title", "title", "views", "url", "caption_language", "film_discussed", "is_review",
              "language_spoken", "sentiment", "recommendation", "verdict_quote", "key_praise", "key_criticism",
              "sponsor_or_promo", "sponsor_note", "confidence", "error", "model", "classified_at"]
    out_csv = OUTPUT_DIR / "reviews" / f"{name}_sentiment.csv"
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for o in results:
            w.writerow({k: ("|".join(v) if isinstance(v, list) else v) for k, v in o.items()})
    (out_csv.with_suffix(".json")).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")

    counts = {}
    for o in results:
        counts[o.get("sentiment", "error")] = counts.get(o.get("sentiment", "error"), 0) + 1
    cost = tokens_in / 1e6 * PRICE_IN + tokens_out / 1e6 * PRICE_OUT
    print(f"\nSentiment counts: {counts}")
    print(f"Tokens this run: {tokens_in:,} in / {tokens_out:,} out ≈ ${cost:.2f}. "
          f"Wrote {out_csv.relative_to(ROOT)} (all values INFERRED; validate on a hand-labelled sample).")


if __name__ == "__main__":
    main()
