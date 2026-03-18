"""
nakedtrader.orchestrator.base — Gelaagde orchestrator met 4-laags regelset.

BaseOrchestrator: gedeelde regelset + ExecutionReport logging.
OrchestratorA: conservatief (live/paper).
TradingBot: backward-compat alias voor OrchestratorA.

Regelset:
  Laag 1 (H1-H5): Harde grenzen — nooit overschrijven
  Laag 2 (R1-R6): Risico-gewogen — afhankelijk van RiskReport + drawdown
  Laag 3 (P1-P4): Portefeuille-coherentie
  Laag 4 (U1-U3): Uitvoering
"""

import json
import time
import random
import logging
from dataclasses import asdict
from typing import Optional
from datetime import datetime
from pathlib import Path

from nakedtrader.config import Config
from nakedtrader.types import TradeSignal, ExecutionReport
from nakedtrader.brokers.base import AbstractBroker
from nakedtrader.money.kelly import KellyPositionSizer
from nakedtrader.money.risk import RiskManager, RiskLimits
from nakedtrader.money.adaptive import AdaptiveEngine
from nakedtrader.brokers.ibkr import IBKRBroker
from nakedtrader.brokers.kraken import KrakenBroker
from nakedtrader.brokers.binance import BinanceBroker
from nakedtrader.performance.tracker import PerformanceTracker
from nakedtrader.performance.paper_engine import PaperEngine
from nakedtrader.db.session import get_db_manager
from nakedtrader.db.repository import DataRepository

from nakedtrader.orchestrator_pkg.constants import BROKER_MIN_SIZE
from nakedtrader.orchestrator_pkg.pause_manager import PauseManager
from nakedtrader.orchestrator_pkg.execution_reporter import ExecutionReporter
from nakedtrader.orchestrator_pkg.signal_filter import SignalFilterMixin
from nakedtrader.orchestrator_pkg.position_sizer import calculate_position_size

log = logging.getLogger(__name__)


def demo_signals(n: int = 3) -> list[TradeSignal]:
    """Genereer willekeurige demo-signalen voor het testen."""
    universe = [
        ("AAPL",      "ibkr",   180.0,   0.60, 0.12, 0.05, "momentum"),
        ("MSFT",      "ibkr",   415.0,   0.58, 0.10, 0.05, "trend-follow"),
        ("SPY",       "ibkr",   520.0,   0.62, 0.08, 0.04, "trend-follow"),
        ("XXBTZEUR",  "kraken", 62000.0, 0.54, 0.18, 0.08, "momentum"),
        ("XETHZEUR",  "kraken", 3200.0,  0.56, 0.14, 0.07, "mean-reversion"),
        ("SOLUSD",    "kraken", 145.0,   0.53, 0.20, 0.09, "breakout"),
    ]
    chosen = random.sample(universe, min(n, len(universe)))
    return [
        TradeSignal(
            symbol=sym, broker=broker, direction="long",
            win_probability=wp, expected_win_pct=ew, expected_loss_pct=el,
            current_price=price + random.uniform(-price * 0.01, price * 0.01),
            strategy_id=sid, notes=f"Demo signaal {sym}",
        )
        for sym, broker, price, wp, ew, el, sid in chosen
    ]


