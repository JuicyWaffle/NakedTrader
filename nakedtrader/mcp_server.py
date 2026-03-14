"""
nakedtrader.mcp_server — MCP interface voor NakedTrader.

12 tools voor conversationele toegang tot het trading systeem:
  Tier 1 (read-only): system status, strategies, overnight, macro, performance, trades
  Tier 2 (write + bevestiging): pause, resume, reset, emergency halt
  Tier 3 (analyse): execution log, orchestrator vergelijking

Start:
  fastmcp run nakedtrader/mcp_server.py
"""

import json
import logging
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path

from fastmcp import FastMCP, Context

log = logging.getLogger(__name__)

mcp = FastMCP("NakedTrader", instructions="Trading system MCP interface")

# ── Lazy init ────────────────────────────────────────────
_config = None
_project_root = None
_data_dir = None


def _init():
    global _config, _project_root, _data_dir
    if _config is None:
        from nakedtrader.config import load_config
        _project_root = Path(__file__).resolve().parent.parent
        _config = load_config(project_dir=_project_root)
        _data_dir = _project_root / _config.data_dir


def _read_json(path: Path) -> list | dict:
    if not path.exists():
        return []
    with open(path) as f:
        return json.load(f)


# ══════════════════════════════════════════════════════════
# TIER 1 — READ-ONLY TOOLS
# ══════════════════════════════════════════════════════════


@mcp.tool(annotations={"readOnlyHint": True})
def get_system_status() -> dict:
    """Current system status: pause state, orchestrator execution stats, virtual bank summary."""
    _init()

    # Pause state
    state_path = _data_dir / "orchestrator_state.json"
    pause_state = {"paused_buys": False, "paused_sells": False, "paused_strategies": []}
    if state_path.exists():
        pause_state = _read_json(state_path)

    # Execution stats per orchestrator
    exec_stats = {}
    for oid in ("A", "B", "C"):
        entries = _read_json(_data_dir / f"executions_orch_{oid}.json")
        if entries:
            executed = sum(1 for e in entries if e.get("decision") == "executed")
            blocked = sum(1 for e in entries if e.get("decision") == "blocked")
            exec_stats[oid] = {"total": len(entries), "executed": executed, "blocked": blocked}
        else:
            exec_stats[oid] = {"total": 0, "executed": 0, "blocked": 0}

    # Virtual bank summary
    vb_summary = {}
    vb_state = _read_json(_data_dir / "virtual_bank_state.json")
    if isinstance(vb_state, dict):
        for sid, bot in vb_state.items():
            if not isinstance(bot, dict):
                continue
            vb_summary[sid] = {
                "cash": round(bot.get("cash", 0), 2),
                "open_positions": len(bot.get("positions", {})),
            }

    return {
        "pause_state": pause_state,
        "execution_stats": exec_stats,
        "virtual_bank": vb_summary,
    }


@mcp.tool(annotations={"readOnlyHint": True})
def get_strategy_status(strategy_id: str = "") -> dict:
    """Adaptive learning state per strategy: win-rate, Sharpe, cooldowns, A/B tests.

    Args:
        strategy_id: Filter on one strategy (e.g. "momentum"). Empty = all strategies.
    """
    _init()

    state_path = _project_root / _config.state_path
    if not state_path.exists():
        return {"strategies": {}}

    data = _read_json(state_path)
    strategies = data.get("strategies", {})

    if strategy_id:
        strategies = {k: v for k, v in strategies.items() if k == strategy_id}

    result = {}
    for sid, s in strategies.items():
        result[sid] = {
            "rolling_win_rate": s.get("rolling_win_rate", 0.5),
            "rolling_sharpe": s.get("rolling_sharpe", 0.0),
            "consecutive_losses": s.get("consecutive_losses", 0),
            "cooldown_until": s.get("cooldown_until"),
            "risk_budget_pct": s.get("risk_budget_pct", 0.20),
            "recent_trades_count": len(s.get("recent_trades", [])),
            "ab_active": s.get("ab_active", False),
        }

    return {"strategies": result}


