#!/usr/bin/env python3
"""NakedTrader Web Dashboard — lightweight server (stdlib only).

Endpoints:
  GET /                          → Dashboard (index.html)
  GET /strategies                → Strategy cards (strategies.html)

  GET /api/charts/intraday       → Trades van vandaag (per-trade detail)
  GET /api/charts/daily          → Dagelijkse samenvattingen (90 dagen)
  GET /api/charts/monthly        → Maandelijkse samenvattingen (36 maanden)

  GET /api/performance           → Legacy: overall performance
  GET /api/bots                  → Bot-lijst
  GET /api/strategies            → Strategy metadata
  GET /api/adaptive              → Adaptive engine metrics
"""

import json
import logging
import socket
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# Voeg project root toe zodat nakedtrader importeerbaar is
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from nakedtrader.config import Config, load_config
from nakedtrader.performance.store import PerformanceStore

log = logging.getLogger(__name__)

PORT = 8081
PUBLIC_DIR = Path(__file__).resolve().parent / "public"

# Globals — geïnitialiseerd in main()
_config: Config = None
_store: PerformanceStore = None


def init_store(config: Config) -> PerformanceStore:
    """Initialiseer PerformanceStore en backfill indien nodig."""
    global _config, _store
    _config = config
    data_dir = str(PROJECT_ROOT / config.data_dir)
    _store = PerformanceStore(data_dir=data_dir, starting_capital=config.total_capital)
    _store.backfill_history(months=36)
    return _store


class DashboardHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        # ── HTML pages ────────────────────────────────
        if path == "/":
            self._serve_file(PUBLIC_DIR / "index.html", "text/html")
        elif path == "/strategies":
            self._serve_file(PUBLIC_DIR / "strategies.html", "text/html")

        # ── New chart API endpoints ───────────────────
        elif path == "/api/charts/intraday":
            strategy = params.get("strategy", [None])[0]
            self._json(200, _store.get_intraday(strategy_id=strategy))

        elif path == "/api/charts/daily":
            days = int(params.get("days", ["90"])[0])
            strategy = params.get("strategy", [None])[0]
            self._json(200, _store.get_daily(days=days, strategy_id=strategy))

        elif path == "/api/charts/monthly":
            months = int(params.get("months", ["36"])[0])
            strategy = params.get("strategy", [None])[0]
            self._json(200, _store.get_monthly(months=months, strategy_id=strategy))

        # ── Legacy / existing API endpoints ───────────
        elif path == "/api/performance":
            self._json(200, _store.get_performance_json())

        elif path == "/api/bots":
            self._json(200, {"bots": _get_bot_list()})

        elif path == "/api/strategies":
            self._serve_strategies()

        elif path == "/api/adaptive":
            self._serve_adaptive()

        elif path.startswith("/public/"):
            self._serve_file(PUBLIC_DIR / path[len("/public/"):])
        else:
            self._json(404, {"error": "not found"})

    def _serve_strategies(self):
        try:
            from nakedtrader.bots.registry import STRATEGIES
            result = []
            for sid, strategy in STRATEGIES.items():
                m = strategy.meta
                result.append({
                    "id": m.id,
                    "name": m.name,
                    "color": m.color,
                    "description": m.description,
                    "description_nl": m.description_nl,
                    "risk_level": m.risk_level,
                    "risk_score": m.risk_score,
                    "expected_return_min": m.expected_return_min,
                    "expected_return_max": m.expected_return_max,
                    "markets": m.markets,
                    "indicators": m.indicators,
                    "timeframe": m.timeframe,
                    "broker": m.broker,
                    "active": m.active,
                })
            self._json(200, {"strategies": result})
        except Exception as e:
            self._json(500, {"error": str(e)})

    def _serve_adaptive(self):
        try:
            from nakedtrader.money.adaptive import AdaptiveEngine
            state_path = str(PROJECT_ROOT / _config.state_path) if _config else "data/strategy_state.json"
            engine = AdaptiveEngine(state_path)
            states = engine.get_all_states()
            self._json(200, {"adaptive": states})
        except Exception as e:
            self._json(500, {"error": str(e)})

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
                ".png": "image/png", ".svg": "image/svg+xml",
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
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        log.info(f"[web] {args[0]}")


def _get_bot_list():
    """Retourneer bot metadata voor legacy /api/bots endpoint."""
    return [
        {"id": "momentum", "name": "Momentum Rider", "color": "#00ff88"},
        {"id": "mean-reversion", "name": "Mean Reversion", "color": "#0088ff"},
        {"id": "breakout", "name": "Breakout Hunter", "color": "#ff4466"},
        {"id": "trend-follow", "name": "Macro Trend", "color": "#ffaa44"},
        {"id": "arbitrage", "name": "Crypto Scalper", "color": "#aa44ff"},
    ]


class FastHTTPServer(HTTPServer):
    """HTTPServer die getfqdn() overslaat (kan hangen op macOS)."""
    allow_reuse_address = True

    def server_bind(self):
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(self.server_address)
        self.server_address = self.socket.getsockname()
        host, port = self.server_address[:2]
        self.server_name = host or "localhost"
        self.server_port = port


def run_server(config: Config = None, port: int = PORT):
    """Start de webserver."""
    if config is None:
        config = load_config()

    init_store(config)

    server = FastHTTPServer(("0.0.0.0", port), DashboardHandler)
    log.info(f"NakedTrader Dashboard op http://localhost:{port}")
    print(f"NakedTrader Dashboard op http://localhost:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard gestopt.")
        server.server_close()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    run_server()