class BaseOrchestrator(SignalFilterMixin):
    """Gelaagde orchestrator met 4-laags regelset en ExecutionReport logging."""

    # ── Configureerbare drempels (subklassen overschrijven) ──
    orchestrator_id: str = "A"
    exposure_limit_pct: float = 0.50
    win_prob_threshold: float = 0.55
    win_prob_orange_add: float = 0.05
    kelly_correction: float = 0.8
    force_paper: bool = False         # True = altijd paper (B en C)
    allow_at_orange: list = []        # strategieën die ook bij orange mogen
    allow_at_red: list = []           # strategieën die ook bij red mogen

    def __init__(self, config: Config, orchestrator_id: str = None):
        self.config = config
        self.ollama_enabled = config.ollama_enabled
        if orchestrator_id:
            self.orchestrator_id = orchestrator_id

        self.data_dir = Path(config.data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # ── Core componenten ──
        self.sizer = KellyPositionSizer(config.kelly_fraction, config.max_position_pct)
        self.tracker = PerformanceTracker(config.trade_log_path)
        self.paper = PaperEngine(config, self.tracker)

        self.adaptive = AdaptiveEngine(config.state_path)
        risk_limits = RiskLimits(
            drawdown_limit_pct=config.drawdown_limit_pct,
            default_sl_pct=config.stop_loss_pct,
            default_tp_pct=config.take_profit_pct,
            max_position_pct=config.max_position_pct,
        )
        self.risk_mgr = RiskManager(risk_limits, config.total_capital)

        # Performance store (optioneel)
        self.store = None

        # Macro risk engine
        self.macro_risk = None
        self._current_risk = None
        self._last_risk_check: Optional[datetime] = None
        if config.macro_risk_enabled:
            try:
                from nakedtrader.risk.macro import MacroRiskEngine
                self.macro_risk = MacroRiskEngine(
                    emergency_score=config.macro_risk_veto_threshold,
                    ollama_enabled=config.ollama_enabled,
                )
                log.info("[%s] Macro risk engine actief", self.orchestrator_id)
            except (ImportError, ConnectionError, ValueError, OSError) as e:
                log.warning("[%s] Macro risk engine niet beschikbaar: %s", self.orchestrator_id, e)

        # ── Broker connections ──
        self.brokers: dict[str, AbstractBroker] = {}

        # ── Database repository ──
        self.db_manager = get_db_manager()
        self.db_manager.init_db()
        self.repo = DataRepository()

        # ── State tracking ──
        self.open_positions: dict[str, float] = {}  # symbol → size_eur
        self._last_rollup_date = None

        # ── Pause manager (composition) ──
        self.pause_mgr = PauseManager(self.data_dir, self.orchestrator_id)

        # ── Execution reporter (composition) ──
        self.reporter = ExecutionReporter(self.data_dir, self.orchestrator_id, self.repo)

    # ── Backward-compat delegation voor pause ──

    @property
    def _pause_path(self):
        return self.pause_mgr._pause_path

    @property
    def _pause_state(self):
        return self.pause_mgr._state

    @_pause_state.setter
    def _pause_state(self, value):
        self.pause_mgr._state = value

    def _load_pause_state(self):
        return self.pause_mgr._load()

    def _save_pause_state(self):
        self.pause_mgr._save()

    def pause_buys(self):
        self.pause_mgr.pause_buys()

    def resume_buys(self):
        self.pause_mgr.resume_buys()

    def pause_sells(self):
        self.pause_mgr.pause_sells()

    def resume_sells(self):
        self.pause_mgr.resume_sells()

    def pause_strategy(self, strategy_id: str):
        self.pause_mgr.pause_strategy(strategy_id)

    def resume_strategy(self, strategy_id: str):
        self.pause_mgr.resume_strategy(strategy_id)

    def get_pause_state(self) -> dict:
        return self.pause_mgr.get_state()

    # ── Backward-compat delegation voor reporter ──

    @property
    def _exec_log_path(self):
        return self.reporter.exec_log_path

    @property
    def _exec_count(self):
        return self.reporter.exec_count

    # ══════════════════════════════════════════════════
    # EXECUTION REPORT CREATION
    # ══════════════════════════════════════════════════

    def _make_report(
        self, signal: TradeSignal, decision: str, reason: str,
        rule: str, size_original: float = 0.0, size_executed: float = 0.0,
    ) -> ExecutionReport:
        risk_score = self._current_risk.risk_score if self._current_risk else 0.0
        drawdown = self.risk_mgr.drawdown_pct
        exposure = sum(self.open_positions.values())
        mode = "paper" if (self.config.paper_mode or self.force_paper) else "live"

        # AI reasoning voor geblokkeerde signalen
        if self.ollama_enabled and decision == "blocked":
            try:
                from nakedtrader.ai.ollama import generate
                from nakedtrader.ai.prompts import SIGNAL_REASONING_SYSTEM
                context = (
                    f"Signal: {signal.strategy_id} {signal.direction} {signal.symbol} "
                    f"@ {signal.current_price}\n"
                    f"Decision: {decision}, Rule: {rule}\n"
                    f"Reason: {reason}\n"
                    f"Risk score: {risk_score:.2f}, Drawdown: {drawdown:.1%}, "
                    f"Exposure: €{exposure:,.0f}"
                )
                enriched = generate(
                    context, task="reasoning",
                    system=SIGNAL_REASONING_SYSTEM, max_tokens=150,
                )
                if enriched:
                    reason = f"{reason} — {enriched}"
            except Exception:
                pass  # graceful degradation

        return ExecutionReport(
            timestamp=datetime.now().isoformat(timespec="seconds"),
            signal=asdict(signal),
            decision=decision,
            reason=reason,
            rule_triggered=rule,
            risk_score=risk_score,
            drawdown_pct=drawdown,
            total_exposure=exposure,
            size_original=size_original,
            size_executed=size_executed,
            orchestrator_id=self.orchestrator_id,
            mode=mode,
        )

    def _log_execution(self, report: ExecutionReport):
        """Sla ExecutionReport op via reporter."""
        self.reporter.log(asdict(report))

    # ══════════════════════════════════════════════════
    # SIGNAL PROCESSING — 4-LAAGS REGELSET
    # ══════════════════════════════════════════════════

    def process_signal(self, signal: TradeSignal) -> ExecutionReport:
        """Verwerk één signaal via de 4-laags regelset."""
        # Auto-fill timestamp als leeg
        if not signal.timestamp:
            signal.timestamp = datetime.now().isoformat(timespec="seconds")
        sid = signal.strategy_id or ""

        # ── Herlaad pause state van disk ──
        self.pause_mgr.reload()

        # ── Pause checks ──
        if signal.direction in ("long", "buy"):
            if self.pause_mgr.paused_buys:
                report = self._make_report(signal, "blocked", "Aankopen gepauzeerd", "PAUSE")
                self._log_execution(report)
                return report

        if sid in self.pause_mgr.paused_strategies:
            report = self._make_report(signal, "blocked", f"Strategie {sid} gepauzeerd", "PAUSE")
            self._log_execution(report)
            return report

        # ── Laag 1: Harde grenzen ──
        blocked = self._check_hard_limits(signal)
        if blocked:
            self._log_execution(blocked)
            return blocked

        # ── Laag 2: Risico-gewogen ──
        blocked = self._check_risk_rules(signal)
        if blocked:
            self._log_execution(blocked)
            return blocked

        # ── Laag 3: Portefeuille-coherentie ──
        blocked = self._check_portfolio_coherence(signal)
        if blocked:
            self._log_execution(blocked)
            return blocked

        # ── Kelly sizing ──
        size_eur = self._calculate_size(signal)
        if size_eur < BROKER_MIN_SIZE.get(signal.broker, 10.0):
            report = self._make_report(
                signal, "blocked", f"Size €{size_eur:.2f} < broker minimum", "H4",
                size_original=size_eur,
            )
            self._log_execution(report)
            return report

        # ── Laag 4: Uitvoering ──
        report = self._execute(signal, size_eur)
        self._log_execution(report)
        return report

    # ── KELLY SIZING ─────────────────────────────────

    def _calculate_size(self, signal: TradeSignal) -> float:
        """Bereken positiegrootte via Kelly + alle correcties."""
        return calculate_position_size(
            signal=signal,
            config=self.config,
            sizer=self.sizer,
            adaptive=self.adaptive,
            risk_mgr=self.risk_mgr,
            current_risk=self._current_risk,
            kelly_correction=self.kelly_correction,
        )

    # ── LAAG 4: UITVOERING ───────────────────────────

    def _execute(self, signal: TradeSignal, size_eur: float) -> ExecutionReport:
        """Voer de trade uit (paper of live) en log het resultaat."""
        sid = signal.strategy_id or ""

        # Alloceer budget
        if sid:
            self.risk_mgr.allocate(sid, size_eur)

        # Per-strategie SL/TP
        risk_params = self.risk_mgr.get_risk_params(sid) if sid else {
            "sl": self.config.stop_loss_pct, "tp": self.config.take_profit_pct,
        }

        trade = None

        if self.config.paper_mode or self.force_paper:
            # Paper mode
            kelly_frac = size_eur / self.config.total_capital if self.config.total_capital > 0 else 0
            trade = self.paper.process_signal(signal, kelly_frac, size_eur)
        else:
            # Live mode — broker routing (U3) via AbstractBroker interface
            sl_pct = risk_params["sl"]
            tp_pct = risk_params["tp"]

            # Macro risk SL aanscherping
            if self._current_risk:
                sl_pct *= self._current_risk.sl_mult

            broker = self.brokers.get(signal.broker)
            if broker:
                broker.place_order(signal, size_eur, sl_pct, tp_pct)
            else:
                log.error("[%s] Broker %s niet geconfigureerd", self.orchestrator_id, signal.broker)

            # Log ook in paper voor tracking
            kelly_frac = size_eur / self.config.total_capital if self.config.total_capital > 0 else 0
            trade = self.paper.process_signal(signal, kelly_frac, size_eur)

        # Post-trade logging
        if sid and trade:
            self.adaptive.record_trade(sid, trade.pnl_pct, trade.outcome)
            self.risk_mgr.update_capital(trade.pnl_eur, sid)

            # Nieuwe database persistentie
            self.repo.add_trade(asdict(trade))
            self.repo.add_equity_point(self.risk_mgr.current_capital, self.risk_mgr.drawdown_pct)

        # Track open positie
        self.open_positions[signal.symbol] = self.open_positions.get(signal.symbol, 0) + size_eur

        report = self._make_report(
            signal, "executed",
            f"{signal.symbol} {signal.direction} via {signal.broker}",
            "", size_original=size_eur, size_executed=size_eur,
        )
        log.info(
            "[%s] EXECUTED %s %s — €%.0f via %s",
            self.orchestrator_id, signal.symbol, signal.direction, size_eur, signal.broker,
        )
        return report

    # ══════════════════════════════════════════════════
    # BROKER MANAGEMENT
    # ══════════════════════════════════════════════════

    def connect_brokers(self):
        if self.config.paper_mode or self.force_paper:
            log.info("[%s] Paper mode — geen broker-verbinding nodig", self.orchestrator_id)
            return

        log.info("[%s] Live mode: verbinding maken met brokers...", self.orchestrator_id)

        # IBKR
        self.brokers["ibkr"] = IBKRBroker(self.config)
        self.brokers["ibkr"].connect()

        # Crypto
        if self.config.crypto_broker == "kraken":
            self.brokers["kraken"] = KrakenBroker(self.config)
            self.brokers["kraken"].connect()
        elif self.config.crypto_broker == "binance":
            self.brokers["binance"] = BinanceBroker(self.config)
            self.brokers["binance"].connect()

    def disconnect_brokers(self):
        for name, broker in self.brokers.items():
            broker.disconnect()
            log.info("[%s] Broker %s verbinding gesloten", self.orchestrator_id, name)
        self.brokers.clear()

    # ══════════════════════════════════════════════════
    # MACRO RISK + ROLLUPS
    # ══════════════════════════════════════════════════

    def _check_macro_risk(self):
        if not self.macro_risk:
            return
        now = datetime.now()
        interval = self.config.macro_risk_interval_min * 60
        if self._last_risk_check and (now - self._last_risk_check).total_seconds() < interval:
            return
        try:
            self._current_risk = self.macro_risk.evaluate()
            self._last_risk_check = now
            log.info(
                "[%s][MACRO] score=%.2f level=%s kelly×%.2f sl×%.2f%s",
                self.orchestrator_id,
                self._current_risk.risk_score,
                self._current_risk.risk_level,
                self._current_risk.kelly_mult,
                self._current_risk.sl_mult,
                " NOODREM" if self._current_risk.emergency_brake else "",
            )
        except (ConnectionError, ValueError, OSError, TimeoutError) as e:
            log.warning("[%s][MACRO] Evaluatie mislukt: %s", self.orchestrator_id, e)

    def _check_rollups(self):
        if not self.store:
            return
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        if now.hour == 23 and now.minute >= 59 and self._last_rollup_date != today:
            self.store.run_eod_rollup(today)
            self._last_rollup_date = today
            import calendar
            _, last_day = calendar.monthrange(now.year, now.month)
            if now.day == last_day:
                self.store.run_eom_rollup(now.strftime("%Y-%m"))

    # ══════════════════════════════════════════════════
    # RUN LOOP
    # ══════════════════════════════════════════════════

    def run_once(self, signals: Optional[list[TradeSignal]] = None):
        mode_label = "PAPER" if (self.config.paper_mode or self.force_paper) else "LIVE"
        log.info(
            "[%s] ─── [%s] Run %s ───",
            self.orchestrator_id, mode_label, datetime.now().strftime('%H:%M:%S'),
        )
        if signals is None:
            signals = demo_signals(n=2)
        for signal in signals:
            self.process_signal(signal)

    def run_loop(self, interval_seconds: int = 60, signals_fn=None):
        mode = "PAPER" if (self.config.paper_mode or self.force_paper) else "LIVE"
        log.info("[%s] Orchestrator gestart modus=%s interval=%ds", self.orchestrator_id, mode, interval_seconds)
        while True:
            try:
                self._check_macro_risk()
                signals = signals_fn() if signals_fn else None
                self.run_once(signals)
                self._check_rollups()
                time.sleep(interval_seconds)
            except KeyboardInterrupt:
                log.info("[%s] Orchestrator gestopt", self.orchestrator_id)
                break
            except Exception as e:
                log.error("[%s] Fout: %s", self.orchestrator_id, e, exc_info=True)
                time.sleep(10)

    def get_status(self) -> dict:
        """Retourneer huidige status voor web API."""
        return {
            "orchestrator_id": self.orchestrator_id,
            "mode": "paper" if (self.config.paper_mode or self.force_paper) else "live",
            "pause_state": self.get_pause_state(),
            "open_positions": len(self.open_positions),
            "total_exposure": sum(self.open_positions.values()),
            "exposure_limit_pct": self.exposure_limit_pct,
            "risk_score": self._current_risk.risk_score if self._current_risk else 0.0,
            "risk_level": self._current_risk.risk_level if self._current_risk else "green",
            "drawdown_pct": self.risk_mgr.drawdown_pct,
            "executions_logged": self._exec_count,
        }


class OrchestratorA(BaseOrchestrator):
    """Conservatieve orchestrator (live/paper). Bij twijfel: niet uitvoeren."""
    orchestrator_id = "A"
    exposure_limit_pct = 0.50
    win_prob_threshold = 0.65
    win_prob_orange_add = 0.05     # → 0.70 bij orange
    kelly_correction = 0.8
    force_paper = False            # volgt config.paper_mode
    allow_at_orange = ["trend-follow"]  # Macro Trend ook bij orange
    allow_at_red = []


# Backward compatibility
TradingBot = OrchestratorA
