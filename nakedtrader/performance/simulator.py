"""
nakedtrader.performance.simulator — Realistische fictieve trade-simulatie per strategie.

Elke strategie heeft een eigen profiel (frequentie, uren, win-rate, PnL-spreiding).
Dagelijks regime (z-score) correleert returns over de hele dag.
"""

import math
import random
import logging
from datetime import datetime, timedelta

from nakedtrader.types import TradeRecord

log = logging.getLogger(__name__)

# ─── Per-strategie simulatieprofielen ─────────────────────

STRATEGY_PROFILES = {
    "momentum": {
        "name": "Momentum Rider",
        "trades_per_day": (4, 10),
        "active_hours": (9, 21),
        "base_win_rate": 0.55,
        "avg_win_pct": 0.025,       # 2.5% per trade
        "avg_loss_pct": 0.015,       # 1.5% per trade
        "win_stddev": 0.010,
        "loss_stddev": 0.005,
        "symbols": ["AAPL", "MSFT", "TSLA", "NVDA", "AMZN"],
        "broker": "ibkr",
        "base_price_range": (100, 500),
    },
    "mean-reversion": {
        "name": "Mean Reversion",
        "trades_per_day": (3, 8),
        "active_hours": (8, 20),
        "base_win_rate": 0.60,
        "avg_win_pct": 0.020,       # 2.0% per trade
        "avg_loss_pct": 0.010,       # 1.0% per trade
        "win_stddev": 0.008,
        "loss_stddev": 0.004,
        "symbols": ["XETHZEUR", "XXBTZEUR", "XLTCZEUR"],
        "broker": "kraken",
        "base_price_range": (50, 65000),
    },
    "breakout": {
        "name": "Breakout Hunter",
        "trades_per_day": (2, 6),
        "active_hours": (10, 22),
        "base_win_rate": 0.42,
        "avg_win_pct": 0.040,       # 4.0% — hogere winst, lagere win-rate
        "avg_loss_pct": 0.020,       # 2.0%
        "win_stddev": 0.015,
        "loss_stddev": 0.008,
        "symbols": ["SOLUSD", "ADAUSD", "DOTUSD"],
        "broker": "kraken",
        "base_price_range": (0.5, 200),
    },
    "arbitrage": {
        "name": "Crypto Scalper",
        "trades_per_day": (15, 30),
        "active_hours": (0, 24),
        "base_win_rate": 0.62,
        "avg_win_pct": 0.005,       # 0.5% — veel kleine trades
        "avg_loss_pct": 0.0015,      # 0.15%
        "win_stddev": 0.002,
        "loss_stddev": 0.0006,
        "symbols": ["XXBTZEUR", "XETHZEUR"],
        "broker": "kraken",
        "base_price_range": (3000, 65000),
    },
    "trend-follow": {
        "name": "Macro Trend",
        "trades_per_day": (1, 3),
        "active_hours": (14, 21),
        "base_win_rate": 0.52,
        "avg_win_pct": 0.035,       # 3.5% — grotere moves, minder trades
        "avg_loss_pct": 0.015,       # 1.5%
        "win_stddev": 0.014,
        "loss_stddev": 0.006,
        "symbols": ["SPY", "QQQ", "TLT", "GLD"],
        "broker": "ibkr",
        "base_price_range": (100, 550),
    },
    "funding-contrarian": {
        "name": "Funding Contrarian",
        "trades_per_day": (1, 4),
        "active_hours": (0, 24),
        "base_win_rate": 0.56,
        "avg_win_pct": 0.030,
        "avg_loss_pct": 0.015,
        "win_stddev": 0.012,
        "loss_stddev": 0.006,
        "symbols": ["XXBTZEUR", "XETHZEUR"],
        "broker": "kraken",
        "base_price_range": (3000, 65000),
    },
    "cross-arb": {
        "name": "Cross-Exchange Arb",
        "trades_per_day": (5, 15),
        "active_hours": (0, 24),
        "base_win_rate": 0.72,
        "avg_win_pct": 0.003,
        "avg_loss_pct": 0.001,
        "win_stddev": 0.001,
        "loss_stddev": 0.0005,
        "symbols": ["XXBTZEUR", "XETHZEUR"],
        "broker": "kraken",
        "base_price_range": (3000, 65000),
    },
    "vol-regime": {
        "name": "Volatility Regime",
        "trades_per_day": (0, 2),
        "active_hours": (8, 22),
        "base_win_rate": 0.54,
        "avg_win_pct": 0.035,
        "avg_loss_pct": 0.018,
        "win_stddev": 0.015,
        "loss_stddev": 0.007,
        "symbols": ["XXBTZEUR", "XETHZEUR"],
        "broker": "kraken",
        "base_price_range": (3000, 65000),
    },
    "equity-momentum": {
        "name": "Equity Momentum",
        "trades_per_day": (1, 5),
        "active_hours": (14, 21),
        "base_win_rate": 0.58,
        "avg_win_pct": 0.030,
        "avg_loss_pct": 0.015,
        "win_stddev": 0.012,
        "loss_stddev": 0.006,
        "symbols": ["AAPL", "MSFT", "GOOGL", "NVDA", "META", "JPM", "UNH", "XOM"],
        "broker": "ibkr",
        "base_price_range": (50, 500),
    },
    "sector-rotation": {
        "name": "Sector Rotation",
        "trades_per_day": (2, 5),
        "active_hours": (14, 21),
        "base_win_rate": 0.56,
        "avg_win_pct": 0.025,
        "avg_loss_pct": 0.012,
        "win_stddev": 0.010,
        "loss_stddev": 0.005,
        "symbols": ["XLB", "XLC", "XLI", "XLP", "XLY", "XBI", "GLD", "EEM", "TLT"],
        "broker": "ibkr",
        "base_price_range": (15, 250),
    },
    "equity-mean-reversion": {
        "name": "Equity Mean Reversion",
        "trades_per_day": (1, 4),
        "active_hours": (14, 21),
        "base_win_rate": 0.62,
        "avg_win_pct": 0.020,
        "avg_loss_pct": 0.010,
        "win_stddev": 0.008,
        "loss_stddev": 0.004,
        "symbols": ["AAPL", "MSFT", "AMZN", "CRM", "INTC", "AMD", "C", "WFC", "MRK", "HD"],
        "broker": "ibkr",
        "base_price_range": (30, 500),
    },
    "etf-trend-follow": {
        "name": "ETF Trend Follow",
        "trades_per_day": (2, 6),
        "active_hours": (14, 21),
        "base_win_rate": 0.48,
        "avg_win_pct": 0.035,
        "avg_loss_pct": 0.018,
        "win_stddev": 0.015,
        "loss_stddev": 0.007,
        "symbols": ["IWM", "MDY", "DIA", "INDA", "ICLN", "SMH", "URA", "GDX", "AGG"],
        "broker": "ibkr",
        "base_price_range": (10, 500),
    },
    "dividend-value": {
        "name": "Dividend & Value",
        "trades_per_day": (1, 3),
        "active_hours": (14, 21),
        "base_win_rate": 0.64,
        "avg_win_pct": 0.015,
        "avg_loss_pct": 0.008,
        "win_stddev": 0.006,
        "loss_stddev": 0.003,
        "symbols": ["KO", "PG", "T", "VZ", "O", "VIG", "SCHD", "NEE", "DUK"],
        "broker": "ibkr",
        "base_price_range": (20, 200),
    },
    "equity-vol-breakout": {
        "name": "Equity Vol Breakout",
        "trades_per_day": (2, 5),
        "active_hours": (14, 22),
        "base_win_rate": 0.44,
        "avg_win_pct": 0.045,
        "avg_loss_pct": 0.025,
        "win_stddev": 0.020,
        "loss_stddev": 0.010,
        "symbols": ["MSTR", "COIN", "XYZ", "PLTR", "SHOP", "SNAP", "GME", "RIVN", "SOFI"],
        "broker": "ibkr",
        "base_price_range": (5, 400),
    },
}


