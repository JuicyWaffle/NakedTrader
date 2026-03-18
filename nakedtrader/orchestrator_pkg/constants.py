"""nakedtrader.orchestrator.constants — Gedeelde constanten."""

# ── Signaalverval limieten (seconden) ─────────────────
MAX_SIGNAL_AGE = {
    "arbitrage": 30,
    "momentum": 4 * 3600,
    "mean-reversion": 2 * 3600,
    "breakout": 8 * 3600,
    "trend-follow": 8 * 3600,
    "funding-contrarian": 4 * 3600,
    "cross-arb": 30,
    "vol-regime": 8 * 3600,
}

# ── Slippage drempels per strategie ───────────────────
MAX_SLIPPAGE = {
    "momentum": 0.002,
    "mean-reversion": 0.002,
    "breakout": 0.003,
    "arbitrage": 0.001,
    "trend-follow": 0.005,
    "funding-contrarian": 0.003,
    "cross-arb": 0.001,
    "vol-regime": 0.005,
}

# ── Broker minimale ordergrootte (EUR) ────────────────
BROKER_MIN_SIZE = {
    "ibkr": 50.0,
    "kraken": 10.0,
    "binance": 10.0,
}

# ── Prioriteit bij kapitaalschaarste ──────────────────
STRATEGY_PRIORITY = [
    "trend-follow", "mean-reversion", "momentum", "vol-regime",
    "funding-contrarian", "breakout", "arbitrage", "cross-arb",
]

# ── Sector tagging voor correlatie ────────────────────
SECTOR_MAP = {
    "XXBTZEUR": "crypto", "XETHZEUR": "crypto", "SOLUSD": "crypto",
    "AAPL": "tech", "MSFT": "tech", "SPY": "equity",
    "TLT": "macro", "EURUSD": "macro",
}

MAX_EXECUTION_LOG = 10_000
