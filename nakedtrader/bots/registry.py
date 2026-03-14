"""
registry.py — Strategie-registratie. IDs matchen bot_performance.json.
"""

from .momentum import MomentumStrategy
from .mean_reversion import MeanReversionStrategy
from .breakout import BreakoutStrategy
from .scalper import ArbitrageStrategy
from .trend import TrendFollowingStrategy
from .funding_rate import FundingRateStrategy
from .cross_arb import CrossArbStrategy
from .vol_regime import VolRegimeStrategy

STRATEGIES = {
    "momentum": MomentumStrategy(),
    "mean-reversion": MeanReversionStrategy(),
    "breakout": BreakoutStrategy(),
    "trend-follow": TrendFollowingStrategy(),
    "arbitrage": ArbitrageStrategy(),
    "funding-contrarian": FundingRateStrategy(),
    "cross-arb": CrossArbStrategy(),
    "vol-regime": VolRegimeStrategy(),
}


def get_all_strategies():
    return STRATEGIES


def get_strategy(strategy_id):
    return STRATEGIES.get(strategy_id)
