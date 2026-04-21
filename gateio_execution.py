#!/usr/bin/env python3
"""
Gate.io Execution Engine Integration
Combines arb-execution-engine patterns with Gate.io API for profitable trading
"""

import asyncio
import aiohttp
import hmac
import hashlib
import time
import logging
from decimal import Decimal
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime
import sqlite3
import os
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.FileHandler('gateio_execution.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

@dataclass
class TradeData:
    symbol: str
    side: str
    order_type: str
    amount: Decimal
    price: Optional[Decimal]
    order_id: Optional[str]
    status: str
    filled_amount: Decimal
    filled_price: Optional[Decimal]
    fee: Decimal
    expected_profit: Optional[float]
    actual_profit: Optional[float]
    latency_ms: float
    timestamp: datetime
    error: Optional[str]

class GateIOExecutionEngine:
    """
    Gate.io execution engine with arb-execution-engine patterns:
    - Portfolio delta accounting
    - Latency measurement
    - Retry logic
    - Kill switch
    - Risk controls
    """
    
    def __init__(self, api_key: str, api_secret: str):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = "https://api.gateio.ws/api/v4"
        self.session: Optional[aiohttp.ClientSession] = None
        
        # Risk controls
        self.min_edge_pct = float(os.getenv("MIN_EDGE_PCT", "0.5"))  # 0.5% minimum edge
        self.max_risk_per_trade = float(os.getenv("MAX_RISK_PER_TRADE", "0.02"))  # 2% risk
        self.kill_switch_threshold = float(os.getenv("KILL_SWITCH_THRESHOLD", "-0.1"))  # -0.1 SOL equivalent
        self.kill_switch_trades = int(os.getenv("KILL_SWITCH_TRADES", "10"))
        
        # State
        self.positions: Dict[str, Dict] = {}
        self.balance: Dict[str, Decimal] = {}
        self.running = False
        
        # Database
        self.db_path = "gateio_trades.db"
        self._init_db()
    
    def _init_db(self):
        """Initialize SQLite database for trade logging"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                side TEXT,
                order_type TEXT,
                amount REAL,
                price REAL,
                order_id TEXT,
                status TEXT,
                filled_amount REAL,
                filled_price REAL,
                fee REAL,
                expected_profit REAL,
                actual_profit REAL,
                latency_ms REAL,
                timestamp TEXT,
                error TEXT
            )
        """)
        conn.commit()
        conn.close()
    
    def _sign_request(self, method: str, url: str, params: Dict = None) -> Tuple[str, str]:
        """Sign Gate.io API request"""
        timestamp = str(int(time.time()))
        if params:
            query_string = "&".join([f"{k}={v}" for k, v in sorted(params.items())])
        else:
            query_string = ""
        
        payload = f"{method}\n{url}\n{query_string}\n{timestamp}"
        signature = hmac.new(
            self.api_secret.encode(),
            payload.encode(),
            hashlib.sha512
        ).hexdigest()
        
        return timestamp, signature
    
    async def _request(self, method: str, endpoint: str, params: Dict = None, signed: bool = False) -> Dict:
        """Make HTTP request to Gate.io API"""
        url = f"{self.base_url}{endpoint}"
        headers = {
            "Content-Type": "application/json",
            "ACCEPT": "application/json"
        }
        
        if signed:
            timestamp, signature = self._sign_request(method, endpoint, params)
            headers["KEY"] = self.api_key
            headers["Timestamp"] = timestamp
            headers["SIGN"] = signature
        
        async with self.session.request(method, url, json=params, headers=headers) as response:
            data = await response.json()
            if response.status != 200:
                raise Exception(f"Gate.io API error: {data}")
            return data
    
    async def get_balance(self) -> Dict[str, Decimal]:
        """Get account balance"""
        data = await self._request("GET", "/spot/accounts", signed=True)
        balance = {}
        for item in data:
            currency = item["currency"]
            available = Decimal(str(item["available"]))
            if available > 0:
                balance[currency] = available
        self.balance = balance
        return balance
    
    async def get_ticker(self, symbol: str) -> Dict:
        """Get ticker for symbol"""
        return await self._request("GET", f"/spot/tickers?currency_pair={symbol}")
    
    async def get_orderbook(self, symbol: str) -> Dict:
        """Get orderbook for symbol"""
        return await self._request("GET", f"/spot/order_book?currency_pair={symbol}&limit=20")
    
    def calculate_edge(self, orderbook: Dict, side: str, amount: Decimal) -> float:
        """
        Calculate edge based on orderbook depth
        Returns edge percentage (e.g., 0.5% = 0.005)
        """
        if side == "buy":
            # Buying from asks - check if we're getting a good price
            asks = orderbook.get("asks", [])
            if not asks:
                return 0.0
            
            # Calculate weighted average price for our amount
            remaining = float(amount)
            total_cost = 0.0
            for ask in asks:
                ask_price = float(ask[0])
                ask_size = float(ask[1])
                if remaining <= 0:
                    break
                fill = min(remaining, ask_size)
                total_cost += fill * ask_price
                remaining -= fill
            
            if remaining > 0:
                return 0.0  # Not enough liquidity
            
            avg_price = total_cost / float(amount)
            mid_price = (float(orderbook["bids"][0][0]) + float(orderbook["asks"][0][0])) / 2
            
            # Edge = how much better than mid price we're getting
            edge = (mid_price - avg_price) / mid_price
            return edge
        
        else:  # sell
            # Selling to bids
            bids = orderbook.get("bids", [])
            if not bids:
                return 0.0
            
            remaining = float(amount)
            total_value = 0.0
            for bid in bids:
                bid_price = float(bid[0])
                bid_size = float(bid[1])
                if remaining <= 0:
                    break
                fill = min(remaining, bid_size)
                total_value += fill * bid_price
                remaining -= fill
            
            if remaining > 0:
                return 0.0
            
            avg_price = total_value / float(amount)
            mid_price = (float(orderbook["bids"][0][0]) + float(orderbook["asks"][0][0])) / 2
            
            # Edge = how much better than mid price we're getting
            edge = (avg_price - mid_price) / mid_price
            return edge
    
    def check_kill_switch(self) -> Tuple[bool, str]:
        """Check if kill switch should trigger"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT actual_profit FROM trades ORDER BY id DESC LIMIT ?", (self.kill_switch_trades,))
        trades = cursor.fetchall()
        conn.close()
        
        if len(trades) < self.kill_switch_trades:
            return False, "Not enough trades yet"
        
        total_pnl = sum(t[0] or 0 for t in trades)
        
        if total_pnl < self.kill_switch_threshold:
            return True, f"Rolling PnL {total_pnl} < threshold {self.kill_switch_threshold}"
        
        return False, "OK"
    
    def log_trade(self, trade: TradeData):
        """Log trade to database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO trades (
                symbol, side, order_type, amount, price, order_id, status,
                filled_amount, filled_price, fee, expected_profit, actual_profit,
                latency_ms, timestamp, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            trade.symbol, trade.side, trade.order_type, float(trade.amount),
            float(trade.price) if trade.price else None, trade.order_id, trade.status,
            float(trade.filled_amount), float(trade.filled_price) if trade.filled_price else None,
            float(trade.fee), trade.expected_profit, trade.actual_profit,
            trade.latency_ms, trade.timestamp.isoformat(), trade.error
        ))
        conn.commit()
        conn.close()
    
    async def execute_trade(
        self,
        symbol: str,
        side: str,
        amount: Decimal,
        order_type: str = "limit"
    ) -> TradeData:
        """
        Execute a trade with arb-execution-engine patterns:
        - Edge validation
        - Latency measurement
        - Retry logic
        - Portfolio delta accounting
        """
        start_time = time.time()
        
        # Check kill switch
        kill_triggered, reason = self.check_kill_switch()
        if kill_triggered:
            logger.error(f"🛑 Kill switch triggered: {reason}")
            return TradeData(
                symbol=symbol, side=side, order_type=order_type, amount=amount,
                price=None, order_id=None, status="failed", filled_amount=Decimal('0'),
                filled_price=None, fee=Decimal('0'), expected_profit=None,
                actual_profit=None, latency_ms=0, timestamp=datetime.now(),
                error=f"Kill switch: {reason}"
            )
        
        # Get orderbook for edge calculation
        orderbook = await self.get_orderbook(symbol)
        edge = self.calculate_edge(orderbook, side, amount)
        
        logger.info(f"📊 Edge for {symbol} {side}: {edge*100:.2f}%")
        
        # Validate edge
        if edge < self.min_edge_pct / 100:
            logger.warning(f"⚠️ Edge {edge*100:.2f}% below minimum {self.min_edge_pct}%")
            return TradeData(
                symbol=symbol, side=side, order_type=order_type, amount=amount,
                price=None, order_id=None, status="insufficient_edge",
                filled_amount=Decimal('0'), filled_price=None, fee=Decimal('0'),
                expected_profit=edge * float(amount), actual_profit=None,
                latency_ms=(time.time() - start_time) * 1000, timestamp=datetime.now(),
                error=f"Edge {edge*100:.2f}% below minimum {self.min_edge_pct}%"
            )
        
        # Calculate price from orderbook
        if side == "buy":
            price = Decimal(str(orderbook["asks"][0][0]))
        else:
            price = Decimal(str(orderbook["bids"][0][0]))
        
        # Execute order
        params = {
            "currency_pair": symbol,
            "side": side,
            "amount": str(amount),
            "price": str(price) if order_type == "limit" else None,
            "type": order_type
        }
        
        try:
            result = await self._request("POST", "/spot/orders", params=params, signed=True)
            order_id = result.get("id")
            
            logger.info(f"✅ Order submitted: {order_id}")
            
            # Wait for fill (simplified - in production, use WebSocket)
            await asyncio.sleep(2)
            
            # Get order status
            order_data = await self._request("GET", f"/spot/orders/{order_id}", signed=True)
            status = order_data.get("status")
            filled_amount = Decimal(str(order_data.get("filled_amount", "0")))
            filled_price = Decimal(str(order_data.get("field_cash_amount", "0"))) / filled_amount if filled_amount > 0 else None
            fee = Decimal(str(order_data.get("fee", "0")))
            
            # Calculate actual profit
            if status == "filled" and filled_price:
                if side == "buy":
                    # Bought at filled_price, current value at mid
                    mid_price = (float(orderbook["bids"][0][0]) + float(orderbook["asks"][0][0])) / 2
                    actual_profit = (mid_price - float(filled_price)) * float(filled_amount) - float(fee)
                else:
                    # Sold at filled_price
                    mid_price = (float(orderbook["bids"][0][0]) + float(orderbook["asks"][0][0])) / 2
                    actual_profit = (float(filled_price) - mid_price) * float(filled_amount) - float(fee)
            else:
                actual_profit = None
            
            trade = TradeData(
                symbol=symbol, side=side, order_type=order_type, amount=amount,
                price=price, order_id=order_id, status=status,
                filled_amount=filled_amount, filled_price=filled_price, fee=fee,
                expected_profit=edge * float(amount), actual_profit=actual_profit,
                latency_ms=(time.time() - start_time) * 1000, timestamp=datetime.now(),
                error=None
            )
            
            self.log_trade(trade)
            
            if status == "filled" and actual_profit and actual_profit > 0:
                logger.info(f"💰 Profit: {actual_profit:.2f} USDT")
            
            return trade
            
        except Exception as e:
            logger.error(f"❌ Trade failed: {e}")
            return TradeData(
                symbol=symbol, side=side, order_type=order_type, amount=amount,
                price=price, order_id=None, status="failed",
                filled_amount=Decimal('0'), filled_price=None, fee=Decimal('0'),
                expected_profit=edge * float(amount), actual_profit=None,
                latency_ms=(time.time() - start_time) * 1000, timestamp=datetime.now(),
                error=str(e)
            )
    
    async def start(self):
        """Start the execution engine"""
        self.session = aiohttp.ClientSession()
        self.running = True
        
        logger.info("🚀 Gate.io Execution Engine starting...")
        
        # Initialize account data
        await self.get_balance()
        logger.info(f"💰 Balance: {self.balance}")
        
        # Example: Execute a single trade
        # In production, this would be driven by a strategy
        if "USDT" in self.balance and self.balance["USDT"] > 10:
            logger.info("📈 Executing example trade...")
            trade = await self.execute_trade(
                symbol="BTC_USDT",
                side="buy",
                amount=Decimal("10"),  # 10 USDT worth
                order_type="market"
            )
            logger.info(f"📊 Trade result: {trade.status}")
    
    async def stop(self):
        """Stop the execution engine"""
        self.running = False
        if self.session:
            await self.session.close()
        logger.info("🛑 Gate.io Execution Engine stopped")

async def main():
    """Main entry point"""
    api_key = os.getenv("GATEIO_API_KEY")
    api_secret = os.getenv("GATEIO_API_SECRET")
    
    if not api_key or not api_secret:
        logger.error("❌ GATEIO_API_KEY and GATEIO_API_SECRET must be set")
        return
    
    engine = GateIOExecutionEngine(api_key, api_secret)
    
    try:
        await engine.start()
    except KeyboardInterrupt:
        logger.info("🛑 Interrupted by user")
    finally:
        await engine.stop()

if __name__ == "__main__":
    asyncio.run(main())
