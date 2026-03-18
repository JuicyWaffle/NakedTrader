"""Broker portfolio endpoints (Kraken + IBKR live & paper data).

IBKR Gateway doet dagelijks een auto-restart (standaard 01:00 CET).
Tijdens die restart wordt de API-connectie verbroken. Dit module
detecteert stale connecties en reconnect automatisch.
"""

import json
import logging
import time
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import datetime as dt, timedelta
from pathlib import Path

from nakedtrader.utils import atomic_write_json

log = logging.getLogger(__name__)

# ── Persistent IBKR connections (live + paper) ──

_ibkr_lock = threading.Lock()
_ibkr_conns = {}          # port -> ib_async.IB instance
_ibkr_last_ok = {}        # port -> timestamp van laatste succesvolle query
_ibkr_backoff_until = {}  # port -> timestamp tot wanneer we niet opnieuw proberen

IBKR_LIVE_PORT = 4001
IBKR_PAPER_PORT = 4002
IBKR_TIMEOUT = 8          # harde timeout per query (seconden)
IBKR_RECONNECT_BACKOFF = 30   # na een mislukte connectie, wacht X seconden
IBKR_STALE_THRESHOLD = 300    # als laatste success >5 min geleden, forceer reconnect

_ibkr_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ibkr")


def _disconnect_ibkr(port):
    """Verwijder en sluit een IBKR-connectie voor een poort. Caller moet _ibkr_lock houden."""
    ib = _ibkr_conns.pop(port, None)
    if ib:
        try:
            ib.disconnect()
        except Exception:
            pass


def _get_ibkr(host, port, client_id):
    """Return een IBKR-connectie, met reconnect na Gateway restart."""
    with _ibkr_lock:
        # Backoff: niet opnieuw proberen als we recent gefaald zijn
        backoff = _ibkr_backoff_until.get(port, 0)
        if time.monotonic() < backoff:
            raise ConnectionError(f"IBKR port {port} in backoff tot {backoff:.0f}")

        ib = _ibkr_conns.get(port)

        # Forceer reconnect als connectie stale is (bv. na Gateway daily restart)
        last_ok = _ibkr_last_ok.get(port, 0)
        if ib is not None and last_ok > 0 and (time.monotonic() - last_ok) > IBKR_STALE_THRESHOLD:
            log.info("IBKR port %d: laatste success %.0fs geleden, forceer reconnect", port, time.monotonic() - last_ok)
            _disconnect_ibkr(port)
            ib = None

        if ib is not None and ib.isConnected():
            return ib

        # Oude connectie opruimen
        if ib is not None:
            _disconnect_ibkr(port)

        from ib_async import IB
        try:
            ib = IB()
            ib.connect(host, port, clientId=client_id, timeout=5)
            _ibkr_conns[port] = ib
            _ibkr_last_ok[port] = time.monotonic()
            _ibkr_backoff_until.pop(port, None)
            log.info("IBKR connectie hersteld (port %d, client %d)", port, client_id)
            return ib
        except Exception as e:
            _ibkr_backoff_until[port] = time.monotonic() + IBKR_RECONNECT_BACKOFF
            log.warning("IBKR port %d connect mislukt, backoff %ds: %s", port, IBKR_RECONNECT_BACKOFF, e)
            raise


def _query_ibkr_account_inner(host, port, client_id):
    """Query een IBKR account (draait in thread pool)."""
    ib = _get_ibkr(host, port, client_id)
    summary = ib.accountSummary()
    cash = 0.0
    total = 0.0
    for item in summary:
        if item.currency != "EUR":
            continue
        if item.tag == "TotalCashValue":
            cash = float(item.value)
        elif item.tag == "NetLiquidation":
            total = float(item.value)

    positions = []
    for p in ib.portfolio():
        positions.append({
            "symbol": p.contract.symbol, "asset": p.contract.symbol,
            "quantity": p.position, "price_eur": round(p.marketPrice, 2),
            "value_eur": round(p.marketValue, 2),
            "cost_price": round(p.averageCost, 2) if p.averageCost else None,
        })

    # Markeer success
    with _ibkr_lock:
        _ibkr_last_ok[port] = time.monotonic()

    return {
        "connected": True, "cash_eur": round(cash, 2),
        "positions": positions, "total_eur": round(total, 2),
    }


def _query_ibkr_account(host, port, client_id):
    """Query een IBKR account met harde timeout en auto-reconnect."""
    try:
        future = _ibkr_executor.submit(_query_ibkr_account_inner, host, port, client_id)
        return future.result(timeout=IBKR_TIMEOUT)
    except FuturesTimeout:
        log.warning("IBKR port %d timeout na %ds — stale connectie verwijderd", port, IBKR_TIMEOUT)
        with _ibkr_lock:
            _disconnect_ibkr(port)
            _ibkr_backoff_until[port] = time.monotonic() + IBKR_RECONNECT_BACKOFF
        return None
    except Exception as e:
        log.debug("IBKR port %d niet beschikbaar: %s", port, e)
        return None


