"""
nakedtrader.performance.rollup — EOD / EOM rollup functies.

Aggregeert trades naar dagelijks en maandelijks overzicht per strategie.
"""

import json
import os
import logging
import tempfile
from dataclasses import asdict
from pathlib import Path

from nakedtrader.types import TradeRecord, DailySummary, MonthlySummary

log = logging.getLogger(__name__)


# ─── Berekeningen ─────────────────────────────────────────

def compute_daily_summary(
    date: str,
    strategy_id: str,
    trades: list[TradeRecord],
    prev_cumulative_pct: float = 0.0,
    starting_capital: float = 10_000.0,
) -> DailySummary:
    """Bereken dagoverzicht voor één strategie op één datum.

    pnl_pct is het portfolio-niveau rendement (pnl_eur / startkapitaal).
    """
    wins = [t for t in trades if t.outcome == "win"]
    losses = [t for t in trades if t.outcome == "loss"]
    pnl_eur = sum(t.pnl_eur for t in trades)
    # Portfolio-level return, niet som van trade-percentages
    pnl_pct = (pnl_eur / starting_capital * 100) if starting_capital else 0.0
    cumulative_pct = prev_cumulative_pct + pnl_pct
    win_rate = len(wins) / len(trades) if trades else 0.0

    return DailySummary(
        date=date,
        strategy_id=strategy_id,
        trades_count=len(trades),
        wins=len(wins),
        losses=len(losses),
        pnl_eur=round(pnl_eur, 2),
        pnl_pct=round(pnl_pct, 2),
        cumulative_pct=round(cumulative_pct, 2),
        win_rate=round(win_rate, 3),
    )


def compute_monthly_summary(
    month: str,
    strategy_id: str,
    daily_summaries: list[DailySummary],
    prev_cumulative_pct: float = 0.0,
) -> MonthlySummary:
    """Aggregeer dagoverzichten naar maandoverzicht."""
    trades_count = sum(d.trades_count for d in daily_summaries)
    wins = sum(d.wins for d in daily_summaries)
    losses = sum(d.losses for d in daily_summaries)
    pnl_eur = sum(d.pnl_eur for d in daily_summaries)
    pnl_pct = sum(d.pnl_pct for d in daily_summaries)
    cumulative_pct = prev_cumulative_pct + pnl_pct
    win_rate = wins / trades_count if trades_count else 0.0

    return MonthlySummary(
        month=month,
        strategy_id=strategy_id,
        trades_count=trades_count,
        wins=wins,
        losses=losses,
        pnl_eur=round(pnl_eur, 2),
        pnl_pct=round(pnl_pct, 2),
        cumulative_pct=round(cumulative_pct, 2),
        win_rate=round(win_rate, 3),
    )


# ─── Opslaan ──────────────────────────────────────────────

def save_daily_rollup(data_dir: Path, date: str, strategies: dict):
    """Sla dagrollup op: data/daily/YYYY-MM-DD.json

    strategies = {sid: {"summary": DailySummary, "trades": [TradeRecord, ...]}}
    """
    daily_dir = data_dir / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)

    out = {"date": date, "strategies": {}}
    for sid, info in strategies.items():
        entry = asdict(info["summary"])
        entry["trades"] = [
            {
                "id": t.id,
                "time": t.timestamp.split("T")[1] if "T" in t.timestamp else t.timestamp,
                "symbol": t.symbol,
                "pnl_pct": t.pnl_pct,
                "pnl_eur": t.pnl_eur,
                "outcome": t.outcome,
            }
            for t in info["trades"]
        ]
        out["strategies"][sid] = entry

    atomic_write_json(daily_dir / f"{date}.json", out)


def save_monthly_rollup(data_dir: Path, month: str, strategies: dict):
    """Sla maandrollup op: data/monthly/YYYY-MM.json

    strategies = {sid: MonthlySummary}
    """
    monthly_dir = data_dir / "monthly"
    monthly_dir.mkdir(parents=True, exist_ok=True)

    out = {"month": month, "strategies": {}}
    for sid, summary in strategies.items():
        out["strategies"][sid] = asdict(summary)

    atomic_write_json(monthly_dir / f"{month}.json", out)


def atomic_write_json(path: Path, data: dict):
    """Schrijf JSON atomisch via tempfile + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
