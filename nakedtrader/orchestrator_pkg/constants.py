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
    "equity-momentum": 24 * 3600,
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
    "equity-momentum": 0.003,
}

# ── Broker minimale ordergrootte (EUR) ────────────────
BROKER_MIN_SIZE = {
    "ibkr": 50.0,
    "kraken": 10.0,
    "binance": 10.0,
}

# ── Prioriteit bij kapitaalschaarste ──────────────────
STRATEGY_PRIORITY = [
    "trend-follow", "mean-reversion", "equity-momentum",
    "momentum", "vol-regime",
    "funding-contrarian", "breakout", "arbitrage", "cross-arb",
]

# ── Sector tagging voor correlatie ────────────────────
SECTOR_MAP = {
    # Crypto
    "XXBTZEUR": "crypto", "XETHZEUR": "crypto", "SOLUSD": "crypto",
    "SOLEUR": "crypto", "ADAEUR": "crypto",
    "DOTEUR": "crypto", "AVAXEUR": "crypto", "LINKEUR": "crypto", "MATICEUR": "crypto",
    # Equity — Tech
    "AAPL": "tech", "MSFT": "tech", "GOOGL": "tech", "AMZN": "tech",
    "META": "tech", "NVDA": "tech", "TSLA": "tech",
    # Equity — Financials
    "JPM": "financials", "GS": "financials", "BAC": "financials", "V": "financials",
    # Equity — Healthcare
    "JNJ": "healthcare", "UNH": "healthcare", "PFE": "healthcare", "ABBV": "healthcare",
    # Equity — Energy
    "XOM": "energy", "CVX": "energy", "COP": "energy",
    # ETFs
    "SPY": "broad", "QQQ": "tech", "XLK": "tech", "XLF": "financials",
    "XLE": "energy", "XLV": "healthcare", "VGK": "europe", "EFA": "intl",
    # Macro
    "TLT": "macro", "EURUSD": "macro",
}

MAX_EXECUTION_LOG = 10_000
