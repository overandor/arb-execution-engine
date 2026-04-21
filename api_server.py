"""
FastAPI wrapper for Arb Execution Engine
Thin adapter layer - execution engine remains pure
"""

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
import os
from arb_engine import ExecutionEngine, TradeDatabase

app = FastAPI(
    title="Arb Execution Engine API",
    description="Solana arbitrage execution with truthful accounting",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API Key Configuration
API_KEY = os.getenv("API_KEY", "dev_key_change_in_production")

# ============================================================================
# API Key Middleware
# ============================================================================

@app.middleware("http")
async def api_key_middleware(request: Request, call_next):
    """Verify API key for protected endpoints"""
    # Skip auth for health check and metrics (public endpoints)
    if request.url.path in ["/health", "/metrics", "/metrics/live"]:
        return await call_next(request)
    
    # Verify API key
    api_key = request.headers.get("x-api-key")
    if not api_key or api_key != API_KEY:
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "Invalid or missing API key"}
        )
    
    return await call_next(request)

# Global engine instance (singleton)
engine: Optional[ExecutionEngine] = None
db = TradeDatabase()


# ============================================================================
# Request/Response Models
# ============================================================================

class TradeRequest(BaseModel):
    inputMint: str
    outputMint: str
    amountLamports: int
    slippageBps: Optional[int] = 50
    maxPriorityFee: Optional[int] = None
    dryRun: Optional[bool] = False


class HealthResponse(BaseModel):
    status: str
    timestamp: str


# ============================================================================
# Lifecycle
# ============================================================================

@app.on_event("startup")
async def startup():
    """Initialize execution engine on startup"""
    global engine
    engine = ExecutionEngine()
    await engine.__aenter__()


@app.on_event("shutdown")
async def shutdown():
    """Cleanup execution engine on shutdown"""
    global engine
    if engine:
        await engine.__aexit__(None, None, None)


# ============================================================================
# Endpoints
# ============================================================================

@app.get("/health", response_model=HealthResponse)
async def health():
    """Health check endpoint"""
    from datetime import datetime
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat()
    }


@app.post("/trade")
async def trade(req: TradeRequest):
    """
    Execute a single arbitrage trade
    
    - inputMint: Input token mint address
    - outputMint: Output token mint address  
    - amountLamports: Amount in lamports
    - slippageBps: Slippage tolerance in basis points (default: 50 = 0.5%)
    - maxPriorityFee: Maximum priority fee in lamports (optional)
    - dryRun: If true, run without executing (stops after signing)
    """
    if not engine:
        raise HTTPException(status_code=503, detail="Engine not initialized")
    
    try:
        # Set dry run flag
        engine._dry_run = req.dryRun
        
        result = await engine.execute_single_trade(
            token=f"{req.inputMint[:8]}→{req.outputMint[:8]}",
            input_mint=req.inputMint,
            output_mint=req.outputMint,
            amount_lamports=req.amountLamports
        )
        
        return result
    except Exception as e:
        if "Kill switch" in str(e):
            raise HTTPException(status_code=403, detail=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/trades")
async def get_trades(limit: int = 50, status: Optional[str] = None):
    """Get trade history from database"""
    trades = db.get_recent_trades(limit=limit)
    
    if status:
        trades = [t for t in trades if t.get('status') == status]
    
    return trades


@app.get("/trades/{trade_id}")
async def get_trade(trade_id: int):
    """Get specific trade by ID"""
    trades = db.get_recent_trades(limit=1000)
    
    for trade in trades:
        if trade.get('id') == trade_id:
            return trade
    
    raise HTTPException(status_code=404, detail="Trade not found")


@app.get("/metrics")
async def get_metrics():
    """Get performance metrics"""
    return db.get_metrics()


@app.get("/metrics/live")
async def get_live_metrics():
    """
    Get live metrics for dashboard (time-series feel)
    
    Returns rolling PnL, win rate, latency, recent trades
    """
    trades = db.get_recent_trades(limit=50)
    
    # Calculate rolling metrics
    successful = [t for t in trades if t.get('status') == 'success']
    win_rate = len(successful) / len(trades) if trades else 0.0
    
    # Calculate rolling PnL (last 20 trades)
    recent_trades = trades[:20]
    pnl_rolling = []
    for t in recent_trades:
        pnl = t.get('actual_profit') or 0
        pnl_rolling.append(pnl)
    
    # Calculate average latency
    latencies = [t.get('latency_ms') for t in trades if t.get('latency_ms')]
    avg_latency = sum(latencies) / len(latencies) if latencies else 0.0
    
    return {
        "pnl_rolling": pnl_rolling,
        "win_rate": round(win_rate, 3),
        "avg_latency_ms": round(avg_latency, 2),
        "recent_trades": trades[:10],  # Last 10 trades
        "total_trades": len(trades),
        "execution_mode": os.getenv("EXECUTION_MODE", "sim")
    }


# REMOVED: /scanner/opportunities endpoint
# 
# Exposing raw opportunities via API creates:
# - Race conditions (multiple users competing for same trade)
# - Stale signals (perishable by the time consumed)
# - Front-running risk
# - Self-competition (you become your own worst enemy)
#
# Instead, use /trade to execute directly.
# Engine handles edge detection internally to preserve alpha.



if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
