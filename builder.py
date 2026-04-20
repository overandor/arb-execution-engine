"""
Transaction Builder - Constructs Jupiter swap instructions with priority fees
"""

import asyncio
import aiohttp
from typing import Dict, Optional, List
from dataclasses import dataclass
from base64 import b64encode

@dataclass
class SwapParams:
    input_mint: str
    output_mint: str
    amount: int
    slippage_bps: int = 50  # 0.5% (more conservative)

@dataclass
class BuiltTransaction:
    transaction: str  # Base64 encoded
    last_valid_block_height: int
    expected_output: int
    expected_profit: float

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

    async def get_jupiter_quote(self, params: SwapParams) -> Dict:
        """
        Get swap quote from Jupiter API
        
        Returns quote with route information
        """
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
        """
        Get swap instructions from Jupiter (not full tx)
        
        Returns instructions ready for transaction building
        """
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
        """
        Get pre-built swap transaction from Jupiter
        
        Returns base64-encoded transaction ready to sign
        """
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
        """
        Calculate expected profit after fees and slippage
        
        Args:
            quote: Jupiter quote response
            input_amount: Input amount in lamports
        
        Returns expected profit in lamports
        """
        out_amount = int(quote.get('outAmount', 0))
        in_amount = int(quote.get('inAmount', input_amount))
        
        # Simple profit calculation (can be refined with gas fees)
        profit = out_amount - in_amount
        
        return float(profit)

    def is_profitable(self, quote: Dict, input_amount: int, min_profit_lamports: int = 1000) -> bool:
        """
        Post-build validation: check if trade is actually profitable
        
        Args:
            quote: Jupiter quote response
            input_amount: Input amount in lamports
            min_profit_lamports: Minimum profit threshold
        
        Returns True if profitable after validation
        """
        profit = self.calculate_expected_profit(quote, input_amount)
        
        # Account for priority fees (estimate)
        estimated_fees = 5000  # Conservative estimate in lamports
        
        net_profit = profit - estimated_fees
        
        return net_profit >= min_profit_lamports


async def main():
    """Test builder"""
    async with TransactionBuilder() as builder:
        # Example: SOL → USDC swap
        params = SwapParams(
            input_mint="So11111111111111111111111111111111111111112",  # SOL
            output_mint="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
            amount=100_000_000,  # 0.1 SOL in lamports (small test amount)
            slippage_bps=50  # 0.5%
        )
        
        try:
            quote = await builder.get_jupiter_quote(params)
            print("Quote:", quote.get('outAmount'), "output")
            
            # Validate profitability
            is_profitable = builder.is_profitable(quote, params.amount)
            print(f"Is profitable: {is_profitable}")
            
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
