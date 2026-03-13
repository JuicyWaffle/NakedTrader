"""
bot.py — Multi-broker algoritmische trading bot
Brokers : Interactive Brokers (aandelen/ETF/forex/futures)
          Kraken (crypto — standaard)
          Binance (crypto — optioneel alternatief)
Strategie: Kelly Criterion positiebepaling (half-Kelly standaard)
Modus   : paper_mode=True by default — schakel over op False voor live trading

Architectuur:
  Config              — alle instellingen
  KellyPositionSizer  — optimale positiegrootte berekenen
  IBKRBroker          — IBKR bracket orders
  KrakenBroker        — Kraken spot orders + stop-loss/take-profit
  BinanceBroker       — Binance market + OCO orders (alternatief)
  PaperEngine         — simuleer trades + bijhouden PnL
  PerformanceTracker  — logboek (trades.json) + snapshots
  TradingBot          — orkestreert alles
"""

import os
import time
import logging
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from datetime import datetime

import yaml
from dotenv import load_dotenv

from paper_engine import PaperEngine, PerformanceTracker
from adaptive import AdaptiveEngine
from risk_manager import RiskManager, RiskLimits

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# 1. CONFIGURATIE
# ─────────────────────────────────────────────

@dataclass
class Config:
    # ── Modus ──────────────────────────────────
    paper_mode: bool = True          # True = simuleer, False = echte orders

    # ── Kapitaal ───────────────────────────────
    total_capital: float = 10_000.0  # totaal beschikbaar kapitaal in EUR

    # ── Kelly instellingen ─────────────────────
    kelly_fraction: float = 0.5      # 0.5 = half-Kelly (aanbevolen)
    max_position_pct: float = 0.20   # max 20% per trade

    # ── Risicobeheer ───────────────────────────
    stop_loss_pct: float = 0.05      # stop-loss op 5% verlies
    take_profit_pct: float = 0.10    # take-profit op 10% winst
    max_open_positions: int = 5      # max gelijktijdige posities
    drawdown_limit_pct: float = 0.15 # halt bij 15% drawdown van peak

    # ── Logboek ────────────────────────────────
    trade_log_path: str = "trades.json"

    # ── Adaptive state ────────────────────────
    state_path: str = "strategy_state.json"

    # ── IBKR ───────────────────────────────────
    ibkr_host: str = "127.0.0.1"
    ibkr_port: int = 7497            # 7497 = paper, 7496 = live
    ibkr_client_id: int = 1

    # ── Crypto broker keuze ────────────────────
    crypto_broker: str = "kraken"    # "kraken" (standaard) of "binance"

    # ── Kraken ─────────────────────────────────
    kraken_api_key: str = "JOUW_KRAKEN_API_KEY"
    kraken_api_secret: str = "JOUW_KRAKEN_SECRET"

    # ── Binance (alternatief) ───────────────────
    binance_api_key: str = "JOUW_BINANCE_API_KEY"
    binance_api_secret: str = "JOUW_BINANCE_SECRET"
    binance_testnet: bool = True     # altijd True zolang paper_mode=True


# ─────────────────────────────────────────────
# 2. KELLY CRITERION POSITIEBEPALING
# ─────────────────────────────────────────────

class KellyPositionSizer:
    """
    Berekent de optimale positiegrootte via Kelly Criterion.

    f* = (p / a) - ((1-p) / b)
      p = winkans
      b = gemiddeld winstpercentage bij een win
      a = gemiddeld verliespercentage bij een loss
    """

    def __init__(self, config: Config):
        self.config = config

    def calculate(
        self,
        win_probability: float,
        avg_win_pct: float,
        avg_loss_pct: float,
    ) -> float:
        """Geeft de positiegrootte als fractie van het kapitaal (0–max_position_pct)."""
        if avg_loss_pct <= 0 or avg_win_pct <= 0:
            return 0.0

        loss_prob = 1 - win_probability
        kelly_full = (win_probability / avg_loss_pct) - (loss_prob / avg_win_pct)
        kelly_adjusted = kelly_full * self.config.kelly_fraction
        fraction = max(0.0, min(kelly_adjusted, self.config.max_position_pct))

        log.debug(
            f"Kelly: volledig={kelly_full:.3f}  "
            f"gecorrigeerd={kelly_adjusted:.3f}  "
            f"toegepast={fraction:.3f}"
        )
        return fraction

    def position_size_eur(
        self,
        win_probability: float,
        avg_win_pct: float,
        avg_loss_pct: float,
    ) -> float:
        fraction = self.calculate(win_probability, avg_win_pct, avg_loss_pct)
        return self.config.total_capital * fraction