class RealisticSimulator:
    """Genereer realistische fictieve trades per strategie met dag-correlatie."""

    def __init__(self, starting_capital: float = 10_000.0, seed: int = 42):
        self.starting_capital = starting_capital
        self.rng = random.Random(seed)
        self._trade_counter = 0

    def _next_id(self) -> str:
        self._trade_counter += 1
        return f"SIM{self._trade_counter:06d}"

    def _lognormal_pct(self, mean_pct: float, stddev: float) -> float:
        """Trek uit log-normale verdeling met gegeven gemiddelde en stddev."""
        if mean_pct <= 0:
            return 0.0
        variance = stddev ** 2
        mu = math.log(mean_pct ** 2 / math.sqrt(mean_pct ** 2 + variance))
        sigma = math.sqrt(math.log(1 + variance / mean_pct ** 2))
        return self.rng.lognormvariate(mu, sigma)

    def simulate_day(
        self,
        date: datetime,
        strategy_id: str,
        capital: float,
    ) -> list[TradeRecord]:
        """Simuleer alle trades voor één strategie op één dag."""
        profile = STRATEGY_PROFILES.get(strategy_id)
        if not profile:
            return []

        # Weekend: geen equity-trades
        if date.weekday() >= 5 and profile["broker"] == "ibkr":
            return []

        # ── Dagelijks regime (z-score) beïnvloedt win-rate ──
        regime = self.rng.gauss(0, 1)
        adjusted_win_rate = profile["base_win_rate"] + regime * 0.05
        adjusted_win_rate = max(0.15, min(0.85, adjusted_win_rate))

        # ── Aantal trades vandaag ──
        min_t, max_t = profile["trades_per_day"]
        trade_count = self.rng.randint(min_t, max_t)
        if regime > 1.0:
            trade_count = min(trade_count + 2, max_t + 3)
        elif regime < -1.0:
            trade_count = max(trade_count - 1, 1)

        # ── Willekeurige tijdstippen binnen actieve uren ──
        h_start, h_end = profile["active_hours"]
        h_end_clamp = min(h_end, 24) - 1

        times = sorted([
            date.replace(
                hour=self.rng.randint(h_start, max(h_start, h_end_clamp)),
                minute=self.rng.randint(0, 59),
                second=self.rng.randint(0, 59),
                microsecond=0,
            )
            for _ in range(trade_count)
        ])

        # ── Genereer trades ──
        trades: list[TradeRecord] = []
        running_capital = capital

        for trade_time in times:
            symbol = self.rng.choice(profile["symbols"])

            # Stabiele basisprijs per symbool+maand
            price_lo, price_hi = profile["base_price_range"]
            sym_hash = hash(symbol + date.strftime("%Y-%m")) % 10000
            base_price = price_lo + (sym_hash / 10000) * (price_hi - price_lo)
            entry_price = max(0.01, round(base_price * (1 + self.rng.gauss(0, 0.02)), 4))

            # Win/verlies
            won = self.rng.random() < adjusted_win_rate

            if won:
                pnl_pct = self._lognormal_pct(
                    profile["avg_win_pct"], profile["win_stddev"]
                )
                exit_price = round(entry_price * (1 + pnl_pct), 4)
                outcome = "win"
            else:
                pnl_pct = -self._lognormal_pct(
                    profile["avg_loss_pct"], profile["loss_stddev"]
                )
                exit_price = round(entry_price * (1 + pnl_pct), 4)
                outcome = "loss"

            # Positiegrootte: 1-4% van kapitaal
            size_frac = self.rng.uniform(0.01, 0.04)
            size_eur = round(running_capital * size_frac, 2)
            if size_eur < 1:
                continue
            quantity = round(size_eur / entry_price, 6)
            pnl_eur = round(pnl_pct * size_eur, 2)

            trade = TradeRecord(
                id=self._next_id(),
                timestamp=trade_time.isoformat(timespec="seconds"),
                symbol=symbol,
                broker=profile["broker"],
                direction="long",
                mode="paper",
                entry_price=entry_price,
                exit_price=exit_price,
                quantity=quantity,
                size_eur=size_eur,
                pnl_eur=pnl_eur,
                pnl_pct=round(pnl_pct * 100, 2),
                outcome=outcome,
                kelly_fraction=round(size_frac, 4),
                win_probability=round(adjusted_win_rate, 3),
                strategy_id=strategy_id,
                notes=f"sim regime={regime:.2f}",
            )
            trades.append(trade)
            running_capital += pnl_eur

        return trades
