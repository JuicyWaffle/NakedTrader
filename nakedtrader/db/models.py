"""
nakedtrader/db/models.py — SQLAlchemy modellen voor NakedTrader.
"""

from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, JSON, ForeignKey
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()

class Trade(Base):
    """Gerealiseerde trades uit paper engine of live brokers."""
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    symbol = Column(String, nullable=False)
    broker = Column(String)
    strategy_id = Column(String)
    direction = Column(String) # long/short
    entry_price = Column(Float)
    exit_price = Column(Float)
    quantity = Column(Float)
    pnl_eur = Column(Float)
    pnl_pct = Column(Float)
    outcome = Column(String) # win/loss
    mode = Column(String)    # paper/live
    notes = Column(String)

class ExecutionLog(Base):
    """Alle beslissingen van de orchestrator (audit trail)."""
    __tablename__ = "execution_log"

    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    orchestrator_id = Column(String)
    symbol = Column(String)
    strategy_id = Column(String)
    decision = Column(String) # executed/blocked/expired
    reason = Column(String)
    rule_triggered = Column(String)
    risk_score = Column(Float)
    drawdown_pct = Column(Float)
    size_executed = Column(Float)

class EquityPoint(Base):
    """Kapitaal-snapshots voor de equity curve."""
    __tablename__ = "equity_curve"

    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    total_capital = Column(Float)
    drawdown_pct = Column(Float)
    mode = Column(String) # paper/live

class StrategyState(Base):
    """Status van adaptive learning per strategie."""
    __tablename__ = "strategy_states"

    strategy_id = Column(String, primary_key=True)
    rolling_win_rate = Column(Float, default=0.5)
    rolling_sharpe = Column(Float, default=0.0)
    consecutive_losses = Column(Integer, default=0)
    cooldown_until = Column(DateTime, nullable=True)
    risk_budget_pct = Column(Float, default=0.20)
    ab_active = Column(Boolean, default=False)
    last_updated = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