@mcp.tool(annotations={"readOnlyHint": True})
def get_overnight_report(hours: int = 12) -> dict:
    """What happened overnight: execution summary, portfolio delta, per-strategy breakdown.

    Args:
        hours: How many hours back to look (default 12).
    """
    _init()

    cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()

    # Execution reports (orchestrator A = primary)
    all_entries = _read_json(_data_dir / "executions_orch_A.json")
    recent = [e for e in all_entries if e.get("timestamp", "") >= cutoff]

    executed = [e for e in recent if e.get("decision") == "executed"]
    blocked = [e for e in recent if e.get("decision") == "blocked"]

    # Per-strategy breakdown
    by_strategy = {}
    for e in recent:
        sid = e.get("signal", {}).get("strategy_id", "unknown")
        if sid not in by_strategy:
            by_strategy[sid] = {"executed": 0, "blocked": 0}
        if e.get("decision") == "executed":
            by_strategy[sid]["executed"] += 1
        elif e.get("decision") == "blocked":
            by_strategy[sid]["blocked"] += 1

    # Portfolio delta from virtual bank history
    portfolio_delta = None
    history = _read_json(_data_dir / "virtual_bank_history.json")
    if history:
        recent_snaps = [s for s in history if s.get("timestamp", "") >= cutoff]
        if recent_snaps and len(history) > 1:
            first = recent_snaps[0] if recent_snaps else history[-2]
            last = history[-1]
            first_vals = first.get("values", {})
            last_vals = last.get("values", {})
            portfolio_delta = {}
            for sid in set(list(first_vals.keys()) + list(last_vals.keys())):
                v0 = first_vals.get(sid, 100_000)
                v1 = last_vals.get(sid, 100_000)
                portfolio_delta[sid] = {
                    "start": round(v0, 2),
                    "end": round(v1, 2),
                    "change_eur": round(v1 - v0, 2),
                    "change_pct": round((v1 - v0) / v0 * 100, 2) if v0 else 0,
                }

    return {
        "period_hours": hours,
        "total_signals": len(recent),
        "executed": len(executed),
        "blocked": len(blocked),
        "by_strategy": by_strategy,
        "portfolio_delta": portfolio_delta,
    }


@mcp.tool(annotations={"readOnlyHint": True})
def get_macro_risk() -> dict:
    """Current macro risk assessment: risk score, level, Kelly multiplier, alerts."""
    _init()

    try:
        from nakedtrader.risk.macro import MacroRiskEngine
        engine = MacroRiskEngine()
        report = engine.evaluate()
        return asdict(report)
    except ImportError:
        return {"error": "MacroRiskEngine niet beschikbaar (ontbrekende dependencies)"}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(annotations={"readOnlyHint": True})
def get_performance(strategy_id: str = "", days: int = 30) -> dict:
    """Performance data: daily PnL, win-rate, trade count per strategy.

    Args:
        strategy_id: Filter on one strategy. Empty = all.
        days: Number of days to look back (default 30).
    """
    _init()

    from nakedtrader.performance.store import PerformanceStore
    store = PerformanceStore(
        data_dir=str(_data_dir),
        starting_capital=_config.total_capital,
    )
    return store.get_daily(days=days, strategy_id=strategy_id or None)


@mcp.tool(annotations={"readOnlyHint": True})
def get_recent_trades(strategy_id: str = "", limit: int = 20) -> dict:
    """Recent virtual bank trades from the ledger.

    Args:
        strategy_id: Filter on one strategy. Empty = all.
        limit: Max number of trades to return (default 20).
    """
    _init()

    ledger_data = _read_json(_data_dir / "ledger.json")
    transactions = ledger_data.get("transactions", []) if isinstance(ledger_data, dict) else []

    if strategy_id:
        transactions = [t for t in transactions if t.get("strategy_id") == strategy_id]

    # Most recent first
    transactions = transactions[-limit:]
    transactions.reverse()

    return {"trades": transactions, "count": len(transactions)}


# ══════════════════════════════════════════════════════════
# TIER 2 — WRITE TOOLS (met bevestiging)
# ══════════════════════════════════════════════════════════


