"""Tests voor Config validatie."""

import pytest

from nakedtrader.config import Config
from nakedtrader.exceptions import ConfigError


def test_default_config_validates():
    """Standaard Config moet valid zijn."""
    Config().validate()


def test_invalid_total_capital():
    with pytest.raises(ConfigError, match="total_capital"):
        Config(total_capital=-100).validate()


def test_invalid_kelly_fraction():
    with pytest.raises(ConfigError, match="kelly_fraction"):
        Config(kelly_fraction=0).validate()
    with pytest.raises(ConfigError, match="kelly_fraction"):
        Config(kelly_fraction=1.5).validate()


def test_invalid_max_position_pct():
    with pytest.raises(ConfigError, match="max_position_pct"):
        Config(max_position_pct=0).validate()


def test_invalid_stop_loss():
    with pytest.raises(ConfigError, match="stop_loss_pct"):
        Config(stop_loss_pct=0).validate()


def test_invalid_take_profit():
    with pytest.raises(ConfigError, match="take_profit_pct"):
        Config(take_profit_pct=0).validate()


def test_invalid_drawdown_limit():
    with pytest.raises(ConfigError, match="drawdown_limit_pct"):
        Config(drawdown_limit_pct=-0.1).validate()
    with pytest.raises(ConfigError, match="drawdown_limit_pct"):
        Config(drawdown_limit_pct=1.5).validate()


def test_invalid_ibkr_port():
    with pytest.raises(ConfigError, match="ibkr_port"):
        Config(ibkr_port=80).validate()
    with pytest.raises(ConfigError, match="ibkr_port"):
        Config(ibkr_port=99999).validate()


def test_invalid_interval_seconds():
    with pytest.raises(ConfigError, match="interval_seconds"):
        Config(interval_seconds=0).validate()


def test_invalid_macro_risk_veto():
    with pytest.raises(ConfigError, match="macro_risk_veto_threshold"):
        Config(macro_risk_veto_threshold=1.5).validate()


def test_multiple_errors_combined():
    """Meerdere fouten worden samengevoegd in één ConfigError."""
    with pytest.raises(ConfigError) as exc_info:
        Config(total_capital=-1, kelly_fraction=0, interval_seconds=-5).validate()
    msg = str(exc_info.value)
    assert "total_capital" in msg
    assert "kelly_fraction" in msg
    assert "interval_seconds" in msg
