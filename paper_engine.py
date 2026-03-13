"""
paper_engine.py — Gesimuleerde trading engine

Werkt volledig zonder echte broker-verbinding.
Simuleert trade-uitkomsten op basis van de verwachte statistieken
van je signalen, slaat elke trade op in een JSON-logboek, en
berekent zowel fictief als reëel rendement.

Gebruik:
    engine = PaperEngine(config)
    engine.process_signal(signal)
    engine.print_summary()
"""

import json
import random
import logging
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# DATASTRUCTUREN
# ─────────────────────────────────────────────

@dataclass
class TradeRecord:
    """Één afgesloten trade — fictief of reëel."""
    id: str
    timestamp: str
    symbol: str
    broker: str
    direction: str
    mode: str                    # "paper" of "live"

    entry_price: float
    exit_price: float
    quantity: float
    size_eur: float

    pnl_eur: float               # winst/verlies in EUR
    pnl_pct: float               # winst/verlies in %
    outcome: str                 # "win" | "loss" | "open"

    kelly_fraction: float
    win_probability: float
    strategy_id: str = ""        # ID van de strategie die deze trade genereerde
    notes: str = ""


@dataclass
class PortfolioSnapshot:
    """Momentopname van het portfolio na een trade."""
    timestamp: str
    trade_id: str
    mode: str
    capital: float
    cumulative_pnl_eur: float
    cumulative_pnl_pct: float
    trade_count: int
    win_count: int
    win_rate: float


# ─────────────────────────────────────────────
# PERFORMANCE TRACKER
# ─────────────────────────────────────────────

