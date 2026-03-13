#!/usr/bin/env python3
"""NakedTrader Web Dashboard — lightweight server (stdlib only)."""

import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from datetime import datetime

PORT = 8081
PROJECT_DIR = Path(__file__).resolve().parent.parent
PUBLIC_DIR = Path(__file__).resolve().parent / "public"
DATA_FILE = PROJECT_DIR / "bot_performance.json"

DEFAULT_BOTS = [
    {"id": "momentum", "name": "Momentum", "color": "#00ff88"},
    {"id": "mean-reversion", "name": "Mean Reversion", "color": "#0088ff"},
    {"id": "breakout", "name": "Breakout", "color": "#ff4466"},
    {"id": "trend-follow", "name": "Trend Follow", "color": "#ffaa44"},
    {"id": "arbitrage", "name": "Arbitrage", "color": "#aa44ff"},
]


def load_data():
    if DATA_FILE.is_file():
        with open(DATA_FILE) as f:
            return json.load(f)
    # Seed met default bots en 14 dagen demo data
    import random
    random.seed(42)
    today = datetime.now()
    entries = []
    values = {b["id"]: 0.0 for b in DEFAULT_BOTS}
    for i in range(14):
        from datetime import timedelta
        date = (today - timedelta(days=13 - i)).strftime("%Y-%m-%d")
        for bot_id in values:
            drift = random.uniform(-0.8, 1.2)
            values[bot_id] = round(values[bot_id] + drift, 2)
        entry = {"date": date}
        entry.update({k: v for k, v in values.items()})
        entries.append(entry)
    data = {"bots": DEFAULT_BOTS, "entries": entries}
    save_data(data)
    return data


def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


class DashboardHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        if self.path == "/":
            self._serve_file(PUBLIC_DIR / "index.html", "text/html")
        elif self.path == "/api/performance":
            data = load_data()
            self._json(200, data)
        elif self.path == "/api/bots":
            data = load_data()
            self._json(200, {"bots": data["bots"]})
        elif self.path.startswith("/public/"):
            self._serve_file(PUBLIC_DIR / self.path[len("/public/"):])
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/api/performance":
            self._add_entry()
        elif self.path == "/api/bots":
            self._update_bot()
        else:
            self._json(404, {"error": "not found"})

    def _add_entry(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            date = body.get("date", datetime.now().strftime("%Y-%m-%d"))

            data = load_data()
            bot_ids = [b["id"] for b in data["bots"]]

            entry = {"date": date}
            for bot_id in bot_ids:
                entry[bot_id] = body.get(bot_id, 0.0)

            # Vervang als datum al bestaat
            data["entries"] = [e for e in data["entries"] if e["date"] != date]
            data["entries"].append(entry)
            data["entries"].sort(key=lambda e: e["date"])

            save_data(data)
            self._json(201, {"ok": True, "entry": entry})
        except Exception as e:
            self._json(400, {"error": str(e)})

    def _update_bot(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            bot_id = body.get("id", "").strip()
            name = body.get("name", "").strip()
            color = body.get("color", "#ffffff").strip()

            if not bot_id or not name:
                self._json(400, {"error": "id en name zijn verplicht"})
                return

            data = load_data()
            existing = next((b for b in data["bots"] if b["id"] == bot_id), None)
            if existing:
                existing["name"] = name
                existing["color"] = color
            else:
                data["bots"].append({"id": bot_id, "name": name, "color": color})
                # Voeg 0.0 toe aan alle bestaande entries
                for entry in data["entries"]:
                    if bot_id not in entry:
                        entry[bot_id] = 0.0

            save_data(data)
            self._json(200, {"ok": True, "bot": {"id": bot_id, "name": name, "color": color}})
        except Exception as e:
            self._json(400, {"error": str(e)})

    def _serve_file(self, filepath, content_type=None):
        filepath = Path(filepath)
        if not filepath.is_file():
            self._json(404, {"error": "not found"})
            return
        if content_type is None:
            ext = filepath.suffix.lower()
            content_type = {
                ".html": "text/html", ".css": "text/css",
                ".js": "application/javascript", ".json": "application/json",
            }.get(ext, "application/octet-stream")
        data = filepath.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        print(f"[dashboard] {args[0]}")


if __name__ == "__main__":
    import socket
    HTTPServer.allow_reuse_address = True
    server = HTTPServer(("0.0.0.0", PORT), DashboardHandler)
    print(f"NakedTrader Dashboard op http://localhost:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard gestopt.")
        server.server_close()
