"""Orchestrator control endpoints (pause/resume, executions, compare)."""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from nakedtrader.utils import atomic_write_json

log = logging.getLogger(__name__)


def serve_status(handler, project_root, config):
    """GET /api/orchestrator/status — Pause state + execution stats."""
    try:
        state_path = Path(str(project_root / config.data_dir)) / "orchestrator_state.json"
        pause_state = {"paused_buys": False, "paused_sells": False, "paused_strategies": []}
        if state_path.exists():
            with open(state_path) as f:
                pause_state = json.load(f)

        data_dir = Path(str(project_root / config.data_dir))
        exec_stats = {}
        for oid in ("A", "B", "C"):
            exec_path = data_dir / f"executions_orch_{oid}.json"
            if exec_path.exists():
                try:
                    with open(exec_path) as f:
                        entries = json.load(f)
                    executed = sum(1 for e in entries if e.get("decision") == "executed")
                    blocked = sum(1 for e in entries if e.get("decision") == "blocked")
                    exec_stats[oid] = {"total": len(entries), "executed": executed, "blocked": blocked}
                except (json.JSONDecodeError, ValueError):
                    exec_stats[oid] = {"total": 0, "executed": 0, "blocked": 0}
            else:
                exec_stats[oid] = {"total": 0, "executed": 0, "blocked": 0}

        handler._json(200, {"pause_state": pause_state, "execution_stats": exec_stats})
    except Exception as e:
        handler._json(500, {"error": str(e)})


def serve_executions(handler, orch_id, limit, project_root, config):
    """GET /api/orchestrator/executions — Last N ExecutionReports."""
    try:
        exec_path = Path(str(project_root / config.data_dir)) / f"executions_orch_{orch_id}.json"
        if not exec_path.exists():
            handler._json(200, {"orchestrator": orch_id, "executions": []})
            return
        with open(exec_path) as f:
            entries = json.load(f)
        entries = entries[-limit:]
        entries.reverse()
        handler._json(200, {"orchestrator": orch_id, "executions": entries})
    except Exception as e:
        handler._json(500, {"error": str(e)})


def serve_compare(handler, period, since, project_root, config):
    """GET /api/orchestrator/compare — A vs B vs C comparison."""
    try:
        from nakedtrader.orchestrator_compare import compare

        since_dt = None
        if since:
            since_dt = datetime.strptime(since, "%Y-%m-%d")
        elif period:
            now = datetime.now()
            if period == "day":
                since_dt = now - timedelta(days=1)
            elif period == "week":
                since_dt = now - timedelta(weeks=1)
            elif period == "month":
                since_dt = now - timedelta(days=30)

        data = compare(since_dt)
        handler._json(200, data)
    except Exception as e:
        handler._json(500, {"error": str(e)})


def do_pause(handler, pause_type, strategy_id, pause, project_root, config):
    """POST /api/orchestrator/pause|resume — Pause/resume buys/sells/strategy."""
    try:
        state_path = Path(str(project_root / config.data_dir)) / "orchestrator_state.json"
        state = {"paused_buys": False, "paused_sells": False, "paused_strategies": []}
        if state_path.exists():
            with open(state_path) as f:
                state = json.load(f)

        action = "paused" if pause else "resumed"

        if pause_type == "buys":
            state["paused_buys"] = pause
        elif pause_type == "sells":
            state["paused_sells"] = pause
        elif pause_type == "strategy":
            if not strategy_id:
                handler._json(400, {"error": "missing id parameter for strategy pause"})
                return
            strategies = state.get("paused_strategies", [])
            if pause and strategy_id not in strategies:
                strategies.append(strategy_id)
            elif not pause and strategy_id in strategies:
                strategies.remove(strategy_id)
            state["paused_strategies"] = strategies
        else:
            handler._json(400, {"error": "type must be 'buys', 'sells', or 'strategy'"})
            return

        state["last_updated"] = datetime.now().isoformat(timespec="seconds")
        atomic_write_json(state_path, state, indent=2)

        handler._json(200, {"status": "ok", "action": action, "type": pause_type, "state": state})
    except Exception as e:
        handler._json(500, {"error": str(e)})
