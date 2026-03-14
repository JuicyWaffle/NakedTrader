"""
nakedtrader.ai.signal_analyzer — LLM-gebaseerde signaalanalyse.

Voegt AI-intelligence toe aan trading signalen:
- Beoordeelt signaalkwaliteit op basis van marktcontext
- Past win_probability aan (+/- 0.05 max)
- Graceful degradation: als Ollama offline is, passeert signaal ongewijzigd

Wordt aangeroepen vanuit BaseStrategy._llm_enhance_signal().
"""

import json
import logging
import time
from datetime import datetime

from nakedtrader.ai import ollama

log = logging.getLogger(__name__)

# Cache om herhaalde calls te voorkomen (key = strategy+symbol+hour)
_analysis_cache: dict[str, dict] = {}
_CACHE_TTL = 300  # 5 minuten

SIGNAL_ANALYSIS_PROMPT = """\
You are a quantitative trading signal analyst. Evaluate this trade signal's quality.

SIGNAL:
- Strategy: {strategy_id}
- Symbol: {symbol}
- Direction: {direction}
- Win probability: {win_prob:.1%}
- Entry price: {price}
- Notes: {notes}

MARKET CONTEXT:
{context}

Respond with JSON only:
{{"quality": 0.0-1.0, "adjustment": -0.05 to +0.05, "confidence": "low|medium|high", "reason": "1 sentence"}}

Rules:
- quality 0.0 = terrible setup, 1.0 = perfect confluence
- adjustment modifies win_probability (positive = strengthen, negative = weaken)
- Keep adjustment small: +-0.01 for minor factors, +-0.03 for strong confluence, +-0.05 only for extreme cases
- Consider: trend alignment, momentum confirmation, risk/reward, timing, market regime"""


def analyze_signal(
    strategy_id: str,
    symbol: str,
    direction: str,
    win_prob: float,
    price: float,
    notes: str,
    market_context: dict,
) -> dict | None:
    """Analyseer een trading signaal met LLM intelligence.

    Args:
        strategy_id: ID van de strategie
        symbol: Trading pair
        direction: "long" of "short"
        win_prob: Huidige geschatte win probability
        price: Entry prijs
        notes: Strategie-specifieke notities
        market_context: Dict met beschikbare marktdata

    Returns:
        {"quality": float, "adjustment": float, "confidence": str, "reason": str}
        of None als LLM niet beschikbaar.
    """
    # Check cache
    cache_key = f"{strategy_id}:{symbol}:{datetime.now().strftime('%Y%m%d%H')}"
    if cache_key in _analysis_cache:
        cached = _analysis_cache[cache_key]
        if time.time() - cached.get("_ts", 0) < _CACHE_TTL:
            return cached

    if not ollama.available():
        return None

    # Bouw context string
    ctx_lines = []
    if "macro_risk" in market_context:
        mr = market_context["macro_risk"]
        ctx_lines.append(f"Macro risk: {mr.get('risk_level', 'unknown')} (score {mr.get('risk_score', 0):.2f})")
    if "funding_rate" in market_context:
        fr = market_context["funding_rate"]
        ctx_lines.append(f"Funding rate: {fr.get('funding_rate', 0)*100:.4f}% (OI: {fr.get('open_interest', 0):.0f})")
    if "fear_greed" in market_context:
        ctx_lines.append(f"Fear & Greed Index: {market_context['fear_greed']}")
    if "vix_proxy" in market_context:
        ctx_lines.append(f"VIX proxy (BTC vol): {market_context['vix_proxy']:.1f}")
    if "rsi" in market_context:
        ctx_lines.append(f"RSI(14): {market_context['rsi']:.1f}")
    if "trend" in market_context:
        ctx_lines.append(f"Trend: {market_context['trend']}")
    if "orderbook_imbalance" in market_context:
        ctx_lines.append(f"Order book imbalance: {market_context['orderbook_imbalance']:.3f}")
    if "recent_performance" in market_context:
        ctx_lines.append(f"Recent performance: {market_context['recent_performance']}")

    context_str = "\n".join(ctx_lines) if ctx_lines else "No additional context available."

    prompt = SIGNAL_ANALYSIS_PROMPT.format(
        strategy_id=strategy_id,
        symbol=symbol,
        direction=direction,
        win_prob=win_prob,
        price=price,
        notes=notes,
        context=context_str,
    )

    try:
        raw = ollama.generate(
            prompt,
            task="reasoning",
            temperature=0.1,
            max_tokens=200,
        )
        if not raw:
            return None

        # Parse JSON
        start = raw.index("{")
        end = raw.rindex("}") + 1
        result = json.loads(raw[start:end])

        # Valideer en clamp adjustment
        adj = float(result.get("adjustment", 0))
        result["adjustment"] = max(-0.05, min(0.05, adj))
        result["quality"] = max(0.0, min(1.0, float(result.get("quality", 0.5))))

        # Cache
        result["_ts"] = time.time()
        _analysis_cache[cache_key] = result

        # Cleanup oude cache entries
        now = time.time()
        stale = [k for k, v in _analysis_cache.items() if now - v.get("_ts", 0) > _CACHE_TTL * 2]
        for k in stale:
            del _analysis_cache[k]

        log.info(
            "LLM signal analysis [%s/%s]: quality=%.2f adj=%+.3f (%s) — %s",
            strategy_id, symbol, result["quality"], result["adjustment"],
            result.get("confidence", "?"), result.get("reason", ""),
        )
        return result
    except Exception as e:
        log.debug("LLM signal analysis fout: %s", e)
        return None


