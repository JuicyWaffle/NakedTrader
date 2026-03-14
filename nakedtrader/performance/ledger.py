"""
nakedtrader.performance.ledger — Append-only transactie-logboek voor Virtual Bank.

Elke virtuele trade wordt opgesplitst in individuele BUY en SELL transacties.
Opslag: data/ledger.json
"""

import json
import logging
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from nakedtrader.types import Transaction, TradeRecord
from nakedtrader.utils import atomic_write_json

log = logging.getLogger(__name__)


class VirtualLedger:
    """Append-only transactie-logboek."""

    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._path = self.data_dir / "ledger.json"
        self._tx_counter = 0
        self._transactions: list[dict] = []

        self._load()

    def _load(self):
        """Laad bestaande transacties uit ledger.json."""
        if self._path.exists():
            try:
                with open(self._path) as f:
                    data = json.load(f)
                self._transactions = data.get("transactions", [])
                self._tx_counter = data.get("tx_counter", len(self._transactions))
            except (json.JSONDecodeError, KeyError):
                log.warning("Corrupt ledger.json — start met leeg ledger")
                self._transactions = []
                self._tx_counter = 0

    def _next_id(self) -> str:
        self._tx_counter += 1
        return f"TX{self._tx_counter:06d}"

    def _save(self):
        """Schrijf ledger naar disk."""
        atomic_write_json(self._path, {
            "transactions": self._transactions,
            "tx_counter": self._tx_counter,
            "last_updated": datetime.now().isoformat(),
        })

    # ── Write API ────────────────────────────────────

    def record_buy(
        self,
        strategy_id: str,
        symbol: str,
        price: float,
        quantity: float,
        size_eur: float,
        trade_id: str = "",
        timestamp: str = "",
    ) -> Transaction:
        """Registreer een BUY transactie."""
        tx = Transaction(
            id=self._next_id(),
            timestamp=timestamp or datetime.now().isoformat(timespec="seconds"),
            strategy_id=strategy_id,
            symbol=symbol,
            tx_type="buy",
            price=price,
            quantity=quantity,
            size_eur=round(size_eur, 2),
            trade_id=trade_id,
        )
        self._transactions.append(asdict(tx))
        self._save()
        return tx

    def record_sell(
        self,
        strategy_id: str,
        symbol: str,
        price: float,
        quantity: float,
        size_eur: float,
        buy_price: float,
        trade_id: str = "",
        timestamp: str = "",
    ) -> Transaction:
        """Registreer een SELL transactie met winst t.o.v. aankoop."""
        pnl_pct = round((price - buy_price) / buy_price * 100, 2) if buy_price else 0.0
        pnl_eur = round((price - buy_price) * quantity, 2) if buy_price else 0.0

        tx = Transaction(
            id=self._next_id(),
            timestamp=timestamp or datetime.now().isoformat(timespec="seconds"),
            strategy_id=strategy_id,
            symbol=symbol,
            tx_type="sell",
            price=price,
            quantity=quantity,
            size_eur=round(size_eur, 2),
            buy_price=buy_price,
            pnl_eur=pnl_eur,
            pnl_pct=pnl_pct,
            trade_id=trade_id,
        )
        self._transactions.append(asdict(tx))
        self._save()
        return tx

    def record_trade(self, trade: TradeRecord) -> tuple[Transaction, Transaction]:
        """Registreer een complete round-trip trade als BUY + SELL."""
        buy = self.record_buy(
            strategy_id=trade.strategy_id,
            symbol=trade.symbol,
            price=trade.entry_price,
            quantity=trade.quantity,
            size_eur=trade.size_eur,
            trade_id=trade.id,
            timestamp=trade.timestamp,
        )
        sell = self.record_sell(
            strategy_id=trade.strategy_id,
            symbol=trade.symbol,
            price=trade.exit_price,
            quantity=trade.quantity,
            size_eur=round(trade.exit_price * trade.quantity, 2),
            buy_price=trade.entry_price,
            trade_id=trade.id,
            timestamp=trade.timestamp,
        )
        return buy, sell

    # ── Read API ─────────────────────────────────────

    def get_recent(self, strategy_id: str = None, limit: int = 20) -> list[dict]:
        """Retourneer de laatste N transacties, optioneel gefilterd op strategie."""
        txs = self._transactions
        if strategy_id:
            txs = [t for t in txs if t["strategy_id"] == strategy_id]
        return txs[-limit:]

    def get_open_positions(self, strategy_id: str = None) -> list[dict]:
        """Retourneer open posities: BUY's zonder bijbehorende SELL."""
        txs = self._transactions
        if strategy_id:
            txs = [t for t in txs if t["strategy_id"] == strategy_id]

        # Tel buys en sells per (strategy_id, symbol, trade_id)
        buys = {}
        for t in txs:
            key = (t["strategy_id"], t["symbol"], t.get("trade_id", ""))
            if t["tx_type"] == "buy":
                buys[key] = t
            elif t["tx_type"] == "sell" and key in buys:
                del buys[key]

        return list(buys.values())
