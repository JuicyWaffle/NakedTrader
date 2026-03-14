"""
nakedtrader/server/app.py — FastAPI dashboard backend voor NakedTrader.
"""

import logging
from typing import List, Optional
from fastapi import FastAPI, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pathlib import Path

from nakedtrader.db.session import DatabaseManager, get_db_manager
from nakedtrader.db.repository import DataRepository
from nakedtrader.config import load_config

log = logging.getLogger(__name__)

app = FastAPI(title="NakedTrader Dashboard API")

# CORS toestaan voor lokale ontwikkeling
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Project paden
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PUBLIC_DIR = PROJECT_ROOT / "web" / "public"

# ── Pydantic Schema's ───────────────────────────────

class TradeSchema(BaseModel):
    id: int
    timestamp: str
    symbol: str
    pnl_eur: float
    pnl_pct: float
    outcome: str
    mode: str

    class Config:
        from_attributes = True

class StatusSchema(BaseModel):
    orchestrator_id: str
    mode: str
    capital: float
    drawdown_pct: float
    open_positions: int
    executions_logged: int

# ── Dependency Injection ─────────────────────────────

def get_repo():
    repo = DataRepository()
    try:
        yield repo
    finally:
        repo.close()

# ── API Endpoints ───────────────────────────────────

@app.get("/api/status", response_model=StatusSchema)
async def get_status(repo: DataRepository = Depends(get_repo)):
    """Haal de actuele status van de bot op."""
    # Voor een echte implementatie zouden we de orchestrator state ergens vandaan moeten halen
    # Hier simuleren we het via de laatste equity curve data
    equity = repo.get_equity_curve(limit=1)
    capital = equity[0].total_capital if equity else 10000.0
    dd = equity[0].drawdown_pct if equity else 0.0
    
    return {
        "orchestrator_id": "A",
        "mode": "paper",
        "capital": capital,
        "drawdown_pct": dd,
        "open_positions": 0, # Moet uit actieve orchestrator komen
        "executions_logged": 0 # Moet uit DB komen
    }

@app.get("/api/trades", response_model=List[TradeSchema])
async def get_trades(limit: int = 50, repo: DataRepository = Depends(get_repo)):
    """Haal de meest recente trades op."""
    trades = repo.get_recent_trades(limit=limit)
    return [
        {
            "id": t.id,
            "timestamp": t.timestamp.isoformat(),
            "symbol": t.symbol,
            "pnl_eur": t.pnl_eur,
            "pnl_pct": t.pnl_pct,
            "outcome": t.outcome,
            "mode": t.mode
        } for t in trades
    ]

@app.get("/api/equity")
async def get_equity(days: int = 30, repo: DataRepository = Depends(get_repo)):
    """Haal data op voor de equity curve grafiek."""
    curve = repo.get_equity_curve(days=days)
    return [
        {
            "timestamp": p.timestamp.isoformat(),
            "capital": p.total_capital,
            "drawdown": p.drawdown_pct
        } for p in curve
    ]

# ── WebSockets voor Live Updates ─────────────────────

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            await connection.send_text(message)

manager = ConnectionManager()

@app.websocket("/ws/events")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # We kunnen berichten sturen vanuit de bot naar de clients
            data = await websocket.receive_text()
            # Echo voor nu
            await websocket.send_text(f"Bericht ontvangen: {data}")
    except WebSocketDisconnect:
        manager.disconnect(websocket)

# ── Static Files ────────────────────────────────────

if PUBLIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(PUBLIC_DIR), html=True), name="static")
else:
    log.warning(f"Public directory niet gevonden op {PUBLIC_DIR}")
