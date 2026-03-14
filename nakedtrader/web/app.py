#!/usr/bin/env python3
"""NakedTrader Web Dashboard — lightweight server (stdlib only).

Route handlers zijn gesplitst in nakedtrader.web.handlers.*:
  - broker.py     — Kraken + IBKR live portfolio
  - virtual_bank.py — Virtual Bank portfolio + history
  - orchestrator.py — Pause/resume + execution logs
"""

import json
import logging
import socket
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# Voeg project root toe zodat nakedtrader importeerbaar is
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from nakedtrader.config import Config, load_config
from nakedtrader.performance.store import PerformanceStore
from nakedtrader.web.handlers import broker as broker_h
from nakedtrader.web.handlers import virtual_bank as vb_h
from nakedtrader.web.handlers import orchestrator as orch_h

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
    return _store


class DashboardHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        # ── HTML pages ────────────────────────────────
        if path == "/":
            self._serve_file(PUBLIC_DIR / "index.html", "text/html")
        elif path == "/virtual-bank":
            self._serve_file(PUBLIC_DIR / "virtual-bank.html", "text/html")
        elif path == "/strategies":
            self._serve_file(PUBLIC_DIR / "strategies.html", "text/html")
        elif path == "/performance":
            self._serve_file(PUBLIC_DIR / "performance.html", "text/html")
        elif path == "/assets":
            self._serve_file(PUBLIC_DIR / "assets.html", "text/html")

        # ── Chart API ────────────────────────────────
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

        # ── Legacy / metadata ────────────────────────
        elif path == "/api/performance":
            self._json(200, _store.get_performance_json())

        elif path == "/api/bots":
            self._json(200, {"bots": _get_bot_list()})

        elif path == "/api/strategies":
            self._serve_strategies()

        elif path == "/api/adaptive":
            self._serve_adaptive()

        elif path == "/api/macro-risk":
            self._serve_macro_risk()

        elif path == "/api/market-data":
            self._serve_market_data()

        elif path == "/api/money-management":
            self._serve_money_management()

        # ── Virtual Bank ─────────────────────────────
        elif path == "/api/virtual-bank/transactions":
            strategy = params.get("strategy", [None])[0]
            limit = int(params.get("limit", ["20"])[0])
            txs = _store.get_recent_transactions(strategy_id=strategy, limit=limit)
            self._json(200, {"transactions": txs})

        elif path == "/api/virtual-bank/portfolio":
            vb_h.serve_portfolio(self, PROJECT_ROOT, _config)

        elif path == "/api/virtual-bank/history":
            vb_h.serve_history(self, params, PROJECT_ROOT, _config)

        # ── Broker ───────────────────────────────────
        elif path == "/api/broker/portfolio":
            broker_h.serve_broker_portfolio(self, PROJECT_ROOT, _config)

        elif path == "/api/broker/history":
            broker_h.serve_broker_history(self, params, PROJECT_ROOT, _config)

        # ── Performance analysis ─────────────────────
        elif path == "/api/performance/analysis":
            self._serve_performance_analysis()

        # ── Orchestrator ─────────────────────────────
        elif path == "/api/orchestrator/status":
            orch_h.serve_status(self, PROJECT_ROOT, _config)

        elif path == "/api/orchestrator/executions":
            orch_id = params.get("orch", ["A"])[0]
            limit = int(params.get("limit", ["50"])[0])
            orch_h.serve_executions(self, orch_id, limit, PROJECT_ROOT, _config)

        elif path == "/api/orchestrator/compare":
            period = params.get("period", [None])[0]
            since = params.get("since", [None])[0]
            orch_h.serve_compare(self, period, since, PROJECT_ROOT, _config)

        # ── Static assets ────────────────────────────
        elif path.startswith("/public/"):
            self._serve_file(PUBLIC_DIR / path[len("/public/"):])
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path == "/api/virtual-bank/reset":
            strategy = params.get("strategy", [None])[0]
            if not strategy:
                self._json(400, {"error": "missing strategy parameter"})
                return
            vb_h.do_reset(self, strategy, PROJECT_ROOT, _config)

        elif path == "/api/orchestrator/pause":
            pause_type = params.get("type", [None])[0]
            strategy_id = params.get("id", [None])[0]
            orch_h.do_pause(self, pause_type, strategy_id, True, PROJECT_ROOT, _config)

        elif path == "/api/orchestrator/resume":
            pause_type = params.get("type", [None])[0]
            strategy_id = params.get("id", [None])[0]
            orch_h.do_pause(self, pause_type, strategy_id, False, PROJECT_ROOT, _config)

        else:
            self._json(404, {"error": "not found"})

    # ── Inline handlers (klein genoeg om hier te houden) ──

    def _serve_strategies(self):
        try:
            from nakedtrader.bots.registry import STRATEGIES
            result = []
            for sid, strategy in STRATEGIES.items():
                m = strategy.meta
                result.append({
                    "id": m.id, "name": m.name, "color": m.color,
                    "description": m.description, "description_nl": m.description_nl,
                    "risk_level": m.risk_level, "risk_score": m.risk_score,
                    "expected_return_min": m.expected_return_min,
                    "expected_return_max": m.expected_return_max,
                    "markets": m.markets, "indicators": m.indicators,
                    "timeframe": m.timeframe, "broker": m.broker, "active": m.active,
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

    def _serve_macro_risk(self):
        try:
            from nakedtrader.risk.macro import MacroRiskEngine
            threshold = _config.macro_risk_veto_threshold if _config else 0.85
            engine = MacroRiskEngine(emergency_score=threshold)
            report = engine.evaluate()
            self._json(200, report.to_dict())
        except Exception as e:
            self._json(500, {"error": str(e)})

    def _serve_market_data(self):
        """Retourneer cross-exchange orderbook, funding rates, en marktdata."""
        try:
            from nakedtrader.bots.data_feeds import (
                _cross_exchange_orderbook, _binance_funding_rate,
                _kraken_futures_funding,
            )
            result = {}
            try:
                result["cross_orderbook"] = _cross_exchange_orderbook("BTC")
            except Exception as e:
                result["cross_orderbook"] = {"error": str(e)}
            try:
                result["btc_funding"] = _binance_funding_rate("BTCUSDT")
            except Exception as e:
                result["btc_funding"] = {"error": str(e)}
            try:
                result["eth_funding"] = _binance_funding_rate("ETHUSDT")
            except Exception as e:
                result["eth_funding"] = {"error": str(e)}
            try:
                result["kraken_futures"] = _kraken_futures_funding("PF_XBTUSD")
            except Exception as e:
                result["kraken_futures"] = {"error": str(e)}
            self._json(200, result)
        except Exception as e:
            self._json(500, {"error": str(e)})

    def _serve_money_management(self):
        try:
            from nakedtrader.money.risk import DEFAULT_RISK_BUDGETS, DEFAULT_SL_TP_OVERRIDES
            result = {
                "kelly_fraction": _config.kelly_fraction if _config else 0.5,
                "max_position_pct": _config.max_position_pct if _config else 0.20,
                "drawdown_limit_pct": _config.drawdown_limit_pct if _config else 0.15,
                "strategies": {},
            }
            for sid in DEFAULT_RISK_BUDGETS:
                budget = DEFAULT_RISK_BUDGETS[sid]
                sl_tp = DEFAULT_SL_TP_OVERRIDES.get(sid, {})
                result["strategies"][sid] = {
                    "risk_budget_pct": budget,
                    "stop_loss_pct": sl_tp.get("sl", _config.stop_loss_pct if _config else 0.05),
                    "take_profit_pct": sl_tp.get("tp", _config.take_profit_pct if _config else 0.10),
                }
            self._json(200, result)
        except Exception as e:
            self._json(500, {"error": str(e)})

    def _serve_performance_analysis(self):
        """Per-bot analyse: verwacht/effectief trades, rendement, kwalitatieve analyse."""
        try:
            from nakedtrader.performance.simulator import STRATEGY_PROFILES
            from nakedtrader.money.adaptive import AdaptiveEngine
            from datetime import datetime, timedelta

            state_path = str(PROJECT_ROOT / _config.state_path) if _config else "data/strategy_state.json"
            adaptive = AdaptiveEngine(state_path)

            daily_data = _store.get_daily(days=90)
            monthly_data = _store.get_monthly(months=3)
            yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

            bots = {}
            for sid, profile in STRATEGY_PROFILES.items():
                tpd_min, tpd_max = profile["trades_per_day"]
                expected_tpd = round((tpd_min + tpd_max) / 2, 1)

                daily_entries = daily_data.get("strategies", {}).get(sid, [])
                yesterday_entry = next((d for d in daily_entries if d.get("date") == yesterday), None)
                actual_yesterday = yesterday_entry["trades_count"] if yesterday_entry else 0
                ret_1d = yesterday_entry["pnl_pct"] if yesterday_entry else 0.0

                cutoff_1m = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
                ret_1m = sum(d.get("pnl_pct", 0.0) for d in daily_entries if d.get("date", "") >= cutoff_1m)

                cutoff_3m = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
                ret_3m = sum(d.get("pnl_pct", 0.0) for d in daily_entries if d.get("date", "") >= cutoff_3m)

                win_rate = adaptive.get_rolling_win_rate(sid)
                sharpe = adaptive.get_rolling_sharpe(sid)
                in_cooldown = adaptive.is_in_cooldown(sid)
                state = adaptive.get_state(sid)
                trades_count = len(state.recent_trades) if state else 0
                consecutive_losses = state.consecutive_losses if state else 0

                analysis = _generate_bot_analysis(
                    sid, profile, expected_tpd, actual_yesterday,
                    ret_1d, ret_1m, ret_3m,
                    win_rate, sharpe, in_cooldown,
                    trades_count, consecutive_losses,
                )

                bots[sid] = {
                    "name": profile["name"],
                    "timeframe": {"momentum": "1h", "mean-reversion": "15m", "breakout": "4h", "arbitrage": "tick", "trend-follow": "1d"}.get(sid, ""),
                    "broker": profile["broker"],
                    "expected_trades_per_day": expected_tpd,
                    "actual_trades_yesterday": actual_yesterday,
                    "return_1d": round(ret_1d, 3),
                    "return_1m": round(ret_1m, 3),
                    "return_3m": round(ret_3m, 3),
                    "win_rate": round(win_rate, 3),
                    "sharpe": round(sharpe, 2),
                    "in_cooldown": in_cooldown,
                    "trades_total": trades_count,
                    "consecutive_losses": consecutive_losses,
                    "analysis": analysis,
                }

            self._json(200, {"bots": bots, "date": yesterday})
        except Exception as e:
            log.warning("Performance analysis fout: %s", e)
            self._json(500, {"error": str(e)})

    # ── Utility methods ──────────────────────────────

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


def _generate_bot_analysis(
    sid, profile, expected_tpd, actual_yesterday,
    ret_1d, ret_1m, ret_3m,
    win_rate, sharpe, in_cooldown,
    trades_count, consecutive_losses,
) -> str:
    """Genereer kwalitatieve analyse tekst per bot."""
    lines = []
    bold_lines = []

    base_wr = profile["base_win_rate"]

    if trades_count < 10:
        lines.append(f"Nog weinig data ({trades_count} trades). Statistieken zijn indicatief.")
    elif actual_yesterday == 0:
        lines.append("Gisteren geen trades uitgevoerd.")
        if expected_tpd >= 5:
            bold_lines.append("**Signaaldrempels verlagen of marktdata-connectie controleren.**")
    elif actual_yesterday < expected_tpd * 0.3:
        lines.append(f"Activiteit laag: {actual_yesterday} trades vs {expected_tpd:.0f} verwacht.")
        bold_lines.append("**Controleer of signaalcondities niet te streng zijn ingesteld.**")
    elif actual_yesterday > expected_tpd * 2:
        lines.append(f"Ongewoon actief: {actual_yesterday} trades (verwacht ~{expected_tpd:.0f}).")
        bold_lines.append("**Overweeg strengere filters om overtrading te voorkomen.**")
    else:
        lines.append(f"Normale activiteit: {actual_yesterday} trades gisteren.")

    if trades_count >= 20:
        if win_rate >= base_wr + 0.10:
            lines.append(f"Win rate uitstekend ({win_rate:.0%} vs {base_wr:.0%} verwacht).")
        elif win_rate >= base_wr - 0.05:
            lines.append(f"Win rate binnen verwachting ({win_rate:.0%}).")
        elif win_rate >= base_wr - 0.15:
            lines.append(f"Win rate onder verwachting ({win_rate:.0%} vs {base_wr:.0%}).")
            bold_lines.append("**Heroverweeg entry-condities of verhoog minimale win-probability.**")
        else:
            lines.append(f"Win rate kritiek laag ({win_rate:.0%}).")
            bold_lines.append("**Bot pauzeren en strategie-parameters herzien.**")

    if trades_count >= 20:
        if sharpe >= 2.0:
            lines.append(f"Sharpe ratio sterk ({sharpe:.2f}).")
        elif sharpe >= 1.0:
            lines.append(f"Sharpe ratio voldoende ({sharpe:.2f}).")
        elif sharpe >= 0:
            lines.append(f"Sharpe ratio matig ({sharpe:.2f}).")
            bold_lines.append("**Overweeg striktere stop-losses om risico/rendement te verbeteren.**")
        else:
            lines.append(f"Sharpe ratio negatief ({sharpe:.2f}) — strategie verliest per risico-eenheid.")
            bold_lines.append("**Strategie herparametriseren of tijdelijk deactiveren.**")

    if ret_3m != 0:
        if ret_3m > 0 and ret_1m > 0:
            lines.append("Consistent positief rendement over 1 en 3 maanden.")
        elif ret_3m > 0 and ret_1m <= 0:
            lines.append("Langetermijn positief maar recente dip.")
            bold_lines.append("**Marktregime mogelijk veranderd — check macro-condities.**")
        elif ret_3m < 0 and ret_1m > 0:
            lines.append("Recent herstel na eerder verliesperiode.")
        elif ret_3m < 0 and ret_1m <= 0:
            lines.append("Aanhoudend verlies over 1 en 3 maanden.")
            bold_lines.append("**Overweeg positiegroottes te halveren tot trend keert.**")

    if in_cooldown:
        lines.append("Bot is momenteel in cooldown na opeenvolgende verliezen.")
    elif consecutive_losses >= 3:
        lines.append(f"Opgelet: {consecutive_losses} opeenvolgende verliezen.")

    text = " ".join(lines)
    if bold_lines:
        text += " " + " ".join(bold_lines)
    return text


def _get_bot_list():
    """Legacy /api/bots endpoint."""
    return [
        {"id": "momentum", "name": "Momentum Rider", "color": "#00ff88"},
        {"id": "mean-reversion", "name": "Mean Reversion", "color": "#0088ff"},
        {"id": "breakout", "name": "Breakout Hunter", "color": "#ff4466"},
        {"id": "trend-follow", "name": "Macro Trend", "color": "#ffaa44"},
        {"id": "arbitrage", "name": "Crypto Scalper", "color": "#aa44ff"},
    ]


class FastHTTPServer(ThreadingMixIn, HTTPServer):
    """Threaded HTTPServer — elke request in een aparte thread, blokkeert niet."""
    allow_reuse_address = True
    daemon_threads = True

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