# ─────────────────────────────────────────────
# 3. INTERACTIVE BROKERS CONNECTOR
# ─────────────────────────────────────────────

class IBKRBroker:
    """
    Verbinding met Interactive Brokers via ib_async.
    pip install ib_async
    TWS of IB Gateway moet draaien op de achtergrond.
    """

    def __init__(self, config: Config):
        self.config = config
        self.ib = None

    def connect(self):
        from ib_async import IB
        self.ib = IB()
        self.ib.connect(
            self.config.ibkr_host,
            self.config.ibkr_port,
            clientId=self.config.ibkr_client_id,
        )
        log.info(f"IBKR verbonden  {self.config.ibkr_host}:{self.config.ibkr_port}")

    def disconnect(self):
        if self.ib and self.ib.isConnected():
            self.ib.disconnect()
            log.info("IBKR verbinding gesloten")

    def get_price(
        self,
        symbol: str,
        exchange: str = "SMART",
        currency: str = "USD",
    ) -> Optional[float]:
        from ib_async import Stock
        contract = Stock(symbol, exchange, currency)
        self.ib.qualifyContracts(contract)
        ticker = self.ib.reqMktData(contract)
        self.ib.sleep(1)
        return ticker.last or ticker.close

    def place_bracket_order(
        self,
        symbol: str,
        quantity: int,
        entry_price: float,
        stop_loss_pct: float,
        take_profit_pct: float,
        exchange: str = "SMART",
        currency: str = "USD",
    ):
        """Bracket order: entry + stop-loss + take-profit in één keer."""
        from ib_async import Stock
        contract = Stock(symbol, exchange, currency)
        self.ib.qualifyContracts(contract)

        stop_price = round(entry_price * (1 - stop_loss_pct), 2)
        profit_price = round(entry_price * (1 + take_profit_pct), 2)

        bracket = self.ib.bracketOrder(
            action="BUY",
            quantity=quantity,
            limitPrice=entry_price,
            takeProfitPrice=profit_price,
            stopLossPrice=stop_price,
        )
        for order in bracket:
            self.ib.placeOrder(contract, order)

        log.info(
            f"IBKR bracket: {symbol} qty={quantity}  "
            f"entry={entry_price}  SL={stop_price}  TP={profit_price}"
        )
        return bracket

    def get_portfolio(self) -> list:
        return self.ib.portfolio()

    def get_stock_bars(
        self,
        symbol: str,
        duration: str = "1 Y",
        bar_size: str = "1 day",
        exchange: str = "SMART",
        currency: str = "USD",
    ) -> list:
        """
        Haal historische bars op voor aandelen/ETFs (Stock contracts).
        Gebruikt voor SPY, TLT, GLD etc. — regime detectie.
        """
        from ib_async import Stock
        contract = Stock(symbol, exchange, currency)
        self.ib.qualifyContracts(contract)
        bars = self.ib.reqHistoricalData(
            contract,
            endDateTime="",
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow="TRADES",
            useRTH=True,
            formatDate=1,
        )
        log.info(f"IBKR stock bars: {symbol}  {len(bars)} bars ({bar_size})")
        return bars

    def get_historical_bars(
        self,
        symbol: str,
        duration: str = "60 D",
        bar_size: str = "1 day",
        what_to_show: str = "MIDPOINT",
        exchange: str = "IDEALPRO",
        currency: str = "USD",
    ) -> list:
        """
        Haal historische bars op via IB Gateway.

        Args:
            symbol: bv. "EUR" (voor EURUSD forex)
            duration: bv. "60 D", "1 Y"
            bar_size: bv. "1 day", "1 hour", "15 mins"
            what_to_show: "MIDPOINT", "TRADES", "BID", "ASK"
            exchange: "IDEALPRO" voor forex, "SMART" voor aandelen
            currency: tegenvaluta

        Returns:
            list van BarData objecten met .date, .open, .high, .low, .close, .volume
        """
        from ib_async import Forex
        contract = Forex(symbol + currency)
        self.ib.qualifyContracts(contract)
        bars = self.ib.reqHistoricalData(
            contract,
            endDateTime="",
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow=what_to_show,
            useRTH=True,
            formatDate=1,
        )
        log.info(f"IBKR historisch: {symbol}{currency}  {len(bars)} bars ({bar_size})")
        return bars


