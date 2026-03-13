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

import json
import logging
import os
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
        "layer_3_macro":        ["macro_calendar_risk", "fed_liquidity"],
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
# MACRO RISK ENGINE
# ─────────────────────────────────────────────────────────────────────────────

class MacroRiskEngine:
    """
    Bewaakt drie lagen van risicosignalen en produceert een RiskReport.

    Alle drempelwaarden zijn aanpasbaar zodat bots ze kunnen overschrijven
    op basis van hun eigen risicoprofiel.
    """

    # ── FRED series IDs ──
    FRED_SERIES = {
        "credit_spread": "BAMLH0A0HYM2",   # ICE BofA High Yield OAS
        "fed_tga":       "WTREGEN",         # Treasury General Account
    }

    # ── Gewichten per signaal (som = 1.0) ──
    SIGNAL_WEIGHTS = {
        "vix":                  0.15,
        "vix_term_structure":   0.10,
        "put_call_ratio":       0.10,
        "credit_spread":        0.15,
        "oil_shock":            0.15,
        "gold_dollar_ratio":    0.10,
        "gdelt_conflict_index": 0.10,
        "macro_calendar_risk":  0.10,
        "fed_liquidity":        0.05,
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
    ) -> None:
        self.fred_api_key                = fred_api_key or os.getenv("FRED_API_KEY", "")
        self.vix_threshold               = vix_threshold
        self.vix_backwardation_threshold = vix_backwardation_threshold
        self.put_call_threshold          = put_call_threshold
        self.oil_shock_pct               = oil_shock_pct
        self.credit_spread_threshold     = credit_spread_threshold
        self.gdelt_conflict_threshold    = gdelt_conflict_threshold
        self.emergency_score             = emergency_score

        self._fred: Optional[object] = None
        if _FRED_AVAILABLE and self.fred_api_key:
            try:
                self._fred = Fred(api_key=self.fred_api_key)
            except Exception as e:
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

        # Laag 3 — Macro-liquiditeit
        signals.update(self._fetch_macro_calendar(alerts, dq))
        signals.update(self._fetch_fed_liquidity(alerts, dq))

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

            # Normaliseer: 15=0.0, 25=0.5, 40+=1.0
            result["vix"] = round(min(max((vix_current - 15) / 25, 0.0), 1.0), 3)
            result["vix_raw"] = round(vix_current, 1)

            if vix_current > self.vix_threshold:
                alerts.append(f"VIX verhoogd: {vix_current:.1f} > drempel {self.vix_threshold}")

            # Term structure proxy: huidige VIX vs 5d gemiddelde
            if len(vix_data) >= 5:
                vix_avg = float(vix_data["Close"].mean())
                ts_ratio = vix_current / vix_avg if vix_avg > 0 else 1.0
                result["vix_term_structure"] = round(
                    min(max((ts_ratio - 0.9) / 0.4, 0.0), 1.0), 3
                )
                result["vix_ts_ratio"] = round(ts_ratio, 3)
                if ts_ratio > self.vix_backwardation_threshold:
                    alerts.append(f"VIX backwardation: ratio={ts_ratio:.2f}")

            # VVIX als extra signaal
            try:
                vvix_data = yf.Ticker("^VVIX").history(period="2d")
                if not vvix_data.empty:
                    vvix = float(vvix_data["Close"].iloc[-1])
                    result["vvix_raw"] = round(vvix, 1)
                    if vvix > 120:
                        alerts.append(f"VVIX sterk verhoogd: {vvix:.1f}")
            except Exception:
                pass

        except Exception as e:
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
            result["put_call_ratio"] = round(min(max((ratio - 0.7) / 1.1, 0.0), 1.0), 3)
            result["put_call_ratio_raw"] = round(ratio, 3)
            dq["yfinance_options"] = True

            if ratio > self.put_call_threshold:
                alerts.append(f"Put/call ratio verhoogd: {ratio:.2f}")

        except Exception as e:
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
            result["credit_spread"] = round(min(max((spread - 300) / 500, 0.0), 1.0), 3)
            result["credit_spread_raw"] = round(spread, 1)
            dq["FRED_credit_spread"] = True

            if spread > self.credit_spread_threshold:
                alerts.append(f"Credit spread verhoogd: {spread:.0f}bps")

        except Exception as e:
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

            result["oil_shock"] = round(min(pct_change / (self.oil_shock_pct * 2), 1.0), 3)
            result["oil_pct_change_raw"] = round(pct_change * 100, 2)
            result["oil_price_raw"] = round(close_today, 2)
            dq["yfinance_oil"] = True

            if pct_change > self.oil_shock_pct:
                alerts.append(f"Olieprijsschok: {pct_change*100:.1f}% in 24u")

        except Exception as e:
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
            dxy = yf.Ticker("DX-Y.NYB").history(period="10d")

            if len(gold) < 5 or len(dxy) < 5:
                raise ValueError("Onvoldoende gold/dollar data")

            gold_ratio = float(gold["Close"].iloc[-1]) / float(gold["Close"].iloc[-5])
            dxy_ratio = float(dxy["Close"].iloc[-1]) / float(dxy["Close"].iloc[-5])

            flight_signal = (gold_ratio - 1.0) - (dxy_ratio - 1.0)
            result["gold_dollar_ratio"] = round(
                min(max((flight_signal + 0.02) / 0.04, 0.0), 1.0), 3
            )
            result["gold_5d_change_pct"] = round((gold_ratio - 1) * 100, 2)
            result["dxy_5d_change_pct"] = round((dxy_ratio - 1) * 100, 2)
            dq["yfinance_gold_dxy"] = True

            if flight_signal > 0.015:
                alerts.append(
                    f"Vlucht naar veilige havens: goud +{(gold_ratio-1)*100:.1f}%, "
                    f"dollar {(dxy_ratio-1)*100:+.1f}% (5d)"
                )

        except Exception as e:
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

            import io
            import zipfile
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
                min(max((-avg_goldstein + 5) / 10, 0.0), 1.0), 3
            )
            result["gdelt_avg_goldstein"] = round(avg_goldstein, 2)
            result["gdelt_conflict_events"] = int(len(conflict_df))
            dq["GDELT"] = True

            if avg_goldstein < self.gdelt_conflict_threshold:
                alerts.append(
                    f"GDELT conflict index verhoogd: Goldstein={avg_goldstein:.1f} "
                    f"({len(conflict_df)} events)"
                )

        except Exception as e:
            log.warning("GDELT mislukt: %s", e)
            dq["GDELT"] = False

        return result

    # ── Laag 3: Macro-liquiditeit ────────────────────────────────────────────

    def _fetch_macro_calendar(self, alerts: list, dq: dict) -> dict:
        result = {"macro_calendar_risk": 0.0}

        # High-impact events (handmatig of via toekomstige API)
        HIGH_IMPACT_EVENTS: list[tuple[str, str]] = [
            # ("YYYY-MM-DD", "omschrijving"),
        ]

        today = datetime.utcnow().date()
        for date_str, description in HIGH_IMPACT_EVENTS:
            event_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            days_away = (event_date - today).days
            if 0 <= days_away <= 2:
                score = 1.0 - (days_away / 3)
                result["macro_calendar_risk"] = max(result["macro_calendar_risk"], score)
                alerts.append(f"High-impact event over {days_away} dag(en): {description}")

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
                observation_start=(datetime.utcnow() - timedelta(days=60)).strftime("%Y-%m-%d"),
            )
            if tga is None or len(tga) < 10:
                raise ValueError("Onvoldoende TGA data")

            tga_current = float(tga.dropna().iloc[-1])
            tga_avg = float(tga.dropna().iloc[-10:].mean())
            tga_change = (tga_current - tga_avg) / tga_avg if tga_avg != 0 else 0
            result["fed_liquidity"] = round(min(max(tga_change * 5 + 0.5, 0.0), 1.0), 3)
            result["tga_current_bn"] = round(tga_current / 1e9, 1)
            result["tga_10d_change_pct"] = round(tga_change * 100, 2)
            dq["FRED_tga"] = True

        except Exception as e:
            log.warning("Fed liquiditeit mislukt: %s", e)
            dq["FRED_tga"] = False

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
        if score < 0.35:
            return "green", 1.00, 1.00
        elif score < 0.65:
            return "orange", 0.50, 0.80
        else:
            return "red", 0.25, 0.60
