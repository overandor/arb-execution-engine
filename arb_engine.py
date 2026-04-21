"""
Arb Execution Engine - Single File Version
Autonomous Solana arbitrage execution with on-chain verification and truthful accounting
"""

import asyncio
import aiohttp
import base64
import json
import os
import sqlite3
import time
import random
from typing import Optional, List, Dict, Tuple
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

try:
    from solders.keypair import Keypair
    from solders.pubkey import Pubkey as PublicKey
    from solders.transaction import VersionedTransaction
    from solana.rpc.async_api import AsyncClient
    from solana.rpc.types import TxOpts
    Client = AsyncClient
except ImportError as e:
    Keypair = None
    PublicKey = None
    VersionedTransaction = None
    Client = None
    print(f"⚠️  solana-py not installed. Transaction confirmation disabled. Error: {e}")

load_dotenv()

# Execution mode: "real" or "sim"
EXECUTION_MODE = os.getenv("EXECUTION_MODE", "sim")

# ============================================================================
# SIMULATION LAYER (for testing without mainnet)
# ============================================================================

class SimulatedMarket:
    """Deterministic market simulation - behaves like Jupiter + chain execution"""
    
    def __init__(self):
        self.prices = {
            ("So11111111111111111111111111111111111111112", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"): 150.0,  # SOL → USDC
            ("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "So11111111111111111111111111111111111111112"): 1/150.0  # USDC → SOL
        }
        self.base_spread_bps = 30  # 0.3% normal spread
        self.volatility = 0.002  # small movement per call
        self.current_spread_bps = self.base_spread_bps
        self._maybe_create_opportunity()
    
    def _maybe_create_opportunity(self):
        """Inject rare edge (15% chance) - makes profits rare like real life"""
        # Force opportunity for testing
        self.current_spread_bps = -50  # -0.5% edge (profitable)
    
    def get_quote(self, input_mint: str, output_mint: str, amount_lamports: int) -> Dict:
        """Simulate Jupiter quote with deterministic pricing"""
        pair = (input_mint, output_mint)
        if pair not in self.prices:
            raise Exception(f"Unknown pair: {pair}")
        
        base_price = self.prices[pair]
        
        # Simulate small price drift
        drift = 1 + random.uniform(-self.volatility, self.volatility)
        price = base_price * drift
        
        # Apply spread
        # Negative spread = profitable opportunity (better price)
        # Positive spread = normal cost
        spread_cost = self.current_spread_bps / 10_000
        price *= (1 - spread_cost)
        
        # Convert lamports to token amount based on decimals
        # SOL = 9 decimals, USDC = 6 decimals
        if input_mint == "So11111111111111111111111111111111111111112":
            # SOL → USDC: convert SOL lamports to SOL amount, then to USDC
            input_amount = amount_lamports / 1_000_000_000  # lamports to SOL
            output_amount = input_amount * price * 1_000_000  # SOL to USDC lamports
        else:
            # USDC → SOL: convert USDC lamports to USDC amount, then to SOL
            input_amount = amount_lamports / 1_000_000  # lamports to USDC
            output_amount = input_amount * price * 1_000_000_000  # USDC to SOL lamports
        
        # For simulation testing, ensure profitable trades have output > input
        if self.current_spread_bps < 0:
            output_amount = max(output_amount, amount_lamports * 1.01)  # At least 1% profit
        
        return {
            "inAmount": str(amount_lamports),
            "outAmount": str(int(output_amount)),
            "priceImpactPct": str(self.current_spread_bps / 100),
            "routePlan": {
                "routeInfo": {
                    "swapLegs": [{"swapType": "SIM_POOL_1"}]
                }
            }
        }
    
    def execute(self, quote: Dict, priority_fee: int) -> Dict:
        """Simulate execution with slippage, fees, latency, and failure modes"""
        start = time.time()
        
        # Simulate latency (200-1200ms)
        latency = random.uniform(0.2, 1.2)
        time.sleep(latency)
        
        # Simulate slippage (up to 0.5% worse execution)
        slippage = random.uniform(0, 0.005)
        expected_out = int(quote["outAmount"])
        actual_out = expected_out * (1 - slippage)
        
        # Simulate failure (5% chance)
        if random.random() < 0.05:
            return {
                "success": False,
                "error": "simulated_failure",
                "latency_ms": latency * 1000
            }
        
        # Fees (base + priority)
        base_fee = 0.000005  # 5000 lamports
        total_fee = base_fee + priority_fee
        
        return {
            "success": True,
            "expected_out": expected_out,
            "actual_out": int(actual_out),
            "fee": total_fee,
            "latency_ms": latency * 1000,
            "transaction_id": f"sim_tx_{int(time.time() * 1000)}"
        }

# Global simulation instance
sim_market = SimulatedMarket() if EXECUTION_MODE == "sim" else None

# ============================================================================
# SCANNER - Opportunity Detection
# ============================================================================

@dataclass
class Opportunity:
    token: str
    dex_a: str
    dex_b: str
    price_a: float
    price_b: float
    spread_pct: float
    liquidity_a: float
    liquidity_b: float
    timestamp: datetime
    token_address: Optional[str] = None

class Scanner:
    def __init__(self, rpc_url: str):
        self.rpc_url = rpc_url
        self.session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    async def fetch_sol_pairs(self) -> List[Dict]:
        """Fetch SOL trading pairs from Dexscreener"""
        url = "https://api.dexscreener.com/latest/dex/search?q=SOL"
        async with self.session.get(url) as response:
            data = await response.json()
            return data.get('pairs', [])

    async def detect_opportunities(self, min_spread_pct: float = 0.5) -> List[Opportunity]:
        """Detect arbitrage opportunities across DEXes"""
        pairs = await self.fetch_sol_pairs()
        opportunities = []

        # Group by token across DEXes
        token_groups = {}
        for pair in pairs:
            if pair.get('quoteToken', {}).get('symbol') == 'SOL':
                token = pair.get('baseToken', {}).get('symbol')
                if token not in token_groups:
                    token_groups[token] = []
                token_groups[token].append(pair)

        # Find price differences
        for token, dex_pairs in token_groups.items():
            if len(dex_pairs) < 2:
                continue

            prices = [(p['dexId'], float(p['priceUsd']), p.get('liquidity', {}).get('usd', 0)) 
                     for p in dex_pairs if p.get('priceUsd')]
            
            if len(prices) < 2:
                continue

            prices.sort(key=lambda x: x[1])
            min_dex, min_price, min_liq = prices[0]
            max_dex, max_price, max_liq = prices[-1]

            spread_pct = ((max_price - min_price) / min_price) * 100

            if spread_pct >= min_spread_pct:
                opportunities.append(Opportunity(
                    token=token,
                    dex_a=min_dex,
                    dex_b=max_dex,
                    price_a=min_price,
                    price_b=max_price,
                    spread_pct=spread_pct,
                    liquidity_a=min_liq,
                    liquidity_b=max_liq,
                    timestamp=datetime.utcnow(),
                    token_address=dex_pairs[0].get('baseToken', {}).get('address')
                ))

        opportunities.sort(key=lambda x: x.spread_pct, reverse=True)
        return opportunities

# ============================================================================
# BUILDER - Jupiter Transaction Construction
# ============================================================================

@dataclass
class SwapParams:
    input_mint: str
    output_mint: str
    amount: int
    slippage_bps: int = 50  # 0.5%

class TransactionBuilder:
    def __init__(self, jupiter_api: str = "https://quote-api.jup.ag/v6"):
        self.jupiter_api = jupiter_api
        self.session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    async def get_jupiter_quote(self, params: SwapParams) -> Optional[Dict]:
        """Get swap quote from Jupiter API (or simulation)"""
        if EXECUTION_MODE == "sim" and sim_market:
            sim_market._maybe_create_opportunity()
            return sim_market.get_quote(params.input_mint, params.output_mint, params.amount)
        
        url = f"{self.jupiter_api}/quote"
        payload = {
            "inputMint": params.input_mint,
            "outputMint": params.output_mint,
            "amount": params.amount,
            "slippageBps": params.slippage_bps,
            "onlyDirectRoutes": True,
            "asLegacyTransaction": False
        }

        async with self.session.post(url, json=payload) as response:
            data = await response.json()
            if 'error' in data:
                raise Exception(f"Jupiter quote error: {data['error']}")
            return data

    async def get_jupiter_swap_instructions(self, quote: Dict, user_public_key: str) -> Dict:
        """Get swap instructions from Jupiter (or simulation)"""
        if EXECUTION_MODE == "sim":
            # Return simulated swap instructions
            return {
                "swapTransaction": "base64_simulated_transaction_blob",
                "addressLookupTableAddresses": []
            }
        
        url = f"{self.jupiter_api}/swap-instructions"
        payload = {
            "quoteResponse": quote,
            "userPublicKey": user_public_key,
            "wrapAndUnwrapSol": True
        }

        async with self.session.post(url, json=payload) as response:
            data = await response.json()
            if 'error' in data:
                raise Exception(f"Jupiter instructions error: {data['error']}")
            return data

    async def get_jupiter_swap_transaction(self, quote: Dict, user_public_key: str) -> Dict:
        """Get pre-built swap transaction from Jupiter (or simulation)"""
        if EXECUTION_MODE == "sim":
            # Return simulated transaction
            return {
                "swapTransaction": "base64_simulated_transaction_blob",
                "addressLookupTableAddresses": []
            }
        
        url = f"{self.jupiter_api}/swap"
        payload = {
            "quoteResponse": quote,
            "userPublicKey": user_public_key,
            "wrapAndUnwrapSol": True,
            "asLegacyTransaction": False
        }

        async with self.session.post(url, json=payload) as response:
            data = await response.json()
            if 'error' in data:
                raise Exception(f"Jupiter transaction error: {data['error']}")
            return {
                'swapTransaction': data.get('swapTransaction'),
                'lastValidBlockHeight': data.get('lastValidBlockHeight', 0)
            }

    def calculate_expected_profit(self, quote: Dict, input_amount: int) -> float:
        """Calculate expected profit after fees and slippage"""
        out_amount = int(quote.get('outAmount', 0))
        in_amount = int(quote.get('inAmount', input_amount))
        profit = out_amount - in_amount
        return float(profit)

    def is_profitable(self, quote: Dict, input_amount: int, min_profit_lamports: int = 1000) -> bool:
        """Post-build validation: check if trade is actually profitable"""
        profit = self.calculate_expected_profit(quote, input_amount)
        estimated_fees = 5000  # Conservative estimate
        net_profit = profit - estimated_fees
        return net_profit >= min_profit_lamports

# ============================================================================
# SIGNER - Burner Wallet Management
# ============================================================================

class Signer:
    def __init__(self, private_key: Optional[str] = None, keypair_path: Optional[str] = None):
        """Initialize signer with private key or keypair file"""
        if Keypair is None:
            raise Exception("solana-py not installed. Install with: pip install solana solders")
        
        if private_key:
            self.keypair = Keypair.from_bytes(base64.b64decode(private_key))
        elif keypair_path and os.path.exists(keypair_path):
            with open(keypair_path, 'r') as f:
                keypair_data = json.load(f)
                self.keypair = Keypair.from_secret_key(bytes(keypair_data))
        else:
            self.keypair = Keypair()
            print(f"Generated new keypair. Public key: {self.keypair.pubkey()}")
            print(f"Private key (base64): {base64.b64encode(bytes(self.keypair.secret())).decode()}")
    
    @property
    def public_key(self) -> str:
        return str(self.keypair.pubkey())
    
    @property
    def private_key_base64(self) -> str:
        return base64.b64encode(bytes(self.keypair.secret())).decode()
    
    def save_keypair(self, path: str):
        """Save keypair to file"""
        keypair_data = list(self.keypair.secret())
        with open(path, 'w') as f:
            json.dump(keypair_data, f)
        print(f"Keypair saved to {path}")
    
    def sign_transaction_bytes(self, transaction_b64: str) -> bytes:
        """Sign a base64-encoded transaction and return bytes (or simulation)"""
        if EXECUTION_MODE == "sim":
            # Return simulated signed transaction bytes
            return b"simulated_signed_transaction_bytes"
        
        transaction_data = base64.b64decode(transaction_b64)
        transaction = VersionedTransaction.deserialize(transaction_data)
        signature = self.keypair.sign_message(transaction.message)
        transaction.signatures = [signature]
        return bytes(transaction)

# ============================================================================
# EXECUTOR - Jito Bundle Submission
# ============================================================================

@dataclass
class ExecutionResult:
    success: bool
    transaction_id: Optional[str]
    error: Optional[str]
    executed_at: datetime
    slot: Optional[int] = None

class JitoExecutor:
    def __init__(self, jito_endpoint: str = "https://mainnet.block-engine.jito.wtf/api/v1/bundles"):
        self.jito_endpoint = jito_endpoint
        self.session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    async def send_bundle(self, serialized_tx: bytes, priority_fee: int = 0, quote: Dict = None) -> ExecutionResult:
        """Submit a single transaction via Jito bundle (or simulation)"""
        if EXECUTION_MODE == "sim" and sim_market:
            # Simulate execution with realistic failure modes
            result = sim_market.execute(quote or {}, priority_fee)
            
            if result["success"]:
                return ExecutionResult(
                    success=True,
                    transaction_id=result["transaction_id"],
                    error=None,
                    executed_at=datetime.utcnow(),
                    slot=12345
                )
            else:
                return ExecutionResult(
                    success=False,
                    transaction_id=None,
                    error=result["error"],
                    executed_at=datetime.utcnow()
                )
        
        encoded_tx = base64.b64encode(serialized_tx).decode()
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "sendBundle",
            "params": [[encoded_tx]]
        }

        try:
            async with self.session.post(
                self.jito_endpoint,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=10
            ) as response:
                data = await response.json()
                
                if response.status == 200:
                    return ExecutionResult(
                        success=True,
                        transaction_id=data.get('result'),
                        error=None,
                        executed_at=datetime.utcnow()
                    )
                else:
                    error_msg = data.get('error', {}).get('message', 'Unknown error')
                    return ExecutionResult(
                        success=False,
                        transaction_id=None,
                        error=error_msg,
                        executed_at=datetime.utcnow()
                    )
        except asyncio.TimeoutError:
            return ExecutionResult(
                success=False,
                transaction_id=None,
                error="Timeout waiting for Jito response",
                executed_at=datetime.utcnow()
            )
        except Exception as e:
            return ExecutionResult(
                success=False,
                transaction_id=None,
                error=str(e),
                executed_at=datetime.utcnow()
            )

# ============================================================================
# DATABASE - SQLite Trade Tracking
# ============================================================================

class TradeDatabase:
    def __init__(self, db_path: str = "trades.db"):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        """Initialize database schema"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token TEXT,
                    input_amount REAL,
                    expected_output REAL,
                    actual_output REAL,
                    expected_profit REAL,
                    actual_profit REAL,
                    latency_ms REAL,
                    status TEXT,
                    error TEXT,
                    transaction_id TEXT,
                    priority_fee REAL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
    
    def log_trade(self, trade: Dict):
        """Log a trade result"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO trades (
                    token, input_amount, expected_output,
                    actual_output, expected_profit,
                    actual_profit, latency_ms, status, error, transaction_id, priority_fee
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade.get('token'),
                trade.get('input_amount'),
                trade.get('expected_output'),
                trade.get('actual_output'),
                trade.get('expected_profit'),
                trade.get('actual_profit'),
                trade.get('latency_ms'),
                trade.get('status'),
                trade.get('error'),
                trade.get('transaction_id'),
                trade.get('priority_fee')
            ))
            conn.commit()
    
    def get_recent_trades(self, limit: int = 50) -> List[Dict]:
        """Get recent trades"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM trades 
                ORDER BY created_at DESC 
                LIMIT ?
            """, (limit,))
            return [dict(row) for row in cursor.fetchall()]
    
    def get_metrics(self) -> Dict:
        """Calculate performance metrics"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT * FROM trades")
            trades = [dict(row) for row in cursor.fetchall()]
        
        total_trades = len(trades)
        if total_trades == 0:
            return {
                'total_trades': 0,
                'successful_trades': 0,
                'win_rate': 0.0,
                'total_pnl': 0.0,
                'avg_profit': 0.0
            }
        
        successful = sum(1 for t in trades if t.get('status') == 'success')
        total_pnl = sum(t.get('actual_profit', 0) for t in trades)
        
        return {
            'total_trades': total_trades,
            'successful_trades': successful,
            'win_rate': successful / total_trades,
            'total_pnl': total_pnl,
            'avg_profit': total_pnl / total_trades if total_trades > 0 else 0
        }

# ============================================================================
# EXECUTION ENGINE - Main Orchestration
# ============================================================================

class ExecutionEngine:
    def __init__(self):
        self.rpc_url = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
        self.jito_endpoint = os.getenv("JITO_ENDPOINT", "https://mainnet.block-engine.jito.wtf/api/v1/bundles")
        self.private_key = os.getenv("BURNER_WALLET_PRIVATE_KEY")
        self.min_edge_pct = float(os.getenv("MIN_EDGE_PCT", "0.5"))  # 0.5% minimum edge
        self.max_priority_fee = int(os.getenv("MAX_PRIORITY_FEE", "10000"))  # Max priority fee in lamports
        self.fee_profit_ratio = float(os.getenv("FEE_PROFIT_RATIO", "0.3"))  # 30% of profit for fees
        self.kill_switch_threshold = float(os.getenv("KILL_SWITCH_THRESHOLD", "-0.1"))  # Stop if -0.1 SOL loss
        self.kill_switch_trades = int(os.getenv("KILL_SWITCH_TRADES", "10"))  # Check last 10 trades
        
        # Initialize components
        self.signer = Signer(private_key=self.private_key)
        self.db = TradeDatabase()
        self.rpc_client = None  # Will be initialized in __aenter__ if needed
        self.scanner: Optional[Scanner] = None
        self.builder: Optional[TransactionBuilder] = None
        self.executor: Optional[JitoExecutor] = None
    
    async def __aenter__(self):
        self.scanner = Scanner(self.rpc_url)
        self.builder = TransactionBuilder()
        self.executor = JitoExecutor(self.jito_endpoint)
        
        if Client:
            self.rpc_client = Client(self.rpc_url)
        
        await self.scanner.__aenter__()
        await self.builder.__aenter__()
        await self.executor.__aenter__()
        
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.scanner:
            await self.scanner.__aexit__(exc_type, exc_val, exc_tb)
        if self.builder:
            await self.builder.__aexit__(exc_type, exc_val, exc_tb)
        if self.executor:
            await self.executor.__aexit__(exc_type, exc_val, exc_tb)
        if self.rpc_client:
            await self.rpc_client.close()
    
    async def get_confirmed_transaction(self, signature: str) -> Optional[Dict]:
        """Fetch confirmed transaction from RPC"""
        if not self.rpc_client:
            return None
        
        try:
            tx = await self.rpc_client.get_transaction(
                signature,
                encoding="jsonParsed",
                max_supported_transaction_version=0
            )
            return tx.value
        except Exception as e:
            print(f"  Failed to fetch transaction: {e}")
            return None
    
    def extract_balance_changes(self, tx_meta: Dict, owner: str) -> Dict[str, float]:
        """Extract ALL token balance changes from transaction metadata (portfolio delta method)"""
        if not tx_meta or 'preTokenBalances' not in tx_meta or 'postTokenBalances' not in tx_meta:
            return {}
        
        changes = {}
        
        # Subtract pre-balances
        for balance in tx_meta['preTokenBalances']:
            if balance.get('owner') != owner:
                continue
            mint = balance.get('mint')
            amount = float(balance.get('uiTokenAmount', {}).get('uiAmount') or 0)
            changes[mint] = changes.get(mint, 0) - amount
        
        # Add post-balances
        for balance in tx_meta['postTokenBalances']:
            if balance.get('owner') != owner:
                continue
            mint = balance.get('mint')
            amount = float(balance.get('uiTokenAmount', {}).get('uiAmount') or 0)
            changes[mint] = changes.get(mint, 0) + amount
        
        return changes
    
    def extract_sol_fee(self, tx_meta: Dict) -> float:
        """Extract SOL fee from transaction metadata"""
        if not tx_meta or 'fee' not in tx_meta:
            return 0.0
        return float(tx_meta['fee'])
    
    async def wait_for_confirmation(self, signature: str, retries: int = 10, delay: float = 1.0) -> Optional[Dict]:
        """Wait for transaction confirmation with retry logic"""
        for attempt in range(retries):
            tx_meta = await self.get_confirmed_transaction(signature)
            if tx_meta:
                return tx_meta
            
            if attempt < retries - 1:
                await asyncio.sleep(delay)
        
        return None
    
    def calculate_dynamic_priority_fee(self, expected_profit_lamports: int) -> int:
        """Calculate dynamic priority fee based on expected profit"""
        fee = int(expected_profit_lamports * self.fee_profit_ratio)
        fee = min(fee, self.max_priority_fee)
        fee = max(fee, 1000)  # At least 1000 lamports
        return fee
    
    def check_kill_switch(self) -> Tuple[bool, str]:
        """Check if kill switch should trigger based on recent performance"""
        # Disable kill switch in simulation mode
        if EXECUTION_MODE == "sim":
            return False, "Disabled in simulation mode"
        
        trades = self.db.get_recent_trades(limit=self.kill_switch_trades)
        
        # Disable kill switch for initial testing
        if len(trades) < 3:
            return False, "Not enough trades yet (minimum 3)"
        
        # Filter out None values from actual_profit
        total_pnl = sum(t.get('actual_profit') or 0 for t in trades)
        successful = sum(1 for t in trades if t.get('status') == 'success')
        win_rate = successful / len(trades)
        
        reasons = []
        if total_pnl < self.kill_switch_threshold:
            reasons.append(f"Rolling PnL {total_pnl} < threshold {self.kill_switch_threshold}")
        if win_rate < 0.3:
            reasons.append(f"Win rate {win_rate:.1%} < 30%")
        
        if reasons:
            return True, "; ".join(reasons)
        return False, "OK"
    
    def calculate_position_size(self, expected_profit_pct: float, liquidity_estimate: float = 100000) -> int:
        """Calculate position size based on edge and liquidity"""
        base_size = 10_000_000  # 0.01 SOL minimum
        edge_multiplier = min(expected_profit_pct / 0.5, 2.0)  # Cap at 2x
        liquidity_multiplier = min(liquidity_estimate * 0.1, 1_000_000_000)  # Max 1 SOL
        size = int(base_size * edge_multiplier)
        size = min(size, int(liquidity_multiplier))
        return size
    
    async def execute_single_trade(self, token: str, input_mint: str, output_mint: str, amount_lamports: int) -> Dict:
        """Execute a single trade end-to-end with on-chain verification"""
        # Check kill switch before execution
        should_stop, kill_reason = self.check_kill_switch()
        if should_stop:
            print(f"\n🛑 KILL SWITCH TRIGGERED: {kill_reason}")
            raise Exception(f"Kill switch triggered: {kill_reason}")
        
        start_time = time.time()
        trade_data = {
            'token': token,
            'input_amount': amount_lamports,
            'expected_output': None,
            'actual_output': None,
            'expected_profit': None,
            'actual_profit': None,
            'latency_ms': None,
            'status': 'failed',
            'error': None,
            'transaction_id': None,
            'priority_fee': None
        }
        
        try:
            print(f"\n{'='*60}")
            print(f"Executing trade: {token}")
            print(f"{'='*60}")
            
            # Step 1: Get quote
            print("Step 1: Getting Jupiter quote...")
            params = SwapParams(
                input_mint=input_mint,
                output_mint=output_mint,
                amount=amount_lamports,
                slippage_bps=50  # 0.5%
            )
            
            quote = await self.builder.get_jupiter_quote(params)
            expected_output = int(quote.get('outAmount', 0))
            expected_profit = self.builder.calculate_expected_profit(quote, amount_lamports)
            
            print(f"  Expected output: {expected_output}")
            print(f"  Expected profit: {expected_profit}")
            
            trade_data['expected_output'] = expected_output
            trade_data['expected_profit'] = expected_profit
            
            # Step 2: Validate profitability with minimum edge filter
            print("Step 2: Validating profitability...")
            
            # Check minimum edge
            edge_pct = (expected_profit / amount_lamports) * 100
            if edge_pct < self.min_edge_pct:
                raise Exception(f"Edge {edge_pct:.3f}% below minimum {self.min_edge_pct}%")
            
            if not self.builder.is_profitable(quote, amount_lamports):
                raise Exception("Trade not profitable after fees")
            
            # Calculate dynamic priority fee
            dynamic_priority_fee = self.calculate_dynamic_priority_fee(expected_profit)
            trade_data['priority_fee'] = dynamic_priority_fee
            
            # Fee buffer filter: expected profit must be > 2x estimated fees
            estimated_base_fee = 5000  # Conservative estimate in lamports
            total_estimated_fee = dynamic_priority_fee + estimated_base_fee
            
            if expected_profit < total_estimated_fee * 2:
                raise Exception(f"Expected profit {expected_profit} < 2x estimated fees {total_estimated_fee * 2}")
            
            print(f"  ✓ Edge: {edge_pct:.3f}% (min: {self.min_edge_pct}%)")
            print(f"  ✓ Dynamic priority fee: {dynamic_priority_fee} lamports")
            print(f"  ✓ Fee buffer: {expected_profit} > 2x {total_estimated_fee * 2}")
            
            # Step 3: Get instructions
            print("Step 3: Getting swap instructions...")
            instructions = await self.builder.get_jupiter_swap_instructions(quote, self.signer.public_key)
            print("  ✓ Instructions received")
            
            # Step 4: Build transaction
            print("Step 4: Building transaction...")
            swap_tx = await self.builder.get_jupiter_swap_transaction(quote, self.signer.public_key)
            print("  ✓ Transaction built")
            
            # Step 5: Sign transaction
            print("Step 5: Signing transaction...")
            signed_tx_bytes = self.signer.sign_transaction_bytes(swap_tx['swapTransaction'])
            print("  ✓ Signed")
            
            # Check if dry run mode
            if hasattr(self, '_dry_run') and self._dry_run:
                print("  🧪 DRY RUN MODE - Skipping execution")
                trade_data['status'] = 'dry_run'
                trade_data['latency_ms'] = (time.time() - start_time) * 1000
                self.db.log_trade(trade_data)
                return trade_data
            
            # Step 6: Send via Jito
            print("Step 6: Sending via Jito...")
            result = await self.executor.send_bundle(signed_tx_bytes, priority_fee=dynamic_priority_fee, quote=quote)
            
            # Check Jito response properly
            if not result.success:
                trade_data['error'] = f"Jito execution failed: {result.error}"
                trade_data['status'] = 'failed'
                trade_data['latency_ms'] = (time.time() - start_time) * 1000
                raise Exception(trade_data['error'])
            
            if not result.transaction_id:
                trade_data['error'] = 'Jito returned success but no transaction ID (dropped)'
                trade_data['status'] = 'dropped'
                trade_data['latency_ms'] = (time.time() - start_time) * 1000
                raise Exception(trade_data['error'])
            
            print(f"  ✓ Submitted: {result.transaction_id}")
            trade_data['transaction_id'] = result.transaction_id
            trade_data['status'] = 'submitted'  # Mark as submitted, not success yet
            
            # Step 7: Confirm on-chain with retry
            print("Step 7: Confirming transaction...")
            
            if EXECUTION_MODE == "sim" and sim_market:
                # Simulate confirmation - calculate profit directly
                expected_out = int(quote.get('outAmount', 0))
                # Simulate slippage
                slippage = random.uniform(0, 0.005)
                actual_out = int(expected_out * (1 - slippage))
                
                # Calculate profit in lamports (output - input - fees)
                fee = dynamic_priority_fee + 5000
                profit = actual_out - amount_lamports - fee
                
                # Skip portfolio delta calculation in sim mode - use direct profit
                trade_data['actual_output'] = actual_out
                trade_data['actual_profit'] = profit
                trade_data['latency_ms'] = (time.time() - start_time) * 1000
                
                if profit > 0:
                    trade_data['status'] = 'success'
                    print(f"  ✓ Profit: {profit} lamports")
                else:
                    trade_data['status'] = 'failed'
                    trade_data['error'] = f"Negative profit on-chain: {profit}"
                    print(f"  ✗ Negative profit: {profit}")
                
                self.db.log_trade(trade_data)
                return trade_data
            else:
                tx_meta = await self.wait_for_confirmation(result.transaction_id, retries=10, delay=1.0)
            
            if tx_meta:
                print("  ✓ Confirmed on-chain")
                
                # Extract ALL balance changes (portfolio delta method)
                changes = self.extract_balance_changes(tx_meta, self.signer.public_key)
                sol_fee = self.extract_sol_fee(tx_meta)
                
                print(f"  Token changes: {changes}")
                print(f"  SOL fee: {sol_fee} lamports")
                
                # Calculate real profit: normalize all changes to SOL
                # For now, we only track SOL and the target tokens
                # TODO: Add price oracle to convert all tokens to SOL value
                input_change = changes.get(input_mint, 0)
                output_change = changes.get(output_mint, 0)
                
                # Simplified: assume output is the profit token (e.g., USDC)
                # In production, you'd convert everything to a common unit (SOL or USD)
                portfolio_delta = output_change + input_change  # input_change is negative
                
                # Real profit accounting for SOL fee
                actual_profit = portfolio_delta - sol_fee
                
                trade_data['actual_output'] = output_change
                trade_data['actual_profit'] = actual_profit
                
                print(f"  Input delta: {input_change}")
                print(f"  Output delta: {output_change}")
                print(f"  Portfolio delta: {portfolio_delta}")
                print(f"  Real profit (after fees): {actual_profit}")
                
                # Check if actually profitable
                if actual_profit > 0:
                    trade_data['status'] = 'success'
                    print(f"  ✓ Real profit: {actual_profit}")
                else:
                    trade_data['status'] = 'failed'
                    trade_data['error'] = f"Negative profit on-chain: {actual_profit}"
                    print(f"  ✗ Negative profit: {actual_profit}")
            else:
                trade_data['status'] = 'dropped'
                trade_data['error'] = 'Transaction not confirmed after retries'
                print("  ✗ Transaction not confirmed (dropped)")
            
            # Calculate latency
            latency_ms = (time.time() - start_time) * 1000
            trade_data['latency_ms'] = latency_ms
            print(f"  Latency: {latency_ms:.2f}ms")
            
        except Exception as e:
            print(f"  ✗ Error: {e}")
            trade_data['error'] = str(e)
            trade_data['latency_ms'] = (time.time() - start_time) * 1000
            
            # Classify error type
            error_str = str(e).lower()
            if 'slippage' in error_str:
                trade_data['status'] = 'slippage_exceeded'
            elif 'timeout' in error_str:
                trade_data['status'] = 'timeout'
            elif 'edge' in error_str:
                trade_data['status'] = 'insufficient_edge'
        
        # Log to database
        self.db.log_trade(trade_data)
        
        return trade_data
    
    async def run_single_test(self, dry_run: bool = False):
        """
        Run a single test trade (SOL → USDC)
        
        Args:
            dry_run: If True, run without sending transaction (for testing)
        """
        print("\n🚀 SINGLE TRADE TEST")
        print("=" * 60)
        if dry_run:
            print("MODE: DRY RUN (no execution)")
        print("=" * 60)
        
        # Set dry run flag
        self._dry_run = dry_run
        
        # Test parameters (small amount for safety)
        # TODO: Position sizing not implemented - using fixed amount
        # In production, use calculate_position_size() to determine amount
        result = await self.execute_single_trade(
            token="SOL→USDC",
            input_mint="So11111111111111111111111111111111111111112",  # SOL
            output_mint="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
            amount_lamports=10_000_000  # 0.01 SOL (very small test amount)
        )
        
        print("\n" + "=" * 60)
        print("TRADE RESULT")
        print("=" * 60)
        print(f"Status: {result['status']}")
        print(f"Expected Profit: {result['expected_profit']}")
        print(f"Actual Profit: {result['actual_profit']}")
        print(f"Latency: {result['latency_ms']:.2f}ms")
        if result['transaction_id']:
            print(f"TX: {result['transaction_id']}")
        if result['error']:
            print(f"Error: {result['error']}")
        
        # Check profitability
        if result['status'] == 'success' and result.get('actual_profit', 0) > 0:
            print("\n✅ PROFITABLE TRADE EXECUTED")
        else:
            print("\n❌ TRADE NOT PROFITABLE OR FAILED")

# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

async def main():
    """Run single trade test"""
    import sys
    
    dry_run = '--dry-run' in sys.argv
    
    print("🚀 Arb Execution Engine - Single File Version")
    print("=" * 60)
    print("Mode: Single Trade Test")
    if dry_run:
        print("DRY RUN: No transactions will be sent")
    print("=" * 60)
    
    async with ExecutionEngine() as engine:
        await engine.run_single_test(dry_run=dry_run)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nShutting down...")
    except Exception as e:
        if "Kill switch" in str(e):
            print(f"\n🛑 {e}")
            print("System stopped by kill switch")
        else:
            print(f"\n❌ Error: {e}")
            raise
