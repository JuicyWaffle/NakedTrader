"""
nakedtrader/money/adaptive.py — Adaptief leersysteem voor NakedTrader.

Per-strategie state bijhouden:
  - Rolling win-rate (laatste 50 trades)
  - Rolling Sharpe ratio (annualized)
  - Consecutive losses → cooldown
  - A/B testing (baseline vs candidate parameters)
  - Maandelijkse parameter-evaluatie

Persistentie via strategy_state.json (atomic writes).
"""

import json
import logging
import tempfile
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

ROLLING_WINDOW = 50          # trades voor rolling metrics
COOLDOWN_THRESHOLD = 5       # opeenvolgende verliezen → cooldown
COOLDOWN_HOURS = 4           # uren pauze na cooldown trigger
AB_TEST_TRADES = 30          # trades per variant voor A/B test evaluatie

DEFAULT_STATE_PATH = "strategy_state.json"


# ─────────────────────────────────────────────
# STRATEGY STATE
# ─────────────────────────────────────────────

@dataclass
class StrategyState:
    """Per-strategie adaptieve state."""
    strategy_id: str
    recent_trades: list = field(default_factory=list)   # [{pnl_pct, outcome, timestamp}, ...]
    rolling_win_rate: float = 0.5
    rolling_sharpe: float = 0.0
    consecutive_losses: int = 0
    cooldown_until: Optional[str] = None                # ISO timestamp of None
    risk_budget_pct: float = 0.20                       # default 20%

    # A/B testing
    ab_active: bool = False
    ab_baseline_params: dict = field(default_factory=dict)
    ab_candidate_params: dict = field(default_factory=dict)
    ab_baseline_results: list = field(default_factory=list)
    ab_candidate_results: list = field(default_factory=list)
    ab_trade_count: int = 0

    # Custom state per strategie (EMA periodes, BB multiplier, etc.)
    custom_state: dict = field(default_factory=dict)

    # Maandelijkse evaluatie
    last_monthly_check: Optional[str] = None


# ─────────────────────────────────────────────
# ADAPTIVE ENGINE
# ─────────────────────────────────────────────

