#!/usr/bin/env python3
"""NakedTrader — Orchestrator entry point.

Draait alle 3 orchestrators parallel op dezelfde signaalstroom:
  A — Conservatief (live/paper, afhankelijk van config.paper_mode)
  B — Agressief (altijd paper/shadow)
  C — Diversificatie (altijd paper/shadow)

Gebruik: python run_orchestrator.py
"""

import logging
import sys
import time
from pathlib import Path

# Voeg project root toe
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

log = logging.getLogger(__name__)


def collect_signals(strategies, adaptive=None):
    """Verzamel signalen van alle strategieen."""
    signals = []
    for sid, strategy in strategies.items():
        if adaptive:
            strategy.set_adaptive(adaptive)
        try:
            sigs = strategy.generate_signals()
            if sigs:
                signals.extend(sigs)
        except Exception as e:
            log.warning("Strategie %s fout: %s", sid, e)
    return signals


def main():
    from nakedtrader.config import load_config
    from nakedtrader.orchestrator import OrchestratorA
    from nakedtrader.orchestrator_b import OrchestratorB
    from nakedtrader.orchestrator_c import OrchestratorC
    from nakedtrader.performance.store import PerformanceStore
    from nakedtrader.bots.registry import get_all_strategies

    config = load_config()

    log.info("=== NakedTrader Orchestrator Service ===")
    log.info("Modus A: %s", "PAPER" if config.paper_mode else "LIVE")
    log.info("Modus B: PAPER (shadow)")
    log.info("Modus C: PAPER (shadow)")
    log.info("Kapitaal: EUR %s", f"{config.total_capital:,.2f}")
    log.info("Interval: %ds", config.interval_seconds)

    # Performance store
    store = PerformanceStore(
        data_dir=config.data_dir,
        starting_capital=config.total_capital,
    )

    # Orchestrators
    orch_a = OrchestratorA(config)
    orch_a.store = store

    orch_b = OrchestratorB(config)
    orch_b.store = store

    orch_c = OrchestratorC(config)
    orch_c.store = store

    # Broker verbinding (alleen A indien live)
    try:
        orch_a.connect_brokers()
    except Exception as e:
        log.warning("Broker verbinding mislukt: %s — draait in paper mode", e)

    # Strategieen laden
    strategies = get_all_strategies()
    log.info("Strategieen geladen: %s", ", ".join(strategies.keys()))

    # Main loop
    interval = config.interval_seconds
    log.info("Orchestrator loop gestart (interval=%ds)", interval)

    while True:
        try:
            # Macro risk check (alle orchestrators)
            orch_a._check_macro_risk()
            orch_b._current_risk = orch_a._current_risk  # Deel macro risk
            orch_c._current_risk = orch_a._current_risk

            # Signalen verzamelen
            signals = collect_signals(strategies, orch_a.adaptive)

            if signals:
                log.info("--- %d signalen ontvangen ---", len(signals))
                for signal in signals:
                    # Alle 3 orchestrators verwerken hetzelfde signaal
                    orch_a.process_signal(signal)
                    orch_b.process_signal(signal)
                    orch_c.process_signal(signal)
            else:
                log.debug("Geen signalen deze ronde")

            # EOD rollups (alleen via A)
            orch_a._check_rollups()

            time.sleep(interval)

        except KeyboardInterrupt:
            log.info("Orchestrator service gestopt")
            break
        except Exception as e:
            log.error("Fout in orchestrator loop: %s", e, exc_info=True)
            time.sleep(10)

    # Cleanup
    try:
        orch_a.disconnect_brokers()
    except Exception:
        pass


if __name__ == "__main__":
    main()
