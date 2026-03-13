"""
dashboard.py — Rendementstrend visualisatie

Toont twee grafieken naast elkaar:
  - Fictief (paper) rendement over de tijd
  - Reëel (live) rendement over de tijd

Gebruik:
    python dashboard.py                    # leest trades.json
    python dashboard.py --log mijn.json    # aangepast logbestand

Vereist: pip install matplotlib pandas
"""

import json
import argparse
from pathlib import Path
from datetime import datetime


def load_snapshots(log_path: str) -> tuple[list, list]:
    """Laad paper- en live-snapshots uit het logboek."""
    path = Path(log_path)
    if not path.exists():
        print(f"Logboek niet gevonden: {log_path}")
        return [], []

    with open(path) as f:
        data = json.load(f)

    snapshots = data.get("snapshots", [])
    paper = [s for s in snapshots if s["mode"] == "paper"]
    live = [s for s in snapshots if s["mode"] == "live"]
    return paper, live


def print_ascii_chart(snapshots: list, mode: str, width: int = 50):
    """
    Eenvoudige ASCII-trendlijn voor gebruik zonder matplotlib.
    Toont cumulatief rendement % over de trades heen.
    """
    if not snapshots:
        print(f"  [{mode.upper()}]  Nog geen data\n")
        return

    values = [s["cumulative_pnl_pct"] for s in snapshots]
    min_v, max_v = min(values), max(values)
    spread = max_v - min_v or 1
    height = 10

    rows = []
    for row in range(height, -1, -1):
        threshold = min_v + (row / height) * spread
        label = f"{threshold:+6.1f}% "
        line = ""
        for v in values:
            normalized = (v - min_v) / spread * height
            if abs(normalized - row) < 0.5:
                line += "●"
            elif row == 0:
                line += "─"
            else:
                line += " "
        rows.append(label + line)

    final = values[-1]
    sign = "+" if final >= 0 else ""
    print(f"\n  [{mode.upper()}]  Cumulatief rendement: {sign}{final:.2f}%  ({len(snapshots)} trades)")
    print("  " + "─" * (width + 9))
    for r in rows:
        print("  " + r)
    print("  " + " " * 9 + "─" * min(len(values), width))
    print()


def plot_matplotlib(paper: list, live: list, log_path: str):
    """Toon grafieken via matplotlib indien beschikbaar."""
    try:
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
    except ImportError:
        print("matplotlib niet gevonden. Installeer met: pip install matplotlib")
        return

    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    fig.suptitle("Trading Bot — Rendementstrend", fontsize=14, fontweight="bold")

    datasets = [
        (paper, "paper", axes[0], "#2196F3", "#E3F2FD"),
        (live, "live", axes[1], "#4CAF50", "#E8F5E9"),
    ]

    for snapshots, mode, ax, color, bg in datasets:
        ax.set_facecolor(bg)
        ax.set_title(f"{'Fictief (paper)' if mode == 'paper' else 'Reëel (live)'}", fontsize=12)
        ax.set_xlabel("Trade #")
        ax.set_ylabel("Cumulatief rendement (%)")
        ax.axhline(0, color="#999", linewidth=0.8, linestyle="--")

        if not snapshots:
            ax.text(0.5, 0.5, "Nog geen data", ha="center", va="center",
                    transform=ax.transAxes, color="#999", fontsize=11)
            continue

        x = list(range(1, len(snapshots) + 1))
        y = [s["cumulative_pnl_pct"] for s in snapshots]
        capital = [s["capital"] for s in snapshots]

        # Kleur positief groen, negatief rood
        for i in range(1, len(x)):
            seg_color = color if y[i] >= y[i - 1] else "#F44336"
            ax.plot(x[i - 1:i + 1], y[i - 1:i + 1], color=seg_color, linewidth=2)

        ax.fill_between(x, y, alpha=0.15, color=color)

        # Annotatie laatste punt
        last_sign = "+" if y[-1] >= 0 else ""
        ax.annotate(
            f"{last_sign}{y[-1]:.2f}%\n€{capital[-1]:,.0f}",
            xy=(x[-1], y[-1]),
            xytext=(8, 0),
            textcoords="offset points",
            fontsize=9,
            color=color,
            fontweight="bold",
        )

        # Win/loss markers
        trades_data = [s for s in snapshots]
        for i, s in enumerate(trades_data):
            trade_pnl = (
                snapshots[i]["cumulative_pnl_eur"] - (snapshots[i - 1]["cumulative_pnl_eur"] if i > 0 else 0)
            )
            marker = "^" if trade_pnl >= 0 else "v"
            mcolor = "#1B5E20" if trade_pnl >= 0 else "#B71C1C"
            ax.scatter(x[i], y[i], marker=marker, color=mcolor, s=40, zorder=5)

        ax.set_xlim(0, max(x) + 1)
        ax.grid(True, alpha=0.3, linestyle=":")

        # Statistieken rechtsonder
        wins = sum(1 for j in range(1, len(snapshots))
                   if snapshots[j]["cumulative_pnl_eur"] > snapshots[j - 1]["cumulative_pnl_eur"])
        total = len(snapshots)
        win_rate = wins / total * 100 if total > 0 else 0
        stats_text = f"Trades: {total}  |  Winratio: {win_rate:.0f}%"
        ax.text(0.02, 0.04, stats_text, transform=ax.transAxes,
                fontsize=8, color="#555", va="bottom")

    plt.tight_layout()
    out = Path(log_path).parent / "dashboard.png"
    plt.savefig(out, dpi=150, bbox_inches="tight")
    print(f"Grafiek opgeslagen: {out}")
    plt.show()


def main():
    parser = argparse.ArgumentParser(description="Trading bot dashboard")
    parser.add_argument("--log", default="trades.json", help="Pad naar trades.json")
    parser.add_argument("--ascii", action="store_true", help="Forceer ASCII output (geen matplotlib)")
    args = parser.parse_args()

    paper, live = load_snapshots(args.log)

    print("\n" + "═" * 60)
    print("  TRADING BOT — RENDEMENT DASHBOARD")
    print(f"  Logboek: {args.log}")
    print("═" * 60)

    # Altijd ASCII tonen als snelle samenvatting
    print_ascii_chart(paper, "paper")
    print_ascii_chart(live, "live")

    # Vergelijking paper vs live (als beide data hebben)
    if paper and live:
        paper_final = paper[-1]["cumulative_pnl_pct"]
        live_final = live[-1]["cumulative_pnl_pct"]
        diff = live_final - paper_final
        print(f"  Vergelijking: paper={paper_final:+.2f}%  live={live_final:+.2f}%  verschil={diff:+.2f}%")
        if abs(diff) > 5:
            print("  ⚠  Verschil > 5% — controleer of je strategie live afwijkt van paper")
        print()

    # Matplotlib grafiek tonen indien beschikbaar
    if not args.ascii:
        plot_matplotlib(paper, live, args.log)


if __name__ == "__main__":
    main()
