"""Broker portfolio endpoints (Kraken + IBKR live data)."""

import json
import logging
from datetime import datetime as dt, timedelta
from pathlib import Path

from nakedtrader.utils import atomic_write_json

log = logging.getLogger(__name__)


def serve_broker_portfolio(handler, project_root, config):
    """GET /api/broker/portfolio — Kraken + IBKR live balances & positions."""
    try:
        import krakenex
        import os

        result = {"kraken": None, "ibkr": None, "total_eur": 0.0}

        # ── Kraken ──
        api_key = os.getenv("KRAKEN_API_KEY", "").strip()
        api_secret = os.getenv("KRAKEN_API_SECRET", "").strip()
        if api_key and api_secret:
            api = krakenex.API(key=api_key, secret=api_secret)

            bal_resp = api.query_private("Balance")
            if bal_resp.get("error"):
                raise RuntimeError(f"Kraken balance: {bal_resp['error']}")
            raw_balances = {a: float(v) for a, v in bal_resp["result"].items() if float(v) > 1e-6}

            tb_resp = api.query_private("TradeBalance", {"asset": "ZEUR"})
            trade_balance = 0.0
            if not tb_resp.get("error"):
                trade_balance = float(tb_resp["result"].get("eb", 0))

            positions = []
            eur_cash = raw_balances.get("ZEUR", 0.0) + raw_balances.get("ZUSD", 0.0) * 0.92

            ASSET_PAIRS = {
                "XXBT": "XXBTZEUR", "XETH": "XETHZEUR", "SOL": "SOLEUR",
                "ADA": "ADAEUR", "USDC": "USDCEUR", "STRK": "STRKEUR", "PAXG": "PAXGEUR",
            }

            for asset, volume in raw_balances.items():
                if asset in ("ZEUR", "ZUSD") or volume < 1e-6:
                    continue
                pair = ASSET_PAIRS.get(asset)
                price_eur = 0.0
                if pair:
                    try:
                        t_resp = api.query_public("Ticker", {"pair": pair})
                        if not t_resp.get("error"):
                            key = list(t_resp["result"].keys())[0]
                            price_eur = float(t_resp["result"][key]["c"][0])
                    except Exception:
                        pass

                value_eur = volume * price_eur
                if value_eur < 0.10:
                    continue
                positions.append({
                    "asset": asset, "symbol": pair or asset,
                    "quantity": round(volume, 8), "price_eur": round(price_eur, 2),
                    "value_eur": round(value_eur, 2),
                })

            result["kraken"] = {
                "cash_eur": round(eur_cash, 2), "positions": positions,
                "total_eur": round(trade_balance, 2),
            }
            result["total_eur"] += trade_balance

        # ── IBKR ──
        try:
            from ib_async import IB
            ib = IB()
            ib.connect(config.ibkr_host, config.ibkr_port, clientId=config.ibkr_client_id + 10, timeout=5)
            summary = ib.accountSummary()
            ibkr_cash = 0.0
            ibkr_total = 0.0
            for item in summary:
                if item.currency != "EUR":
                    continue
                if item.tag == "TotalCashValue":
                    ibkr_cash = float(item.value)
                elif item.tag == "NetLiquidation":
                    ibkr_total = float(item.value)

            ibkr_positions = []
            for p in ib.portfolio():
                ibkr_positions.append({
                    "symbol": p.contract.symbol, "asset": p.contract.symbol,
                    "quantity": p.position, "price_eur": round(p.marketPrice, 2),
                    "value_eur": round(p.marketValue, 2),
                })
            ib.disconnect()
            result["ibkr"] = {
                "connected": True, "cash_eur": round(ibkr_cash, 2),
                "positions": ibkr_positions, "total_eur": round(ibkr_total, 2),
            }
            result["total_eur"] += ibkr_total
        except Exception as ibkr_err:
            log.debug("IBKR niet beschikbaar: %s", ibkr_err)
            result["ibkr"] = {
                "connected": False, "cash_eur": 0.0, "positions": [],
                "total_eur": 0.0, "note": str(ibkr_err),
            }

        try:
            _record_broker_snapshot(result, project_root, config)
        except Exception as snap_err:
            log.debug("Broker snapshot fout: %s", snap_err)

        handler._json(200, result)
    except Exception as e:
        log.warning("Broker portfolio fout: %s", e)
        handler._json(500, {"error": str(e)})


def _record_broker_snapshot(portfolio_data, project_root, config):
    """Append broker portfolio value snapshot to history file."""
    data_dir = Path(str(project_root / config.data_dir))
    history_path = data_dir / "broker_portfolio_history.json"

    entry = {
        "timestamp": dt.now().isoformat(timespec="seconds"),
        "total_eur": portfolio_data.get("total_eur", 0.0),
        "kraken_eur": 0.0, "ibkr_eur": 0.0,
    }
    if portfolio_data.get("kraken"):
        entry["kraken_eur"] = portfolio_data["kraken"].get("total_eur", 0.0)
    if portfolio_data.get("ibkr"):
        entry["ibkr_eur"] = portfolio_data["ibkr"].get("total_eur", 0.0)

    history = []
    if history_path.exists():
        try:
            with open(history_path) as f:
                history = json.load(f)
        except (json.JSONDecodeError, ValueError):
            history = []

    if history:
        last_ts = history[-1].get("timestamp", "")
        if last_ts and last_ts >= (dt.now().replace(second=0)).isoformat(timespec="seconds"):
            return

    history.append(entry)
    if len(history) > 50_000:
        history = history[-50_000:]
    atomic_write_json(history_path, history)


def serve_broker_history(handler, params, project_root, config):
    """GET /api/broker/history — Broker portfolio value history."""
    try:
        data_dir = Path(str(project_root / config.data_dir))
        history_path = data_dir / "broker_portfolio_history.json"

        if not history_path.exists():
            handler._json(200, {"snapshots": []})
            return

        with open(history_path) as f:
            history = json.load(f)

        hours = int(params.get("hours", ["0"])[0])
        if hours > 0:
            cutoff = (dt.now() - timedelta(hours=hours)).isoformat()
            history = [s for s in history if s.get("timestamp", "") >= cutoff]

        max_points = int(params.get("max_points", ["500"])[0])
        if len(history) > max_points:
            step = len(history) / max_points
            history = [history[int(i * step)] for i in range(max_points)]

        handler._json(200, {"snapshots": history})
    except Exception as e:
        handler._json(500, {"error": str(e)})
