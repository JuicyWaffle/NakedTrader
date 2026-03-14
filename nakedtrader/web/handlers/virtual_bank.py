"""Virtual Bank portfolio endpoints."""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from nakedtrader.utils import atomic_write_json

log = logging.getLogger(__name__)

STARTING_CAPITAL = 100_000.0


def serve_portfolio(handler, project_root, config):
    """GET /api/virtual-bank/portfolio — Current virtual bank state."""
    try:
        from nakedtrader.virtual_bank import get_portfolio_from_state
        data_dir = str(project_root / config.data_dir)
        portfolio = get_portfolio_from_state(data_dir)
        handler._json(200, {"portfolio": portfolio})
    except Exception as e:
        handler._json(500, {"error": str(e)})


def serve_history(handler, params, project_root, config):
    """GET /api/virtual-bank/history — Portfolio value history snapshots."""
    try:
        data_dir = project_root / config.data_dir
        history_path = data_dir / "virtual_bank_history.json"

        if not history_path.exists():
            handler._json(200, {"snapshots": [], "starting_capital": STARTING_CAPITAL})
            return

        with open(history_path) as f:
            history = json.load(f)

        hours = int(params.get("hours", ["0"])[0])
        if hours > 0:
            cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
            history = [s for s in history if s.get("timestamp", "") >= cutoff]

        max_points = int(params.get("max_points", ["500"])[0])
        if len(history) > max_points:
            step = len(history) / max_points
            history = [history[int(i * step)] for i in range(max_points)]

        handler._json(200, {"snapshots": history, "starting_capital": STARTING_CAPITAL})
    except Exception as e:
        handler._json(500, {"error": str(e)})


def do_reset(handler, strategy_id, project_root, config):
    """POST /api/virtual-bank/reset — Reset bot to starting capital."""
    try:
        state_path = Path(str(project_root / config.data_dir)) / "virtual_bank_state.json"
        if state_path.exists():
            with open(state_path) as f:
                state = json.load(f)
        else:
            state = {}

        state[strategy_id] = {"cash": STARTING_CAPITAL, "positions": {}}
        atomic_write_json(state_path, state, indent=2)

        handler._json(200, {"status": "ok", "strategy": strategy_id, "cash": STARTING_CAPITAL})
    except Exception as e:
        handler._json(500, {"error": str(e)})