class AdaptiveEngine:
    """
    Beheert adaptieve state voor alle strategieen.
    Laadt bij startup, slaat op na elke wijziging.
    """

    def __init__(self, state_path: str = DEFAULT_STATE_PATH):
        self.state_path = Path(state_path)
        self.states: dict[str, StrategyState] = {}
        self._load()

    # ── Persistentie ────────────────────────────

    def _load(self):
        """Laad state uit JSON bestand."""
        if self.state_path.exists():
            try:
                with open(self.state_path) as f:
                    data = json.load(f)
                for sid, state_dict in data.get("strategies", {}).items():
                    self.states[sid] = StrategyState(**state_dict)
                log.info(f"Adaptive state geladen: {len(self.states)} strategieen")
            except Exception as e:
                log.warning(f"Fout bij laden adaptive state: {e}")

    def _save(self):
        """Atomic write naar JSON bestand."""
        data = {
            "strategies": {sid: asdict(s) for sid, s in self.states.items()},
            "last_updated": datetime.now().isoformat(),
        }
        try:
            dir_path = self.state_path.parent
            fd, tmp_path = tempfile.mkstemp(dir=str(dir_path), suffix=".tmp")
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, str(self.state_path))
        except Exception as e:
            log.error(f"Fout bij opslaan adaptive state: {e}")

    # ── State toegang ───────────────────────────

    def get_state(self, strategy_id: str) -> StrategyState:
        """Geef state voor strategie, maak aan als die niet bestaat."""
        if strategy_id not in self.states:
            self.states[strategy_id] = StrategyState(strategy_id=strategy_id)
        return self.states[strategy_id]

    # ── Trade registratie ───────────────────────

    def record_trade(self, strategy_id: str, pnl_pct: float, outcome: str):
        """
        Registreer een afgeronde trade en herbereken metrics.

        Args:
            strategy_id: ID van de strategie
            pnl_pct: winst/verlies in percentage (bv. 5.2 of -3.1)
            outcome: "win" of "loss"
        """
        state = self.get_state(strategy_id)

        # Trade toevoegen aan rolling window
        trade_entry = {
            "pnl_pct": pnl_pct,
            "outcome": outcome,
            "timestamp": datetime.now().isoformat(),
        }
        state.recent_trades.append(trade_entry)

        # Beperk tot ROLLING_WINDOW
        if len(state.recent_trades) > ROLLING_WINDOW:
            state.recent_trades = state.recent_trades[-ROLLING_WINDOW:]

        # Consecutive losses bijwerken
        if outcome == "loss":
            state.consecutive_losses += 1
        else:
            state.consecutive_losses = 0

        # Cooldown activeren als drempel bereikt
        if state.consecutive_losses >= COOLDOWN_THRESHOLD:
            until = datetime.now() + timedelta(hours=COOLDOWN_HOURS)
            state.cooldown_until = until.isoformat()
            state.consecutive_losses = 0
            log.warning(
                f"[ADAPTIVE] {strategy_id}: {COOLDOWN_THRESHOLD} verliezen op rij "
                f"→ cooldown tot {until.strftime('%H:%M')}"
            )

        # A/B test trade registreren
        if state.ab_active:
            state.ab_trade_count += 1
            if state.ab_trade_count % 2 == 0:
                state.ab_baseline_results.append(pnl_pct)
            else:
                state.ab_candidate_results.append(pnl_pct)

        # Herbereken rolling metrics
        self._update_rolling_metrics(state)
        self._save()

    def _update_rolling_metrics(self, state: StrategyState):
        """Herbereken win-rate en Sharpe op basis van recent_trades."""
        trades = state.recent_trades
        if not trades:
            state.rolling_win_rate = 0.5
            state.rolling_sharpe = 0.0
            return

        # Win rate
        wins = sum(1 for t in trades if t["outcome"] == "win")
        state.rolling_win_rate = round(wins / len(trades), 4)

        # Sharpe ratio (annualized)
        returns = [t["pnl_pct"] for t in trades]
        if len(returns) >= 2:
            import numpy as np
            arr = np.array(returns)
            mean_ret = np.mean(arr)
            std_ret = np.std(arr, ddof=1)
            if std_ret > 0:
                # Annualize: aanname ~250 trades/jaar
                state.rolling_sharpe = round(
                    (mean_ret / std_ret) * (250 ** 0.5), 4
                )
            else:
                state.rolling_sharpe = 0.0
        else:
            state.rolling_sharpe = 0.0

    # ── Cooldown ────────────────────────────────

    def is_in_cooldown(self, strategy_id: str) -> bool:
        """Check of strategie in cooldown zit."""
        state = self.get_state(strategy_id)
        if state.cooldown_until is None:
            return False
        try:
            until = datetime.fromisoformat(state.cooldown_until)
            if datetime.now() < until:
                return True
            # Cooldown verlopen — reset
            state.cooldown_until = None
            self._save()
            return False
        except (ValueError, TypeError):
            state.cooldown_until = None
            return False

    # ── Rolling metrics ─────────────────────────

    def get_rolling_win_rate(self, strategy_id: str) -> float:
        """Geef rolling win-rate (0.0 – 1.0)."""
        return self.get_state(strategy_id).rolling_win_rate

    def get_rolling_sharpe(self, strategy_id: str) -> float:
        """Geef rolling Sharpe ratio."""
        return self.get_state(strategy_id).rolling_sharpe

    # ── A/B Testing ─────────────────────────────

    def start_ab_test(
        self,
        strategy_id: str,
        baseline_params: dict,
        candidate_params: dict,
    ):
        """Start een A/B test voor een strategie."""
        state = self.get_state(strategy_id)
        state.ab_active = True
        state.ab_baseline_params = baseline_params
        state.ab_candidate_params = candidate_params
        state.ab_baseline_results = []
        state.ab_candidate_results = []
        state.ab_trade_count = 0
        self._save()
        log.info(f"[ADAPTIVE] A/B test gestart voor {strategy_id}")

    def get_ab_params(self, strategy_id: str) -> Optional[dict]:
        """
        Geef de parameters voor de volgende trade in een A/B test.
        Even trades → baseline, oneven → candidate.
        Retourneert None als er geen actieve test is.
        """
        state = self.get_state(strategy_id)
        if not state.ab_active:
            return None
        if state.ab_trade_count % 2 == 0:
            return state.ab_baseline_params
        return state.ab_candidate_params

    def evaluate_ab_test(self, strategy_id: str) -> Optional[dict]:
        """
        Evalueer een A/B test als er genoeg trades zijn.
        Retourneert resultaat dict of None als test nog loopt.
        """
        state = self.get_state(strategy_id)
        if not state.ab_active:
            return None

        min_trades = AB_TEST_TRADES
        if (len(state.ab_baseline_results) < min_trades
                or len(state.ab_candidate_results) < min_trades):
            return None

        import numpy as np
        baseline_mean = np.mean(state.ab_baseline_results)
        candidate_mean = np.mean(state.ab_candidate_results)

        winner = "candidate" if candidate_mean > baseline_mean else "baseline"
        result = {
            "baseline_mean_pnl": round(float(baseline_mean), 4),
            "candidate_mean_pnl": round(float(candidate_mean), 4),
            "baseline_trades": len(state.ab_baseline_results),
            "candidate_trades": len(state.ab_candidate_results),
            "winner": winner,
        }

        # Stop de test
        state.ab_active = False
        self._save()

        log.info(
            f"[ADAPTIVE] A/B test klaar {strategy_id}: "
            f"baseline={result['baseline_mean_pnl']:.2f}% vs "
            f"candidate={result['candidate_mean_pnl']:.2f}% → {winner}"
        )
        return result

    # ── Maandelijkse evaluatie ──────────────────

    def monthly_check(self, strategy_id: str) -> dict:
        """
        Maandelijkse parameter-evaluatie.
        Retourneert advies op basis van rolling metrics.
        """
        state = self.get_state(strategy_id)
        now = datetime.now()

        # Check of het al een maand geleden is
        if state.last_monthly_check:
            last = datetime.fromisoformat(state.last_monthly_check)
            if (now - last).days < 30:
                return {"action": "skip", "reason": "Minder dan 30 dagen sinds laatste check"}

        state.last_monthly_check = now.isoformat()

        advice = {
            "strategy_id": strategy_id,
            "win_rate": state.rolling_win_rate,
            "sharpe": state.rolling_sharpe,
            "trades_count": len(state.recent_trades),
            "action": "hold",
            "recommendations": [],
        }

        # Slechte performance → reduce risk budget
        if state.rolling_win_rate < 0.35 and len(state.recent_trades) >= 20:
            advice["action"] = "reduce"
            advice["recommendations"].append(
                "Win-rate onder 35% — overweeg risk budget te verlagen"
            )

        # Slechte Sharpe → waarschuwing
        if state.rolling_sharpe < -0.5 and len(state.recent_trades) >= 20:
            advice["action"] = "reduce"
            advice["recommendations"].append(
                "Negatieve Sharpe ratio — strategie presteert slecht"
            )

        # Goede performance → optioneel verhogen
        if state.rolling_win_rate > 0.65 and state.rolling_sharpe > 1.0:
            advice["action"] = "increase"
            advice["recommendations"].append(
                "Sterke performance — overweeg risk budget te verhogen"
            )

        self._save()
        return advice

    # ── Overzicht ───────────────────────────────

    def get_all_states(self) -> dict:
        """Geef een samenvatting van alle strategie-states."""
        result = {}
        for sid, state in self.states.items():
            result[sid] = {
                "rolling_win_rate": state.rolling_win_rate,
                "rolling_sharpe": state.rolling_sharpe,
                "consecutive_losses": state.consecutive_losses,
                "cooldown_until": state.cooldown_until,
                "in_cooldown": self.is_in_cooldown(sid),
                "risk_budget_pct": state.risk_budget_pct,
                "trades_count": len(state.recent_trades),
                "ab_active": state.ab_active,
                "custom_state": state.custom_state,
            }
        return result