class PerformanceTracker:
    """
    Houdt alle trades bij en berekent rendementstrends
    voor zowel de paper-modus als de live-modus.

    Slaat alles op als JSON zodat de trend bewaard blijft
    tussen herstart van de bot.
    """

    def __init__(self, log_path: str = "trades.json"):
        self.log_path = Path(log_path)
        self.trades: list[TradeRecord] = []
        self.snapshots: list[PortfolioSnapshot] = []
        self._load()

    def _load(self):
        """Laad bestaande trades uit het logboek."""
        if self.log_path.exists():
            with open(self.log_path) as f:
                data = json.load(f)
            self.trades = [TradeRecord(**t) for t in data.get("trades", [])]
            self.snapshots = [PortfolioSnapshot(**s) for s in data.get("snapshots", [])]
            log.info(f"Logboek geladen: {len(self.trades)} trades")

    def _save(self):
        """Bewaar alle trades naar JSON."""
        with open(self.log_path, "w") as f:
            json.dump(
                {
                    "trades": [asdict(t) for t in self.trades],
                    "snapshots": [asdict(s) for s in self.snapshots],
                    "last_updated": datetime.now().isoformat(),
                },
                f,
                indent=2,
                ensure_ascii=False,
            )

    def record_trade(self, trade: TradeRecord, starting_capital: float):
        """Voeg een afgesloten trade toe en maak een snapshot."""
        self.trades.append(trade)

        mode_trades = [t for t in self.trades if t.mode == trade.mode]
        wins = [t for t in mode_trades if t.outcome == "win"]
        cumulative_pnl = sum(t.pnl_eur for t in mode_trades)
        win_rate = len(wins) / len(mode_trades) if mode_trades else 0.0
        capital_now = starting_capital + cumulative_pnl

        snapshot = PortfolioSnapshot(
            timestamp=trade.timestamp,
            trade_id=trade.id,
            mode=trade.mode,
            capital=round(capital_now, 2),
            cumulative_pnl_eur=round(cumulative_pnl, 2),
            cumulative_pnl_pct=round(cumulative_pnl / starting_capital * 100, 2),
            trade_count=len(mode_trades),
            win_count=len(wins),
            win_rate=round(win_rate, 3),
        )
        self.snapshots.append(snapshot)
        self._save()
        return snapshot

    def get_snapshots(self, mode: str) -> list[PortfolioSnapshot]:
        return [s for s in self.snapshots if s.mode == mode]

    def get_trades(self, mode: str) -> list[TradeRecord]:
        return [t for t in self.trades if t.mode == mode]

    def summary(self, mode: str, starting_capital: float) -> dict:
        """Geef een samenvatting van alle trades in een bepaalde modus."""
        trades = self.get_trades(mode)
        if not trades:
            return {"mode": mode, "trades": 0}

        wins = [t for t in trades if t.outcome == "win"]
        losses = [t for t in trades if t.outcome == "loss"]
        total_pnl = sum(t.pnl_eur for t in trades)
        avg_win = sum(t.pnl_pct for t in wins) / len(wins) if wins else 0
        avg_loss = sum(t.pnl_pct for t in losses) / len(losses) if losses else 0

        return {
            "mode": mode,
            "trades": len(trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": f"{len(wins)/len(trades):.1%}",
            "total_pnl_eur": round(total_pnl, 2),
            "total_pnl_pct": round(total_pnl / starting_capital * 100, 2),
            "current_capital": round(starting_capital + total_pnl, 2),
            "avg_win_pct": round(avg_win, 2),
            "avg_loss_pct": round(avg_loss, 2),
        }

    def print_summary(self, starting_capital: float):
        """Print een overzicht naar de console."""
        print("\n" + "═" * 52)
        print("  RENDEMENT OVERZICHT")
        print("═" * 52)
        for mode in ["paper", "live"]:
            s = self.summary(mode, starting_capital)
            if s.get("trades", 0) == 0:
                print(f"\n  [{mode.upper()}]  Nog geen trades")
                continue
            pnl_sign = "+" if s["total_pnl_eur"] >= 0 else ""
            print(f"\n  [{mode.upper()}]")
            print(f"  Trades       : {s['trades']} ({s['wins']}W / {s['losses']}L)")
            print(f"  Winstpercentage: {s['win_rate']}")
            print(f"  Totaal PnL   : {pnl_sign}€{s['total_pnl_eur']:.2f}  ({pnl_sign}{s['total_pnl_pct']:.2f}%)")
            print(f"  Huidig kap.  : €{s['current_capital']:.2f}")
            print(f"  Gem. winst   : +{s['avg_win_pct']:.2f}%  |  Gem. verlies: {s['avg_loss_pct']:.2f}%")
        print("═" * 52 + "\n")


# ─────────────────────────────────────────────
# PAPER ENGINE
# ─────────────────────────────────────────────

class PaperEngine:
    """
    Simuleert trade-uitkomsten zonder echte orders.

    In paper-modus wordt de uitkomst van een trade bepaald door
    de opgegeven winkans + stop-loss/take-profit niveaus.
    Dit weerspiegelt realistisch wat de strategie zou doen.

    In live-modus worden echte broker-calls gedaan én gelogd.
    """

    def __init__(self, config, tracker: Optional[PerformanceTracker] = None):
        self.config = config
        self.tracker = tracker or PerformanceTracker(
            log_path=getattr(config, "trade_log_path", "trades.json")
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
        """
        Simuleer of een trade wint of verliest.
        Geeft (exit_price, outcome) terug.
        """
        won = random.random() < win_probability
        if won:
            exit_price = round(entry_price * (1 + take_profit_pct), 4)
            return exit_price, "win"
        else:
            exit_price = round(entry_price * (1 - stop_loss_pct), 4)
            return exit_price, "loss"

    def process_signal(self, signal, kelly_fraction: float, size_eur: float) -> TradeRecord:
        """
        Verwerk een signaal als paper trade:
        simuleer uitkomst → bereken PnL → log → snapshot.
        """
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
            # Live: exit_price wordt later ingevuld vanuit broker callback
            # Hier als placeholder — in productie koppel je de order-fill
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
            notes=signal.notes,
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
