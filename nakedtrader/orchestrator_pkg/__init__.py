"""nakedtrader.orchestrator — Re-exports voor backward-compatibiliteit."""

from nakedtrader.orchestrator_pkg.base import (
    BaseOrchestrator,
    OrchestratorA,
    TradingBot,
    demo_signals,
)
from nakedtrader.orchestrator_pkg.constants import (
    MAX_SIGNAL_AGE,
    MAX_SLIPPAGE,
    BROKER_MIN_SIZE,
    STRATEGY_PRIORITY,
    SECTOR_MAP,
    MAX_EXECUTION_LOG,
)
from nakedtrader.orchestrator_pkg.pause_manager import PauseManager
from nakedtrader.orchestrator_pkg.execution_reporter import ExecutionReporter
from nakedtrader.orchestrator_pkg.signal_filter import SignalFilterMixin
from nakedtrader.orchestrator_pkg.position_sizer import calculate_position_size
from nakedtrader.risk.macro import RiskThresholds

__all__ = [
    "BaseOrchestrator",
    "OrchestratorA",
    "TradingBot",
    "demo_signals",
    "MAX_SIGNAL_AGE",
    "MAX_SLIPPAGE",
    "BROKER_MIN_SIZE",
    "STRATEGY_PRIORITY",
    "SECTOR_MAP",
    "MAX_EXECUTION_LOG",
    "PauseManager",
    "ExecutionReporter",
    "SignalFilterMixin",
    "calculate_position_size",
    "RiskThresholds",
]
