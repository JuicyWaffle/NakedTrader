"""
nakedtrader.orchestrator — TradingBot: orkestreert signalen → sizing → orders → logging.
"""

import time
import random
import logging
from typing import Optional
from datetime import datetime

from nakedtrader.config import Config
from nakedtrader.types import TradeSignal
from nakedtrader.money.kelly import KellyPositionSizer
from nakedtrader.money.risk import RiskManager, RiskLimits
from nakedtrader.money.adaptive import AdaptiveEngine
from nakedtrader.brokers.ibkr import IBKRBroker
from nakedtrader.brokers.kraken import KrakenBroker
from nakedtrader.brokers.binance import BinanceBroker
from nakedtrader.performance.tracker import PerformanceTracker
from nakedtrader.performance.paper_engine import PaperEngine

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
            strategy_id=sid,
            notes=f"Demo signaal {sym}",
        )
        for sym, broker, price, wp, ew, el, sid in chosen
    ]


class TradingBot:
    """Orkestreert signalen → positiebepaling → orders → logging."""

    def __init__(self, config: Config):
        self.config = config
        self.sizer = KellyPositionSizer(config.kelly_fraction, config.max_position_pct)
        self.tracker = PerformanceTracker(config.trade_log_path)
        self.paper = PaperEngine(config, self.tracker)

        # Adaptive + Risk
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
                )
                log.info("Macro risk engine actief (interval=%dm)", config.macro_risk_interval_min)
            except Exception as e:
                log.warning("Macro risk engine niet beschikbaar: %s", e)

        self.ibkr: Optional[IBKRBroker] = None
        self.kraken: Optional[KrakenBroker] = None
        self.binance: Optional[BinanceBroker] = None
        self.open_positions: list[str] = []

        self._last_rollup_date = None

    def connect_brokers(self):
        if self.config.paper_mode:
            log.info("Paper mode actief — geen broker-verbinding nodig")
            return
        log.info("Live mode: verbinding maken met brokers...")
        self.ibkr = IBKRBroker(self.config)
        self.ibkr.connect()
        if self.config.crypto_broker == "kraken":
            self.kraken = KrakenBroker(self.config)
            self.kraken.connect()
        else:
            self.binance = BinanceBroker(self.config)
            self.binance.connect()

    def disconnect_brokers(self):
        if self.ibkr:
            self.ibkr.disconnect()
        log.info("Alle verbindingen gesloten")

    def process_signal(self, signal: TradeSignal):
        """Verwerk één signaal via de signal flow."""
        sid = signal.strategy_id or ""

        if len(self.open_positions) >= self.config.max_open_positions:
            log.warning(f"Max posities bereikt, {signal.symbol} overgeslagen")
            return

        # 0. Macro risk veto
        if self._current_risk and self._current_risk.emergency_brake:
            log.warning(f"[MACRO] Noodrem actief (score={self._current_risk.risk_score:.2f}) — {signal.symbol} overgeslagen")
            return

        # 1. Drawdown circuit-breaker
        if self.risk_mgr.halted:
            log.warning(f"[RISK] Circuit-breaker actief — {signal.symbol} overgeslagen")
            return

        # 2. Cooldown check
        if sid and self.adaptive.is_in_cooldown(sid):
            log.info(f"[ADAPTIVE] {sid} in cooldown — {signal.symbol} overgeslagen")
            return

        # 3. Kelly met adaptive win-rate
        win_prob = signal.win_probability
        if sid:
            state = self.adaptive.get_state(sid)
            if len(state.recent_trades) >= 10:
                win_prob = self.adaptive.get_rolling_win_rate(sid)

        kelly_frac = self.sizer.calculate(win_prob, signal.expected_win_pct, signal.expected_loss_pct)

        # Macro risk Kelly-reductie
        if self._current_risk:
            kelly_frac *= self._current_risk.kelly_mult

        size_eur = self.config.total_capital * kelly_frac

        if size_eur < 10:
            return

        # 4. Per-strategie budget check
        if sid and not self.risk_mgr.allow_trade(sid, size_eur):
            return

        # 5. Per-strategie SL/TP
        risk_params = self.risk_mgr.get_risk_params(sid) if sid else {
            "sl": self.config.stop_loss_pct, "tp": self.config.take_profit_pct,
        }

        if sid:
            self.risk_mgr.allocate(sid, size_eur)

        if self.config.paper_mode:
            trade = self.paper.process_signal(signal, kelly_frac, size_eur)
        else:
            sl_pct = risk_params["sl"]
            tp_pct = risk_params["tp"]

            if signal.broker == "ibkr" and self.ibkr:
                quantity = int(size_eur / signal.current_price)
                if quantity >= 1:
                    self.ibkr.place_bracket_order(
                        symbol=signal.symbol, quantity=quantity,
                        entry_price=signal.current_price,
                        stop_loss_pct=sl_pct, take_profit_pct=tp_pct,
                    )
            elif signal.broker == "kraken" and self.kraken:
                qty = round(size_eur / signal.current_price, 8)
                self.kraken.place_market_order(signal.symbol, "buy", qty)
                stop = round(signal.current_price * (1 - sl_pct), 2)
                tp = round(signal.current_price * (1 + tp_pct), 2)
                self.kraken.place_stop_loss_take_profit(signal.symbol, qty, stop, tp)
            elif signal.broker == "binance" and self.binance:
                qty = round(size_eur / signal.current_price, 5)
                self.binance.place_market_order(signal.symbol, "BUY", qty)
                stop = round(signal.current_price * (1 - sl_pct), 2)
                tp = round(signal.current_price * (1 + tp_pct), 2)
                self.binance.place_oco_order(signal.symbol, qty, stop, tp)

            trade = self.paper.process_signal(signal, kelly_frac, size_eur)

        # 6. Log naar AdaptiveEngine + RiskManager
        if sid and trade:
            self.adaptive.record_trade(sid, trade.pnl_pct, trade.outcome)
            self.risk_mgr.update_capital(trade.pnl_eur, sid)

            # Record naar PerformanceStore als beschikbaar
            if self.store:
                self.store.record_trade(trade)

        self.open_positions.append(signal.symbol)

    def _check_macro_risk(self):
        """Periodieke macro risk hercheck."""
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
                "[MACRO] score=%.2f level=%s kelly×%.2f sl×%.2f%s",
                self._current_risk.risk_score,
                self._current_risk.risk_level,
                self._current_risk.kelly_mult,
                self._current_risk.sl_mult,
                " ⚠ NOODREM" if self._current_risk.emergency_brake else "",
            )
        except Exception as e:
            log.warning("[MACRO] Evaluatie mislukt: %s", e)

    def _check_rollups(self):
        """Check of EOD/EOM rollup nodig is."""
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

    def run_once(self, signals: Optional[list[TradeSignal]] = None):
        mode_label = "PAPER" if self.config.paper_mode else "LIVE"
        log.info(f"─── [{mode_label}] Run {datetime.now().strftime('%H:%M:%S')} ───")

        if signals is None:
            signals = demo_signals(n=2)

        for signal in signals:
            self.process_signal(signal)

        self.tracker.print_summary(self.config.total_capital)

    def run_loop(self, interval_seconds: int = 60, signals_fn=None):
        mode = "PAPER" if self.config.paper_mode else "LIVE"
        log.info(f"Bot gestart  modus={mode}  interval={interval_seconds}s")

        while True:
            try:
                self._check_macro_risk()
                signals = signals_fn() if signals_fn else None
                self.run_once(signals)
                self._check_rollups()
                time.sleep(interval_seconds)
            except KeyboardInterrupt:
                log.info("Bot gestopt")
                break
            except Exception as e:
                log.error(f"Fout: {e}", exc_info=True)
                time.sleep(10)
