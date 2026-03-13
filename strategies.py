"""Backward compatibility shim — gebruik nakedtrader.bots."""
from nakedtrader.types import StrategyMeta, TradeSignal  # noqa: F401
from nakedtrader.bots.base import BaseStrategy  # noqa: F401
from nakedtrader.bots.registry import STRATEGIES  # noqa: F401
from nakedtrader.bots.momentum import MomentumStrategy  # noqa: F401
from nakedtrader.bots.mean_reversion import MeanReversionStrategy  # noqa: F401
from nakedtrader.bots.breakout import BreakoutStrategy  # noqa: F401
from nakedtrader.bots.scalper import ArbitrageStrategy  # noqa: F401
from nakedtrader.bots.trend import TrendFollowingStrategy  # noqa: F401
