"""Integration tests for nakedtrader.orchestrator rule evaluation."""

import json
from datetime import datetime, timedelta

import pytest

from nakedtrader.config import Config
from nakedtrader.types import TradeSignal, RiskReport, ExecutionReport
from nakedtrader.orchestrator import BaseOrchestrator, OrchestratorA


def _make_config(tmp_path) -> Config:
    """Config pointing to temp directories — no real broker connections."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    return Config(
        paper_mode=True,
        total_capital=10_000.0,
        kelly_fraction=0.5,
        max_position_pct=0.20,
        stop_loss_pct=0.05,
        take_profit_pct=0.10,
        drawdown_limit_pct=0.15,
        trade_log_path=str(data_dir / "trades.json"),
        state_path=str(data_dir / "state.json"),
        data_dir=str(data_dir),
        macro_risk_enabled=False,
    )


def _make_signal(**overrides) -> TradeSignal:
    """Convenience helper for building test signals."""
    defaults = dict(
        symbol="XXBTZEUR",
        broker="kraken",
        direction="long",
        win_probability=0.70,
        expected_win_pct=0.12,
        expected_loss_pct=0.05,
        current_price=62000.0,
        strategy_id="momentum",
    )
    defaults.update(overrides)
    return TradeSignal(**defaults)


@pytest.fixture
def orch(tmp_path):
    """OrchestratorA with paper-mode config in a temp directory."""
    config = _make_config(tmp_path)
    return OrchestratorA(config)


# ══════════════════════════════════════════════
# BASIC SIGNAL PROCESSING
# ══════════════════════════════════════════════

class TestBasicProcessing:
    def test_signal_executes(self, orch):
        signal = _make_signal(win_probability=0.70)
        report = orch.process_signal(signal)
        assert report.decision == "executed"
        assert report.orchestrator_id == "A"
        assert report.mode == "paper"

    def test_report_is_execution_report(self, orch):
        report = orch.process_signal(_make_signal())
        assert isinstance(report, ExecutionReport)

    def test_execution_logged_to_file(self, orch):
        orch.process_signal(_make_signal())
        assert orch._exec_log_path.exists()
        with open(orch._exec_log_path) as f:
            entries = json.load(f)
        assert len(entries) == 1
        assert entries[0]["decision"] in ("executed", "blocked")


# ══════════════════════════════════════════════
# LAAG 1: HARDE GRENZEN (H1 – H5)
# ══════════════════════════════════════════════

class TestHardLimits:
    def test_h1_emergency_brake(self, orch):
        """Emergency brake blocks all signals."""
        orch._current_risk = RiskReport(
            risk_score=0.95, risk_level="red",
            kelly_mult=0.0, sl_mult=0.5,
            emergency_brake=True,
            signals={}, alerts=[], data_quality={},
        )
        report = orch.process_signal(_make_signal())
        assert report.decision == "blocked"
        assert report.rule_triggered == "H1"

    def test_h2_exposure_limit(self, orch):
        """Blocks when total exposure >= limit."""
        # capital = 10000, limit = 50% = 5000
        orch.open_positions = {"XETHZEUR": 3000.0, "SOLUSD": 2000.0}
        report = orch.process_signal(_make_signal())
        assert report.decision == "blocked"
        assert report.rule_triggered == "H2"

    def test_h2_exposure_below_limit(self, orch):
        """Allows when exposure is below limit."""
        orch.open_positions = {"XETHZEUR": 1000.0}
        report = orch.process_signal(_make_signal())
        assert report.decision == "executed"

    def test_h3_instrument_concentration(self, orch):
        """Blocks when single instrument exceeds max_position_pct."""
        # max_position_pct = 0.20 → 2000 EUR
        orch.open_positions = {"XXBTZEUR": 2000.0}
        report = orch.process_signal(_make_signal(symbol="XXBTZEUR"))
        assert report.decision == "blocked"
        assert report.rule_triggered == "H3"

    def test_h4_below_broker_minimum(self, orch):
        """Blocks when calculated size < broker minimum."""
        # Signal with low win probability → tiny Kelly size
        signal = _make_signal(
            win_probability=0.50, expected_win_pct=0.01, expected_loss_pct=0.01,
        )
        report = orch.process_signal(signal)
        # Kelly = 0 for 50/50 with equal win/loss → blocked at H4 or R2
        assert report.decision == "blocked"

    def test_h5_expired_signal(self, orch):
        """Blocks when signal is too old."""
        old_time = (datetime.now() - timedelta(hours=10)).isoformat()
        signal = _make_signal(strategy_id="momentum")
        signal.timestamp = old_time
        report = orch.process_signal(signal)
        assert report.decision == "expired"
        assert report.rule_triggered == "H5"


# ══════════════════════════════════════════════
# LAAG 2: RISICO-GEWOGEN (R1 – R6)
# ══════════════════════════════════════════════

class TestRiskRules:
    def test_r1_low_win_probability(self, orch):
        """Blocks signals with win_probability below threshold (0.65 for A)."""
        report = orch.process_signal(_make_signal(win_probability=0.55))
        assert report.decision == "blocked"
        assert report.rule_triggered == "R1"

    def test_r2_orange_higher_threshold(self, orch):
        """At orange risk, threshold increases by win_prob_orange_add."""
        orch._current_risk = RiskReport(
            risk_score=0.55, risk_level="orange",
            kelly_mult=0.7, sl_mult=0.8,
            emergency_brake=False,
            signals={}, alerts=[], data_quality={},
        )
        # OrchestratorA: threshold = 0.65 + 0.05 = 0.70
        # win_prob 0.68 < 0.70 → blocked
        report = orch.process_signal(_make_signal(
            win_probability=0.68, strategy_id="momentum",
        ))
        assert report.decision == "blocked"
        assert report.rule_triggered == "R2"

    def test_r2_orange_allows_trend_follow(self, orch):
        """Trend-follow is allowed at orange even below raised threshold."""
        orch._current_risk = RiskReport(
            risk_score=0.55, risk_level="orange",
            kelly_mult=0.7, sl_mult=0.8,
            emergency_brake=False,
            signals={}, alerts=[], data_quality={},
        )
        report = orch.process_signal(_make_signal(
            win_probability=0.68, strategy_id="trend-follow",
        ))
        # trend-follow is in allow_at_orange for OrchestratorA
        assert report.decision == "executed"

    def test_r2_scalper_paused_at_orange(self, orch):
        """Arbitrage/scalper is always blocked at orange risk."""
        orch._current_risk = RiskReport(
            risk_score=0.55, risk_level="orange",
            kelly_mult=0.7, sl_mult=0.8,
            emergency_brake=False,
            signals={}, alerts=[], data_quality={},
        )
        report = orch.process_signal(_make_signal(
            win_probability=0.90, strategy_id="arbitrage",
        ))
        assert report.decision == "blocked"
        assert report.rule_triggered == "R2"

    def test_r3_red_blocks_all(self, orch):
        """Red risk blocks all strategies (OrchestratorA has no allow_at_red)."""
        orch._current_risk = RiskReport(
            risk_score=0.80, risk_level="red",
            kelly_mult=0.3, sl_mult=0.5,
            emergency_brake=False,
            signals={}, alerts=[], data_quality={},
        )
        report = orch.process_signal(_make_signal(win_probability=0.90))
        assert report.decision == "blocked"
        assert report.rule_triggered == "R3"

    def test_r5_drawdown_10pct(self, orch):
        """At >10% drawdown, only mean-reversion and trend-follow allowed."""
        orch.risk_mgr.current_capital = 8500.0  # 15% drawdown from 10000
        # momentum should be blocked
        report = orch.process_signal(_make_signal(strategy_id="momentum"))
        assert report.decision == "blocked"
        assert report.rule_triggered in ("R5", "R6")

    def test_r5_drawdown_allows_mean_reversion(self, orch):
        """Mean-reversion is allowed at >10% drawdown (but <15%)."""
        orch.risk_mgr.current_capital = 8800.0  # 12% drawdown
        report = orch.process_signal(_make_signal(
            strategy_id="mean-reversion", win_probability=0.70,
        ))
        assert report.decision == "executed"

    def test_r6_circuit_breaker(self, orch):
        """Circuit breaker at >= 15% drawdown halts all trading."""
        orch.risk_mgr.current_capital = 8400.0  # 16% drawdown
        orch.risk_mgr._check_drawdown()  # triggers halted=True
        report = orch.process_signal(_make_signal(win_probability=0.90))
        assert report.decision == "blocked"
        assert report.rule_triggered == "R6"


# ══════════════════════════════════════════════
# LAAG 3: PORTEFEUILLE-COHERENTIE (P1 – P4)
# ══════════════════════════════════════════════

class TestPortfolioCoherence:
    def test_p4_cooldown_blocks(self, orch):
        """Strategy in cooldown is blocked."""
        # Record enough losses to trigger cooldown
        from nakedtrader.money.adaptive import COOLDOWN_THRESHOLD
        for _ in range(COOLDOWN_THRESHOLD):
            orch.adaptive.record_trade("momentum", -2.0, "loss")
        assert orch.adaptive.is_in_cooldown("momentum")

        report = orch.process_signal(_make_signal(strategy_id="momentum"))
        assert report.decision == "blocked"
        assert report.rule_triggered == "P4"

    def test_p1_conflict_low_winrate(self, orch):
        """Blocks when instrument already held and strategy win-rate < 0.5."""
        orch.open_positions = {"XXBTZEUR": 500.0}
        # Don't record any wins → default win rate is 0.5 from no trades
        # With no trades, get_rolling_win_rate returns 0.5 (neutral)
        # Record a loss to push it below 0.5
        orch.adaptive.record_trade("momentum", -2.0, "loss")

        report = orch.process_signal(_make_signal(
            symbol="XXBTZEUR", strategy_id="momentum",
        ))
        assert report.decision == "blocked"
        assert report.rule_triggered == "P1"

    def test_p2_capital_scarcity(self, orch):
        """Low-priority strategies blocked when exposure near limit."""
        # capital=10000, limit=50%=5000, scarcity threshold=80%*5000=4000
        orch.open_positions = {"XETHZEUR": 4100.0}
        # breakout has priority index 3 (> 2) → blocked
        report = orch.process_signal(_make_signal(
            strategy_id="breakout", symbol="SOLUSD",
        ))
        assert report.decision == "blocked"
        assert report.rule_triggered == "P2"

    def test_p2_allows_high_priority(self, orch):
        """High-priority strategies pass even at capital scarcity."""
        orch.open_positions = {"XETHZEUR": 4100.0}
        # trend-follow has priority index 0 → allowed
        report = orch.process_signal(_make_signal(
            strategy_id="trend-follow", symbol="SPY", broker="ibkr",
            current_price=520.0,
        ))
        assert report.decision == "executed"


# ══════════════════════════════════════════════
# PAUSE MECHANISM
# ══════════════════════════════════════════════

class TestPauseMechanism:
    def test_pause_buys_blocks_long(self, orch):
        orch.pause_buys()
        report = orch.process_signal(_make_signal(direction="long"))
        assert report.decision == "blocked"
        assert report.rule_triggered == "PAUSE"

    def test_resume_buys_unblocks(self, orch):
        orch.pause_buys()
        orch.resume_buys()
        report = orch.process_signal(_make_signal())
        assert report.decision == "executed"

    def test_pause_strategy_blocks(self, orch):
        orch.pause_strategy("momentum")
        report = orch.process_signal(_make_signal(strategy_id="momentum"))
        assert report.decision == "blocked"
        assert report.rule_triggered == "PAUSE"

    def test_pause_strategy_others_pass(self, orch):
        orch.pause_strategy("momentum")
        report = orch.process_signal(_make_signal(strategy_id="trend-follow"))
        assert report.decision == "executed"

    def test_pause_state_persists(self, orch):
        orch.pause_buys()
        orch.pause_strategy("breakout")
        state = orch.get_pause_state()
        assert state["paused_buys"] is True
        assert "breakout" in state["paused_strategies"]

    def test_pause_sells(self, orch):
        orch.pause_sells()
        state = orch.get_pause_state()
        assert state["paused_sells"] is True
        orch.resume_sells()
        state = orch.get_pause_state()
        assert state["paused_sells"] is False


# ══════════════════════════════════════════════
# EXECUTION REPORT FIELDS
# ══════════════════════════════════════════════

class TestExecutionReportFields:
    def test_blocked_report_fields(self, orch):
        report = orch.process_signal(_make_signal(win_probability=0.40))
        assert report.decision == "blocked"
        assert report.orchestrator_id == "A"
        assert report.mode == "paper"
        assert report.size_executed == 0.0
        assert "signal" in report.__dict__ or hasattr(report, "signal")
        assert report.signal["symbol"] == "XXBTZEUR"

    def test_executed_report_fields(self, orch):
        report = orch.process_signal(_make_signal())
        assert report.decision == "executed"
        assert report.size_executed > 0
        assert report.total_exposure >= 0

    def test_multiple_executions_logged(self, orch):
        for _ in range(5):
            orch.process_signal(_make_signal(
                symbol="SPY", broker="ibkr", current_price=520.0,
            ))
        with open(orch._exec_log_path) as f:
            entries = json.load(f)
        assert len(entries) == 5


# ══════════════════════════════════════════════
# ORCHESTRATOR A SPECIFIC
# ══════════════════════════════════════════════

class TestOrchestratorAConfig:
    def test_conservative_thresholds(self, orch):
        assert orch.exposure_limit_pct == 0.50
        assert orch.win_prob_threshold == 0.65
        assert orch.kelly_correction == 0.8
        assert orch.force_paper is False

    def test_allow_at_orange(self, orch):
        assert "trend-follow" in orch.allow_at_orange

    def test_allow_at_red_empty(self, orch):
        assert orch.allow_at_red == []

    def test_get_status(self, orch):
        status = orch.get_status()
        assert status["orchestrator_id"] == "A"
        assert status["mode"] == "paper"
        assert "pause_state" in status
        assert "drawdown_pct" in status
