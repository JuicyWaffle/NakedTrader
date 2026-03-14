"""
nakedtrader.virtual_bank — Virtuele trading bots met echt marktdata.

Elke bot krijgt €100.000 startkapitaal en handelt op basis van echte
Kraken-marktprijzen via strategy.generate_signals().

State opslag: data/virtual_bank_state.json
Transacties: via VirtualLedger (data/ledger.json)
Portfolio history: data/virtual_bank_history.json (append-only snapshots)
"""

import json
import logging
import time
from datetime import datetime
from pathlib import Path

from nakedtrader.bots.data_feeds import _kraken_ticker
from nakedtrader.performance.ledger import VirtualLedger
from nakedtrader.utils import atomic_write_json

log = logging.getLogger(__name__)

STARTING_CAPITAL = 100_000.0

# Alle strategieën die via Kraken public API + andere gratis data handelen.
# trend-follow werkt nu ook via Kraken fallback (BTC als macro proxy).
KRAKEN_STRATEGIES = {
    "momentum", "mean-reversion", "breakout", "arbitrage",
    "trend-follow", "funding-contrarian", "cross-arb", "vol-regime",
}


class VirtualBankBot:
    """Virtuele trading bot — koopt en verkoopt op echte marktprijzen."""

    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.ledger = VirtualLedger(data_dir=data_dir)
        self.state_path = self.data_dir / "virtual_bank_state.json"
        self.history_path = self.data_dir / "virtual_bank_history.json"
        self.state = self._load_state()

    # ─── State persistence ─────────────────────────

    def _load_state(self) -> dict:
        """Laad state uit JSON of initialiseer met startkapitaal."""
        if self.state_path.exists():
            try:
                with open(self.state_path) as f:
                    return json.load(f)
            except (json.JSONDecodeError, KeyError):
                log.warning("Corrupt virtual_bank_state.json — herinitialiseren")

        return self._init_state()

    def _init_state(self) -> dict:
        """Initialiseer state voor alle strategieën."""
        from nakedtrader.bots.registry import STRATEGIES
        state = {}
        for sid in STRATEGIES:
            state[sid] = {"cash": STARTING_CAPITAL, "positions": {}}
        self._save_state(state)
        return state

    def _save_state(self, state: dict = None):
        """Schrijf state naar disk (atomic)."""
        if state is None:
            state = self.state
        atomic_write_json(self.state_path, state, indent=2)

    # ─── Portfolio history snapshots ─────────────────

    def _record_snapshot(self, live_prices: dict):
        """Append timestamped portfolio value per strategy to history file."""
        ts = datetime.now().isoformat(timespec="seconds")
        entry = {"timestamp": ts, "values": {}}

        for sid, bot_state in self.state.items():
            cash = bot_state.get("cash", STARTING_CAPITAL)
            total = cash
            for symbol, pos in bot_state.get("positions", {}).items():
                price = live_prices.get(symbol, pos.get("buy_price", 0))
                total += price * pos.get("quantity", 0)
            entry["values"][sid] = round(total, 2)

        # Load existing history
        history = []
        if self.history_path.exists():
            try:
                with open(self.history_path) as f:
                    history = json.load(f)
            except (json.JSONDecodeError, ValueError):
                history = []

        history.append(entry)

        # Keep max 50.000 entries (~70 dagen bij 2 min interval)
        if len(history) > 50_000:
            history = history[-50_000:]

        atomic_write_json(self.history_path, history)

    def _fetch_live_prices(self) -> dict:
        """Haal live prijzen op voor alle open posities."""
        symbols = set()
        for bot_state in self.state.values():
            symbols.update(bot_state.get("positions", {}).keys())

        prices = {}
        for symbol in symbols:
            try:
                price = _kraken_ticker(symbol)
                if price is not None:
                    prices[symbol] = price
            except Exception:
                pass
        return prices

    # ─── Main loop ─────────────────────────────────

    def run_loop(self, interval: int = 120):
        """Hoofdloop: elke N seconden signalen ophalen en posities checken."""
        from nakedtrader.bots.registry import STRATEGIES

        log.info(f"VirtualBankBot gestart — interval={interval}s, {len(KRAKEN_STRATEGIES)} actieve bots")
        for sid in KRAKEN_STRATEGIES:
            cash = self.state.get(sid, {}).get("cash", STARTING_CAPITAL)
            n_pos = len(self.state.get(sid, {}).get("positions", {}))
            log.info(f"  {sid}: cash=€{cash:,.2f}, open posities={n_pos}")

        while True:
            # Herlaad state van disk (pikt resets op via web API)
            self.state = self._load_state()

            for sid in KRAKEN_STRATEGIES:
                strategy = STRATEGIES.get(sid)
                if strategy is None:
                    continue

                # Ensure state exists for this strategy
                if sid not in self.state:
                    self.state[sid] = {"cash": STARTING_CAPITAL, "positions": {}}

                try:
                    self._check_exits(sid)
                except Exception as e:
                    log.warning(f"[{sid}] Exit check fout: {e}")

                try:
                    self._check_signals(sid, strategy)
                except Exception as e:
                    log.warning(f"[{sid}] Signal check fout: {e}")

            self._save_state()

            # Record portfolio value snapshot
            try:
                live_prices = self._fetch_live_prices()
                self._record_snapshot(live_prices)
            except Exception as e:
                log.warning(f"Snapshot fout: {e}")

            try:
                time.sleep(interval)
            except KeyboardInterrupt:
                log.info("VirtualBankBot gestopt.")
                break

    # ─── Signal handling ───────────────────────────

    def _check_signals(self, sid: str, strategy):
        """Roep strategy.generate_signals() aan, open posities als cash beschikbaar."""
        signals = strategy.generate_signals(aggressive=True)
        if not signals:
            return

        for signal in signals:
            # Skip als we al een positie in dit symbool hebben
            positions = self.state[sid].get("positions", {})
            if signal.symbol in positions:
                continue

            cash = self.state[sid]["cash"]
            size = cash * 0.20  # max 20% per trade
            if size < 50:
                log.debug(f"[{sid}] Te weinig cash (€{cash:.2f}) voor {signal.symbol}")
                continue

            price = signal.current_price
            if price <= 0:
                continue

            qty = size / price

            # Record BUY in ledger
            self.ledger.record_buy(
                strategy_id=sid,
                symbol=signal.symbol,
                price=price,
                quantity=qty,
                size_eur=size,
                timestamp=datetime.now().isoformat(timespec="seconds"),
            )

            # Update state
            self.state[sid]["cash"] -= size
            self.state[sid]["positions"][signal.symbol] = {
                "quantity": qty,
                "buy_price": price,
                "buy_time": datetime.now().isoformat(timespec="seconds"),
                "size_eur": size,
                "sl_pct": signal.expected_loss_pct,
                "tp_pct": signal.expected_win_pct,
            }

            log.info(
                f"[{sid}] BUY {signal.symbol} — "
                f"qty={qty:.6f} @ €{price:,.2f} = €{size:,.2f} "
                f"(cash: €{self.state[sid]['cash']:,.2f})"
            )

    def _check_exits(self, sid: str):
        """Check SL/TP op open posities, verkoop als drempel bereikt."""
        positions = self.state[sid].get("positions", {})
        if not positions:
            return

        for symbol, pos in list(positions.items()):
            try:
                current_price = _kraken_ticker(symbol)
            except Exception:
                continue

            if current_price is None:
                continue

            buy_price = pos["buy_price"]
            pnl_pct = (current_price - buy_price) / buy_price

            tp_pct = pos.get("tp_pct", 0.10)
            sl_pct = pos.get("sl_pct", 0.05)

            if pnl_pct >= tp_pct or pnl_pct <= -sl_pct:
                qty = pos["quantity"]
                sell_size = current_price * qty

                # Record SELL in ledger
                self.ledger.record_sell(
                    strategy_id=sid,
                    symbol=symbol,
                    price=current_price,
                    quantity=qty,
                    size_eur=sell_size,
                    buy_price=buy_price,
                    timestamp=datetime.now().isoformat(timespec="seconds"),
                )

                # Update state
                self.state[sid]["cash"] += sell_size
                del self.state[sid]["positions"][symbol]

                outcome = "TP" if pnl_pct >= tp_pct else "SL"
                pnl_eur = (current_price - buy_price) * qty
                log.info(
                    f"[{sid}] SELL {symbol} ({outcome}) — "
                    f"qty={qty:.6f} @ €{current_price:,.2f} "
                    f"PnL: €{pnl_eur:+,.2f} ({pnl_pct:+.2%}) "
                    f"(cash: €{self.state[sid]['cash']:,.2f})"
                )

    # ─── Portfolio API ─────────────────────────────

    def get_portfolio(self, strategy_id: str = None) -> dict:
        """Retourneer cash + open posities per bot."""
        from nakedtrader.bots.registry import STRATEGIES
        sids = [strategy_id] if strategy_id else list(STRATEGIES.keys())
        return _build_portfolio(self.state, sids)

    def reset_bot(self, strategy_id: str) -> dict:
        """Reset een bot naar startkapitaal, liquideer alle posities."""
        self.state[strategy_id] = {"cash": STARTING_CAPITAL, "positions": {}}
        self._save_state()
        log.info(f"[{strategy_id}] Reset naar €{STARTING_CAPITAL:,.2f}")
        return {"status": "ok", "strategy": strategy_id, "cash": STARTING_CAPITAL}


