"""nakedtrader.orchestrator.execution_reporter — ExecutionReport logging."""

import json
import logging
from pathlib import Path

from nakedtrader.utils import atomic_write_json
from nakedtrader.orchestrator_pkg.constants import MAX_EXECUTION_LOG

log = logging.getLogger(__name__)


class ExecutionReporter:
    """Slaat ExecutionReports op in database + JSON logbestand."""

    def __init__(self, data_dir: Path, orchestrator_id: str, repo):
        self.orchestrator_id = orchestrator_id
        self.repo = repo
        self.exec_log_path = data_dir / f"executions_orch_{orchestrator_id}.json"
        self.exec_count = 0

    def log(self, report_dict: dict):
        """Sla ExecutionReport op in de database + JSON logbestand."""
        # Database persistentie
        try:
            self.repo.add_execution_report(report_dict)
        except (OSError, ValueError) as e:
            log.warning("[%s] DB execution log mislukt: %s", self.orchestrator_id, e)

        # JSON logbestand (voor web dashboard handlers)
        try:
            entries = []
            if self.exec_log_path.exists():
                with open(self.exec_log_path) as f:
                    entries = json.load(f)
            entries.append(report_dict)
            if len(entries) > MAX_EXECUTION_LOG:
                entries = entries[-MAX_EXECUTION_LOG:]
            atomic_write_json(self.exec_log_path, entries)
        except (OSError, json.JSONDecodeError, TypeError) as e:
            log.warning("[%s] JSON execution log mislukt: %s", self.orchestrator_id, e)

        self.exec_count += 1
