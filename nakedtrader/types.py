"""
nakedtrader.types — Gedeelde datastructuren voor alle componenten.

Alle modules importeren types hiervandaan, niet van elkaar.
Dit voorkomt circulaire imports en maakt componenten onafhankelijk.
"""

from dataclasses import dataclass, field
from typing import Optional


# ─────────────────────────────────────────────
# TRADE SIGNAL (bots → orchestrator)
# ─────────────────────────────────────────────

@dataclass
class TradeSignal:
    """Één handelssignaal vanuit een strategie."""
    symbol: str
    broker: str              # "ibkr" | "kraken" | "binance"
    direction: str           # "long" | "short"
    win_probability: float
    expected_win_pct: float
    expected_loss_pct: float
    current_price: float
    strategy_id: str = ""
    notes: str = ""


# ─────────────────────────────────────────────
# TRADE RECORD (performance logging)
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
    pnl_eur: float
    pnl_pct: float
    outcome: str                 # "win" | "loss" | "open"
    kelly_fraction: float
    win_probability: float
    strategy_id: str = ""
    notes: str = ""


# ─────────────────────────────────────────────
# PORTFOLIO SNAPSHOT
# ─────────────────────────────────────────────

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
# DAILY / MONTHLY SUMMARY (performance store)
# ─────────────────────────────────────────────

@dataclass
class DailySummary:
    """Dagelijks overzicht per strategie."""
    date: str                    # "YYYY-MM-DD"
    strategy_id: str
    trades_count: int
    wins: int
    losses: int
    pnl_eur: float
    pnl_pct: float               # dag-return %
    cumulative_pct: float         # cumulatief rendement % t.o.v. start
    win_rate: float


@dataclass
class MonthlySummary:
    """Maandelijks overzicht per strategie."""
    month: str                   # "YYYY-MM"
    strategy_id: str
    trades_count: int
    wins: int
    losses: int
    pnl_eur: float
    pnl_pct: float               # maand-return %
    cumulative_pct: float         # cumulatief rendement % t.o.v. start
    win_rate: float


# ─────────────────────────────────────────────
# STRATEGY META
# ─────────────────────────────────────────────

@dataclass
class StrategyMeta:
    """Metadata voor een strategie."""
    id: str
    name: str
    color: str
    description: str
    description_nl: str
    risk_level: str
    risk_score: int
    expected_return_min: float
    expected_return_max: float
    markets: list[str]
    indicators: list[str]
    timeframe: str
    broker: str
    active: bool = True
