"""
FastAPI - API layer for UI connection
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
import os

app = FastAPI(
    title="Arb Execution Engine API",
    description="API for autonomous Solana arbitrage execution",
    version="1.0.0"
)

# CORS for UI connection
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global engine instance (would be initialized on startup)
engine_instance = None

# Models
class OpportunityResponse(BaseModel):
    token: str
    dex_a: str
    dex_b: str
    spread_pct: float
    timestamp: str

class TradeResponse(BaseModel):
    token: str
    success: bool
    transaction_id: Optional[str]
    executed_at: str
    pnl_usd: Optional[float]

class MetricsResponse(BaseModel):
    total_trades: int
    successful_trades: int
    win_rate: float
    total_pnl_usd: float
    avg_spread_pct: float

# In-memory storage (replace with database in production)
opportunities_store: List[dict] = []
trades_store: List[dict] = []

@app.get("/")
async def root():
    return {
        "message": "Arb Execution Engine API",
        "version": "1.0.0",
        "status": "running"
    }

@app.get("/health")
async def health():
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}

@app.get("/signals", response_model=List[OpportunityResponse])
async def get_signals(limit: int = 10):
    """
    Get current trading signals/opportunities
    """
    # In production, this would query the scanner
    # For now, return mock data
    return [
        {
            "token": "EXAMPLE",
            "dex_a": "raydium",
            "dex_b": "jupiter",
            "spread_pct": 0.75,
            "timestamp": datetime.utcnow().isoformat()
        }
    ]

@app.get("/trades", response_model=List[TradeResponse])
async def get_trades(limit: int = 50, offset: int = 0):
    """
    Get trade history
    """
    # In production, this would query the database
    return trades_store[offset:offset + limit]

@app.get("/metrics", response_model=MetricsResponse)
async def get_metrics():
    """
    Get performance metrics
    """
    # In production, this would calculate from trade history
    total_trades = len(trades_store)
    successful_trades = sum(1 for t in trades_store if t.get("success", False))
    
    return MetricsResponse(
        total_trades=total_trades,
        successful_trades=successful_trades,
        win_rate=successful_trades / total_trades if total_trades > 0 else 0.0,
        total_pnl_usd=sum(t.get("pnl_usd", 0) for t in trades_store),
        avg_spread_pct=0.0  # Calculate from actual data
    )

@app.post("/manual-trade")
async def manual_trade(token: str, amount_sol: float):
    """
    Manually trigger a trade for a specific token
    """
    # In production, this would call engine.execute_opportunity()
    return {
        "success": False,
        "message": "Manual trade not implemented yet"
    }

@app.get("/engine/status")
async def engine_status():
    """
    Get execution engine status
    """
    return {
        "running": engine_instance is not None,
        "last_scan": None,
        "trades_executed": len(trades_store)
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
