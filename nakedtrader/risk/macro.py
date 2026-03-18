"""
nakedtrader.risk.macro — Geopolitieke en macro-economische risicobewaking.

Bewaakt drie lagen van risicosignalen en produceert een RiskReport:
  Laag 1 — Marktstructuur  : VIX, put/call ratio, credit spreads (FRED)
  Laag 2 — Geopolitieke proxies: olieprijs, goud/dollar, GDELT events
  Laag 3 — Macro-liquiditeit: Fed TGA, macro-kalender events (FRED)

Gebruik:
    from nakedtrader.risk.macro import MacroRiskEngine
    report = MacroRiskEngine().evaluate()
    kelly_adjusted = kelly_fraction * report.kelly_mult

Standalone:
    python run_risk.py              # eenmalige evaluatie
    python run_risk.py --watch      # elke 60 minuten
    python run_risk.py --json       # output als JSON
    python run_risk.py --emergency  # noodrem direct

Dependencies: yfinance, fredapi, requests, pandas (allen optioneel — degraded mode)
API keys: FRED_API_KEY in .env (gratis: fred.stlouisfed.org/api_key)
"""

from __future__ import annotations

import io
import json
import logging
import os
import zipfile
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Optional

# ── Optionele dependencies: module werkt in degraded mode als ze ontbreken ──

try:
    import yfinance as yf
    _YF_AVAILABLE = True
except ImportError:
    _YF_AVAILABLE = False

try:
    from fredapi import Fred
    _FRED_AVAILABLE = True
except ImportError:
    _FRED_AVAILABLE = False

try:
    import requests
    _REQUESTS_AVAILABLE = True
except ImportError:
    _REQUESTS_AVAILABLE = False

try:
    import pandas as pd
    _PANDAS_AVAILABLE = True
except ImportError:
    _PANDAS_AVAILABLE = False

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# MODULE MANIFEST
# ─────────────────────────────────────────────────────────────────────────────

MANIFEST = {
    "module":       "nakedtrader.risk.macro",
    "version":      "1.0.0",
    "role":         "Geopolitieke en macro-risicobewaking",
    "output_type":  "RiskReport",
    "risk_levels":  ["green", "orange", "red"],
    "kelly_mults":  {"green": 1.0, "orange": 0.5, "red": 0.25},
    "sl_mults":     {"green": 1.0, "orange": 0.8, "red": 0.6},
    "signal_layers": {
        "layer_1_market":       ["vix", "vix_term_structure", "put_call_ratio", "credit_spread"],
        "layer_2_geopolitical": ["oil_shock", "gold_dollar_ratio", "gdelt_conflict_index"],
        "layer_3_macro":        ["macro_calendar_risk", "fed_liquidity", "fear_greed", "funding_rate"],
    },
    "data_sources": {
        "yfinance": "gratis, geen API key — ^VIX, ^VVIX, GC=F, CL=F, SPY",
        "FRED":     "gratis API key vereist — fred.stlouisfed.org/api_key",
        "GDELT":    "gratis, geen API key — CSV download elke 15 min",
    },
    "api_keys_required": ["FRED_API_KEY"],
}


