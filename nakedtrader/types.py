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
# MACRO RISK REPORT
# ─────────────────────────────────────────────

@dataclass
class RiskReport:
    """Gestandaardiseerde output van MacroRiskEngine.evaluate()."""
    risk_score: float              # 0.0 (laag) → 1.0 (kritiek)
    risk_level: str                # "green" | "orange" | "red"
    kelly_mult: float              # vermenigvuldiger voor Kelly-fractie
    sl_mult: float                 # stop-loss aanscherping (< 1.0 = strikter)
    emergency_brake: bool          # True = halt alle posities
    signals: dict                  # ruwe indicatorwaarden
    alerts: list[str]              # mensleesbare waarschuwingen
    data_quality: dict             # beschikbaarheid per databron
    timestamp: str = ""


# ─────────────────────────────────────────────
# TRANSACTION (virtual bank ledger)
# ─────────────────────────────────────────────

@dataclass
class Transaction:
    """Individuele buy/sell transactie in het virtuele ledger."""
    id: str                        # "TX000001"
    timestamp: str                 # ISO format
    strategy_id: str               # "momentum", "breakout", etc.
    symbol: str                    # "AAPL", "XXBTZEUR"
    tx_type: str                   # "buy" | "sell"
    price: float                   # uitvoeringsprijs
    quantity: float                # aantal eenheden
    size_eur: float                # positiegrootte in EUR
    buy_price: float = 0.0         # oorspronkelijke aankoopprijs (alleen bij sell)
    pnl_eur: float = 0.0           # winst/verlies in EUR (alleen bij sell)
    pnl_pct: float = 0.0           # winst/verlies in % (alleen bij sell)
    trade_id: str = ""             # referentie naar TradeRecord


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
