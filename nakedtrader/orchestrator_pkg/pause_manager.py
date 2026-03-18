"""nakedtrader.orchestrator.pause_manager — Pause/resume beheer."""

import json
import logging
from datetime import datetime
from pathlib import Path

from nakedtrader.utils import atomic_write_json

log = logging.getLogger(__name__)


class PauseManager:
    """Beheert pause state voor buys, sells en individuele strategieën."""

    def __init__(self, data_dir: Path, orchestrator_id: str = "A"):
        self.orchestrator_id = orchestrator_id
        self._pause_path = data_dir / "orchestrator_state.json"
        self._state = self._load()

    def _load(self) -> dict:
        if self._pause_path.exists():
            try:
                with open(self._pause_path) as f:
                    return json.load(f)
            except (json.JSONDecodeError, KeyError):
                pass
        return {"paused_buys": False, "paused_sells": False, "paused_strategies": []}

    def _save(self):
        self._state["last_updated"] = datetime.now().isoformat(timespec="seconds")
        atomic_write_json(self._pause_path, self._state, indent=2)

    def pause_buys(self):
        self._state["paused_buys"] = True
        self._save()
        log.info("[%s] Aankopen gepauzeerd", self.orchestrator_id)

    def resume_buys(self):
        self._state["paused_buys"] = False
        self._save()
        log.info("[%s] Aankopen hervat", self.orchestrator_id)

    def pause_sells(self):
        self._state["paused_sells"] = True
        self._save()
        log.info("[%s] Verkopen gepauzeerd", self.orchestrator_id)

    def resume_sells(self):
        self._state["paused_sells"] = False
        self._save()
        log.info("[%s] Verkopen hervat", self.orchestrator_id)

    def pause_strategy(self, strategy_id: str):
        if strategy_id not in self._state["paused_strategies"]:
            self._state["paused_strategies"].append(strategy_id)
            self._save()
            log.info("[%s] Strategie %s gepauzeerd", self.orchestrator_id, strategy_id)

    def resume_strategy(self, strategy_id: str):
        if strategy_id in self._state["paused_strategies"]:
            self._state["paused_strategies"].remove(strategy_id)
            self._save()
            log.info("[%s] Strategie %s hervat", self.orchestrator_id, strategy_id)

    def get_state(self) -> dict:
        """Herlaad van disk en retourneer state."""
        self._state = self._load()
        return dict(self._state)

    def reload(self):
        """Herlaad state van disk."""
        self._state = self._load()

    @property
    def paused_buys(self) -> bool:
        return self._state.get("paused_buys", False)

    @property
    def paused_sells(self) -> bool:
        return self._state.get("paused_sells", False)

    @property
    def paused_strategies(self) -> list:
        return self._state.get("paused_strategies", [])
