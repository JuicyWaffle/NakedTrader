"""
nakedtrader.orchestrator_compare — Vergelijk A vs B vs C orchestrators.

Gebruik:
  python -m nakedtrader.orchestrator_compare --period week
  python -m nakedtrader.orchestrator_compare --period month
  python -m nakedtrader.orchestrator_compare --since 2026-01-01
"""

import argparse
import json
import logging
import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

DATA_DIR = Path("data")


def load_executions(orch_id: str, since: datetime = None) -> list[dict]:
    """Laad ExecutionReports voor een orchestrator uit de database."""
    try:
        from nakedtrader.db.repository import DataRepository
        repo = DataRepository()
        logs = repo.get_execution_reports(orchestrator_id=orch_id, limit=10_000)
        entries = []
        for e in logs:
            ts_str = str(e.timestamp) if e.timestamp else ""
            if since and ts_str[:10] < since.isoformat()[:10]:
                continue
            entries.append({
                "timestamp": ts_str,
                "orchestrator_id": e.orchestrator_id,
                "decision": e.decision,
                "reason": e.reason,
                "rule_triggered": e.rule_triggered,
                "risk_score": e.risk_score,
                "drawdown_pct": e.drawdown_pct,
                "size_executed": e.size_executed,
                "signal": {
                    "symbol": e.symbol,
                    "strategy_id": e.strategy_id,
                },
            })
        repo.close()
        # Oudste eerst (DB retourneert nieuwste eerst)
        entries.reverse()
        return entries
    except Exception:
        # Fallback naar JSON
        path = DATA_DIR / f"executions_orch_{orch_id}.json"
        if not path.exists():
            return []
        with open(path) as f:
            entries = json.load(f)
        if since:
            since_str = since.isoformat()[:10]
            entries = [e for e in entries if e.get("timestamp", "")[:10] >= since_str]
        return entries


def analyze(entries: list[dict]) -> dict:
    """Analyseer ExecutionReports en retourneer metrics."""
    if not entries:
        return {
            "count": 0, "executed": 0, "blocked": 0,
            "pnl_eur": 0, "win_rate": 0, "sharpe": 0,
            "top_rules": [],
        }

    executed = [e for e in entries if e["decision"] == "executed"]
    blocked = [e for e in entries if e["decision"] == "blocked"]

    # PnL uit uitgevoerde trades
    pnl_values = []
    wins = 0
    for e in executed:
        sig = e.get("signal", {})
        size = e.get("size_executed", 0)
        # Schat PnL op basis van expected_win/loss (we hebben geen trade record hier)
        # In werkelijkheid wordt dit bijgehouden via PerformanceStore
        win_pct = sig.get("expected_win_pct", 0.10)
        loss_pct = sig.get("expected_loss_pct", 0.05)
        win_prob = sig.get("win_probability", 0.5)
        # Verwachte PnL per trade
        expected_pnl = size * (win_prob * win_pct - (1 - win_prob) * loss_pct)
        pnl_values.append(expected_pnl)
        if expected_pnl > 0:
            wins += 1

    total_pnl = sum(pnl_values)
    win_rate = wins / len(executed) if executed else 0

    # Sharpe (vereenvoudigd)
    if len(pnl_values) >= 2:
        mean = sum(pnl_values) / len(pnl_values)
        var = sum((x - mean) ** 2 for x in pnl_values) / (len(pnl_values) - 1)
        std = math.sqrt(var) if var > 0 else 1
        sharpe = (mean / std) * math.sqrt(252) if std > 0 else 0
    else:
        sharpe = 0

    # Top geblokkeerde regels
    rule_counts = Counter(e["rule_triggered"] for e in blocked if e.get("rule_triggered"))
    top_rules = rule_counts.most_common(5)

    return {
        "count": len(entries),
        "executed": len(executed),
        "blocked": len(blocked),
        "exec_pct": round(len(executed) / len(entries) * 100, 1) if entries else 0,
        "pnl_eur": round(total_pnl, 2),
        "win_rate": round(win_rate, 3),
        "sharpe": round(sharpe, 2),
        "top_rules": top_rules,
    }