# ─────────────────────────────────────────────
# 4. BINANCE CONNECTOR
# ─────────────────────────────────────────────

class BinanceBroker:
    """
    Verbinding met Binance via python-binance.
    pip install python-binance
    """

    def __init__(self, config: Config):
        self.config = config
        self.client = None

    def connect(self):
        from binance.client import Client
        self.client = Client(
            self.config.binance_api_key,
            self.config.binance_api_secret,
            testnet=self.config.binance_testnet,
        )
        log.info(f"Binance verbonden  testnet={self.config.binance_testnet}")

    def get_price(self, symbol: str) -> float:
        ticker = self.client.get_symbol_ticker(symbol=symbol)
        return float(ticker["price"])

    def place_market_order(self, symbol: str, side: str, quantity: float):
        from binance.enums import ORDER_TYPE_MARKET
        return self.client.create_order(
            symbol=symbol,
            side=side,
            type=ORDER_TYPE_MARKET,
            quantity=quantity,
        )

    def place_oco_order(
        self,
        symbol: str,
        quantity: float,
        stop_price: float,
        take_profit_price: float,
    ):
        """OCO: stop-loss + take-profit, één annuleert de andere."""
        return self.client.create_oco_order(
            symbol=symbol,
            side="SELL",
            quantity=quantity,
            price=str(take_profit_price),
            stopPrice=str(stop_price),
            stopLimitPrice=str(round(stop_price * 0.99, 2)),
            stopLimitTimeInForce="GTC",
        )

    def get_balances(self) -> dict:
        account = self.client.get_account()
        return {
            b["asset"]: float(b["free"])
            for b in account["balances"]
            if float(b["free"]) > 0
        }




# ─────────────────────────────────────────────
# 5. KRAKEN CONNECTOR (standaard crypto broker)
# ─────────────────────────────────────────────

