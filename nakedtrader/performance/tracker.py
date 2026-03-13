"""
nakedtrader.performance.tracker — Performance tracking en trade logging.
"""

import json
import logging
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from nakedtrader.types import TradeRecord, PortfolioSnapshot

log = logging.getLogger(__name__)


class PerformanceTracker:
    """
    Houdt alle trades bij en berekent rendementstrends
    voor zowel de paper-modus als de live-modus.
    Slaat alles op als JSON.
    """

    def __init__(self, log_path: str = "data/trades.json"):
        self.log_path = Path(log_path)
        self.trades: list[TradeRecord] = []
        self.snapshots: list[PortfolioSnapshot] = []
        self._load()

    def _load(self):
        if self.log_path.exists():
            with open(self.log_path) as f:
                data = json.load(f)
            self.trades = [TradeRecord(**t) for t in data.get("trades", [])]
            self.snapshots = [PortfolioSnapshot(**s) for s in data.get("snapshots", [])]
            log.info(f"Logboek geladen: {len(self.trades)} trades")

    def _save(self):
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
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