@mcp.tool()
async def pause_trading(pause_type: str, strategy_id: str = "", ctx: Context = None) -> dict:
    """Pause trading activity. Requires confirmation.

    Args:
        pause_type: What to pause: "buys", "sells", or "strategy".
        strategy_id: Required when pause_type is "strategy" (e.g. "momentum").
    """
    _init()

    if pause_type not in ("buys", "sells", "strategy"):
        return {"error": "pause_type must be 'buys', 'sells', or 'strategy'"}
    if pause_type == "strategy" and not strategy_id:
        return {"error": "strategy_id required when pause_type is 'strategy'"}

    desc = f"Pause {pause_type}" + (f" for strategy '{strategy_id}'" if strategy_id else "")
    response = await ctx.elicit(
        message=f"{desc}. This will prevent the orchestrator from executing matching signals. Confirm?",
        schema={"type": "object", "properties": {"confirm": {"type": "boolean", "default": True}}},
    )
    if not response.data or not response.data.get("confirm"):
        return {"status": "cancelled"}

    return _do_pause(pause_type, strategy_id, pause=True)


@mcp.tool()
async def resume_trading(resume_type: str, strategy_id: str = "", ctx: Context = None) -> dict:
    """Resume paused trading activity. Requires confirmation.

    Args:
        resume_type: What to resume: "buys", "sells", or "strategy".
        strategy_id: Required when resume_type is "strategy" (e.g. "momentum").
    """
    _init()

    if resume_type not in ("buys", "sells", "strategy"):
        return {"error": "resume_type must be 'buys', 'sells', or 'strategy'"}
    if resume_type == "strategy" and not strategy_id:
        return {"error": "strategy_id required when resume_type is 'strategy'"}

    desc = f"Resume {resume_type}" + (f" for strategy '{strategy_id}'" if strategy_id else "")
    response = await ctx.elicit(
        message=f"{desc}. Trading will be re-enabled. Confirm?",
        schema={"type": "object", "properties": {"confirm": {"type": "boolean", "default": True}}},
    )
    if not response.data or not response.data.get("confirm"):
        return {"status": "cancelled"}

    return _do_pause(resume_type, strategy_id, pause=False)


def _do_pause(pause_type: str, strategy_id: str, pause: bool) -> dict:
    """Shared logic for pause/resume."""
    from nakedtrader.utils import atomic_write_json

    state_path = _data_dir / "orchestrator_state.json"
    state = {"paused_buys": False, "paused_sells": False, "paused_strategies": []}
    if state_path.exists():
        state = _read_json(state_path)

    action = "paused" if pause else "resumed"

    if pause_type == "buys":
        state["paused_buys"] = pause
    elif pause_type == "sells":
        state["paused_sells"] = pause
    elif pause_type == "strategy":
        strategies = state.get("paused_strategies", [])
        if pause and strategy_id not in strategies:
            strategies.append(strategy_id)
        elif not pause and strategy_id in strategies:
            strategies.remove(strategy_id)
        state["paused_strategies"] = strategies

    state["last_updated"] = datetime.now().isoformat(timespec="seconds")
    atomic_write_json(state_path, state, indent=2)

    return {"status": "ok", "action": action, "type": pause_type, "state": state}


@mcp.tool()
async def reset_virtual_bank(strategy_id: str, ctx: Context = None) -> dict:
    """Reset a virtual bank strategy to starting capital (EUR 100,000). Requires confirmation.

    Args:
        strategy_id: Which strategy to reset (e.g. "momentum", "breakout").
    """
    _init()

    # Show current value
    vb_state = _read_json(_data_dir / "virtual_bank_state.json")
    current = vb_state.get(strategy_id, {}) if isinstance(vb_state, dict) else {}
    current_cash = current.get("cash", 0)
    n_positions = len(current.get("positions", {}))

    response = await ctx.elicit(
        message=(
            f"Reset '{strategy_id}' to EUR 100,000 starting capital.\n"
            f"Current: cash=EUR {current_cash:,.2f}, {n_positions} open positions.\n"
            f"All positions will be wiped. Confirm?"
        ),
        schema={"type": "object", "properties": {"confirm": {"type": "boolean", "default": True}}},
    )
    if not response.data or not response.data.get("confirm"):
        return {"status": "cancelled"}

    from nakedtrader.utils import atomic_write_json

    state_path = _data_dir / "virtual_bank_state.json"
    state = _read_json(state_path) if state_path.exists() else {}
    if not isinstance(state, dict):
        state = {}
    state[strategy_id] = {"cash": 100_000.0, "positions": {}}
    atomic_write_json(state_path, state, indent=2)

    return {"status": "ok", "strategy": strategy_id, "cash": 100_000.0}