class KrakenBroker:
    """
    Verbinding met Kraken via krakenex (direct, zonder pykrakenapi).

    Kraken gebruikt andere symboolnotatie dan Binance:
      Bitcoin  → XXBTZEUR  (BTC/EUR)  of  XBTUSDT
      Ethereum → XETHZEUR  (ETH/EUR)  of  ETHUSD
    Gebruik altijd de volledige Kraken paar-naam.
    """

    def __init__(self, config):
        self.config = config
        self.api = None

    def _check_errors(self, response, context="Kraken"):
        """Controleer op API fouten en gooi RuntimeError als nodig."""
        errors = response.get("error", [])
        if errors:
            raise RuntimeError(f"{context}: {errors}")

    def connect(self):
        import krakenex
        self.api = krakenex.API(
            key=self.config.kraken_api_key,
            secret=self.config.kraken_api_secret,
        )
        # Verbinding testen via accountbalans
        response = self.api.query_private("Balance")
        self._check_errors(response, "Kraken connect")
        assets = [a for a, v in response["result"].items() if float(v) > 0]
        log.info(f"Kraken verbonden. Assets met saldo: {assets}")

    def get_price(self, pair: str) -> float:
        """
        Haal de huidige biedprijs op.
        pair: bv. "XXBTZEUR", "XETHZEUR", "XBTUSDT"
        """
        response = self.api.query_public("Ticker", {"pair": pair})
        self._check_errors(response, f"Kraken prijs {pair}")
        # Eerste (en enige) key in result bevat de ticker data
        ticker_key = list(response["result"].keys())[0]
        price = float(response["result"][ticker_key]["b"][0])  # beste biedprijs
        log.info(f"Kraken prijs {pair}: {price}")
        return price

    def place_market_order(
        self,
        pair: str,
        side: str,          # "buy" of "sell"
        volume: float,
    ) -> dict:
        """Plaats een marktorder op Kraken."""
        response = self.api.query_private("AddOrder", {
            "pair": pair,
            "type": side,
            "ordertype": "market",
            "volume": str(round(volume, 8)),
        })
        self._check_errors(response, "Kraken market order")
        tx_ids = response["result"].get("txid", [])
        log.info(f"Kraken market order: {side} {volume} {pair}  txid={tx_ids}")
        return response["result"]

    def place_stop_loss_take_profit(
        self,
        pair: str,
        volume: float,
        stop_price: float,
        take_profit_price: float,
    ):
        """
        Plaats twee aparte orders na een koop:
          1. Stop-loss (stop-market order)
          2. Take-profit (limit sell order)

        Kraken heeft geen native OCO — beide orders staan open
        en je annuleert de andere zodra één geraakt wordt.
        """
        # Stop-loss
        sl_response = self.api.query_private("AddOrder", {
            "pair": pair,
            "type": "sell",
            "ordertype": "stop-loss",
            "price": str(round(stop_price, 2)),
            "volume": str(round(volume, 8)),
        })
        self._check_errors(sl_response, "Kraken SL")

        # Take-profit
        tp_response = self.api.query_private("AddOrder", {
            "pair": pair,
            "type": "sell",
            "ordertype": "take-profit",
            "price": str(round(take_profit_price, 2)),
            "volume": str(round(volume, 8)),
        })
        self._check_errors(tp_response, "Kraken TP")

        sl_id = sl_response["result"].get("txid", [])
        tp_id = tp_response["result"].get("txid", [])
        log.info(f"Kraken SL+TP: {pair}  SL={stop_price} (txid={sl_id})  TP={take_profit_price} (txid={tp_id})")
        return {"stop_loss": sl_response["result"], "take_profit": tp_response["result"]}

    def get_open_orders(self) -> dict:
        """Geeft alle open orders terug."""
        response = self.api.query_private("OpenOrders")
        self._check_errors(response, "Kraken open orders")
        return response.get("result", {}).get("open", {})

    def cancel_order(self, txid: str):
        """Annuleer een order op basis van zijn transactie-ID."""
        response = self.api.query_private("CancelOrder", {"txid": txid})
        self._check_errors(response, "Kraken cancel")
        log.info(f"Kraken order geannuleerd: {txid}")
        return response

    def get_ohlc(self, pair: str, interval: int = 60, since: int = None) -> list:
        """
        Haal OHLC (candlestick) data op via de publieke Kraken API.

        Args:
            pair: bv. "XXBTZEUR", "XETHZEUR"
            interval: interval in minuten (1, 5, 15, 30, 60, 240, 1440, 10080, 21600)
            since: Unix timestamp — geeft candles na dit tijdstip

        Returns:
            list van [time, open, high, low, close, vwap, volume, count]
        """
        import krakenex
        api = self.api if self.api else krakenex.API()
        params = {"pair": pair, "interval": interval}
        if since is not None:
            params["since"] = since
        response = api.query_public("OHLC", params)
        self._check_errors(response, f"Kraken OHLC {pair}")
        # Resultaat bevat pair-key + "last" — we willen de pair-key
        result = response["result"]
        ohlc_key = [k for k in result if k != "last"][0]
        return result[ohlc_key]

    def get_orderbook(self, pair: str, count: int = 25) -> dict:
        """
        Haal het orderboek op via de publieke Kraken Depth API.

        Args:
            pair: bv. "XXBTZEUR", "XETHZEUR"
            count: aantal levels (max 500)

        Returns:
            dict met "bids" en "asks", elk een list van [prijs, volume, timestamp]
        """
        import krakenex
        api = self.api if self.api else krakenex.API()
        response = api.query_public("Depth", {"pair": pair, "count": count})
        self._check_errors(response, f"Kraken orderbook {pair}")
        result = response["result"]
        book_key = [k for k in result][0]
        return {
            "bids": result[book_key].get("bids", []),
            "asks": result[book_key].get("asks", []),
        }

    def get_balances(self) -> dict:
        """Geeft alle saldi terug als dict {asset: balance}."""
        response = self.api.query_private("Balance")
        self._check_errors(response, "Kraken balance")
        return {asset: float(vol) for asset, vol in response["result"].items() if float(vol) > 0}

# ─────────────────────────────────────────────
# 6. HANDELSSIGNAAL
# ─────────────────────────────────────────────