# ─────────────────────────────────────────────────────────────────────────────
# RISK REPORT
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RiskReport:
    """Gestandaardiseerde output van MacroRiskEngine.evaluate()."""

    risk_score:      float            # 0.0 (laag) → 1.0 (kritiek)
    risk_level:      str              # "green" | "orange" | "red"
    kelly_mult:      float            # vermenigvuldiger voor Kelly-fractie
    sl_mult:         float            # stop-loss aanscherping (< 1.0 = strikter)
    emergency_brake: bool             # True = halt alle posities
    signals:         dict             # ruwe indicatorwaarden
    alerts:          list[str]        # mensleesbare waarschuwingen
    data_quality:    dict             # beschikbaarheid per databron
    timestamp:       str = field(
        default_factory=lambda: datetime.utcnow().isoformat() + "Z"
    )

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)

    def summary(self) -> str:
        lines = [
            f"┌─ RiskReport ─ {self.timestamp}",
            f"│  score      : {self.risk_score:.2f}  [{self.risk_level.upper()}]",
            f"│  kelly_mult : x{self.kelly_mult:.2f}",
            f"│  sl_mult    : x{self.sl_mult:.2f}",
            f"│  noodrem    : {'JA' if self.emergency_brake else 'nee'}",
        ]
        if self.alerts:
            lines.append("│  alerts     :")
            for a in self.alerts:
                lines.append(f"│    - {a}")
        lines.append("│  databronnen:")
        for src, ok in self.data_quality.items():
            lines.append(f"│    {'V' if ok else 'X'} {src}")
        lines.append("└" + "─" * 50)
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# RISK THRESHOLDS
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RiskThresholds:
    """Alle drempelwaarden voor de MacroRiskEngine op één plek."""

    # ── Alert drempels ──
    vix_threshold: float = 25.0
    vix_backwardation_threshold: float = 1.05
    put_call_threshold: float = 1.20
    oil_shock_pct: float = 0.03
    credit_spread_threshold: float = 450.0
    gdelt_conflict_threshold: float = -3.0
    emergency_score: float = 0.85

    # ── VIX normalisatie: vix_norm_min=0.0, vix_norm_max=1.0 voor range [15, 40] ──
    vix_norm_low: float = 15.0
    vix_norm_range: float = 25.0       # 40 - 15
    vvix_alert_level: float = 120.0

    # ── VIX term structure normalisatie ──
    vix_ts_norm_low: float = 0.9
    vix_ts_norm_range: float = 0.4     # 1.3 - 0.9

    # ── Put/call normalisatie ──
    pcr_norm_low: float = 0.7
    pcr_norm_range: float = 1.1        # 1.8 - 0.7

    # ── Credit spread normalisatie (bps) ──
    cs_norm_low: float = 300.0
    cs_norm_range: float = 500.0       # 800 - 300

    # ── Oil shock normalisatie ──
    oil_shock_norm_factor: float = 2.0  # pct_change / (oil_shock_pct * factor)

    # ── Gold/dollar flight-to-safety ──
    gd_norm_offset: float = 0.02
    gd_norm_range: float = 0.04
    gd_flight_alert: float = 0.015

    # ── GDELT normalisatie ──
    gdelt_norm_offset: float = 5.0
    gdelt_norm_range: float = 10.0

    # ── Fed TGA normalisatie ──
    tga_norm_factor: float = 5.0
    tga_lookback_days: int = 90

    # ── Fear & Greed alert levels ──
    fg_extreme_fear: int = 20
    fg_extreme_greed: int = 80

    # ── Funding rate normalisatie ──
    fr_norm_divisor: float = 0.001
    fr_alert_high: float = 0.0005
    fr_alert_low: float = -0.0005

    # ── Score classificatie grenzen ──
    score_green_max: float = 0.35
    score_orange_max: float = 0.65

    # ── Kelly & SL multipliers per risk level ──
    kelly_green: float = 1.00
    kelly_orange: float = 0.50
    kelly_red: float = 0.25
    sl_green: float = 1.00
    sl_orange: float = 0.80
    sl_red: float = 0.60

    # ── Strategy fit correctie ──
    strategy_fit_factor: float = 0.4


# ─────────────────────────────────────────────────────────────────────────────
# MACRO RISK ENGINE
# ─────────────────────────────────────────────────────────────────────────────

