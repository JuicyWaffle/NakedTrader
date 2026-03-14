"""Shared test fixtures for NakedTrader."""

import json
import tempfile
from pathlib import Path

import pytest

from nakedtrader.types import TradeSignal


@pytest.fixture
def tmp_data_dir(tmp_path):
    """Temporary data directory for state files."""
    return tmp_path


@pytest.fixture
def sample_signal():
    """A basic TradeSignal for testing."""
    return TradeSignal(
        symbol="XXBTZEUR",
        broker="kraken",
        direction="long",
        win_probability=0.60,
        expected_win_pct=0.12,
        expected_loss_pct=0.05,
        current_price=62000.0,
        strategy_id="momentum",
        notes="test signal",
    )


@pytest.fixture
def sample_signals():
    """Multiple TradeSignals covering different strategies."""
    return [
        TradeSignal(
            symbol="XXBTZEUR", broker="kraken", direction="long",
            win_probability=0.60, expected_win_pct=0.12, expected_loss_pct=0.05,
            current_price=62000.0, strategy_id="momentum",
        ),
        TradeSignal(
            symbol="XETHZEUR", broker="kraken", direction="long",
            win_probability=0.55, expected_win_pct=0.10, expected_loss_pct=0.06,
            current_price=1850.0, strategy_id="mean-reversion",
        ),
        TradeSignal(
            symbol="SPY", broker="ibkr", direction="long",
            win_probability=0.62, expected_win_pct=0.08, expected_loss_pct=0.04,
            current_price=520.0, strategy_id="trend-follow",
        ),
    ]
