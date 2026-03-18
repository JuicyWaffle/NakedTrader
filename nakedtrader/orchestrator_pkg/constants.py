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
    "sector-rotation": 24 * 3600,
    "equity-mean-reversion": 24 * 3600,
    "etf-trend-follow": 24 * 3600,
    "dividend-value": 48 * 3600,
    "equity-vol-breakout": 8 * 3600,
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
    "sector-rotation": 0.003,
    "equity-mean-reversion": 0.003,
    "etf-trend-follow": 0.004,
    "dividend-value": 0.002,
    "equity-vol-breakout": 0.005,
}

# ── Broker minimale ordergrootte (EUR) ────────────────
BROKER_MIN_SIZE = {
    "ibkr": 50.0,
    "kraken": 10.0,
    "binance": 10.0,
}

# ── Prioriteit bij kapitaalschaarste ──────────────────
STRATEGY_PRIORITY = [
    "trend-follow", "equity-mean-reversion", "dividend-value",
    "equity-momentum", "sector-rotation", "etf-trend-follow",
    "mean-reversion", "equity-vol-breakout",
    "momentum", "vol-regime",
    "funding-contrarian", "breakout", "arbitrage", "cross-arb",
]

# ── Sector tagging voor correlatie ────────────────────
SECTOR_MAP = {
    # Crypto
    "XXBTZEUR": "crypto", "XETHZEUR": "crypto", "SOLUSD": "crypto",
    "SOLEUR": "crypto", "ADAEUR": "crypto",
    "DOTEUR": "crypto", "AVAXEUR": "crypto", "LINKEUR": "crypto", "MATICEUR": "crypto",
    # Equity — Tech (equity-momentum)
    "AAPL": "tech", "MSFT": "tech", "GOOGL": "tech", "AMZN": "tech",
    "META": "tech", "NVDA": "tech", "TSLA": "tech",
    # Equity — Financials (equity-momentum)
    "JPM": "financials", "GS": "financials", "BAC": "financials", "V": "financials",
    # Equity — Healthcare (equity-momentum)
    "JNJ": "healthcare", "UNH": "healthcare", "PFE": "healthcare", "ABBV": "healthcare",
    # Equity — Energy (equity-momentum)
    "XOM": "energy", "CVX": "energy", "COP": "energy",
    # ETFs (equity-momentum)
    "SPY": "broad", "QQQ": "tech", "XLK": "tech", "XLF": "financials",
    "XLE": "energy", "XLV": "healthcare", "VGK": "europe", "EFA": "intl",
    # Macro
    "TLT": "macro", "EURUSD": "macro",
    # Sector Rotation ETFs
    "XLB": "materials", "XLC": "communication", "XLI": "industrials",
    "XLP": "staples", "XLU": "utilities", "XLRE": "real-estate",
    "XLY": "discretionary", "XBI": "biotech", "XHB": "homebuilders",
    "XME": "metals-mining", "XOP": "oil-gas", "XRT": "retail",
    "XSD": "semiconductors", "EEM": "emerging", "EWJ": "japan",
    "EWZ": "brazil", "EWG": "germany", "FXI": "china",
    "IEF": "bonds", "LQD": "bonds", "HYG": "bonds",
    "GLD": "gold", "SLV": "silver", "GDX": "gold-miners",
    "USO": "oil", "UNG": "nat-gas", "ARKK": "innovation",
    "KWEB": "china-internet", "IBIT": "btc-etf",
    # Equity Mean Reversion
    "CRM": "tech", "ADBE": "tech", "ORCL": "tech", "INTC": "tech",
    "AMD": "tech", "AVGO": "tech", "QCOM": "tech",
    "C": "financials", "WFC": "financials", "MS": "financials",
    "BLK": "financials", "SCHW": "financials",
    "MRK": "healthcare", "LLY": "healthcare", "TMO": "healthcare",
    "BMY": "healthcare", "AMGN": "healthcare",
    "CAT": "industrials", "DE": "industrials", "HON": "industrials",
    "UPS": "industrials", "RTX": "defense",
    "DIS": "consumer", "SBUX": "consumer", "NKE": "consumer", "HD": "consumer",
    # ETF Trend Follow
    "IWM": "small-cap", "MDY": "mid-cap", "DIA": "large-cap",
    "RSP": "broad", "VTI": "broad",
    "INDA": "india", "EWT": "taiwan", "EWY": "south-korea",
    "EWA": "australia", "EWC": "canada", "EWU": "uk",
    "EUFN": "europe-fin", "VWO": "emerging",
    "ICLN": "clean-energy", "LIT": "lithium", "HACK": "cybersecurity",
    "ROBO": "robotics", "BLOK": "blockchain", "TAN": "solar",
    "JETS": "airlines", "PAVE": "infrastructure",
    "SHY": "bonds", "AGG": "bonds", "BNDX": "bonds",
    "EMB": "bonds", "TIP": "bonds",
    "DBA": "commodities", "DBC": "commodities", "COPX": "copper",
    "URA": "uranium", "WEAT": "commodities",
    "VIXY": "vix", "SQQQ": "inverse", "TQQQ": "leverage", "UVXY": "vix",
    # Dividend Value
    "KO": "staples", "PG": "staples", "MMM": "industrials",
    "T": "telecom", "VZ": "telecom", "IBM": "tech",
    "MO": "staples", "PM": "staples", "EMR": "industrials",
    "ADP": "tech", "CL": "staples", "SYY": "staples",
    "WBA": "healthcare", "BEN": "financials", "TROW": "financials",
    "O": "reit", "MAIN": "reit", "STAG": "reit",
    "EPD": "energy", "MPC": "energy", "PSX": "energy", "VLO": "energy",
    "VIG": "div-etf", "SCHD": "div-etf", "DVY": "div-etf",
    "HDV": "div-etf", "SPYD": "div-etf",
    "NEE": "utilities", "DUK": "utilities", "SO": "utilities",
    # Equity Vol Breakout
    "MSTR": "tech", "COIN": "fintech", "SQ": "fintech",
    "SHOP": "tech", "SNAP": "tech", "PLTR": "tech",
    "NET": "tech", "DKNG": "gaming", "RBLX": "gaming",
    "ROKU": "tech", "SOFI": "fintech", "UPST": "fintech",
    "ARKW": "innovation", "ARKG": "genomics",
    "SMH": "semiconductors", "SOXX": "semiconductors",
    "IBB": "biotech",
    "GME": "meme", "AMC": "meme", "RIVN": "ev", "LCID": "ev",
    "VXX": "vix", "SVXY": "vix", "SPXS": "inverse",
}

MAX_EXECUTION_LOG = 10_000