class MacroRiskEngine:
    """
    Bewaakt drie lagen van risicosignalen en produceert een RiskReport.

    Alle drempelwaarden zijn aanpasbaar zodat bots ze kunnen overschrijven
    op basis van hun eigen risicoprofiel.
    """

    # ── Strategy Sensitivity Matrix ──
    # Per strategie: hoe beïnvloedt elk macro-signaal de strategie?
    # Positief = gunstig bij hoog signaal, negatief = ongunstig
    STRATEGY_SENSITIVITY = {
        "momentum":          {"vix": -0.6, "credit_spread": -0.3, "oil_shock": -0.2, "funding_rate": 0.0,  "fear_greed": -0.2},
        "mean-reversion":    {"vix": -0.3, "credit_spread": -0.2, "oil_shock": -0.1, "funding_rate": +0.3, "fear_greed": +0.3},
        "breakout":          {"vix": +0.7, "credit_spread": +0.2, "oil_shock": +0.4, "funding_rate": 0.0,  "fear_greed": +0.2},
        "arbitrage":         {"vix": -0.4, "credit_spread": -0.5, "oil_shock": -0.2, "funding_rate": +0.2, "fear_greed": 0.0},
        "trend-follow":      {"vix": +0.3, "credit_spread": -0.4, "oil_shock": -0.3, "funding_rate": 0.0,  "fear_greed": -0.3},
        "funding-contrarian":{"vix": -0.2, "credit_spread": -0.1, "oil_shock": 0.0,  "funding_rate": +0.8, "fear_greed": 0.0},
        "cross-arb":         {"vix": -0.5, "credit_spread": -0.6, "oil_shock": -0.3, "funding_rate": +0.4, "fear_greed": 0.0},
        "vol-regime":        {"vix": +0.8, "credit_spread": +0.3, "oil_shock": +0.5, "funding_rate": 0.0,  "fear_greed": +0.4},
    }

    # ── FRED series IDs ──
    FRED_SERIES = {
        "credit_spread": "BAMLH0A0HYM2",   # ICE BofA High Yield OAS
        "fed_tga":       "WTREGEN",         # Treasury General Account
    }

    # ── Gewichten per signaal (som = 1.0) ──
    SIGNAL_WEIGHTS = {
        "vix":                  0.13,
        "vix_term_structure":   0.09,
        "put_call_ratio":       0.09,
        "credit_spread":        0.13,
        "oil_shock":            0.13,
        "gold_dollar_ratio":    0.09,
        "gdelt_conflict_index": 0.09,
        "macro_calendar_risk":  0.09,
        "fed_liquidity":        0.05,
        "fear_greed":           0.06,
        "funding_rate":         0.05,
    }

    def __init__(
        self,
        fred_api_key:                Optional[str] = None,
        vix_threshold:               float = 25.0,
        vix_backwardation_threshold: float = 1.05,
        put_call_threshold:          float = 1.20,
        oil_shock_pct:               float = 0.03,
        credit_spread_threshold:     float = 450.0,
        gdelt_conflict_threshold:    float = -3.0,
        emergency_score:             float = 0.85,
        ollama_enabled:              bool = False,
        thresholds:                  Optional[RiskThresholds] = None,
    ) -> None:
        # Laad .env als dat nog niet gebeurd is
        if not os.getenv("FRED_API_KEY"):
            from pathlib import Path
            env_path = Path(__file__).resolve().parent.parent.parent / ".env"
            if env_path.exists():
                try:
                    from dotenv import load_dotenv
                    load_dotenv(env_path)
                except ImportError:
                    pass
        self.fred_api_key = fred_api_key or os.getenv("FRED_API_KEY", "")
        self.ollama_enabled = ollama_enabled

        # RiskThresholds: expliciet of vanuit kwargs (backward-compat)
        if thresholds is not None:
            self.thresholds = thresholds
        else:
            self.thresholds = RiskThresholds(
                vix_threshold=vix_threshold,
                vix_backwardation_threshold=vix_backwardation_threshold,
                put_call_threshold=put_call_threshold,
                oil_shock_pct=oil_shock_pct,
                credit_spread_threshold=credit_spread_threshold,
                gdelt_conflict_threshold=gdelt_conflict_threshold,
                emergency_score=emergency_score,
            )

        # Backward-compat shortcuts
        self.emergency_score = self.thresholds.emergency_score

        self._fred: Optional[object] = None
        if _FRED_AVAILABLE and self.fred_api_key:
            try:
                self._fred = Fred(api_key=self.fred_api_key)
            except (ConnectionError, ValueError, OSError) as e:
                log.warning("FRED initialisatie mislukt: %s", e)

    # ── Publieke interface ───────────────────────────────────────────────────

    def evaluate(self) -> RiskReport:
        """Haalt alle signalen op, berekent risk_score, retourneert RiskReport."""
        log.info("Macro risk evaluatie gestart — %d signalen", len(self.SIGNAL_WEIGHTS))

        signals: dict = {}
        alerts: list[str] = []
        dq: dict = {}

        # Laag 1 — Marktstructuur
        signals.update(self._fetch_vix(alerts, dq))
        signals.update(self._fetch_put_call(alerts, dq))
        signals.update(self._fetch_credit_spread(alerts, dq))

        # Laag 2 — Geopolitieke proxies
        signals.update(self._fetch_oil_shock(alerts, dq))
        signals.update(self._fetch_gold_dollar(alerts, dq))
        signals.update(self._fetch_gdelt(alerts, dq))

        # Laag 3 — Macro-liquiditeit + sentiment
        signals.update(self._fetch_macro_calendar(alerts, dq))
        signals.update(self._fetch_fed_liquidity(alerts, dq))
        signals.update(self._fetch_fear_greed(alerts, dq))
        signals.update(self._fetch_funding_rates(alerts, dq))

        # Score berekenen
        risk_score = self._compute_score(signals)
        risk_level, kelly_mult, sl_mult = self._classify(risk_score)
        emergency = risk_score >= self.emergency_score

        if emergency:
            alerts.insert(0, f"NOODREM: risk_score={risk_score:.2f} >= {self.emergency_score}")
            kelly_mult = 0.0

        report = RiskReport(
            risk_score=round(risk_score, 3),
            risk_level=risk_level,
            kelly_mult=kelly_mult,
            sl_mult=sl_mult,
            emergency_brake=emergency,
            signals=signals,
            alerts=alerts,
            data_quality=dq,
        )

        log.info("Macro risk: score=%.2f level=%s kelly=x%.2f sl=x%.2f",
                 risk_score, risk_level, kelly_mult, sl_mult)
        return report

    def emergency_halt(self) -> RiskReport:
        """Activeer noodrem direct, ongeacht de actuele risk_score."""
        log.warning("HANDMATIGE NOODREM geactiveerd")
        return RiskReport(
            risk_score=1.0,
            risk_level="red",
            kelly_mult=0.0,
            sl_mult=0.5,
            emergency_brake=True,
            signals={},
            alerts=["HANDMATIGE NOODREM — alle trading gepauzeerd"],
            data_quality={},
        )

    # ── Per-strategie macro-fit ────────────────────────────────────────────

    def strategy_fit(self, sid: str, signals: dict | None = None) -> float:
        """Bereken hoe goed de huidige macro-omgeving past bij strategie *sid*.

        Returns float in [-1, +1]: positief = gunstig, negatief = ongunstig.
        Als *signals* niet opgegeven, wordt een evaluate() gedaan.
        """
        sensitivity = self.STRATEGY_SENSITIVITY.get(sid)
        if not sensitivity:
            return 0.0

        if signals is None:
            report = self.evaluate()
            signals = report.signals

        total = 0.0
        count = 0
        for signal_key, weight in sensitivity.items():
            value = signals.get(signal_key)
            if value is None:
                continue
            # Signal loopt 0..1 (0=laag risico, 1=hoog). Centreer rond 0.5.
            centered = value - 0.5
            total += centered * weight
            count += 1

        if count == 0:
            return 0.0
        return max(-1.0, min(1.0, total))

    def strategy_kelly_mult(self, sid: str, signals: dict | None = None) -> float:
        """Per-strategie Kelly multiplier: globale kelly × strategie-fit correctie."""
        if signals is None:
            report = self.evaluate()
            signals = report.signals
            base_kelly = report.kelly_mult
        else:
            score = self._compute_score(signals)
            _, base_kelly, _ = self._classify(score)

        fit = self.strategy_fit(sid, signals)
        return round(base_kelly * max(0.1, 1.0 + fit * self.thresholds.strategy_fit_factor), 3)

    # ── Laag 1: Marktstructuur ───────────────────────────────────────────────

    def _fetch_vix(self, alerts: list, dq: dict) -> dict:
        result = {"vix": 0.5, "vix_term_structure": 0.5}
        if not _YF_AVAILABLE:
            dq["yfinance_vix"] = False
            return result

        try:
            vix_data = yf.Ticker("^VIX").history(period="5d")
            if vix_data.empty:
                raise ValueError("Geen VIX data")

            vix_current = float(vix_data["Close"].iloc[-1])
            dq["yfinance_vix"] = True

            # Normaliseer VIX naar 0..1
            result["vix"] = round(min(max(
                (vix_current - self.thresholds.vix_norm_low) / self.thresholds.vix_norm_range,
                0.0), 1.0), 3)
            result["vix_raw"] = round(vix_current, 1)

            if vix_current > self.thresholds.vix_threshold:
                alerts.append(f"VIX verhoogd: {vix_current:.1f} > drempel {self.thresholds.vix_threshold}")

            # Term structure proxy: huidige VIX vs 5d gemiddelde
            if len(vix_data) >= 5:
                vix_avg = float(vix_data["Close"].mean())
                ts_ratio = vix_current / vix_avg if vix_avg > 0 else 1.0
                result["vix_term_structure"] = round(
                    min(max((ts_ratio - self.thresholds.vix_ts_norm_low) / self.thresholds.vix_ts_norm_range, 0.0), 1.0), 3
                )
                result["vix_ts_ratio"] = round(ts_ratio, 3)
                if ts_ratio > self.thresholds.vix_backwardation_threshold:
                    alerts.append(f"VIX backwardation: ratio={ts_ratio:.2f}")

            # VVIX als extra signaal
            try:
                vvix_data = yf.Ticker("^VVIX").history(period="2d")
                if not vvix_data.empty:
                    vvix = float(vvix_data["Close"].iloc[-1])
                    result["vvix_raw"] = round(vvix, 1)
                    if vvix > self.thresholds.vvix_alert_level:
                        alerts.append(f"VVIX sterk verhoogd: {vvix:.1f}")
            except Exception:
                pass

        except (ConnectionError, ValueError, OSError, KeyError) as e:
            log.warning("VIX ophalen mislukt: %s", e)
            dq["yfinance_vix"] = False

        return result

    def _fetch_put_call(self, alerts: list, dq: dict) -> dict:
        result = {"put_call_ratio": 0.5}
        if not _YF_AVAILABLE:
            dq["yfinance_options"] = False
            return result

        try:
            spy = yf.Ticker("SPY")
            expirations = spy.options
            if not expirations:
                raise ValueError("Geen opties voor SPY")

            opts = spy.option_chain(expirations[0])
            total_put_vol = float(opts.puts["volume"].fillna(0).sum())
            total_call_vol = float(opts.calls["volume"].fillna(0).sum())
            if total_call_vol == 0:
                raise ValueError("Call volume = 0")

            ratio = total_put_vol / total_call_vol
            result["put_call_ratio"] = round(min(max(
                (ratio - self.thresholds.pcr_norm_low) / self.thresholds.pcr_norm_range,
                0.0), 1.0), 3)
            result["put_call_ratio_raw"] = round(ratio, 3)
            dq["yfinance_options"] = True

            if ratio > self.thresholds.put_call_threshold:
                alerts.append(f"Put/call ratio verhoogd: {ratio:.2f}")

        except (ConnectionError, ValueError, OSError, KeyError) as e:
            log.warning("Put/call ratio mislukt: %s", e)
            dq["yfinance_options"] = False

        return result

    def _fetch_credit_spread(self, alerts: list, dq: dict) -> dict:
        result = {"credit_spread": 0.5}
        if not self._fred:
            dq["FRED_credit_spread"] = False
            return result

        try:
            series = self._fred.get_series(
                self.FRED_SERIES["credit_spread"],
                observation_start=(datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d"),
            )
            if series is None or len(series) == 0:
                raise ValueError("Lege FRED serie")

            spread = float(series.dropna().iloc[-1])
            result["credit_spread"] = round(min(max(
                (spread - self.thresholds.cs_norm_low) / self.thresholds.cs_norm_range,
                0.0), 1.0), 3)
            result["credit_spread_raw"] = round(spread, 1)
            dq["FRED_credit_spread"] = True

            if spread > self.thresholds.credit_spread_threshold:
                alerts.append(f"Credit spread verhoogd: {spread:.0f}bps")

        except (ConnectionError, ValueError, OSError, KeyError) as e:
            log.warning("Credit spread mislukt: %s", e)
            dq["FRED_credit_spread"] = False

        return result

    # ── Laag 2: Geopolitieke proxies ─────────────────────────────────────────

    def _fetch_oil_shock(self, alerts: list, dq: dict) -> dict:
        result = {"oil_shock": 0.0}
        if not _YF_AVAILABLE:
            dq["yfinance_oil"] = False
            return result

        try:
            cl = yf.Ticker("CL=F").history(period="3d")
            if len(cl) < 2:
                raise ValueError("Onvoldoende olie-data")

            close_today = float(cl["Close"].iloc[-1])
            close_prev = float(cl["Close"].iloc[-2])
            pct_change = abs((close_today - close_prev) / close_prev)

            result["oil_shock"] = round(min(
                pct_change / (self.thresholds.oil_shock_pct * self.thresholds.oil_shock_norm_factor),
                1.0), 3)
            result["oil_pct_change_raw"] = round(pct_change * 100, 2)
            result["oil_price_raw"] = round(close_today, 2)
            dq["yfinance_oil"] = True

            if pct_change > self.thresholds.oil_shock_pct:
                alerts.append(f"Olieprijsschok: {pct_change*100:.1f}% in 24u")

        except (ConnectionError, ValueError, OSError, KeyError) as e:
            log.warning("Olieprijs mislukt: %s", e)
            dq["yfinance_oil"] = False

        return result

    def _fetch_gold_dollar(self, alerts: list, dq: dict) -> dict:
        result = {"gold_dollar_ratio": 0.5}
        if not _YF_AVAILABLE:
            dq["yfinance_gold_dxy"] = False
            return result

        try:
            gold = yf.Ticker("GC=F").history(period="10d")
            dxy = yf.Ticker("DX=F").history(period="10d")

            if len(gold) < 5 or len(dxy) < 5:
                raise ValueError("Onvoldoende gold/dollar data")

            gold_ratio = float(gold["Close"].iloc[-1]) / float(gold["Close"].iloc[-5])
            dxy_ratio = float(dxy["Close"].iloc[-1]) / float(dxy["Close"].iloc[-5])

            flight_signal = (gold_ratio - 1.0) - (dxy_ratio - 1.0)
            result["gold_dollar_ratio"] = round(
                min(max((flight_signal + self.thresholds.gd_norm_offset) / self.thresholds.gd_norm_range, 0.0), 1.0), 3
            )
            result["gold_5d_change_pct"] = round((gold_ratio - 1) * 100, 2)
            result["dxy_5d_change_pct"] = round((dxy_ratio - 1) * 100, 2)
            dq["yfinance_gold_dxy"] = True

            if flight_signal > self.thresholds.gd_flight_alert:
                alerts.append(
                    f"Vlucht naar veilige havens: goud +{(gold_ratio-1)*100:.1f}%, "
                    f"dollar {(dxy_ratio-1)*100:+.1f}% (5d)"
                )

        except (ConnectionError, ValueError, OSError, KeyError) as e:
            log.warning("Goud/dollar mislukt: %s", e)
            dq["yfinance_gold_dxy"] = False

        return result

    def _fetch_gdelt(self, alerts: list, dq: dict) -> dict:
        result = {"gdelt_conflict_index": 0.5}
        if not _REQUESTS_AVAILABLE or not _PANDAS_AVAILABLE:
            dq["GDELT"] = False
            return result

        try:
            now = datetime.utcnow()
            minutes_15 = (now.minute // 15) * 15
            ts = now.replace(minute=minutes_15, second=0, microsecond=0)

            # Probeer huidig en vorig 15-min interval
            for offset in [0, 15]:
                check_ts = ts - timedelta(minutes=offset)
                ts_str = check_ts.strftime("%Y%m%d%H%M%S")
                url = f"http://data.gdeltproject.org/gdeltv2/{ts_str}.export.CSV.zip"
                response = requests.get(url, timeout=15)
                if response.status_code == 200:
                    break
            else:
                raise ValueError("GDELT niet bereikbaar")

            with zipfile.ZipFile(io.BytesIO(response.content)) as z:
                with z.open(z.namelist()[0]) as f:
                    df = pd.read_csv(
                        f, sep="\t", header=None, low_memory=False,
                        usecols=[1, 26, 30],
                        names=["event_id", "event_code", "goldstein"],
                    )

            # Filter militaire/conflict CAMEO codes (19x = geweld)
            conflict_mask = df["event_code"].astype(str).str.startswith("19")
            conflict_df = df[conflict_mask]

            if conflict_df.empty:
                result["gdelt_conflict_index"] = 0.1
                dq["GDELT"] = True
                return result

            avg_goldstein = float(conflict_df["goldstein"].mean())
            result["gdelt_conflict_index"] = round(
                min(max((-avg_goldstein + self.thresholds.gdelt_norm_offset) / self.thresholds.gdelt_norm_range, 0.0), 1.0), 3
            )
            result["gdelt_avg_goldstein"] = round(avg_goldstein, 2)
            result["gdelt_conflict_events"] = int(len(conflict_df))
            dq["GDELT"] = True

            if avg_goldstein < self.thresholds.gdelt_conflict_threshold:
                alert_text = (
                    f"GDELT conflict index verhoogd: Goldstein={avg_goldstein:.1f} "
                    f"({len(conflict_df)} events)"
                )

                # LLM-verrijking: contextualiseer de GDELT events
                if self.ollama_enabled:
                    try:
                        from nakedtrader.ai.ollama import generate
                        from nakedtrader.ai.prompts import GDELT_ANALYSIS_SYSTEM
                        context = (
                            f"Conflict events: {len(conflict_df)}, "
                            f"avg Goldstein score: {avg_goldstein:.2f} "
                            f"(scale: -10=extreme conflict, +10=cooperation)"
                        )
                        analysis = generate(
                            context, task="sentiment",
                            system=GDELT_ANALYSIS_SYSTEM, max_tokens=200,
                        )
                        if analysis:
                            alert_text += f" — AI: {analysis}"
                    except Exception as ai_err:
                        log.debug("GDELT AI verrijking mislukt: %s", ai_err)

                alerts.append(alert_text)

        except (ConnectionError, ValueError, OSError, KeyError, zipfile.BadZipFile) as e:
            log.warning("GDELT mislukt: %s", e)
            dq["GDELT"] = False

        return result

    # ── Laag 3: Macro-liquiditeit ────────────────────────────────────────────

    def _fetch_macro_calendar(self, alerts: list, dq: dict) -> dict:
        result = {"macro_calendar_risk": 0.0}

        events: list[tuple[str, str]] = []

        # Probeer Finnhub economic calendar (gratis tier, 60 req/min)
        finnhub_key = os.getenv("FINNHUB_API_KEY", "")
        if finnhub_key and _REQUESTS_AVAILABLE:
            try:
                today = datetime.utcnow().date()
                from_date = today.strftime("%Y-%m-%d")
                to_date = (today + timedelta(days=3)).strftime("%Y-%m-%d")
                resp = requests.get(
                    "https://finnhub.io/api/v1/calendar/economic",
                    params={"from": from_date, "to": to_date, "token": finnhub_key},
                    timeout=10,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    for ev in data.get("economicCalendar", []):
                        if ev.get("impact", "").lower() == "high":
                            events.append((ev.get("date", "")[:10], ev.get("event", "onbekend")))
                    log.debug("Finnhub calendar: %d high-impact events", len(events))
            except Exception as e:
                log.debug("Finnhub calendar mislukt: %s", e)

        # Fallback: handmatige high-impact events
        STATIC_EVENTS: list[tuple[str, str]] = [
            # ("YYYY-MM-DD", "omschrijving"),
        ]
        events.extend(STATIC_EVENTS)

        today = datetime.utcnow().date()
        for date_str, description in events:
            try:
                event_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            except (ValueError, TypeError):
                continue
            days_away = (event_date - today).days
            if 0 <= days_away <= 2:
                score = 1.0 - (days_away / 3)
                result["macro_calendar_risk"] = max(result["macro_calendar_risk"], score)
                alerts.append(f"High-impact event over {days_away} dag(en): {description}")

        result["macro_calendar_events"] = len(events)
        dq["macro_calendar"] = True
        return result

    def _fetch_fed_liquidity(self, alerts: list, dq: dict) -> dict:
        result = {"fed_liquidity": 0.5}
        if not self._fred:
            dq["FRED_tga"] = False
            return result

        try:
            tga = self._fred.get_series(
                self.FRED_SERIES["fed_tga"],
                observation_start=(datetime.utcnow() - timedelta(days=90)).strftime("%Y-%m-%d"),
            )
            if tga is None or len(tga) < 4:
                raise ValueError("Onvoldoende TGA data")

            tga_clean = tga.dropna()
            tga_current = float(tga_clean.iloc[-1])
            tga_avg = float(tga_clean.iloc[-min(10, len(tga_clean)):].mean())
            tga_change = (tga_current - tga_avg) / tga_avg if tga_avg != 0 else 0
            result["fed_liquidity"] = round(min(max(
                tga_change * self.thresholds.tga_norm_factor + 0.5,
                0.0), 1.0), 3)
            result["tga_current_bn"] = round(tga_current / 1e9, 1)
            result["tga_10d_change_pct"] = round(tga_change * 100, 2)
            dq["FRED_tga"] = True

        except (ConnectionError, ValueError, OSError, KeyError) as e:
            log.warning("Fed liquiditeit mislukt: %s", e)
            dq["FRED_tga"] = False

        return result

    # ── Laag 3b: Crypto sentiment + funding ──────────────────────────────────

    def _fetch_fear_greed(self, alerts: list, dq: dict) -> dict:
        """Crypto Fear & Greed Index (alternative.me — gratis, geen key)."""
        result = {"fear_greed": 0.5}
        if not _REQUESTS_AVAILABLE:
            dq["fear_greed"] = False
            return result
        try:
            resp = requests.get(
                "https://api.alternative.me/fng/?limit=1", timeout=10,
            )
            data = resp.json().get("data", [])
            if not data:
                raise ValueError("Geen Fear & Greed data")
            value = int(data[0]["value"])  # 0=extreme fear, 100=extreme greed
            label = data[0].get("value_classification", "")
            # Normaliseer: extreme fear (0-25)=hoog risico, extreme greed (75-100)=hoog risico
            # Neutraal (40-60)=laag risico
            distance_from_50 = abs(value - 50)
            result["fear_greed"] = round(min(distance_from_50 / 50, 1.0), 3)
            result["fear_greed_raw"] = value
            result["fear_greed_label"] = label
            dq["fear_greed"] = True

            if value <= self.thresholds.fg_extreme_fear:
                alerts.append(f"Extreme Fear: F&G={value} ({label})")
            elif value >= self.thresholds.fg_extreme_greed:
                alerts.append(f"Extreme Greed: F&G={value} ({label})")

        except (ConnectionError, ValueError, OSError, KeyError) as e:
            log.warning("Fear & Greed mislukt: %s", e)
            dq["fear_greed"] = False
        return result

    def _fetch_funding_rates(self, alerts: list, dq: dict) -> dict:
        """Binance perpetual funding rates (gratis, public API)."""
        result = {"funding_rate": 0.5}
        if not _REQUESTS_AVAILABLE:
            dq["funding_rate"] = False
            return result
        try:
            resp = requests.get(
                "https://fapi.binance.com/fapi/v1/fundingRate",
                params={"symbol": "BTCUSDT", "limit": 1},
                timeout=10,
            )
            data = resp.json()
            if not data:
                raise ValueError("Geen funding rate data")
            rate = float(data[0]["fundingRate"])
            result["funding_rate_raw"] = round(rate * 100, 4)  # als percentage

            # Normaliseer: -0.1% tot +0.1% = neutraal, extremen = hoog risico
            abs_rate = abs(rate)
            result["funding_rate"] = round(min(abs_rate / self.thresholds.fr_norm_divisor, 1.0), 3)
            dq["funding_rate"] = True

            if rate > self.thresholds.fr_alert_high:
                alerts.append(f"Hoge funding rate BTC: {rate*100:.4f}% (long-heavy)")
            elif rate < self.thresholds.fr_alert_low:
                alerts.append(f"Negatieve funding rate BTC: {rate*100:.4f}% (short-heavy)")

            # Voeg ook ETH en open interest toe
            try:
                eth_resp = requests.get(
                    "https://fapi.binance.com/fapi/v1/fundingRate",
                    params={"symbol": "ETHUSDT", "limit": 1},
                    timeout=10,
                )
                eth_data = eth_resp.json()
                if eth_data:
                    result["eth_funding_rate_raw"] = round(float(eth_data[0]["fundingRate"]) * 100, 4)
            except Exception:
                pass

            # Open Interest
            try:
                oi_resp = requests.get(
                    "https://fapi.binance.com/fapi/v1/openInterest",
                    params={"symbol": "BTCUSDT"},
                    timeout=10,
                )
                oi_data = oi_resp.json()
                if oi_data:
                    result["btc_open_interest"] = float(oi_data.get("openInterest", 0))
            except Exception:
                pass

        except (ConnectionError, ValueError, OSError, KeyError) as e:
            log.warning("Funding rate mislukt: %s", e)
            dq["funding_rate"] = False
        return result

    # ── Score aggregatie ─────────────────────────────────────────────────────

    def _compute_score(self, signals: dict) -> float:
        total_weight = 0.0
        weighted_sum = 0.0

        for signal, weight in self.SIGNAL_WEIGHTS.items():
            value = signals.get(signal)
            if value is None:
                continue
            weighted_sum += value * weight
            total_weight += weight

        if total_weight == 0:
            return 0.5

        return weighted_sum / total_weight

    def _classify(self, score: float) -> tuple[str, float, float]:
        t = self.thresholds
        if score < t.score_green_max:
            return "green", t.kelly_green, t.sl_green
        elif score < t.score_orange_max:
            return "orange", t.kelly_orange, t.sl_orange
        else:
            return "red", t.kelly_red, t.sl_red
