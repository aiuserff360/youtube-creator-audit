"""Dashboard server: type any @handle or channel ID, get the dashboard.

    python src/serve.py            # locally: opens http://127.0.0.1:8765

Hosted (e.g. Render): set PORT (the host does), YOUTUBE_API_KEY and, to keep
strangers from spending the quota, DASHBOARD_PASSWORD. The key never reaches
the browser; it only talks to this server. Audits are cached on disk, so
repeating a channel costs no quota while the cache lasts.
"""

import json
import os
import sys
import threading
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dashboard_data import (audited_channels, discovery_info, load_channel, slim_for_site,  # noqa: E402
                            slim_videos, tracked_films)
from yt_channel_audit import (OUTPUT_DIR, ApiError, QuotaExceeded, YouTubeClient,  # noqa: E402
                              audit_channel, load_api_key, slugify)

TEMPLATE = Path(__file__).resolve().parent / "dashboard_template.html"
HOSTED = "PORT" in os.environ          # a host like Render sets PORT
PORT = int(os.environ.get("PORT", 8765))
PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")
_lock = threading.Lock()  # one audit at a time


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):  # quieter log
        if "/api/" in (args[0] if args else ""):
            super().log_message(fmt, *args)

    def _json(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/":
            body = TEMPLATE.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif url.path == "/healthz":
            self._json(200, {"ok": True})
        elif url.path.startswith("/api/") and PASSWORD and self.headers.get("X-Dashboard-Key") != PASSWORD:
            self._json(401, {"error": "password required"})
        elif url.path == "/api/channels":
            self._json(200, audited_channels())
        elif url.path == "/api/compare":
            disc, films = discovery_info(), tracked_films()
            self._json(200, [slim_for_site(load_channel(c["slug"], disc), films) for c in audited_channels()])
        elif url.path == "/api/audit":
            self._audit(parse_qs(url.query))
        else:
            self._json(404, {"error": "not found"})

    def _audit(self, params):
        identifier = (params.get("channel") or [""])[0].strip()
        refresh = (params.get("refresh") or ["0"])[0] == "1"
        if not identifier:
            return self._json(400, {"error": "channel is required"})
        with _lock:
            client = YouTubeClient(API_KEY, refresh=refresh)
            try:
                result = audit_channel(client, identifier)
            except QuotaExceeded:
                return self._json(429, {"error": "Daily YouTube quota exceeded; try again after midnight Pacific."})
            except ApiError as e:
                return self._json(400, {"error": str(e)})
            except Exception as e:  # keep the server up
                return self._json(500, {"error": f"{type(e).__name__}: {e}"})
        if result is None:
            return self._json(400, {"error": "Channel found but it has no public uploads."})
        summary, videos = result
        slug = slugify(summary["profile"]["title"])
        audience_file = OUTPUT_DIR / slug / f"{slug}_audience.json"
        audience = json.loads(audience_file.read_text(encoding="utf-8")) if audience_file.exists() else None
        self._json(200, {"summary": summary, "videos": slim_videos(videos), "audience": audience,
                         "discovery": discovery_info().get(summary["profile"]["channel_id"]),
                         "quota_units_used": client.units_used})


def main():
    global API_KEY
    API_KEY = load_api_key()
    host = "0.0.0.0" if HOSTED else "127.0.0.1"
    server = ThreadingHTTPServer((host, PORT), Handler)
    print(f"Dashboard running at http://{host}:{PORT}  (Ctrl+C to stop)"
          + ("" if PASSWORD else "  [no DASHBOARD_PASSWORD set: anyone who can reach it can spend quota]"))
    if not HOSTED:
        threading.Timer(0.8, lambda: webbrowser.open(f"http://127.0.0.1:{PORT}")).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
