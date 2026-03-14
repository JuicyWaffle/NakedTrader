"""
nakedtrader.orchestrator — Gelaagde orchestrator met 4-laags regelset.

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
from nakedtrader.utils import atomic_write_json
from nakedtrader.db.session import get_db_manager
from nakedtrader.db.repository import DataRepository

log = logging.getLogger(__name__)

# ... (omitted signal age and slippage thresholds for brevity in this replace call context, but I will include them in the full rewrite if needed. Actually, I should use the file content I have)

# ── Signaalverval limieten (seconden) ─────────────────
MAX_SIGNAL_AGE = {
    "arbitrage": 30,
    "momentum": 4 * 3600,
    "mean-reversion": 2 * 3600,
    "breakout": 8 * 3600,
    "trend-follow": 8 * 3600,
    "funding-contrarian": 4 * 3600,
    "cross-arb": 30,
    "vol-regime": 8 * 3600,
}

# ── Slippage drempels per strategie ───────────────────
MAX_SLIPPAGE = {
    "momentum": 0.002,
    "mean-reversion": 0.002,
    "breakout": 0.003,
    "arbitrage": 0.001,
    "trend-follow": 0.005,
    "funding-contrarian": 0.003,
    "cross-arb": 0.001,
    "vol-regime": 0.005,
}

# ── Broker minimale ordergrootte (EUR) ────────────────
BROKER_MIN_SIZE = {
    "ibkr": 50.0,
    "kraken": 10.0,
    "binance": 10.0,
}

# ── Prioriteit bij kapitaalschaarste ──────────────────
STRATEGY_PRIORITY = [
    "trend-follow", "mean-reversion", "momentum", "vol-regime",
    "funding-contrarian", "breakout", "arbitrage", "cross-arb",
]

# ── Sector tagging voor correlatie ────────────────────
SECTOR_MAP = {
    "XXBTZEUR": "crypto", "XETHZEUR": "crypto", "SOLUSD": "crypto",
    "AAPL": "tech", "MSFT": "tech", "SPY": "equity",
    "TLT": "macro", "EURUSD": "macro",
}

MAX_EXECUTION_LOG = 10_000


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


class BaseOrchestrator:
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
            except Exception as e:
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

        # ── Pause state ──
        self._pause_path = self.data_dir / "orchestrator_state.json"
        self._pause_state = self._load_pause_state()

        # ── Execution log ──
        self._exec_log_path = self.data_dir / f"executions_orch_{self.orchestrator_id}.json"
        self._exec_count = 0

    # ══════════════════════════════════════════════════
    # PAUSE MECHANISM
    # ══════════════════════════════════════════════════

    def _load_pause_state(self) -> dict:
        if self._pause_path.exists():
            try:
                with open(self._pause_path) as f:
                    return json.load(f)
            except (json.JSONDecodeError, KeyError):
                pass
        return {"paused_buys": False, "paused_sells": False, "paused_strategies": []}

    def _save_pause_state(self):
        self._pause_state["last_updated"] = datetime.now().isoformat(timespec="seconds")
        atomic_write_json(self._pause_path, self._pause_state, indent=2)

    def pause_buys(self):
        self._pause_state["paused_buys"] = True
        self._save_pause_state()
        log.info("[%s] Aankopen gepauzeerd", self.orchestrator_id)

    def resume_buys(self):
        self._pause_state["paused_buys"] = False
        self._save_pause_state()
        log.info("[%s] Aankopen hervat", self.orchestrator_id)

    def pause_sells(self):
        self._pause_state["paused_sells"] = True
        self._save_pause_state()
        log.info("[%s] Verkopen gepauzeerd", self.orchestrator_id)

    def resume_sells(self):
        self._pause_state["paused_sells"] = False
        self._save_pause_state()
        log.info("[%s] Verkopen hervat", self.orchestrator_id)

    def pause_strategy(self, strategy_id: str):
        if strategy_id not in self._pause_state["paused_strategies"]:
            self._pause_state["paused_strategies"].append(strategy_id)
            self._save_pause_state()
            log.info("[%s] Strategie %s gepauzeerd", self.orchestrator_id, strategy_id)

    def resume_strategy(self, strategy_id: str):
        if strategy_id in self._pause_state["paused_strategies"]:
            self._pause_state["paused_strategies"].remove(strategy_id)
            self._save_pause_state()
            log.info("[%s] Strategie %s hervat", self.orchestrator_id, strategy_id)

    def get_pause_state(self) -> dict:
        # Herlaad van disk (voor web API)
        self._pause_state = self._load_pause_state()
        return dict(self._pause_state)

    # ══════════════════════════════════════════════════
    # EXECUTION REPORT LOGGING
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
        """Sla ExecutionReport op in de database + JSON logbestand."""
        report_dict = asdict(report)

        # Database persistentie
        try:
            self.repo.add_execution_report(report_dict)
        except Exception as e:
            log.warning("[%s] DB execution log mislukt: %s", self.orchestrator_id, e)

        # JSON logbestand (voor web dashboard handlers)
        try:
            entries = []
            if self._exec_log_path.exists():
                with open(self._exec_log_path) as f:
                    entries = json.load(f)
            entries.append(report_dict)
            if len(entries) > MAX_EXECUTION_LOG:
                entries = entries[-MAX_EXECUTION_LOG:]
            atomic_write_json(self._exec_log_path, entries)
        except Exception as e:
            log.warning("[%s] JSON execution log mislukt: %s", self.orchestrator_id, e)

        self._exec_count += 1

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
        self._pause_state = self._load_pause_state()

        # ── Pause checks ──
        if signal.direction in ("long", "buy"):
            if self._pause_state.get("paused_buys", False):
                report = self._make_report(signal, "blocked", "Aankopen gepauzeerd", "PAUSE")
                self._log_execution(report)
                return report

        if sid in self._pause_state.get("paused_strategies", []):
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

    # ── LAAG 1: HARDE GRENZEN ────────────────────────

    def _check_hard_limits(self, signal: TradeSignal) -> Optional[ExecutionReport]:
        sid = signal.strategy_id or ""

        # H1: Emergency brake
        if self._current_risk and self._current_risk.emergency_brake:
            return self._make_report(
                signal, "blocked",
                f"Noodrem actief (score={self._current_risk.risk_score:.2f})", "H1",
            )

        # H2: Maximale blootstelling
        total_exposure = sum(self.open_positions.values())
        limit = self.config.total_capital * self.exposure_limit_pct
        if total_exposure >= limit:
            return self._make_report(
                signal, "blocked",
                f"Exposure €{total_exposure:,.0f} >= limiet €{limit:,.0f} ({self.exposure_limit_pct:.0%})", "H2",
            )

        # H3: Instrument-concentratie
        symbol_exposure = self.open_positions.get(signal.symbol, 0.0)
        max_per_instrument = self.config.total_capital * self.config.max_position_pct
        if symbol_exposure >= max_per_instrument:
            return self._make_report(
                signal, "blocked",
                f"Concentratie {signal.symbol}: €{symbol_exposure:,.0f} >= max €{max_per_instrument:,.0f}", "H3",
            )

        # H5: Signaalverval
        max_age = MAX_SIGNAL_AGE.get(sid, 4 * 3600)
        if hasattr(signal, 'timestamp') and signal.timestamp:
            try:
                signal_time = datetime.fromisoformat(signal.timestamp)
                age = (datetime.now() - signal_time).total_seconds()
                if age > max_age:
                    return self._make_report(
                        signal, "expired",
                        f"Signaal te oud ({age:.0f}s > {max_age}s)", "H5",
                    )
            except (ValueError, TypeError):
                pass

        return None

    # ── LAAG 2: RISICO-GEWOGEN ───────────────────────

    def _check_risk_rules(self, signal: TradeSignal) -> Optional[ExecutionReport]:
        sid = signal.strategy_id or ""
        risk_level = self._current_risk.risk_level if self._current_risk else "green"
        drawdown = self.risk_mgr.drawdown_pct

        # R6: Drawdown halt (>15%, circuit-breaker)
        if self.risk_mgr.halted or drawdown >= 0.15:
            return self._make_report(
                signal, "blocked",
                f"Circuit-breaker: drawdown {drawdown:.1%}", "R6",
            )

        # R5: Drawdown reduce (>10%)
        if drawdown >= 0.10:
            allowed = {"mean-reversion", "trend-follow"}
            if sid not in allowed:
                return self._make_report(
                    signal, "blocked",
                    f"Drawdown {drawdown:.1%} > 10%: alleen {allowed} actief", "R5",
                )

        # R3: Red — geen nieuwe posities (uitzondering via allow_at_red)
        if risk_level == "red" and sid not in self.allow_at_red:
            return self._make_report(
                signal, "blocked",
                f"Risk level RED: {sid} niet toegelaten", "R3",
            )

        # R2: Orange — verhoogde drempel
        if risk_level == "orange":
            threshold = self.win_prob_threshold + self.win_prob_orange_add
            if signal.win_probability < threshold and sid not in self.allow_at_orange:
                return self._make_report(
                    signal, "blocked",
                    f"Orange: win_prob {signal.win_probability:.2f} < drempel {threshold:.2f}", "R2",
                )

        # R1: Green — win_prob check
        if risk_level == "green":
            if signal.win_probability < self.win_prob_threshold:
                return self._make_report(
                    signal, "blocked",
                    f"Win prob {signal.win_probability:.2f} < drempel {self.win_prob_threshold:.2f}", "R1",
                )

        return None

    # ── LAAG 3: PORTEFEUILLE-COHERENTIE ──────────────

    def _check_portfolio_coherence(self, signal: TradeSignal) -> Optional[ExecutionReport]:
        sid = signal.strategy_id or ""

        # P4: Cooldown na verliesreeks (AdaptiveEngine)
        if sid and self.adaptive.is_in_cooldown(sid):
            return self._make_report(
                signal, "blocked",
                f"{sid} in cooldown na verliesreeks", "P4",
            )

        # P1: Conflictresolutie — 2 bots tegengesteld op zelfde instrument
        # (check of er al een positie in het instrument zit van een andere strategie)
        for existing_sym in self.open_positions:
            if existing_sym == signal.symbol:
                # Zelfde instrument, check of andere strategie
                # Winnaar = hoogste rolling win-rate
                own_wr = self.adaptive.get_rolling_win_rate(sid)
                # Hier laten we de bestaande positie staan als wr lager is
                if own_wr < 0.5:
                    return self._make_report(
                        signal, "blocked",
                        f"Conflict: {signal.symbol} al in portefeuille, win-rate {own_wr:.2f} < 0.50", "P1",
                    )

        # P2: Prioriteit bij kapitaalschaarste
        total_exposure = sum(self.open_positions.values())
        if total_exposure > self.config.total_capital * self.exposure_limit_pct * 0.8:
            prio = STRATEGY_PRIORITY.index(sid) if sid in STRATEGY_PRIORITY else 99
            if prio > 2:  # Alleen top 3 strategieën bij schaarste
                return self._make_report(
                    signal, "blocked",
                    f"Kapitaalschaarste: {sid} (prio {prio}) overgeslagen", "P2",
                )

        return None

    # ── KELLY SIZING ─────────────────────────────────

    def _calculate_size(self, signal: TradeSignal) -> float:
        """Bereken positiegrootte via Kelly + alle correcties."""
        sid = signal.strategy_id or ""
        drawdown = self.risk_mgr.drawdown_pct

        # Adaptive win-rate (als voldoende data)
        win_prob = signal.win_probability
        if sid:
            state = self.adaptive.get_state(sid)
            if len(state.recent_trades) >= 10:
                win_prob = self.adaptive.get_rolling_win_rate(sid)

        kelly_frac = self.sizer.calculate(win_prob, signal.expected_win_pct, signal.expected_loss_pct)

        # Macro risk Kelly-reductie
        if self._current_risk:
            kelly_frac *= self._current_risk.kelly_mult

        # Orchestrator-specifieke correctie
        kelly_frac *= self.kelly_correction

        # R4: Drawdown caution (>5%) — halveer
        if drawdown >= 0.05:
            kelly_frac *= 0.5

        size_eur = self.config.total_capital * kelly_frac

        # Per-strategie budget check
        if sid and not self.risk_mgr.allow_trade(sid, size_eur):
            return 0.0

        return size_eur

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
        except Exception as e:
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
