"""
vol_regime.py — Volatility Regime Rotation strategie.

Meta-strategie die handelt op volatiliteitsregime-transities:
- VIX proxy omhoog: defensief (stablecoins, bonds proxy)
- VIX proxy omlaag: offensief (high-beta crypto)
- Fear & Greed extremen: contrarian positioning

Gebruikt de nieuwe macro risk signalen als entry/exit triggers.
"""

import logging

import numpy as np
import requests

from nakedtrader.types import TradeSignal, StrategyMeta
from nakedtrader.indicators import sma, vix_proxy
from .base import BaseStrategy
from .data_feeds import _kraken_ohlc, _kraken_ticker

log = logging.getLogger(__name__)


def _fetch_fear_greed_value() -> int:
    """Haal Fear & Greed Index op (0-100). Returns 50 bij fout."""
    try:
        resp = requests.get("https://api.alternative.me/fng/?limit=1", timeout=10)
        data = resp.json().get("data", [])
        return int(data[0]["value"]) if data else 50
    except Exception:
        return 50


class VolRegimeStrategy(BaseStrategy):
    """Volatility Regime Rotation — handelt op volatiliteits-regime transities."""

    meta = StrategyMeta(
        id="vol-regime",
        name="Volatility Regime",
        color="#ddaa00",
        description="Trades volatility regime transitions. Rotates between defensive and offensive positions based on VIX proxy, Fear & Greed, and funding rate signals.",
        description_nl="Handelt op volatiliteitsregime-transities. Roteert tussen defensieve en offensieve posities op basis van VIX proxy, Fear & Greed, en funding rate signalen.",
        risk_level="Gematigd",
        risk_score=2,
        expected_return_min=8.0,
        expected_return_max=30.0,
        markets=["BTC/EUR", "ETH/EUR"],
        indicators=["VIX proxy", "SMA(20)", "Fear & Greed", "Funding Rate"],
        timeframe="4h",
        broker="kraken",
    )

    OFFENSIVE_PAIRS = {"BTC/EUR": "XXBTZEUR", "ETH/EUR": "XETHZEUR"}
    VIX_HIGH_THRESHOLD = 25.0   # boven = defensief regime
    VIX_LOW_THRESHOLD = 18.0    # onder = offensief regime

    def generate_signals(self, kraken=None, ibkr=None, aggressive=False) -> list[TradeSignal]:
        signals = []

        # Bereken VIX proxy van BTC als marktbrede volatiliteits-indicator
        try:
            btc_data = _kraken_ohlc("XXBTZEUR", interval=240, count=200)
            if not btc_data:
                return signals
            closes = btc_data["close"]
            vix_vals = vix_proxy(closes, 20)
            vix_sma = sma(vix_vals[~np.isnan(vix_vals)], 20) if np.sum(~np.isnan(vix_vals)) >= 20 else None
        except Exception as e:
            log.warning("Vol Regime VIX proxy mislukt: %s", e)
            return signals

        latest_vix = vix_vals[-1] if not np.isnan(vix_vals[-1]) else 20
        prev_vix = vix_vals[-2] if len(vix_vals) > 1 and not np.isnan(vix_vals[-2]) else latest_vix

        # Detecteer regime-transitie
        vix_rising = latest_vix > prev_vix * 1.05   # 5% stijging
        vix_falling = latest_vix < prev_vix * 0.95   # 5% daling

        # Fear & Greed als contrarian bevestiging
        fg_value = _fetch_fear_greed_value()
        extreme_fear = fg_value <= 20
        extreme_greed = fg_value >= 80

        # Custom state voor regime tracking
        custom = self._get_custom_state()
        current_regime = custom.get("regime", "neutral")

        # Regime transitie detectie
        new_regime = current_regime
        if latest_vix > self.VIX_HIGH_THRESHOLD and vix_rising:
            new_regime = "defensive"
        elif latest_vix < self.VIX_LOW_THRESHOLD and vix_falling:
            new_regime = "offensive"

        # Extreme Fear override: ga offensief (contrarian)
        if extreme_fear and current_regime != "offensive":
            new_regime = "offensive"
            log.info("Vol Regime: Extreme Fear (%d) → contrarian offensief", fg_value)

        # Extreme Greed override: ga defensief (contrarian)
        if extreme_greed and current_regime != "defensive":
            new_regime = "defensive"
            log.info("Vol Regime: Extreme Greed (%d) → contrarian defensief", fg_value)

        # Alleen signalen genereren bij regime-TRANSITIE
        if new_regime == current_regime:
            return signals

        self._set_custom_state("regime", new_regime)
        self._set_custom_state("regime_vix", round(float(latest_vix), 1))
        self._set_custom_state("regime_fg", fg_value)

        win_rate = self._get_rolling_win_rate()

        if new_regime == "offensive":
            # Koop high-beta crypto
            for market, pair in self.OFFENSIVE_PAIRS.items():
                try:
                    price = _kraken_ticker(pair) or float(closes[-1])
                    signals.append(TradeSignal(
                        symbol=pair,
                        broker="kraken",
                        direction="long",
                        win_probability=max(0.50, min(0.70, win_rate)),
                        expected_win_pct=0.12,
                        expected_loss_pct=0.06,
                        current_price=price,
                        strategy_id="vol-regime",
                        notes=f"Vol Regime → OFFENSIEF {market}: VIX={latest_vix:.1f} F&G={fg_value}",
                    ))
                except Exception as e:
                    log.warning("Vol Regime offensief fout %s: %s", market, e)

        elif new_regime == "defensive":
            # Signaal om posities af te bouwen (short of exit)
            for market, pair in self.OFFENSIVE_PAIRS.items():
                try:
                    price = _kraken_ticker(pair) or float(closes[-1])
                    signals.append(TradeSignal(
                        symbol=pair,
                        broker="kraken",
                        direction="short",
                        win_probability=max(0.50, min(0.65, win_rate)),
                        expected_win_pct=0.08,
                        expected_loss_pct=0.05,
                        current_price=price,
                        strategy_id="vol-regime",
                        notes=f"Vol Regime → DEFENSIEF {market}: VIX={latest_vix:.1f} F&G={fg_value}",
                    ))
                except Exception as e:
                    log.warning("Vol Regime defensief fout %s: %s", market, e)

        return signals
