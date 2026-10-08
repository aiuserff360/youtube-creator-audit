"""Write shareable, self-contained dashboard HTML files (no server needed).

    python src/export_dashboard.py            # every audited channel + docs/index.html
    python src/export_dashboard.py filmi_indian   # one channel only

Per channel: output/<slug>/<slug>_dashboard.html
All channels in one page with a picker: docs/index.html (served by GitHub Pages)
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dashboard_data import audited_channels, load_channel  # noqa: E402
from yt_channel_audit import OUTPUT_DIR  # noqa: E402

TEMPLATE = Path(__file__).resolve().parent / "dashboard_template.html"
PLACEHOLDER = "/*__AUDIT_DATA__*/"
LIVE_APP_URL = os.environ.get("LIVE_APP_URL", "")  # the hosted server, linked from the static page


def embed(data):
    # "</" would end the <script> block early if it appeared in a title.
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return (TEMPLATE.read_text(encoding="utf-8").replace(PLACEHOLDER, payload)
            .replace("/*__LIVE_APP_URL__*/", LIVE_APP_URL))


SITE_RECENT, SITE_TOP = 400, 100


def slim_for_site(data):
    """The all-channels page embeds dozens of channels; keep each one's recent + top videos only."""
    videos = sorted(data["videos"], key=lambda v: v["published"] or "", reverse=True)
    keep = {v["video_id"] for v in videos[:SITE_RECENT]}
    keep |= {v["video_id"] for v in sorted(videos, key=lambda v: v["views"] or 0, reverse=True)[:SITE_TOP]}
    slim = [v for v in videos if v["video_id"] in keep]
    capped = len(slim) < len(videos)
    return dict(data, videos=slim, videos_capped=capped,
                videos_total=len(videos), site_note=(f"This shared page holds the {SITE_RECENT} most recent and {SITE_TOP} most-viewed "
                                                      f"of {len(videos)} videos; the film check and top-10 cover those." if capped else ""))


def main():
    slugs = sys.argv[1:] or [c["slug"] for c in audited_channels()]
    if not slugs:
        sys.exit("No audited channels yet. Run src/yt_channel_audit.py first.")
    datasets = []
    for slug in slugs:
        data = load_channel(slug)
        datasets.append(data)
        out = OUTPUT_DIR / slug / f"{slug}_dashboard.html"
        out.write_text(embed(data), encoding="utf-8")
        print(f"wrote {out.relative_to(OUTPUT_DIR.parent)}")
    if not sys.argv[1:]:
        site = OUTPUT_DIR.parent / "docs" / "index.html"
        site.parent.mkdir(exist_ok=True)
        site.write_text(embed([slim_for_site(d) for d in datasets]), encoding="utf-8")
        print(f"wrote {site.relative_to(OUTPUT_DIR.parent)} ({len(datasets)} channels)")


if __name__ == "__main__":
    main()