@mcp.tool()
async def emergency_halt(ctx: Context = None) -> dict:
    """Emergency halt: pause ALL buys, sells, and strategies immediately. Requires confirmation."""
    _init()

    response = await ctx.elicit(
        message=(
            "EMERGENCY HALT: This will pause ALL trading activity.\n"
            "- All buys paused\n"
            "- All sells paused\n"
            "- All strategies paused\n"
            "Confirm emergency halt?"
        ),
        schema={"type": "object", "properties": {"confirm": {"type": "boolean", "default": True}}},
    )
    if not response.data or not response.data.get("confirm"):
        return {"status": "cancelled"}

    from nakedtrader.utils import atomic_write_json
    from nakedtrader.bots.registry import STRATEGIES

    state = {
        "paused_buys": True,
        "paused_sells": True,
        "paused_strategies": list(STRATEGIES.keys()),
        "last_updated": datetime.now().isoformat(timespec="seconds"),
        "emergency_halt": True,
    }
    atomic_write_json(_data_dir / "orchestrator_state.json", state, indent=2)

    return {"status": "ok", "action": "emergency_halt", "state": state}


# ══════════════════════════════════════════════════════════
# TIER 3 — ANALYSE TOOLS
# ══════════════════════════════════════════════════════════


@mcp.tool(annotations={"readOnlyHint": True})
def get_execution_log(orchestrator_id: str = "A", limit: int = 50) -> dict:
    """Recent ExecutionReports from an orchestrator.

    Args:
        orchestrator_id: Which orchestrator: "A" (conservative), "B" (aggressive), "C" (diversification).
        limit: Max entries to return (default 50).
    """
    _init()

    entries = _read_json(_data_dir / f"executions_orch_{orchestrator_id}.json")
    entries = entries[-limit:]
    entries.reverse()

    return {"orchestrator": orchestrator_id, "executions": entries}


@mcp.tool(annotations={"readOnlyHint": True})
def compare_orchestrators(period: str = "week") -> dict:
    """Compare orchestrator A vs B vs C: PnL, Sharpe, win-rate, divergences.

    Args:
        period: Time period: "day", "week", or "month".
    """
    _init()

    from nakedtrader.orchestrator_compare import compare

    now = datetime.now()
    if period == "day":
        since = now - timedelta(days=1)
    elif period == "week":
        since = now - timedelta(weeks=1)
    elif period == "month":
        since = now - timedelta(days=30)
    else:
        since = None

    return compare(since)


# ══════════════════════════════════════════════════════════
# TIER 4 — AI-POWERED TOOLS (Ollama)
# ══════════════════════════════════════════════════════════