def _query_ibkr_fills(host, port, client_id):
    """Haal IBKR fills/trades op met timeout-bescherming."""
    def _inner():
        ib = _get_ibkr(host, port, client_id)
        txs = []
        for fill in ib.fills():
            ex = fill.execution
            comm = fill.commissionReport
            txs.append({
                "id": ex.execId,
                "broker": "IBKR",
                "time": ex.time.timestamp() if hasattr(ex.time, 'timestamp') else 0,
                "pair": ex.contract.symbol if hasattr(ex, 'contract') else getattr(ex, 'symbol', '?'),
                "side": ex.side,
                "price": ex.price,
                "quantity": ex.shares,
                "cost": round(ex.price * ex.shares, 2),
                "fee": comm.commission if comm and comm.commission < 1e9 else 0,
            })
        seen_ids = {t["id"] for t in txs}
        for trade in ib.reqCompletedOrders(apiOnly=False):
            for fill in (trade.fills or []):
                ex = fill.execution
                if ex.execId in seen_ids:
                    continue
                comm = fill.commissionReport
                txs.append({
                    "id": ex.execId,
                    "broker": "IBKR",
                    "time": ex.time.timestamp() if hasattr(ex.time, 'timestamp') else 0,
                    "pair": trade.contract.symbol if trade.contract else '?',
                    "side": ex.side,
                    "price": ex.price,
                    "quantity": ex.shares,
                    "cost": round(ex.price * ex.shares, 2),
                    "fee": comm.commission if comm and comm.commission < 1e9 else 0,
                })
        with _ibkr_lock:
            _ibkr_last_ok[port] = time.monotonic()
        return txs

    try:
        future = _ibkr_executor.submit(_inner)
        return future.result(timeout=IBKR_TIMEOUT)
    except FuturesTimeout:
        log.warning("IBKR fills timeout na %ds", IBKR_TIMEOUT)
        with _ibkr_lock:
            _disconnect_ibkr(port)
            _ibkr_backoff_until[port] = time.monotonic() + IBKR_RECONNECT_BACKOFF
        return []
    except Exception as e:
        log.debug("IBKR fills niet beschikbaar: %s", e)
        return []


def _kraken_avg_cost(api):
    """Bereken gewogen gemiddelde aankoopprijs per asset uit Kraken trades.

    Loopt door alle BUY trades en berekent cost basis.  SELL trades
    verlagen het volume maar niet de gemiddelde prijs (FIFO-achtig).
    """
    # Map Kraken pair → base asset
    PAIR_TO_ASSET = {
        "XXBTZEUR": "XXBT", "XETHZEUR": "XETH", "SOLEUR": "SOL",
        "ADAEUR": "ADA", "USDCEUR": "USDC", "STRKEUR": "STRK", "PAXGEUR": "PAXG",
    }
    cost_basis = {}  # asset -> {"total_cost": float, "total_qty": float}
    try:
        resp = api.query_private("TradesHistory", {"trades": True})
        if resp.get("error"):
            return {}
        for _, t in resp["result"].get("trades", {}).items():
            pair = t.get("pair", "")
            # Normaliseer pair-naam
            asset = None
            for p, a in PAIR_TO_ASSET.items():
                if pair == p or pair.replace(".", "") == p:
                    asset = a
                    break
            if not asset:
                continue
            vol = float(t.get("vol", 0))
            cost = float(t.get("cost", 0))
            side = t.get("type", "")
            if asset not in cost_basis:
                cost_basis[asset] = {"total_cost": 0.0, "total_qty": 0.0}
            if side == "buy":
                cost_basis[asset]["total_cost"] += cost
                cost_basis[asset]["total_qty"] += vol
            elif side == "sell":
                cb = cost_basis[asset]
                if cb["total_qty"] > 0:
                    avg = cb["total_cost"] / cb["total_qty"]
                    sell_qty = min(vol, cb["total_qty"])
                    cb["total_cost"] -= avg * sell_qty
                    cb["total_qty"] -= sell_qty
    except Exception as e:
        log.debug("Kraken avg cost fout: %s", e)
        return {}

    result = {}
    for asset, cb in cost_basis.items():
        if cb["total_qty"] > 1e-8:
            result[asset] = cb["total_cost"] / cb["total_qty"]
    return result


