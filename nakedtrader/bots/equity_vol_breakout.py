"""
equity_vol_breakout.py — Equity Vol Breakout strategie via IBKR.

Volatiliteitssqueeze-detectie op hoog-beta aandelen.
Stapt in wanneer volatiliteit comprimeert en dan expandeert,
meeliftend op de breakout in de richting van prijsmomentum.
"""

import logging

import numpy as np

from nakedtrader.types import TradeSignal, StrategyMeta
from nakedtrader.indicators import ema, rsi, atr, bollinger_bands, vix_proxy
from .base import BaseStrategy
from .data_feeds import _equity_daily_bars, _equity_get_price

log = logging.getLogger(__name__)


class EquityVolBreakoutStrategy(BaseStrategy):
    """Equity Vol Breakout — volatiliteitssqueeze detectie op high-beta aandelen."""

    meta = StrategyMeta(
        id="equity-vol-breakout",
        name="Equity Vol Breakout",
        color="#ff44aa",
        description="Volatility squeeze detection on high-beta stocks. Enters when vol compresses then expands, riding the breakout in the direction of price momentum.",
        description_nl="Volatiliteitssqueeze-detectie op hoog-beta aandelen. Stapt in wanneer volatiliteit comprimeert en dan expandeert, meeliftend op de breakout in de richting van prijsmomentum.",
        risk_level="Risicovol",
        risk_score=5,
        expected_return_min=-5.0,
        expected_return_max=50.0,
        markets=[
            "MSTR", "COIN", "SQ", "SHOP", "SNAP", "PLTR",
            "NET", "DKNG", "RBLX", "ROKU", "SOFI", "UPST",
            "ARKW", "ARKG", "SMH", "SOXX", "IBB",
            "GME", "AMC", "RIVN", "LCID",
            "VXX", "SVXY", "SPXS", "XBI",
        ],
        indicators=["VIX proxy(20)", "BB(20,2.0) on VIX", "ATR(14)", "EMA(9)", "RSI(14)"],
        timeframe="1d",
        broker="ibkr",
    )

    UNIVERSE = {
        # High-beta tech
        "MSTR": "tech-highbeta", "COIN": "fintech", "SQ": "fintech",
        "SHOP": "tech", "SNAP": "tech", "PLTR": "tech",
        "NET": "tech", "DKNG": "gaming", "RBLX": "gaming",
        "ROKU": "tech", "SOFI": "fintech", "UPST": "fintech",
        # High-beta ETFs
        "ARKW": "innovation", "ARKG": "genomics",
        "SMH": "semiconductors", "SOXX": "semiconductors", "IBB": "biotech",
        # Meme / EV
        "GME": "meme", "AMC": "meme", "RIVN": "ev", "LCID": "ev",
        # Volatility-linked
        "VXX": "vix", "SVXY": "inverse-vix", "SPXS": "inverse-sp",
        "XBI": "biotech-small",
    }

    def generate_signals(self, kraken=None, ibkr=None, aggressive=False) -> list[TradeSignal]:
        signals = []
        ibkr_ok = ibkr and getattr(ibkr, "is_connected", False)

        for symbol, sector in self.UNIVERSE.items():
            try:
                data = _equity_daily_bars(symbol, ibkr, ibkr_ok, period="6mo")
                if not data or len(data.get("close", [])) < 50:
                    continue

                closes = data["close"]
                highs = data["high"]
                lows = data["low"]

                # VIX proxy (rolling volatiliteit)
                vix_vals = vix_proxy(closes, 20)
                valid_vix = vix_vals[~np.isnan(vix_vals)]
                if len(valid_vix) < 20:
                    continue

                # Bollinger Bands op de VIX proxy zelf
                vix_bb_upper, vix_bb_mid, vix_bb_lower = bollinger_bands(valid_vix, 20, 2.0)
                if np.isnan(vix_bb_lower[-1]) or np.isnan(vix_bb_mid[-1]):
                    continue

                # ATR voor squeeze confirmatie
                atr_vals = atr(highs, lows, closes, 14)
                ema9 = ema(closes, 9)
                rsi_vals = rsi(closes, 14)

                if any(np.isnan(x[-1]) for x in [atr_vals, ema9, rsi_vals]):
                    continue

                latest_vix = valid_vix[-1]
                prev_vix = valid_vix[-2] if len(valid_vix) > 1 else latest_vix

                # ── Squeeze detectie ──
                if aggressive:
                    # Versoepeld: VIX proxy < middle BB
                    squeeze = latest_vix < vix_bb_mid[-1]
                else:
                    # Strikt: VIX proxy < lower BB
                    squeeze = latest_vix < vix_bb_lower[-1]

                # ATR op 20-dag low
                atr_window = 20 if not aggressive else 10
                if len(atr_vals) > atr_window:
                    recent_atr = atr_vals[-atr_window:]
                    valid_atr = recent_atr[~np.isnan(recent_atr)]
                    if len(valid_atr) > 0:
                        atr_at_low = atr_vals[-1] <= np.min(valid_atr) * 1.05
                    else:
                        atr_at_low = False
                else:
                    atr_at_low = False

                if not (squeeze and atr_at_low):
                    continue

                # ── Breakout entry: VIX proxy kruist boven middle BB ──
                expansion = latest_vix > vix_bb_mid[-1] or (
                    prev_vix < vix_bb_mid[-2] if len(vix_bb_mid) > 1 and not np.isnan(vix_bb_mid[-2]) else False
                )

                # Voor squeeze + ATR low, check of expansie recent begonnen is
                # (binnen laatste 3 bars)
                recent_expansion = False
                if len(valid_vix) >= 3 and len(vix_bb_mid) >= 3:
                    for i in range(1, min(4, len(valid_vix))):
                        idx = -i
                        mid_idx = -i
                        if not np.isnan(vix_bb_mid[mid_idx]):
                            if valid_vix[idx] > vix_bb_mid[mid_idx]:
                                recent_expansion = True
                                break

                if not (expansion or recent_expansion):
                    # Squeeze gedetecteerd maar nog geen expansie — skip
                    continue

                # Richting op basis van prijs vs EMA(9)
                if closes[-1] > ema9[-1]:
                    direction = "long"
                elif aggressive:
                    direction = "short"
                else:
                    continue  # Alleen long in normaal mode

                price = _equity_get_price(symbol, ibkr, ibkr_ok) or float(closes[-1])
                win_rate = self._get_rolling_win_rate()
                wp = max(0.40, min(0.62, win_rate))

                signals.append(TradeSignal(
                    symbol=symbol,
                    broker="ibkr",
                    direction=direction,
                    win_probability=wp,
                    expected_win_pct=0.12,
                    expected_loss_pct=0.06,
                    current_price=price,
                    strategy_id="equity-vol-breakout",
                    notes=(
                        f"Vol Breakout {direction.upper()} {symbol} ({sector}): "
                        f"VIX={latest_vix:.1f} ATR={atr_vals[-1]:.2f} "
                        f"RSI={rsi_vals[-1]:.0f} squeeze→expansion"
                    ),
                ))

            except Exception as e:
                log.warning("Equity Vol Breakout fout %s: %s", symbol, e)

        return self._llm_enhance_signals(signals)