@dataclass
class TradeSignal:
    """
    Één handelssignaal vanuit je strategie.
    De win_probability en verwacht rendement komen idealiter
    uit backtests of een ML-model.
    """
    symbol: str
    broker: str              # "ibkr" | "kraken" | "binance"
    direction: str           # "long" | "short"
    win_probability: float   # bv. 0.58
    expected_win_pct: float  # bv. 0.12  (12% gemiddelde winst)
    expected_loss_pct: float # bv. 0.06  (6% gemiddeld verlies)
    current_price: float
    strategy_id: str = ""    # ID van de strategie die dit signaal genereerde
    notes: str = ""


def demo_signals(n: int = 3) -> list[TradeSignal]:
    """
    Genereer willekeurige demo-signalen voor het testen
    van de paper engine. Vervang dit door je echte strategie.
    """
    universe = [
        # IBKR — aandelen, ETF, forex
        ("AAPL",      "ibkr",   180.0,   0.60, 0.12, 0.05),
        ("MSFT",      "ibkr",   415.0,   0.58, 0.10, 0.05),
        ("NVDA",      "ibkr",   875.0,   0.55, 0.15, 0.07),
        ("SPY",       "ibkr",   520.0,   0.62, 0.08, 0.04),
        ("EURUSD",    "ibkr",   1.085,   0.52, 0.01, 0.008),
        # Kraken — crypto (Kraken paar-notatie: XXBTZEUR, XETHZEUR)
        ("XXBTZEUR",  "kraken", 62000.0, 0.54, 0.18, 0.08),
        ("XETHZEUR",  "kraken", 3200.0,  0.56, 0.14, 0.07),
        ("SOLUSD",    "kraken", 145.0,   0.53, 0.20, 0.09),
    ]
    chosen = random.sample(universe, min(n, len(universe)))
    return [
        TradeSignal(
            symbol=sym,
            broker=broker,
            direction="long",
            win_probability=wp,
            expected_win_pct=ew,
            expected_loss_pct=el,
            current_price=price + random.uniform(-price * 0.01, price * 0.01),
            notes=f"Demo signaal {sym}",
        )
        for sym, broker, price, wp, ew, el in chosen
    ]


# ─────────────────────────────────────────────
# 7. TRADING BOT
# ─────────────────────────────────────────────

