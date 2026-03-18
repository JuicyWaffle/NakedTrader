"""
nakedtrader.config — Configuratie laden uit config.yml + .env
"""

import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv

from nakedtrader.exceptions import ConfigError


@dataclass
class Config:
    # ── Modus ──────────────────────────────────
    paper_mode: bool = True

    # ── Kapitaal ───────────────────────────────
    total_capital: float = 10_000.0

    # ── Kelly instellingen ─────────────────────
    kelly_fraction: float = 0.5
    max_position_pct: float = 0.20

    # ── Risicobeheer ───────────────────────────
    stop_loss_pct: float = 0.05
    take_profit_pct: float = 0.10
    max_open_positions: int = 5
    drawdown_limit_pct: float = 0.15

    # ── Logboek ────────────────────────────────
    trade_log_path: str = "data/trades.json"

    # ── Adaptive state ────────────────────────
    state_path: str = "data/strategy_state.json"

    # ── Data directory ────────────────────────
    data_dir: str = "data"

    # ── IBKR ───────────────────────────────────
    ibkr_host: str = "127.0.0.1"
    ibkr_port: int = 7497
    ibkr_client_id: int = 1

    # ── Crypto broker keuze ────────────────────
    crypto_broker: str = "kraken"

    # ── Kraken ─────────────────────────────────
    kraken_api_key: str = ""
    kraken_api_secret: str = ""

    # ── Binance ────────────────────────────────
    binance_api_key: str = ""
    binance_api_secret: str = ""
    binance_testnet: bool = True

    # ── Macro Risk ────────────────────────────
    macro_risk_enabled: bool = True
    macro_risk_interval_min: int = 60
    macro_risk_veto_threshold: float = 0.85

    # ── Ollama AI ────────────────────────────────
    ollama_enabled: bool = False
    ollama_url: str = "http://localhost:11434"

    # ── Loop ───────────────────────────────────
    interval_seconds: int = 60

    def validate(self):
        """Valideer alle configuratiewaarden. Gooit ConfigError bij fouten."""
        errors: list[str] = []

        if self.total_capital <= 0:
            errors.append(f"total_capital moet > 0 zijn, is {self.total_capital}")
        if not (0 < self.kelly_fraction <= 1):
            errors.append(f"kelly_fraction moet in (0, 1] zijn, is {self.kelly_fraction}")
        if not (0 < self.max_position_pct <= 1):
            errors.append(f"max_position_pct moet in (0, 1] zijn, is {self.max_position_pct}")
        if not (0 < self.stop_loss_pct <= 1):
            errors.append(f"stop_loss_pct moet in (0, 1] zijn, is {self.stop_loss_pct}")
        if not (0 < self.take_profit_pct <= 1):
            errors.append(f"take_profit_pct moet in (0, 1] zijn, is {self.take_profit_pct}")
        if not (0 <= self.drawdown_limit_pct <= 1):
            errors.append(f"drawdown_limit_pct moet in [0, 1] zijn, is {self.drawdown_limit_pct}")
        if self.max_open_positions < 1:
            errors.append(f"max_open_positions moet >= 1 zijn, is {self.max_open_positions}")
        if not (1024 <= self.ibkr_port <= 65535):
            errors.append(f"ibkr_port moet in [1024, 65535] zijn, is {self.ibkr_port}")
        if self.interval_seconds <= 0:
            errors.append(f"interval_seconds moet > 0 zijn, is {self.interval_seconds}")
        if not (0 <= self.macro_risk_veto_threshold <= 1):
            errors.append(f"macro_risk_veto_threshold moet in [0, 1] zijn, is {self.macro_risk_veto_threshold}")

        if errors:
            raise ConfigError("Configuratiefouten:\n  - " + "\n  - ".join(errors))


def load_config(config_path="config.yml", env_path=".env", project_dir=None) -> Config:
    """Laad configuratie uit config.yml + secrets uit .env"""
    if project_dir is None:
        # Zoek project root: waar config.yml staat
        project_dir = Path(__file__).resolve().parent.parent

    project_dir = Path(project_dir)

    # Laad .env (secrets)
    env_file = project_dir / env_path
    load_dotenv(env_file)

    # Laad config.yml
    cfg_file = project_dir / config_path
    yml = {}
    if cfg_file.is_file():
        with open(cfg_file) as f:
            yml = yaml.safe_load(f) or {}

    config = Config(
        paper_mode=yml.get("paper_mode", True),
        total_capital=yml.get("total_capital", 10_000.0),
        kelly_fraction=yml.get("kelly_fraction", 0.5),
        max_position_pct=yml.get("max_position_pct", 0.20),
        stop_loss_pct=yml.get("stop_loss_pct", 0.05),
        take_profit_pct=yml.get("take_profit_pct", 0.10),
        max_open_positions=yml.get("max_open_positions", 5),
        drawdown_limit_pct=yml.get("drawdown_limit_pct", 0.15),
        trade_log_path=yml.get("trade_log_path", "data/trades.json"),
        state_path=yml.get("state_path", "data/strategy_state.json"),
        data_dir=yml.get("data_dir", "data"),
        ibkr_host=yml.get("ibkr_host", "127.0.0.1"),
        ibkr_port=yml.get("ibkr_port", 7497),
        ibkr_client_id=yml.get("ibkr_client_id", 1),
        crypto_broker=yml.get("crypto_broker", "kraken"),
        kraken_api_key=os.environ.get("KRAKEN_API_KEY", ""),
        kraken_api_secret=os.environ.get("KRAKEN_API_SECRET", ""),
        binance_api_key=os.environ.get("BINANCE_API_KEY", ""),
        binance_api_secret=os.environ.get("BINANCE_API_SECRET", ""),
        binance_testnet=yml.get("binance_testnet", True),
        macro_risk_enabled=yml.get("macro_risk_enabled", True),
        macro_risk_interval_min=yml.get("macro_risk_interval_min", 60),
        macro_risk_veto_threshold=yml.get("macro_risk_veto_threshold", 0.85),
        ollama_enabled=yml.get("ollama_enabled", False),
        ollama_url=yml.get("ollama_url", "http://localhost:11434"),
        interval_seconds=yml.get("interval_seconds", 60),
    )

    # Valideer configuratie
    config.validate()

    # Auto-migratie: als oude paden bestaan maar nieuwe niet
    _migrate_data_files(project_dir, config)

    return config


def _migrate_data_files(project_dir: Path, config: Config):
    """Verplaats oude flat-file data naar data/ directory."""
    data_dir = project_dir / config.data_dir
    data_dir.mkdir(exist_ok=True)
    (data_dir / "daily").mkdir(exist_ok=True)
    (data_dir / "monthly").mkdir(exist_ok=True)

    # Migreer trades.json
    old_trades = project_dir / "trades.json"
    new_trades = project_dir / config.trade_log_path
    if old_trades.exists() and not new_trades.exists() and str(config.trade_log_path).startswith("data/"):
        import shutil
        shutil.copy2(old_trades, new_trades)

    # Migreer strategy_state.json
    old_state = project_dir / "strategy_state.json"
    new_state = project_dir / config.state_path
    if old_state.exists() and not new_state.exists() and str(config.state_path).startswith("data/"):
        import shutil
        shutil.copy2(old_state, new_state)
