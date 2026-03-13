"""
nakedtrader.performance.paper_engine — Gesimuleerde trading engine.
"""

import random
import logging
from datetime import datetime
from typing import Optional

from nakedtrader.types import TradeRecord
from .tracker import PerformanceTracker

log = logging.getLogger(__name__)


class PaperEngine:
    """
    Simuleert trade-uitkomsten zonder echte orders.
    In paper-modus wordt de uitkomst bepaald door winkans + SL/TP niveaus.
    """

    def __init__(self, config, tracker: Optional[PerformanceTracker] = None):
        self.config = config
        self.tracker = tracker or PerformanceTracker(
            log_path=getattr(config, "trade_log_path", "data/trades.json")
        )
        self._trade_counter = len(self.tracker.trades)

    def _next_id(self) -> str:
        self._trade_counter += 1
        return f"T{self._trade_counter:04d}"

    def _simulate_outcome(
        self,
        win_probability: float,
        entry_price: float,
        stop_loss_pct: float,
        take_profit_pct: float,
    ) -> tuple[float, str]:
        won = random.random() < win_probability
        if won:
            exit_price = round(entry_price * (1 + take_profit_pct), 4)
            return exit_price, "win"
        else:
            exit_price = round(entry_price * (1 - stop_loss_pct), 4)
            return exit_price, "loss"

    def process_signal(self, signal, kelly_fraction: float, size_eur: float) -> TradeRecord:
        mode = "paper" if self.config.paper_mode else "live"
        trade_id = self._next_id()
        now = datetime.now().isoformat(timespec="seconds")

        entry = signal.current_price
        quantity = round(size_eur / entry, 6)

        if mode == "paper":
            exit_price, outcome = self._simulate_outcome(
                signal.win_probability,
                entry,
                self.config.stop_loss_pct,
                self.config.take_profit_pct,
            )
        else:
            exit_price = entry
            outcome = "open"

        pnl_pct = (exit_price - entry) / entry
        pnl_eur = round(pnl_pct * size_eur, 2)

        trade = TradeRecord(
            id=trade_id,
            timestamp=now,
            symbol=signal.symbol,
            broker=signal.broker,
            direction=signal.direction,
            mode=mode,
            entry_price=entry,
            exit_price=exit_price,
            quantity=quantity,
            size_eur=round(size_eur, 2),
            pnl_eur=pnl_eur,
            pnl_pct=round(pnl_pct * 100, 2),
            outcome=outcome,
            kelly_fraction=round(kelly_fraction, 4),
            win_probability=signal.win_probability,
            strategy_id=getattr(signal, "strategy_id", ""),
            notes=getattr(signal, "notes", ""),
        )

        snapshot = self.tracker.record_trade(trade, self.config.total_capital)

        status = "WIN" if outcome == "win" else ("VERLIES" if outcome == "loss" else "OPEN")
        pnl_sign = "+" if pnl_eur >= 0 else ""
        log.info(
            f"[{mode.upper()}] {trade_id} {signal.symbol} → {status}  "
            f"PnL: {pnl_sign}€{pnl_eur:.2f} ({pnl_sign}{trade.pnl_pct:.2f}%)  "
            f"Kapitaal: €{snapshot.capital:.2f}"
        )
        return trade
