"""
nakedtrader/db/repository.py — Data Access Object (DAO) voor de database.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Any
from sqlalchemy.orm import Session
from nakedtrader.db.models import Trade, ExecutionLog, EquityPoint, StrategyState
from nakedtrader.db.session import get_db_manager

log = logging.getLogger(__name__)

class DataRepository:
    """Repository voor alle data-operaties (CRUD)."""

    def __init__(self, db_session: Session = None):
        self.db = db_session or get_db_manager().get_session()

    # ── Trades ─────────────────────────

    def add_trade(self, trade_data: dict) -> Trade:
        """Voeg een gerealiseerde trade toe."""
        try:
            # Als timestamp een string is, converteer naar datetime
            ts = trade_data.get("timestamp")
            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts.replace("Z", ""))
            
            trade = Trade(
                timestamp=ts or datetime.now(timezone.utc),
                symbol=trade_data.get("symbol"),
                broker=trade_data.get("broker"),
                strategy_id=trade_data.get("strategy_id"),
                direction=trade_data.get("direction"),
                entry_price=trade_data.get("entry_price"),
                exit_price=trade_data.get("exit_price"),
                quantity=trade_data.get("quantity"),
                pnl_eur=trade_data.get("pnl_eur"),
                pnl_pct=trade_data.get("pnl_pct"),
                outcome=trade_data.get("outcome"),
                mode=trade_data.get("mode", "paper"),
                notes=trade_data.get("notes")
            )
            self.db.add(trade)
            self.db.commit()
            self.db.refresh(trade)
            return trade
        except Exception as e:
            self.db.rollback()
            log.error(f"Fout bij toevoegen trade: {e}")
            raise

    def get_recent_trades(self, limit: int = 50, strategy_id: str = None) -> List[Trade]:
        """Haal recente trades op."""
        query = self.db.query(Trade)
        if strategy_id:
            query = query.filter(Trade.strategy_id == strategy_id)
        return query.order_by(Trade.timestamp.desc()).limit(limit).all()

    # ── Execution Logs ─────────────────

    def add_execution_report(self, report_data: dict):
        """Voeg een audit log van een orchestrator besluit toe."""
        try:
            # Signal data uitpakken
            signal = report_data.get("signal", {})
            
            log_entry = ExecutionLog(
                timestamp=datetime.now(timezone.utc),
                orchestrator_id=report_data.get("orchestrator_id"),
                symbol=signal.get("symbol"),
                strategy_id=signal.get("strategy_id"),
                decision=report_data.get("decision"),
                reason=report_data.get("reason"),
                rule_triggered=report_data.get("rule_triggered"),
                risk_score=report_data.get("risk_score"),
                drawdown_pct=report_data.get("drawdown_pct"),
                size_executed=report_data.get("size_executed", 0.0)
            )
            self.db.add(log_entry)
            self.db.commit()
        except Exception as e:
            self.db.rollback()
            log.error(f"Fout bij toevoegen execution log: {e}")

    # ── Strategy State ─────────────────

    def update_strategy_state(self, strategy_id: str, updates: dict):
        """Update adaptive state voor een strategie."""
        try:
            state = self.db.query(StrategyState).filter(StrategyState.strategy_id == strategy_id).first()
            if not state:
                state = StrategyState(strategy_id=strategy_id)
                self.db.add(state)
            
            for key, value in updates.items():
                if hasattr(state, key):
                    setattr(state, key, value)
            
            self.db.commit()
        except Exception as e:
            self.db.rollback()
            log.error(f"Fout bij updaten strategy state: {e}")

    def get_strategy_state(self, strategy_id: str) -> Optional[StrategyState]:
        """Haal status op per strategie."""
        return self.db.query(StrategyState).filter(StrategyState.strategy_id == strategy_id).first()

    def get_all_strategy_states(self) -> List[StrategyState]:
        """Haal status van alle strategieën op."""
        return self.db.query(StrategyState).all()

    # ── Equity Curve ───────────────────

    def add_equity_point(self, capital: float, drawdown: float, mode: str = "paper"):
        """Sla actueel kapitaal op voor trend-analyse."""
        try:
            point = EquityPoint(
                timestamp=datetime.now(timezone.utc),
                total_capital=capital,
                drawdown_pct=drawdown,
                mode=mode
            )
            self.db.add(point)
            self.db.commit()
        except Exception as e:
            self.db.rollback()
            log.error(f"Fout bij toevoegen equity point: {e}")

    def get_equity_curve(self, days: int = 30, mode: str = "paper") -> List[EquityPoint]:
        """Haal historische equity data op."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        return self.db.query(EquityPoint).filter(
            EquityPoint.timestamp >= cutoff,
            EquityPoint.mode == mode
        ).order_by(EquityPoint.timestamp.asc()).all()

    def close(self):
        """Sluit de sessie."""
        self.db.close()
