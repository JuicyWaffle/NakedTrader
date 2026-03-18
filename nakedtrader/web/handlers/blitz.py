"""Blitz — snel beleggingsadvies + directe uitvoering."""

import json
import logging
from dataclasses import asdict
from datetime import datetime

log = logging.getLogger(__name__)


def serve_blitz_advice(handler, budget, project_root, config):
    """GET /api/blitz/advice?budget=500 — Verzamel signalen en geef blitzadvies."""
    try:
        from nakedtrader.bots.registry import STRATEGIES
        from nakedtrader.risk.macro import MacroRiskEngine
        from nakedtrader.money.kelly import KellyPositionSizer

        # Macro risk
        macro_engine = MacroRiskEngine(
            emergency_score=config.macro_risk_veto_threshold
        )
        try:
            risk_report = macro_engine.evaluate()
            macro = {
                "risk_score": risk_report.risk_score,
                "risk_level": risk_report.risk_level,
                "kelly_mult": risk_report.kelly_mult,
                "emergency_brake": risk_report.emergency_brake,
                "alerts": risk_report.alerts,
            }
        except Exception as e:
            log.warning("Macro risk niet beschikbaar: %s", e)
            macro = {
                "risk_score": 0.5, "risk_level": "orange",
                "kelly_mult": 0.7, "emergency_brake": False, "alerts": [],
            }

        kelly = KellyPositionSizer(
            kelly_fraction=config.kelly_fraction,
            max_position_pct=config.max_position_pct,
        )

        # Verzamel signalen van alle actieve strategieën
        advice_list = []
        for sid, strategy in STRATEGIES.items():
            if not strategy.meta.active:
                continue
            try:
                signals = strategy.generate_signals()
            except Exception as e:
                log.warning("Strategie %s fout: %s", sid, e)
                continue
            if not signals:
                continue

            meta = strategy.meta
            for sig in signals:
                # Kelly sizing
                fraction = kelly.calculate(
                    sig.win_probability,
                    sig.expected_win_pct,
                    sig.expected_loss_pct,
                )
                # Pas macro kelly_mult toe
                fraction *= macro["kelly_mult"]
                size_eur = min(budget * fraction / config.max_position_pct, budget)
                size_eur = max(size_eur, 0)

                if size_eur < 10:
                    continue

                # Return schattingen op basis van StrategyMeta annual returns
                ret_annual_min = meta.expected_return_min / 100
                ret_annual_max = meta.expected_return_max / 100
                ret_annual_avg = (ret_annual_min + ret_annual_max) / 2
                wp = sig.win_probability
                km = macro["kelly_mult"]

                def _estimate(months):
                    factor = months / 12
                    return {
                        "min": round(size_eur * ret_annual_min * factor * km * wp, 2),
                        "max": round(size_eur * ret_annual_max * factor * km * wp, 2),
                        "expected": round(size_eur * ret_annual_avg * factor * km * wp, 2),
                    }

                # Strategy fit score
                try:
                    fit = macro_engine.strategy_fit(sid, [sig])
                except Exception:
                    fit = 0.0

                advice_list.append({
                    "symbol": sig.symbol,
                    "strategy_id": sid,
                    "strategy_name": meta.name,
                    "direction": sig.direction,
                    "current_price": sig.current_price,
                    "size_eur": round(size_eur, 2),
                    "return_1m": _estimate(1),
                    "return_3m": _estimate(3),
                    "risk_level": meta.risk_level,
                    "risk_score": meta.risk_score,
                    "win_probability": round(sig.win_probability, 3),
                    "expected_win_pct": sig.expected_win_pct,
                    "expected_loss_pct": sig.expected_loss_pct,
                    "strategy_fit": round(fit, 2),
                    "broker": meta.broker,
                    "notes": sig.notes,
                    "ai_reasoning": None,
                })

        # Sorteer op verwacht rendement 3M (descending)
        advice_list.sort(key=lambda a: a["return_3m"]["expected"], reverse=True)

        # Optioneel: AI reasoning via Ollama
        try:
            from nakedtrader.ai.ollama import generate, available
            from nakedtrader.ai.prompts import BLITZ_ADVICE_SYSTEM
            if available() and advice_list:
                for item in advice_list[:5]:
                    prompt = (
                        f"Signaal: {item['direction'].upper()} {item['symbol']} "
                        f"via {item['strategy_name']} @ €{item['current_price']:.2f}\n"
                        f"Positiegrootte: €{item['size_eur']:.2f}\n"
                        f"Win-kans: {item['win_probability']:.0%}\n"
                        f"Macro risk: {macro['risk_level']} (score {macro['risk_score']:.2f})\n"
                        f"Verwacht rendement 1M: €{item['return_1m']['expected']:.2f}\n"
                        f"Verwacht rendement 3M: €{item['return_3m']['expected']:.2f}\n"
                        f"Notities: {item['notes']}"
                    )
                    reasoning = generate(
                        prompt, task="reasoning",
                        system=BLITZ_ADVICE_SYSTEM,
                        max_tokens=150,
                    )
                    item["ai_reasoning"] = reasoning
        except Exception as e:
            log.debug("AI reasoning niet beschikbaar: %s", e)

        handler._json(200, {
            "budget": budget,
            "macro_risk": macro,
            "advice": advice_list,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        })
    except Exception as e:
        log.error("Blitz advice fout: %s", e)
        handler._json(500, {"error": str(e)})


def do_blitz_execute(handler, body, project_root, config):
    """POST /api/blitz/execute — Voer een blitz-advies uit via OrchestratorA."""
    try:
        from nakedtrader.types import TradeSignal
        from nakedtrader.orchestrator import OrchestratorA
        from dataclasses import asdict

        signal = TradeSignal(
            symbol=body["symbol"],
            broker=body.get("broker", "kraken"),
            direction=body.get("direction", "long"),
            win_probability=body.get("win_probability", 0.5),
            expected_win_pct=body.get("expected_win_pct", 0.10),
            expected_loss_pct=body.get("expected_loss_pct", 0.05),
            current_price=body.get("current_price", 0),
            strategy_id=body.get("strategy_id", ""),
            notes=f"Blitz execute: €{body.get('size_eur', 0):.2f}",
            timestamp=datetime.now().isoformat(timespec="seconds"),
        )

        orch = OrchestratorA(config)
        report = orch.process_signal(signal)

        if report:
            result = {
                "decision": report.decision,
                "reason": report.reason,
                "rule_triggered": report.rule_triggered,
                "size_executed": report.size_executed,
                "risk_score": report.risk_score,
                "orchestrator_id": report.orchestrator_id,
            }
        else:
            result = {"decision": "error", "reason": "Geen rapport ontvangen"}

        handler._json(200, result)
    except Exception as e:
        log.error("Blitz execute fout: %s", e)
        handler._json(500, {"error": str(e)})