class TradingBot:
    """
    Orkestreert signalen → positiebepaling → orders → logging.

    In paper_mode=True:
      - Geen echte broker-verbindingen nodig
      - Uitkomsten worden gesimuleerd op basis van winkansen
      - Volledige PnL-tracking via PerformanceTracker

    In paper_mode=False:
      - Verbindt met IBKR + Kraken (of Binance als alternatief)
      - Echte orders met bracket/SL-TP bescherming
      - Zelfde tracking, nu met echte resultaten
    """

    def __init__(self, config: Config):
        self.config = config
        self.sizer = KellyPositionSizer(config)
        self.tracker = PerformanceTracker(config.trade_log_path)
        self.paper = PaperEngine(config, self.tracker)

        # Adaptive + Risk
        self.adaptive = AdaptiveEngine(config.state_path)
        risk_limits = RiskLimits(
            drawdown_limit_pct=config.drawdown_limit_pct,
            default_sl_pct=config.stop_loss_pct,
            default_tp_pct=config.take_profit_pct,
            max_position_pct=config.max_position_pct,
        )
        self.risk_mgr = RiskManager(risk_limits, config.total_capital)

        self.ibkr: Optional[IBKRBroker] = None
        self.kraken: Optional[KrakenBroker] = None
        self.binance: Optional[BinanceBroker] = None
        self.open_positions: list[str] = []

    def connect_brokers(self):
        """Verbind met echte brokers (alleen nodig in live mode)."""
        if self.config.paper_mode:
            log.info("Paper mode actief — geen broker-verbinding nodig")
            return
        log.info("Live mode: verbinding maken met brokers...")
        self.ibkr = IBKRBroker(self.config)
        self.ibkr.connect()
        if self.config.crypto_broker == "kraken":
            self.kraken = KrakenBroker(self.config)
            self.kraken.connect()
        else:
            self.binance = BinanceBroker(self.config)
            self.binance.connect()

    def disconnect_brokers(self):
        if self.ibkr:
            self.ibkr.disconnect()
        log.info("Alle verbindingen gesloten")

    def process_signal(self, signal: TradeSignal):
        """
        Verwerk één signaal via de nieuwe signal flow:
        1. RiskManager: halted? (drawdown circuit-breaker)
        2. AdaptiveEngine: cooldown? (verliesreeks check)
        3. Kelly(adaptive win_rate) — rolling win-rate i.p.v. hardcoded
        4. RiskManager: allow_trade? (per-strategie budget)
        5. Execute met per-strategie SL/TP
        6. Log → AdaptiveEngine.record_trade → RiskManager.update_capital
        """
        sid = signal.strategy_id or ""

        if len(self.open_positions) >= self.config.max_open_positions:
            log.warning(f"Max posities bereikt, {signal.symbol} overgeslagen")
            return

        # 1. Drawdown circuit-breaker
        if self.risk_mgr.halted:
            log.warning(f"[RISK] Circuit-breaker actief — {signal.symbol} overgeslagen")
            return

        # 2. Cooldown check
        if sid and self.adaptive.is_in_cooldown(sid):
            log.info(f"[ADAPTIVE] {sid} in cooldown — {signal.symbol} overgeslagen")
            return

        # 3. Kelly met adaptive win-rate
        win_prob = signal.win_probability
        if sid:
            adaptive_wr = self.adaptive.get_rolling_win_rate(sid)
            state = self.adaptive.get_state(sid)
            # Gebruik adaptive win-rate als er genoeg trades zijn
            if len(state.recent_trades) >= 10:
                win_prob = adaptive_wr

        kelly_frac = self.sizer.calculate(
            win_prob,
            signal.expected_win_pct,
            signal.expected_loss_pct,
        )
        size_eur = self.config.total_capital * kelly_frac

        if size_eur < 10:
            log.debug(f"Positie te klein (€{size_eur:.2f}), {signal.symbol} overgeslagen")
            return

        # 4. Per-strategie budget check
        if sid and not self.risk_mgr.allow_trade(sid, size_eur):
            return

        # 5. Per-strategie SL/TP
        risk_params = self.risk_mgr.get_risk_params(sid) if sid else {
            "sl": self.config.stop_loss_pct,
            "tp": self.config.take_profit_pct,
        }
        sl_pct = risk_params["sl"]
        tp_pct = risk_params["tp"]

        # Alloceer budget
        if sid:
            self.risk_mgr.allocate(sid, size_eur)

        if self.config.paper_mode:
            # ── PAPER: simuleer en log ─────────────
            trade = self.paper.process_signal(signal, kelly_frac, size_eur)

        else:
            # ── LIVE: echte orders ─────────────────
            if signal.broker == "ibkr" and self.ibkr:
                quantity = int(size_eur / signal.current_price)
                if quantity >= 1:
                    self.ibkr.place_bracket_order(
                        symbol=signal.symbol,
                        quantity=quantity,
                        entry_price=signal.current_price,
                        stop_loss_pct=sl_pct,
                        take_profit_pct=tp_pct,
                    )

            elif signal.broker == "kraken" and self.kraken:
                qty = round(size_eur / signal.current_price, 8)
                self.kraken.place_market_order(signal.symbol, "buy", qty)
                stop = round(signal.current_price * (1 - sl_pct), 2)
                tp = round(signal.current_price * (1 + tp_pct), 2)
                self.kraken.place_stop_loss_take_profit(signal.symbol, qty, stop, tp)

            elif signal.broker == "binance" and self.binance:
                qty = round(size_eur / signal.current_price, 5)
                self.binance.place_market_order(signal.symbol, "BUY", qty)
                stop = round(signal.current_price * (1 - sl_pct), 2)
                tp = round(signal.current_price * (1 + tp_pct), 2)
                self.binance.place_oco_order(signal.symbol, qty, stop, tp)

            trade = self.paper.process_signal(signal, kelly_frac, size_eur)

        # 6. Log naar AdaptiveEngine + RiskManager
        if sid and trade:
            self.adaptive.record_trade(sid, trade.pnl_pct, trade.outcome)
            self.risk_mgr.update_capital(trade.pnl_eur, sid)

        self.open_positions.append(signal.symbol)

    def run_once(self, signals: Optional[list[TradeSignal]] = None):
        """Één iteratie van de bot."""
        mode_label = "PAPER" if self.config.paper_mode else "LIVE"
        log.info(f"─── [{mode_label}] Run {datetime.now().strftime('%H:%M:%S')} ───")

        if signals is None:
            signals = demo_signals(n=2)

        for signal in signals:
            self.process_signal(signal)

        self.tracker.print_summary(self.config.total_capital)

    def run_loop(
        self,
        interval_seconds: int = 60,
        signals_fn=None,
    ):
        """
        Draai de bot in een loop.

        Args:
            interval_seconds : wachttijd in seconden tussen runs
            signals_fn       : optionele functie() -> list[TradeSignal]
                               Als None, worden demo_signals() gebruikt
        """
        mode = "PAPER" if self.config.paper_mode else "LIVE"
        log.info(f"Bot gestart  modus={mode}  interval={interval_seconds}s")
        log.info("Druk Ctrl+C om te stoppen  |  dashboard: python dashboard.py")

        while True:
            try:
                signals = signals_fn() if signals_fn else None
                self.run_once(signals)
                time.sleep(interval_seconds)
            except KeyboardInterrupt:
                log.info("Bot gestopt")
                break
            except Exception as e:
                log.error(f"Fout: {e}", exc_info=True)
                time.sleep(10)


