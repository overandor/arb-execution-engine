"""
FastAPI wrapper for Arb Execution Engine
Thin adapter layer - execution engine remains pure
"""

from fastapi import FastAPI, HTTPException
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


@app.get("/scanner/opportunities")
async def scan_opportunities(min_spread_pct: float = 0.5):
    """Scan for arbitrage opportunities"""
    if not engine:
        raise HTTPException(status_code=503, detail="Engine not initialized")
    
    if not engine.scanner:
        raise HTTPException(status_code=503, detail="Scanner not initialized")
    
    opportunities = await engine.scanner.detect_opportunities(min_spread_pct=min_spread_pct)
    
    return [
        {
            "token": opp.token,
            "dex_a": opp.dex_a,
            "dex_b": opp.dex_b,
            "price_a": opp.price_a,
            "price_b": opp.price_b,
            "spread_pct": opp.spread_pct,
            "liquidity_a": opp.liquidity_a,
            "liquidity_b": opp.liquidity_b,
            "timestamp": opp.timestamp.isoformat(),
            "token_address": opp.token_address
        }
        for opp in opportunities
    ]


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
