#!/usr/bin/env python3
"""NakedTrader — Macro Risk Service CLI.

Gebruik:
    python run_risk.py              # eenmalige evaluatie
    python run_risk.py --watch      # elke 60 minuten
    python run_risk.py --json       # output als JSON
    python run_risk.py --emergency  # noodrem direct
    python run_risk.py --manifest   # toon module manifest
"""

import argparse
import json
import logging
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

from nakedtrader.risk.macro import MacroRiskEngine, MANIFEST


def main():
    parser = argparse.ArgumentParser(
        description="MacroRiskEngine — geopolitieke risicobewaking voor NakedTrader.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--watch", action="store_true", help="Herhaal elke N minuten")
    parser.add_argument("--interval", type=int, default=60, help="Interval in minuten (default: 60)")
    parser.add_argument("--json", action="store_true", help="Output als JSON")
    parser.add_argument("--emergency", action="store_true", help="Activeer noodrem direct")
    parser.add_argument("--manifest", action="store_true", help="Toon module manifest")
    args = parser.parse_args()

    if args.manifest:
        print(json.dumps(MANIFEST, indent=2, ensure_ascii=False))
        return

    engine = MacroRiskEngine()

    if args.emergency:
        report = engine.emergency_halt()
        print(report.to_json() if args.json else report.summary())
        return

    def run_once():
        report = engine.evaluate()
        print(report.to_json() if args.json else report.summary())
        return report

    if args.watch:
        while True:
            run_once()
            time.sleep(args.interval * 60)
    else:
        run_once()


if __name__ == "__main__":
    main()