def build_market_context(
    symbol: str,
    strategy_id: str,
    win_rate: float = 0.5,
) -> dict:
    """Bouw marktcontext voor signaalanalyse uit beschikbare data feeds.

    Haalt alle relevante data op in één call — caching voorkomt dubbele API calls.
    """
    context = {}

    # Macro risk
    try:
        from nakedtrader.risk.macro import MacroRiskEngine
        engine = MacroRiskEngine()
        report = engine.evaluate()
        context["macro_risk"] = {
            "risk_score": report.risk_score,
            "risk_level": report.risk_level,
            "alerts": report.alerts[:3],
        }
    except Exception:
        pass

    # Funding rate
    try:
        from nakedtrader.bots.data_feeds import _binance_funding_rate
        sym_map = {"XXBTZEUR": "BTCUSDT", "XETHZEUR": "ETHUSDT", "SOLEUR": "SOLUSDT"}
        bn_sym = sym_map.get(symbol)
        if bn_sym:
            context["funding_rate"] = _binance_funding_rate(bn_sym)
    except Exception:
        pass

    # Fear & Greed
    try:
        import requests
        resp = requests.get("https://api.alternative.me/fng/?limit=1", timeout=5)
        data = resp.json().get("data", [])
        if data:
            context["fear_greed"] = int(data[0]["value"])
    except Exception:
        pass

    # VIX proxy + RSI van BTC
    try:
        from nakedtrader.bots.data_feeds import _kraken_ohlc
        from nakedtrader.indicators import vix_proxy, rsi
        import numpy as np

        btc_data = _kraken_ohlc("XXBTZEUR", interval=60, count=50)
        if btc_data:
            closes = btc_data["close"]
            vix_vals = vix_proxy(closes, 20)
            if not np.isnan(vix_vals[-1]):
                context["vix_proxy"] = float(vix_vals[-1])
            rsi_vals = rsi(closes, 14)
            if not np.isnan(rsi_vals[-1]):
                context["rsi"] = float(rsi_vals[-1])
    except Exception:
        pass

    # Recent performance
    if win_rate != 0.5:
        context["recent_performance"] = f"Win rate: {win_rate:.0%}"

    return context