# ─────────────────────────────────────────────
# 8. OPSTARTEN
# ─────────────────────────────────────────────

def load_config(config_path="config.yml", env_path=".env"):
    """Laad configuratie uit config.yml + secrets uit .env"""
    project_dir = Path(__file__).resolve().parent

    # Laad .env (secrets)
    env_file = project_dir / env_path
    load_dotenv(env_file)

    # Laad config.yml
    cfg_file = project_dir / config_path
    yml = {}
    if cfg_file.is_file():
        with open(cfg_file) as f:
            yml = yaml.safe_load(f) or {}

    return Config(
        paper_mode=yml.get("paper_mode", True),
        total_capital=yml.get("total_capital", 10_000.0),
        kelly_fraction=yml.get("kelly_fraction", 0.5),
        max_position_pct=yml.get("max_position_pct", 0.20),
        stop_loss_pct=yml.get("stop_loss_pct", 0.05),
        take_profit_pct=yml.get("take_profit_pct", 0.10),
        max_open_positions=yml.get("max_open_positions", 5),
        drawdown_limit_pct=yml.get("drawdown_limit_pct", 0.15),
        trade_log_path=yml.get("trade_log_path", "trades.json"),
        state_path=yml.get("state_path", "strategy_state.json"),
        ibkr_host=yml.get("ibkr_host", "127.0.0.1"),
        ibkr_port=yml.get("ibkr_port", 7497),
        ibkr_client_id=yml.get("ibkr_client_id", 1),
        crypto_broker=yml.get("crypto_broker", "kraken"),
        kraken_api_key=os.environ.get("KRAKEN_API_KEY", ""),
        kraken_api_secret=os.environ.get("KRAKEN_API_SECRET", ""),
        binance_api_key=os.environ.get("BINANCE_API_KEY", ""),
        binance_api_secret=os.environ.get("BINANCE_API_SECRET", ""),
        binance_testnet=yml.get("binance_testnet", True),
    )


if __name__ == "__main__":
    config = load_config()
    interval = 60  # default

    # Probeer interval uit config.yml te lezen
    cfg_file = Path(__file__).resolve().parent / "config.yml"
    if cfg_file.is_file():
        with open(cfg_file) as f:
            yml = yaml.safe_load(f) or {}
            interval = yml.get("interval_seconds", 60)

    log.info(f"Modus: {'PAPER' if config.paper_mode else 'LIVE'}")
    log.info(f"Kapitaal: €{config.total_capital:,.2f}")
    log.info(f"IBKR: {config.ibkr_host}:{config.ibkr_port}")
    log.info(f"Crypto broker: {config.crypto_broker}")
    log.info(f"Interval: {interval}s")

    bot = TradingBot(config)

    try:
        bot.connect_brokers()
        bot.run_loop(interval_seconds=interval)
    finally:
        bot.disconnect_brokers()
