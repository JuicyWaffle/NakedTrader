# Trading bot — setup & gebruik

## Bestanden

| Bestand | Rol |
|---|---|
| `bot.py` | Hoofdbot — signalen, Kelly, orders |
| `paper_engine.py` | Simulatie-engine + PerformanceTracker |
| `dashboard.py` | Rendementstrend visualisatie |
| `trades.json` | Automatisch aangemaakt — logboek van alle trades |
| `requirements.txt` | Python dependencies |

## Snelstart (paper mode — geen broker nodig)

```bash
pip install matplotlib pandas
python bot.py
```

De bot start in paper mode, genereert demo-signalen en simuleert trades.
Elk resultaat wordt opgeslagen in `trades.json`.

## Dashboard bekijken

```bash
python dashboard.py           # opent matplotlib grafiek + ASCII trend
python dashboard.py --ascii   # alleen ASCII (geen matplotlib)
```

## Configuratie (in bot.py)

```python
config = Config(
    paper_mode=True,          # False voor live trading
    total_capital=10_000.0,   # startkapitaal in EUR
    kelly_fraction=0.5,       # 0.5 = half-Kelly (aanbevolen)
    stop_loss_pct=0.05,       # 5% stop-loss
    take_profit_pct=0.15,     # 15% take-profit
    ibkr_port=7497,           # 7497=paper, 7496=live
    binance_testnet=True,
)
```

## Overschakelen naar live

1. Stel `paper_mode=False` in
2. Zet `ibkr_port=7496` (live IBKR)
3. Zet `binance_testnet=False`
4. Vul je echte API keys in (gebruik omgevingsvariabelen!)
5. Start IBKR TWS of IB Gateway

## Je eigen strategie koppelen

Vervang `demo_signals()` in `bot.py` door een functie die echte signalen
geeft op basis van jouw logica (technische indicatoren, ML-model, etc.):

```python
def mijn_strategie() -> list[TradeSignal]:
    # jouw code hier
    return [TradeSignal(...)]

bot.run_loop(interval_seconds=60, signals_fn=mijn_strategie)
```

## Rendementstracking

De bot houdt automatisch twee gescheiden trends bij:

- **paper** — gesimuleerde trades (fictief rendement)
- **live** — echte trades (reëel rendement)

Het verschil tussen beide is een indicator van hoe goed je strategie
in werkelijkheid presteert ten opzichte van de simulatie.

## Beveiliging

- Bewaar API keys nooit in code — gebruik omgevingsvariabelen:
  ```bash
  export BINANCE_API_KEY="..."
  export BINANCE_SECRET="..."
  ```
- Begin altijd met paper mode en kleine bedragen
- Raadpleeg een financieel adviseur voor live trading