def serve_broker_portfolio(handler, project_root, config):
    """GET /api/broker/portfolio — Kraken + IBKR live + IBKR paper."""
    try:
        import krakenex
        import os

        result = {
            "kraken": None,
            "ibkr_live": None,
            "ibkr": None,  # backward compat
            "total_eur": 0.0,
            "paper_mode": config.paper_mode,
        }

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

            # Bereken gemiddelde aankoopprijs per asset uit Kraken trades
            avg_cost = _kraken_avg_cost(api)

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
                cost_price = avg_cost.get(asset)
                positions.append({
                    "asset": asset, "symbol": pair or asset,
                    "quantity": round(volume, 8), "price_eur": round(price_eur, 2),
                    "value_eur": round(value_eur, 2),
                    "cost_price": round(cost_price, 2) if cost_price else None,
                })

            result["kraken"] = {
                "cash_eur": round(eur_cash, 2), "positions": positions,
                "total_eur": round(trade_balance, 2),
            }
            result["total_eur"] += trade_balance

        # ── IBKR Live (port 4001) ──
        live = _query_ibkr_account(config.ibkr_host, IBKR_LIVE_PORT, config.ibkr_client_id + 10)
        if live:
            result["ibkr_live"] = live
            result["total_eur"] += live["total_eur"]

        # Backward compat
        result["ibkr"] = live or {
            "connected": False, "cash_eur": 0.0, "positions": [], "total_eur": 0.0,
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
    """Append broker portfolio value snapshot to history file.

    Als een broker (Kraken/IBKR) tijdelijk niet bereikbaar is, wordt de
    laatst bekende waarde overgenomen (forward-fill) zodat de grafiek
    geen valse dalingen toont.
    """
    data_dir = Path(str(project_root / config.data_dir))
    history_path = data_dir / "broker_portfolio_history.json"

    kraken_eur = 0.0
    ibkr_eur = 0.0
    if portfolio_data.get("kraken"):
        kraken_eur = portfolio_data["kraken"].get("total_eur", 0.0)
    if portfolio_data.get("ibkr_live"):
        ibkr_eur = portfolio_data["ibkr_live"].get("total_eur", 0.0)

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

    # Forward-fill: als een broker 0 retourneert maar eerder data had,
    # neem de laatst bekende reële waarde over
    if history:
        prev = history[-1]
        if kraken_eur == 0 and prev.get("kraken_eur", 0) > 0:
            kraken_eur = prev["kraken_eur"]
        if ibkr_eur == 0 and prev.get("ibkr_eur", 0) > 0:
            ibkr_eur = prev["ibkr_eur"]

    entry = {
        "timestamp": dt.now().isoformat(timespec="seconds"),
        "total_eur": round(kraken_eur + ibkr_eur, 2),
        "kraken_eur": kraken_eur,
        "ibkr_eur": ibkr_eur,
    }

    history.append(entry)
    if len(history) > 50_000:
        history = history[-50_000:]
    atomic_write_json(history_path, history)


def serve_broker_transactions(handler, params, project_root, config):
    """GET /api/broker/transactions — Recent Kraken + IBKR trades."""
    try:
        import os
        txs = []

        # ── Kraken trades ──
        try:
            import krakenex
            api_key = os.getenv("KRAKEN_API_KEY", "").strip()
            api_secret = os.getenv("KRAKEN_API_SECRET", "").strip()
            if api_key and api_secret:
                api = krakenex.API(key=api_key, secret=api_secret)
                resp = api.query_private("TradesHistory", {"trades": True})
                if not resp.get("error"):
                    for tid, t in resp["result"].get("trades", {}).items():
                        pair = t.get("pair", "")
                        pretty = pair
                        for old, new in [("XXBT", "BTC"), ("XETH", "ETH"), ("ZEUR", "EUR"), ("ZUSD", "USD")]:
                            pretty = pretty.replace(old, new)
                        if "/" not in pretty:
                            for quote in ["EUR", "USD", "BTC", "ETH", "USDT"]:
                                if pretty.endswith(quote) and len(pretty) > len(quote):
                                    pretty = pretty[:-len(quote)] + "/" + quote
                                    break
                        txs.append({
                            "id": tid,
                            "broker": "Kraken",
                            "time": t.get("time", 0),
                            "pair": pretty,
                            "side": t.get("type", "").upper(),
                            "price": float(t.get("price", 0)),
                            "quantity": float(t.get("vol", 0)),
                            "cost": float(t.get("cost", 0)),
                            "fee": float(t.get("fee", 0)),
                        })
        except Exception as e:
            log.debug("Kraken trades fout: %s", e)

        # ── IBKR fills (met timeout-bescherming) ──
        ibkr_txs = _query_ibkr_fills(config.ibkr_host, IBKR_LIVE_PORT, config.ibkr_client_id + 10)
        txs.extend(ibkr_txs)

        # Sort by time descending, limit
        txs.sort(key=lambda t: t.get("time", 0), reverse=True)
        limit = int(params.get("limit", ["50"])[0])
        handler._json(200, {"transactions": txs[:limit]})
    except Exception as e:
        log.warning("Broker transactions fout: %s", e)
        handler._json(500, {"error": str(e)})


def _forward_fill_history(history):
    """Corrigeer bestaande history: vul missende broker-waarden aan
    met de laatst bekende reële waarde (forward-fill)."""
    last_kraken = 0.0
    last_ibkr = 0.0
    changed = False
    for s in history:
        k = s.get("kraken_eur", 0.0)
        i = s.get("ibkr_eur", 0.0)
        if k > 0:
            last_kraken = k
        elif last_kraken > 0:
            s["kraken_eur"] = last_kraken
            changed = True
        if i > 0:
            last_ibkr = i
        elif last_ibkr > 0:
            s["ibkr_eur"] = last_ibkr
            changed = True
        if changed:
            s["total_eur"] = round(s.get("kraken_eur", 0) + s.get("ibkr_eur", 0), 2)
    return history


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

        # Forward-fill missende broker-waarden in bestaande data
        history = _forward_fill_history(history)

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
