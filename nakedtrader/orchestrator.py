"""
nakedtrader.orchestrator — Backward-compatibele shim.

Alle logica is verplaatst naar nakedtrader.orchestrator_pkg/.
Dit bestand herexporteert alles zodat bestaande imports blijven werken.
"""

from nakedtrader.orchestrator_pkg import (  # noqa: F401
    BaseOrchestrator,
    OrchestratorA,
    TradingBot,
    demo_signals,
    MAX_SIGNAL_AGE,
    MAX_SLIPPAGE,
    BROKER_MIN_SIZE,
    STRATEGY_PRIORITY,
    SECTOR_MAP,
    MAX_EXECUTION_LOG,
)