def _build_portfolio(state: dict, strategy_ids: list[str]) -> dict:
    """Bouw portfolio-overzicht met live Kraken prijzen.

    Gedeelde logica voor zowel VirtualBankBot.get_portfolio() als
    get_portfolio_from_state().
    """
    # Verzamel alle unieke symbolen en haal prijzen op in één keer
    all_symbols = set()
    for sid in strategy_ids:
        positions = state.get(sid, {}).get("positions", {})
        all_symbols.update(positions.keys())

    live_prices = {}
    for symbol in all_symbols:
        try:
            price = _kraken_ticker(symbol)
            if price is not None:
                live_prices[symbol] = price
        except Exception:
            pass

    result = {}
    for sid in strategy_ids:
        bot_state = state.get(sid, {"cash": STARTING_CAPITAL, "positions": {}})
        cash = bot_state["cash"]
        positions = bot_state.get("positions", {})

        pos_list = []
        total_value = cash
        for symbol, pos in positions.items():
            qty = pos["quantity"]
            buy_price = pos["buy_price"]
            current_price = live_prices.get(symbol, buy_price)
            market_value = current_price * qty
            pnl_eur = (current_price - buy_price) * qty
            pnl_pct = (current_price - buy_price) / buy_price * 100 if buy_price > 0 else 0.0

            pos_list.append({
                "symbol": symbol,
                "quantity": round(qty, 8),
                "buy_price": buy_price,
                "current_price": current_price,
                "market_value": round(market_value, 2),
                "pnl_eur": round(pnl_eur, 2),
                "pnl_pct": round(pnl_pct, 2),
                "buy_time": pos.get("buy_time", ""),
                "sl_pct": pos.get("sl_pct", 0.05),
                "tp_pct": pos.get("tp_pct", 0.10),
            })
            total_value += market_value

        result[sid] = {
            "cash": round(cash, 2),
            "positions": pos_list,
            "total_value": round(total_value, 2),
            "starting_capital": STARTING_CAPITAL,
            "pnl_total_eur": round(total_value - STARTING_CAPITAL, 2),
            "pnl_total_pct": round((total_value - STARTING_CAPITAL) / STARTING_CAPITAL * 100, 2),
        }

    return result


def get_portfolio_from_state(data_dir: str = "data") -> dict:
    """Portfolio uit state file met live Kraken prijzen."""
    state_path = Path(data_dir) / "virtual_bank_state.json"
    if not state_path.exists():
        return {}

    with open(state_path) as f:
        state = json.load(f)

    from nakedtrader.bots.registry import STRATEGIES
    return _build_portfolio(state, list(STRATEGIES.keys()))
