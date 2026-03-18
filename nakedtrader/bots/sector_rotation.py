"""
sector_rotation.py — Sector Rotation strategie via IBKR.

Relatieve momentum-ranking over 30 sector/thematische ETFs.
Roteert naar de sterkste sectoren met samengestelde ROC(21/63)-score.
"""

import logging

import numpy as np

from nakedtrader.types import TradeSignal, StrategyMeta
from nakedtrader.indicators import sma, rsi, roc
from .base import BaseStrategy
from .data_feeds import _equity_daily_bars, _equity_get_price

log = logging.getLogger(__name__)


class SectorRotationStrategy(BaseStrategy):
    """Sector Rotation — roteert naar de sterkste sectoren op momentum-ranking."""

    meta = StrategyMeta(
        id="sector-rotation",
        name="Sector Rotation",
        color="#ff6600",
        description="Relative momentum ranking across 30 sector/thematic ETFs. Rotates into top-ranked sectors with composite ROC(21/63) scoring.",
        description_nl="Relatieve momentum-ranking over 30 sector/thematische ETFs. Roteert naar de sterkste sectoren met samengestelde ROC(21/63)-score.",
        risk_level="Gematigd",
        risk_score=3,
        expected_return_min=6.0,
        expected_return_max=25.0,
        markets=[
            "XLB", "XLC", "XLI", "XLP", "XLU", "XLRE", "XLY",
            "XBI", "XHB", "XME", "XOP", "XRT", "XSD",
            "EEM", "EWJ", "EWZ", "EWG", "FXI",
            "TLT", "IEF", "LQD", "HYG",
            "GLD", "SLV", "GDX", "USO", "UNG",
            "ARKK", "KWEB", "IBIT",
        ],
        indicators=["ROC(21)", "ROC(63)", "SMA(50)", "RSI(14)"],
        timeframe="1d",
        broker="ibkr",
    )

    UNIVERSE = {
        # Sector ETFs
        "XLB": "materials", "XLC": "communication", "XLI": "industrials",
        "XLP": "staples", "XLU": "utilities", "XLRE": "real-estate",
        "XLY": "discretionary", "XBI": "biotech", "XHB": "homebuilders",
        "XME": "metals-mining", "XOP": "oil-gas", "XRT": "retail",
        "XSD": "semiconductors",
        # International / Regional
        "EEM": "emerging", "EWJ": "japan", "EWZ": "brazil",
        "EWG": "germany", "FXI": "china",
        # Fixed Income / Defensive
        "TLT": "bonds-long", "IEF": "bonds-mid", "LQD": "corp-bonds",
        "HYG": "high-yield",
        # Commodity-linked
        "GLD": "gold", "SLV": "silver", "GDX": "gold-miners",
        "USO": "oil", "UNG": "nat-gas",
        # Specialty
        "ARKK": "innovation", "KWEB": "china-internet", "IBIT": "btc-etf",
    }

    def generate_signals(self, kraken=None, ibkr=None, aggressive=False) -> list[TradeSignal]:
        signals = []
        ibkr_ok = ibkr and getattr(ibkr, "is_connected", False)

        # Bereken momentum scores voor alle ETFs
        scores = []
        for symbol, sector in self.UNIVERSE.items():
            try:
                data = _equity_daily_bars(symbol, ibkr, ibkr_ok)
                if not data or len(data.get("close", [])) < 63:
                    continue

                closes = data["close"]

                roc21 = roc(closes, 21)
                roc63 = roc(closes, 63)
                sma50 = sma(closes, 50)
                rsi_vals = rsi(closes, 14)

                if any(np.isnan(x[-1]) for x in [roc21, roc63, sma50, rsi_vals]):
                    continue

                composite = 0.6 * roc21[-1] + 0.4 * roc63[-1]
                scores.append({
                    "symbol": symbol,
                    "sector": sector,
                    "composite": composite,
                    "close": closes[-1],
                    "sma50": sma50[-1],
                    "rsi": rsi_vals[-1],
                })
            except Exception as e:
                log.warning("Sector Rotation fout %s: %s", symbol, e)

        if not scores:
            return signals

        # Rank op composite score
        scores.sort(key=lambda x: x["composite"], reverse=True)
        win_rate = self._get_rolling_win_rate()

        # Long: top ranked ETFs
        for rank, s in enumerate(scores):
            if rank >= 10:
                break

            # Filters
            abs_threshold = -2 if aggressive else 0
            if s["composite"] <= abs_threshold:
                continue
            if s["close"] <= s["sma50"]:
                continue

            rsi_low = 25 if aggressive else 30
            rsi_high = 80 if aggressive else 75
            if not (rsi_low < s["rsi"] < rsi_high):
                continue

            price = _equity_get_price(s["symbol"], ibkr, ibkr_ok) or s["close"]
            rank_boost = 0.03 if rank < 3 else 0.01 if rank < 6 else 0.0
            wp = max(0.45, min(0.70, win_rate + rank_boost))

            signals.append(TradeSignal(
                symbol=s["symbol"],
                broker="ibkr",
                direction="long",
                win_probability=wp,
                expected_win_pct=0.06,
                expected_loss_pct=0.03,
                current_price=price,
                strategy_id="sector-rotation",
                notes=(
                    f"Sector Rotation LONG {s['symbol']} ({s['sector']}): "
                    f"rank={rank+1} composite={s['composite']:.1f}% "
                    f"RSI={s['rsi']:.0f}"
                ),
            ))

        # Short: bottom ranked (aggressive only)
        if aggressive:
            for rank, s in enumerate(reversed(scores)):
                if rank >= 5:
                    break
                if s["composite"] >= -5:
                    continue
                if s["close"] >= s["sma50"]:
                    continue
                if not (40 < s["rsi"] < 80):
                    continue

                price = _equity_get_price(s["symbol"], ibkr, ibkr_ok) or s["close"]
                wp = max(0.40, min(0.60, win_rate - 0.02))

                signals.append(TradeSignal(
                    symbol=s["symbol"],
                    broker="ibkr",
                    direction="short",
                    win_probability=wp,
                    expected_win_pct=0.06,
                    expected_loss_pct=0.03,
                    current_price=price,
                    strategy_id="sector-rotation",
                    notes=(
                        f"Sector Rotation SHORT {s['symbol']} ({s['sector']}): "
                        f"composite={s['composite']:.1f}% RSI={s['rsi']:.0f}"
                    ),
                ))

        return self._llm_enhance_signals(signals)