def find_divergences(exec_a: list[dict], exec_b: list[dict], exec_c: list[dict]) -> list[dict]:
    """Vind signalen waar A blokkeerde maar B of C uitvoerde (of omgekeerd)."""
    # Index op timestamp + symbol
    def key(e):
        sig = e.get("signal", {})
        return (e.get("timestamp", "")[:16], sig.get("symbol", ""), sig.get("strategy_id", ""))

    a_map = {key(e): e for e in exec_a}
    b_map = {key(e): e for e in exec_b}
    c_map = {key(e): e for e in exec_c}

    all_keys = set(a_map.keys()) | set(b_map.keys()) | set(c_map.keys())

    divergences = []
    for k in sorted(all_keys):
        a = a_map.get(k)
        b = b_map.get(k)
        c = c_map.get(k)

        decisions = {
            "A": a["decision"] if a else "n/a",
            "B": b["decision"] if b else "n/a",
            "C": c["decision"] if c else "n/a",
        }

        unique = set(d for d in decisions.values() if d != "n/a")
        if len(unique) > 1:
            divergences.append({
                "timestamp": k[0],
                "symbol": k[1],
                "strategy": k[2],
                "decisions": decisions,
                "a_reason": (a or {}).get("reason", ""),
                "b_reason": (b or {}).get("reason", ""),
                "c_reason": (c or {}).get("reason", ""),
            })

    return divergences


def compare(since: datetime = None) -> dict:
    """Genereer vergelijkingsrapport."""
    exec_a = load_executions("A", since)
    exec_b = load_executions("B", since)
    exec_c = load_executions("C", since)

    result = {
        "period": since.isoformat()[:10] if since else "all",
        "generated": datetime.now().isoformat(timespec="seconds"),
        "orchestrators": {
            "A": analyze(exec_a),
            "B": analyze(exec_b),
            "C": analyze(exec_c),
        },
        "divergences": find_divergences(exec_a, exec_b, exec_c),
    }

    return result


def print_report(data: dict):
    """Print vergelijkingsrapport naar terminal."""
    print(f"\n{'='*60}")
    print(f"  ORCHESTRATOR VERGELIJKING — periode: {data['period']}")
    print(f"  Gegenereerd: {data['generated']}")
    print(f"{'='*60}\n")

    for oid in ("A", "B", "C"):
        m = data["orchestrators"][oid]
        label = {"A": "Conservatief (live)", "B": "Agressief (shadow)", "C": "Diversificatie (shadow)"}
        print(f"  Orchestrator {oid} — {label[oid]}")
        print(f"  {'─'*45}")
        print(f"  Signalen:     {m['count']:>6}")
        print(f"  Uitgevoerd:   {m['executed']:>6}  ({m.get('exec_pct', 0):.1f}%)")
        print(f"  Geblokkeerd:  {m['blocked']:>6}")
        print(f"  PnL (verwacht): €{m['pnl_eur']:>+10,.2f}")
        print(f"  Win-rate:     {m['win_rate']:>6.1%}")
        print(f"  Sharpe:       {m['sharpe']:>6.2f}")
        if m["top_rules"]:
            print(f"  Top regels:")
            for rule, cnt in m["top_rules"]:
                print(f"    {rule:>5}: {cnt}x")
        print()

    divs = data.get("divergences", [])
    if divs:
        print(f"  DIVERGENTIES ({len(divs)} gevonden)")
        print(f"  {'─'*45}")
        for d in divs[:10]:
            a_d = d["decisions"]["A"]
            b_d = d["decisions"]["B"]
            c_d = d["decisions"]["C"]
            print(f"  {d['timestamp']} {d['symbol']:>10} {d['strategy']:>15}")
            print(f"    A={a_d:<10} B={b_d:<10} C={c_d:<10}")
        if len(divs) > 10:
            print(f"  ... en {len(divs) - 10} meer")
    else:
        print("  Geen divergenties gevonden.")

    print()


def main():
    parser = argparse.ArgumentParser(description="Vergelijk orchestrator A vs B vs C")
    parser.add_argument("--period", choices=["day", "week", "month"], help="Tijdsperiode")
    parser.add_argument("--since", help="Startdatum (YYYY-MM-DD)")
    parser.add_argument("--json", action="store_true", help="Output als JSON")
    args = parser.parse_args()

    since = None
    if args.since:
        since = datetime.strptime(args.since, "%Y-%m-%d")
    elif args.period:
        now = datetime.now()
        if args.period == "day":
            since = now - timedelta(days=1)
        elif args.period == "week":
            since = now - timedelta(weeks=1)
        elif args.period == "month":
            since = now - timedelta(days=30)

    data = compare(since)

    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print_report(data)


if __name__ == "__main__":
    main()
