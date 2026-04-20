"""
Main Execution Engine - Orchestrates the complete arbitrage pipeline
"""

import asyncio
import os
from typing import Optional, List
from datetime import datetime
from scanner import Scanner, Opportunity
from builder import TransactionBuilder, SwapParams, BuiltTransaction
from signer import Signer
from executor import JitoExecutor, ExecutionResult
from dotenv import load_dotenv

load_dotenv()

class ExecutionEngine:
    def __init__(self):
        self.rpc_url = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
        self.jito_endpoint = os.getenv("JITO_ENDPOINT", "https://mainnet.block-engine.jito.wtf/api/v1/transactions")
        self.private_key = os.getenv("BURNER_WALLET_PRIVATE_KEY")
        
        # Initialize components
        self.signer = Signer(private_key=self.private_key)
        self.scanner: Optional[Scanner] = None
        self.builder: Optional[TransactionBuilder] = None
        self.executor: Optional[JitoExecutor] = None
        
        # Track executed trades
        self.executed_trades: List[Dict] = []
    
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
    
    async def execute_opportunity(self, opportunity: Opportunity, amount_sol: float = 0.1) -> Optional[ExecutionResult]:
        """
        Execute a single arbitrage opportunity
        
        Args:
            opportunity: The opportunity to execute
            amount_sol: Amount of SOL to trade (in SOL, not lamports)
        
        Returns execution result
        """
        try:
            print(f"\nExecuting opportunity: {opportunity.token}")
            print(f"  Spread: {opportunity.spread_pct:.2f}%")
            print(f"  Path: {opportunity.dex_a} → {opportunity.dex_b}")
            
            # 1. Build swap transaction
            # Note: This is a simplified example. In reality, you'd need to:
            # - Get actual token addresses
            # - Calculate optimal routing
            # - Handle both directions of the arbitrage
            
            # For now, we'll skip actual execution and just log
            print("  Building transaction...")
            
            # In production:
            # params = SwapParams(...)
            # quote = await self.builder.get_jupiter_quote(params)
            # built_tx = await self.builder.get_jupiter_swap_transaction(quote, self.signer.public_key)
            # signed_tx = self.signer.sign_transaction(built_tx.transaction)
            # result = await self.executor.submit_bundle([signed_tx])
            
            # Mock result for now
            result = ExecutionResult(
                success=True,
                transaction_id="mock_tx_id",
                error=None,
                executed_at=datetime.utcnow()
            )
            
            # Track trade
            self.executed_trades.append({
                "opportunity": opportunity.__dict__,
                "result": {
                    "success": result.success,
                    "transaction_id": result.transaction_id,
                    "executed_at": result.executed_at.isoformat()
                },
                "amount_sol": amount_sol
            })
            
            print(f"  Result: {'SUCCESS' if result.success else 'FAILED'}")
            if result.transaction_id:
                print(f"  TX: {result.transaction_id}")
            
            return result
            
        except Exception as e:
            print(f"  Error: {e}")
            return ExecutionResult(
                success=False,
                transaction_id=None,
                error=str(e),
                executed_at=datetime.utcnow()
            )
    
    async def run_scan_and_execute(self, min_spread_pct: float = 0.5, max_trades: int = 5):
        """
        Scan for opportunities and execute them
        
        Args:
            min_spread_pct: Minimum spread percentage to execute
            max_trades: Maximum number of trades to execute in one scan
        """
        print(f"\n{'='*60}")
        print(f"Scanning for opportunities (min spread: {min_spread_pct}%)")
        print(f"{'='*60}")
        
        opportunities = await self.scanner.detect_opportunities(min_spread_pct=min_spread_pct)
        
        print(f"\nFound {len(opportunities)} opportunities")
        
        if not opportunities:
            print("No opportunities found")
            return
        
        # Execute top opportunities
        trades_executed = 0
        for opp in opportunities[:max_trades]:
            if trades_executed >= max_trades:
                break
            
            result = await self.execute_opportunity(opp)
            if result and result.success:
                trades_executed += 1
    
    async def run_continuous(self, scan_interval_seconds: int = 10):
        """
        Run continuous scan and execute loop
        
        Args:
            scan_interval_seconds: Time between scans
        """
        print(f"\nStarting continuous execution loop (interval: {scan_interval_seconds}s)")
        print(f"Press Ctrl+C to stop\n")
        
        try:
            while True:
                await self.run_scan_and_execute()
                print(f"\nWaiting {scan_interval_seconds}s before next scan...")
                await asyncio.sleep(scan_interval_seconds)
        except KeyboardInterrupt:
            print("\n\nStopping execution engine")
            print(f"\nTotal trades executed: {len(self.executed_trades)}")
            for trade in self.executed_trades:
                print(f"  - {trade['opportunity']['token']}: {trade['result']['success']}")


async def main():
    """Run the execution engine"""
    async with ExecutionEngine() as engine:
        # Run single scan
        await engine.run_scan_and_execute(min_spread_pct=0.5)
        
        # Or run continuous:
        # await engine.run_continuous(scan_interval_seconds=10)


if __name__ == "__main__":
    asyncio.run(main())
