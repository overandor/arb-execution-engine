"""
Main Execution Engine - Real arbitrage pipeline with on-chain verification
"""

import asyncio
import os
import time
from typing import Optional
from datetime import datetime
from scanner import Scanner, Opportunity
from builder import TransactionBuilder, SwapParams
from signer import Signer
from executor import JitoExecutor, ExecutionResult
from database import TradeDatabase
from dotenv import load_dotenv

try:
    from solana.rpc.api import Client
    from solana.rpc.types import TxOpts
except ImportError:
    Client = None
    print("⚠️  solana-py not installed. Transaction confirmation disabled.")

load_dotenv()

class ExecutionEngine:
    def __init__(self):
        self.rpc_url = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
        self.jito_endpoint = os.getenv("JITO_ENDPOINT", "https://mainnet.block-engine.jito.wtf/api/v1/bundles")
        self.private_key = os.getenv("BURNER_WALLET_PRIVATE_KEY")
        self.min_edge_pct = float(os.getenv("MIN_EDGE_PCT", "0.5"))  # 0.5% minimum edge (raised from 0.2%)
        self.max_priority_fee = int(os.getenv("MAX_PRIORITY_FEE", "10000"))  # Max priority fee in lamports
        self.fee_profit_ratio = float(os.getenv("FEE_PROFIT_RATIO", "0.3"))  # 30% of profit for fees
        self.kill_switch_threshold = float(os.getenv("KILL_SWITCH_THRESHOLD", "-0.1"))  # Stop if -0.1 SOL loss
        self.kill_switch_trades = int(os.getenv("KILL_SWITCH_TRADES", "10"))  # Check last 10 trades
        
        # Initialize components
        self.signer = Signer(private_key=self.private_key)
        self.db = TradeDatabase()
        self.rpc_client = Client(self.rpc_url) if Client else None
        self.scanner: Optional[Scanner] = None
        self.builder: Optional[TransactionBuilder] = None
        self.executor: Optional[JitoExecutor] = None
    
    async def __aenter__(self):
        self.scanner = Scanner(self.rpc_url)
        self.builder = TransactionBuilder()
        self.executor = JitoExecutor(self.jito_endpoint)
        
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
    
    def get_confirmed_transaction(self, signature: str) -> Optional[Dict]:
        """
        Fetch confirmed transaction from RPC
        
        Args:
            signature: Transaction signature
        
        Returns transaction metadata or None if not found
        """
        if not self.rpc_client:
            return None
        
        try:
            tx = self.rpc_client.get_transaction(
                signature,
                encoding="jsonParsed",
                max_supported_transaction_version=0
            )
            return tx.value
        except Exception as e:
            print(f"  Failed to fetch transaction: {e}")
            return None
    
    def extract_balance_changes(self, tx_meta: Dict, owner: str) -> Dict[str, float]:
        """
        Extract ALL token balance changes from transaction metadata (portfolio delta method)
        
        Args:
            tx_meta: Transaction metadata
            owner: Wallet address
        
        Returns dict of mint -> balance change (positive = received, negative = sent)
        """
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
        """
        Extract SOL fee from transaction metadata
        
        Args:
            tx_meta: Transaction metadata
        
        Returns SOL fee in lamports
        """
        if not tx_meta or 'fee' not in tx_meta:
            return 0.0
        return float(tx_meta['fee'])
    
    def wait_for_confirmation(self, signature: str, retries: int = 10, delay: float = 1.0) -> Optional[Dict]:
        """
        Wait for transaction confirmation with retry logic
        
        Args:
            signature: Transaction signature
            retries: Number of retries
            delay: Delay between retries in seconds
        
        Returns transaction metadata or None if not confirmed
        """
        for attempt in range(retries):
            tx_meta = self.get_confirmed_transaction(signature)
            if tx_meta:
                return tx_meta
            
            if attempt < retries - 1:
                time.sleep(delay)
        
        return None
    
    def calculate_dynamic_priority_fee(self, expected_profit_lamports: int) -> int:
        """
        Calculate dynamic priority fee based on expected profit
        
        Args:
            expected_profit_lamports: Expected profit in lamports
        
        Returns priority fee in lamports
        """
        # Calculate fee as percentage of expected profit
        fee = int(expected_profit_lamports * self.fee_profit_ratio)
        
        # Cap at maximum
        fee = min(fee, self.max_priority_fee)
        
        # Ensure minimum fee
        fee = max(fee, 1000)  # At least 1000 lamports
        
        return fee
    
    def check_kill_switch(self) -> tuple[bool, str]:
        """
        Check if kill switch should trigger based on recent performance
        
        Returns (should_stop, reason)
        """
        trades = self.db.get_recent_trades(limit=self.kill_switch_trades)
        
        if len(trades) < self.kill_switch_trades:
            return False, "Not enough trades yet"
        
        # Calculate rolling PnL
        total_pnl = sum(t.get('actual_profit', 0) for t in trades)
        
        if total_pnl < self.kill_switch_threshold:
            return True, f"Rolling PnL {total_pnl} below threshold {self.kill_switch_threshold}"
        
        # Check win rate
        successful = sum(1 for t in trades if t.get('status') == 'success')
        win_rate = successful / len(trades)
        
        if win_rate < 0.3:  # Less than 30% success rate
            return True, f"Win rate {win_rate:.2%} below 30%"
        
        return False, "System healthy"
    
    def calculate_position_size(self, expected_profit_pct: float, liquidity_estimate: float = 100000) -> int:
        """
        Calculate position size based on edge and liquidity
        
        Args:
            expected_profit_pct: Expected profit as percentage
            liquidity_estimate: Estimated liquidity in base token
        
        Returns position size in lamports
        """
        # Base size
        base_size = 10_000_000  # 0.01 SOL minimum
        
        # Scale with edge (more edge = more size)
        edge_multiplier = min(expected_profit_pct / 0.5, 2.0)  # Cap at 2x
        
        # Scale with liquidity (but cap at 10% of liquidity)
        liquidity_multiplier = min(liquidity_estimate * 0.1, 1_000_000_000)  # Max 1 SOL
        
        # Calculate final size
        size = int(base_size * edge_multiplier)
        size = min(size, int(liquidity_multiplier))
        
        return size
    
    async def execute_single_trade(self, token: str, input_mint: str, output_mint: str, amount_lamports: int) -> Dict:
        """
        Execute a single trade end-to-end with on-chain verification
        
        Args:
            token: Token symbol
            input_mint: Input token mint address
            output_mint: Output token mint address
            amount_lamports: Amount in lamports
        
        Returns trade result
        """
        # Check kill switch before execution
        should_stop, kill_reason = self.check_kill_switch()
        if should_stop:
            print(f"\n🛑 KILL SWITCH TRIGGERED: {kill_reason}")
            return {
                'status': 'killed',
                'error': kill_reason,
                'token': token,
                'input_amount': amount_lamports
            }
        
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
            
            # Step 6: Send via Jito
            print("Step 6: Sending via Jito...")
            result = await self.executor.send_bundle(signed_tx_bytes)
            
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
            tx_meta = self.wait_for_confirmation(result.transaction_id, retries=10, delay=1.0)
            
            if tx_meta:
                print("  ✓ Confirmed on-chain")
                
                # Extract ALL balance changes (portfolio delta method)
                changes = self.extract_balance_changes(tx_meta, self.signer.public_key)
                sol_fee = self.extract_sol_fee(tx_meta)
                
                print(f"  Token changes: {changes}")
                print(f"  SOL fee: {sol_fee} lamports")
                
                # Calculate real profit: output_received - input_spent - sol_fee
                input_change = changes.get(input_mint, 0)
                output_change = changes.get(output_mint, 0)
                
                # Portfolio delta: sum of all changes (should be net profit/loss)
                portfolio_delta = sum(changes.values())
                
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
    
    async def run_single_test(self):
        """
        Run a single test trade (SOL → USDC)
        
        This is the FIRST PROFITABLE TRADE CHECKLIST item
        """
        print("\n🚀 SINGLE TRADE TEST")
        print("=" * 60)
        
        # Test parameters (small amount for safety)
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


async def main():
    """Run single trade test"""
    async with ExecutionEngine() as engine:
        await engine.run_single_test()


if __name__ == "__main__":
    asyncio.run(main())
