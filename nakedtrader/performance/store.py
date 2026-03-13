"""
nakedtrader.performance.store — Multi-timeframe performance data store.

Bron van waarheid:
  - data/trades.json     — recente trades (lopende dag + niet-opgerolde)
  - data/daily/*.json    — dagrollups (incl. per-trade detail)
  - data/monthly/*.json  — maandrollups

Web API methoden: get_intraday(), get_daily(), get_monthly()
"""

import json
import logging
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path

from nakedtrader.types import TradeRecord, DailySummary, MonthlySummary
from .simulator import RealisticSimulator, STRATEGY_PROFILES
from .ledger import VirtualLedger
from .rollup import (
    compute_daily_summary,
    compute_monthly_summary,
    save_daily_rollup,
    save_monthly_rollup,
    atomic_write_json,
)

log = logging.getLogger(__name__)


class PerformanceStore:
    """Multi-timeframe performance data store."""

    def __init__(self, data_dir: str = "data", starting_capital: float = 10_000.0):
        self.data_dir = Path(data_dir)
        self.starting_capital = starting_capital
        self._trades_path = self.data_dir / "trades.json"

        # Directories aanmaken
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "daily").mkdir(exist_ok=True)
        (self.data_dir / "monthly").mkdir(exist_ok=True)

        # Virtual bank ledger
        self.ledger = VirtualLedger(data_dir=str(self.data_dir))

        # In-memory buffer voor intraday weergave
        self._today_trades: list[TradeRecord] = []
        self._today_date: str = datetime.now().strftime("%Y-%m-%d")

    # ─── Write API ─────────────────────────────────────────

    def record_trade(self, trade: TradeRecord) -> None:
        """Registreer een live/paper trade (append naar trades.json + geheugen)."""
        trade_date = trade.timestamp[:10]

        if trade_date > self._today_date:
            self._today_trades = [trade]
            self._today_date = trade_date
        elif trade_date == self._today_date:
            self._today_trades.append(trade)

        self._append_trade_to_file(trade)
        self.ledger.record_trade(trade)

    def _append_trade_to_file(self, trade: TradeRecord):
        """Append één trade aan trades.json."""
        trades_list: list[dict] = []
        if self._trades_path.exists():
            with open(self._trades_path) as f:
                data = json.load(f)
            trades_list = data.get("trades", [])

        trades_list.append(asdict(trade))

        atomic_write_json(self._trades_path, {
            "trades": trades_list,
            "last_updated": datetime.now().isoformat(),
        })

    def run_eod_rollup(self, date: str) -> None:
        """Rol alle trades van een datum op naar een daily summary bestand."""
        daily_path = self.data_dir / "daily" / f"{date}.json"
        if daily_path.exists():
            return

        trades_by_strategy = self._load_trades_for_date(date)
        if not trades_by_strategy:
            return

        strategies = {}
        for sid, trades in trades_by_strategy.items():
            prev_cum = self._prev_daily_cumulative(sid, date)
            summary = compute_daily_summary(
                date, sid, trades, prev_cum, self.starting_capital,
            )
            strategies[sid] = {"summary": summary, "trades": trades}

        save_daily_rollup(self.data_dir, date, strategies)
        log.info(f"EOD rollup voltooid: {date}")

    def run_eom_rollup(self, month: str) -> None:
        """Rol dagrollups van een maand op naar een monthly summary bestand."""
        monthly_path = self.data_dir / "monthly" / f"{month}.json"
        if monthly_path.exists():
            return

        daily_dir = self.data_dir / "daily"
        daily_files = sorted(daily_dir.glob(f"{month}-*.json"))

        per_strategy: dict[str, list[DailySummary]] = defaultdict(list)
        for fp in daily_files:
            with open(fp) as f:
                data = json.load(f)
            for sid, info in data.get("strategies", {}).items():
                per_strategy[sid].append(DailySummary(
                    date=info["date"],
                    strategy_id=sid,
                    trades_count=info["trades_count"],
                    wins=info["wins"],
                    losses=info["losses"],
                    pnl_eur=info["pnl_eur"],
                    pnl_pct=info["pnl_pct"],
                    cumulative_pct=info["cumulative_pct"],
                    win_rate=info["win_rate"],
                ))

        if not per_strategy:
            return

        strategies = {}
        for sid, dailies in per_strategy.items():
            prev_cum = self._prev_monthly_cumulative(sid, month)
            strategies[sid] = compute_monthly_summary(month, sid, dailies, prev_cum)

        save_monthly_rollup(self.data_dir, month, strategies)
        log.info(f"EOM rollup voltooid: {month}")

    # ─── Backfill ──────────────────────────────────────────

    def backfill_history(self, months: int = 36) -> None:
        """Genereer N maanden realistische historische data.

        Maakt daily/*.json en monthly/*.json bestanden aan.
        Slaat alleen recente trades op in trades.json (laatste 7 dagen).
        """
        daily_dir = self.data_dir / "daily"
        existing = list(daily_dir.glob("*.json"))
        if len(existing) > 30:
            log.info(f"Backfill: al {len(existing)} dagbestanden — overslaan")
            return

        log.info(f"Backfill: {months} maanden historische data genereren...")

        sim = RealisticSimulator(starting_capital=self.starting_capital)

        end_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        start_date = end_date - timedelta(days=months * 30)

        cumulative = {sid: 0.0 for sid in STRATEGY_PROFILES}
        # Elke strategie krijgt 1/5 van het totaalkapitaal
        n_strats = len(STRATEGY_PROFILES)
        per_strat_capital = self.starting_capital / n_strats

        current = start_date
        total_trades = 0
        total_days = 0
        recent_trades: list[dict] = []  # laatste 7 dagen voor trades.json
        recent_cutoff = end_date - timedelta(days=7)

        while current < end_date:
            date_str = current.strftime("%Y-%m-%d")
            day_data: dict = {}

            for sid in STRATEGY_PROFILES:
                # Gebruik vast startkapitaal per strategie (geen compounding)
                trades = sim.simulate_day(current, sid, per_strat_capital)
                if not trades:
                    continue

                summary = compute_daily_summary(
                    date_str, sid, trades, cumulative[sid], per_strat_capital,
                )
                day_data[sid] = {"summary": summary, "trades": trades}
                cumulative[sid] = summary.cumulative_pct
                total_trades += len(trades)

                # Bewaar recente trades voor trades.json
                if current >= recent_cutoff:
                    recent_trades.extend(asdict(t) for t in trades)

            if day_data:
                save_daily_rollup(self.data_dir, date_str, day_data)
                total_days += 1

            # Maandrollup aan einde van maand
            next_day = current + timedelta(days=1)
            if next_day.month != current.month:
                month_str = current.strftime("%Y-%m")
                self.run_eom_rollup(month_str)

            current = next_day

        # Sla recente trades op in trades.json
        atomic_write_json(self._trades_path, {
            "trades": recent_trades,
            "last_updated": datetime.now().isoformat(),
            "backfill": {"months": months, "total_trades": total_trades, "days": total_days},
        })

        log.info(
            f"Backfill compleet: {total_trades:,} trades over {total_days} dagen"
        )

    # ─── Read API (web endpoints) ──────────────────────────

    def get_intraday(self, strategy_id: str = None) -> dict:
        """Retourneer trades van vandaag (per-trade detail)."""
        today = datetime.now().strftime("%Y-%m-%d")

        trades = list(self._today_trades)

        # Fallback: lees uit trades.json als in-memory leeg is
        if not trades:
            by_strat = self._load_trades_for_date(today)
            for sid_trades in by_strat.values():
                trades.extend(sid_trades)

        if strategy_id:
            trades = [t for t in trades if t.strategy_id == strategy_id]

        trades.sort(key=lambda t: t.timestamp)

        return {
            "date": today,
            "trades": [
                {
                    "id": t.id,
                    "time": t.timestamp,
                    "symbol": t.symbol,
                    "strategy_id": t.strategy_id,
                    "pnl_pct": t.pnl_pct,
                    "pnl_eur": t.pnl_eur,
                    "outcome": t.outcome,
                    "size_eur": t.size_eur,
                }
                for t in trades
            ],
            "summary": self._quick_summary(trades),
        }

    def get_daily(self, days: int = 90, strategy_id: str = None) -> dict:
        """Retourneer dagelijkse samenvattingen voor de laatste N dagen."""
        daily_dir = self.data_dir / "daily"
        end = datetime.now()
        start = end - timedelta(days=days)

        strategies: dict[str, list[dict]] = defaultdict(list)

        current = start
        while current <= end:
            date_str = current.strftime("%Y-%m-%d")
            fp = daily_dir / f"{date_str}.json"

            if fp.exists():
                with open(fp) as f:
                    data = json.load(f)
                for sid, info in data.get("strategies", {}).items():
                    if strategy_id and sid != strategy_id:
                        continue
                    strategies[sid].append({
                        "date": info["date"],
                        "pnl_pct": info["pnl_pct"],
                        "pnl_eur": info["pnl_eur"],
                        "cumulative_pct": info["cumulative_pct"],
                        "trades_count": info["trades_count"],
                        "wins": info["wins"],
                        "losses": info["losses"],
                        "win_rate": info["win_rate"],
                    })

            current += timedelta(days=1)

        n_strats = len(STRATEGY_PROFILES)
        return {
            "days": days,
            "starting_balance": round(self.starting_capital / n_strats, 2),
            "strategies": dict(strategies),
        }

    def get_monthly(self, months: int = 36, strategy_id: str = None) -> dict:
        """Retourneer maandelijkse samenvattingen voor de laatste N maanden."""
        monthly_dir = self.data_dir / "monthly"

        files = sorted(monthly_dir.glob("*.json"))
        files = files[-months:] if len(files) > months else files

        strategies: dict[str, list[dict]] = defaultdict(list)

        for fp in files:
            with open(fp) as f:
                data = json.load(f)
            for sid, info in data.get("strategies", {}).items():
                if strategy_id and sid != strategy_id:
                    continue
                strategies[sid].append({
                    "month": info["month"],
                    "pnl_pct": info["pnl_pct"],
                    "pnl_eur": info["pnl_eur"],
                    "cumulative_pct": info["cumulative_pct"],
                    "trades_count": info["trades_count"],
                    "wins": info["wins"],
                    "losses": info["losses"],
                    "win_rate": info["win_rate"],
                })

        n_strats = len(STRATEGY_PROFILES)
        return {
            "months": months,
            "starting_balance": round(self.starting_capital / n_strats, 2),
            "strategies": dict(strategies),
        }

    def get_recent_transactions(self, strategy_id: str = None, limit: int = 20) -> list[dict]:
        """Retourneer recente transacties uit het virtual bank ledger."""
        return self.ledger.get_recent(strategy_id=strategy_id, limit=limit)

    def get_performance_json(self) -> dict:
        """Legacy backward compat: overzicht performance data."""
        daily = self.get_daily(days=90)
        monthly = self.get_monthly(months=36)

        total_trades = 0
        total_wins = 0
        total_pnl = 0.0

        for sid_data in daily["strategies"].values():
            for day in sid_data:
                total_trades += day["trades_count"]
                total_wins += day["wins"]
                total_pnl += day["pnl_eur"]

        return {
            "total_trades": total_trades,
            "total_wins": total_wins,
            "win_rate": round(total_wins / total_trades, 3) if total_trades else 0,
            "total_pnl_eur": round(total_pnl, 2),
            "total_pnl_pct": round(total_pnl / self.starting_capital * 100, 2),
            "daily": daily,
            "monthly": monthly,
        }

    # ─── Private helpers ───────────────────────────────────

    def _load_trades_for_date(self, date: str) -> dict[str, list[TradeRecord]]:
        """Laad trades per strategie uit trades.json voor een datum."""
        result: dict[str, list[TradeRecord]] = defaultdict(list)

        if not self._trades_path.exists():
            return result

        with open(self._trades_path) as f:
            data = json.load(f)

        fields = set(TradeRecord.__dataclass_fields__.keys())
        for t in data.get("trades", []):
            if t.get("timestamp", "")[:10] == date:
                record = TradeRecord(**{k: v for k, v in t.items() if k in fields})
                result[record.strategy_id].append(record)

        return result

    def _prev_daily_cumulative(self, strategy_id: str, date: str) -> float:
        """Haal cumulative_pct op van de vorige handelsdag."""
        d = datetime.strptime(date, "%Y-%m-%d")
        for offset in range(1, 8):
            prev = (d - timedelta(days=offset)).strftime("%Y-%m-%d")
            fp = self.data_dir / "daily" / f"{prev}.json"
            if fp.exists():
                with open(fp) as f:
                    data = json.load(f)
                info = data.get("strategies", {}).get(strategy_id, {})
                return info.get("cumulative_pct", 0.0)
        return 0.0

    def _prev_monthly_cumulative(self, strategy_id: str, month: str) -> float:
        """Haal cumulative_pct op van de vorige maand."""
        d = datetime.strptime(month + "-01", "%Y-%m-%d")
        prev = (d - timedelta(days=1)).strftime("%Y-%m")
        fp = self.data_dir / "monthly" / f"{prev}.json"
        if fp.exists():
            with open(fp) as f:
                data = json.load(f)
            info = data.get("strategies", {}).get(strategy_id, {})
            return info.get("cumulative_pct", 0.0)
        return 0.0

    def _quick_summary(self, trades: list) -> dict:
        """Snelle samenvatting van een lijst trades."""
        if not trades:
            return {"count": 0, "wins": 0, "losses": 0, "pnl_eur": 0, "pnl_pct": 0}

        wins = sum(1 for t in trades if t.outcome == "win")
        return {
            "count": len(trades),
            "wins": wins,
            "losses": len(trades) - wins,
            "pnl_eur": round(sum(t.pnl_eur for t in trades), 2),
            "pnl_pct": round(sum(t.pnl_pct for t in trades), 2),
            "win_rate": round(wins / len(trades), 3),
        }