@mcp.tool(annotations={"readOnlyHint": True})
def ai_market_analysis(question: str) -> dict:
    """Ask the AI about current market conditions, signals, or risk.

    The AI receives current system data (risk, executions, portfolio) as context
    and answers your question based on that data.

    Args:
        question: Your question, e.g. "What happened overnight?",
                  "Should I be worried about the current risk level?",
                  "Explain the recent blocked signals"
    """
    _init()

    from nakedtrader.ai.ollama import generate, available
    from nakedtrader.ai.prompts import MARKET_ANALYSIS_SYSTEM

    if not available():
        return {"error": "Ollama niet bereikbaar op " + _config.ollama_url}

    # Verzamel context
    context_parts = []

    # Recente executions
    entries = _read_json(_data_dir / "executions_orch_A.json")
    recent = entries[-20:] if entries else []
    if recent:
        executed = sum(1 for e in recent if e.get("decision") == "executed")
        blocked = sum(1 for e in recent if e.get("decision") == "blocked")
        context_parts.append(
            f"Recent executions (last 20): {executed} executed, {blocked} blocked"
        )
        # Top block reasons
        reasons = {}
        for e in recent:
            if e.get("decision") == "blocked":
                rule = e.get("rule_triggered", "?")
                reasons[rule] = reasons.get(rule, 0) + 1
        if reasons:
            top = sorted(reasons.items(), key=lambda x: -x[1])[:5]
            context_parts.append(
                "Top block rules: " + ", ".join(f"{r}={c}x" for r, c in top)
            )

    # Pause state
    pause = _read_json(_data_dir / "orchestrator_state.json")
    if isinstance(pause, dict):
        context_parts.append(
            f"Pause state: buys={'PAUSED' if pause.get('paused_buys') else 'active'}, "
            f"sells={'PAUSED' if pause.get('paused_sells') else 'active'}, "
            f"paused strategies: {pause.get('paused_strategies', [])}"
        )

    # Virtual bank summary
    vb = _read_json(_data_dir / "virtual_bank_state.json")
    if isinstance(vb, dict):
        for sid, bot in vb.items():
            if isinstance(bot, dict):
                cash = bot.get("cash", 0)
                n_pos = len(bot.get("positions", {}))
                context_parts.append(f"VB {sid}: cash=€{cash:,.0f}, positions={n_pos}")

    # Portfolio delta (last 12h)
    history = _read_json(_data_dir / "virtual_bank_history.json")
    if history and len(history) >= 2:
        cutoff = (datetime.now() - timedelta(hours=12)).isoformat()
        recent_snaps = [s for s in history if s.get("timestamp", "") >= cutoff]
        if recent_snaps:
            first_vals = recent_snaps[0].get("values", {})
            last_vals = history[-1].get("values", {})
            deltas = []
            for sid in first_vals:
                v0, v1 = first_vals.get(sid, 0), last_vals.get(sid, 0)
                if v0:
                    deltas.append(f"{sid}: {(v1-v0)/v0*100:+.2f}%")
            if deltas:
                context_parts.append("12h portfolio delta: " + ", ".join(deltas))

    context = "\n".join(context_parts)
    full_prompt = f"SYSTEM DATA:\n{context}\n\nTRADER QUESTION: {question}"

    analysis = generate(
        full_prompt, task="report",
        system=MARKET_ANALYSIS_SYSTEM, max_tokens=800,
    )

    return {
        "analysis": analysis or "Ollama kon geen analyse genereren.",
        "model": "qwen2.5:14b",
        "data_context": context_parts,
    }


@mcp.tool(annotations={"readOnlyHint": True})
def ai_risk_narrative() -> dict:
    """AI-generated narrative of the current macro risk situation.

    Fetches a fresh RiskReport and asks the AI to write a human-readable summary.
    """
    _init()

    from nakedtrader.ai.ollama import generate, available
    from nakedtrader.ai.prompts import RISK_NARRATIVE_SYSTEM

    if not available():
        return {"error": "Ollama niet bereikbaar op " + _config.ollama_url}

    # Haal RiskReport op
    try:
        from nakedtrader.risk.macro import MacroRiskEngine
        engine = MacroRiskEngine(ollama_enabled=False)  # geen recursie
        report = engine.evaluate()
        report_data = report.to_dict()
    except Exception as e:
        return {"error": f"RiskReport ophalen mislukt: {e}"}

    # Bouw context voor LLM
    context = (
        f"Risk Score: {report.risk_score:.2f} [{report.risk_level.upper()}]\n"
        f"Kelly multiplier: x{report.kelly_mult:.2f}\n"
        f"Stop-loss multiplier: x{report.sl_mult:.2f}\n"
        f"Emergency brake: {'YES' if report.emergency_brake else 'no'}\n"
    )
    if report.alerts:
        context += "Alerts:\n" + "\n".join(f"- {a}" for a in report.alerts) + "\n"

    # Key signals
    sigs = report.signals
    context += "\nKey signals:\n"
    for key in ["vix", "vix_raw", "put_call_ratio", "credit_spread",
                "oil_shock", "gdelt_conflict_index", "fed_liquidity"]:
        if key in sigs:
            context += f"- {key}: {sigs[key]}\n"

    narrative = generate(
        context, task="narrative",
        system=RISK_NARRATIVE_SYSTEM, max_tokens=500,
    )

    return {
        "narrative": narrative or "Ollama kon geen narratief genereren.",
        "risk_report": report_data,
        "model": "qwen2.5:7b",
    }
