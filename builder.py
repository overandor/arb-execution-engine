"""
Transaction Builder - Constructs Jupiter swap instructions and Jito bundles
"""

import asyncio
import aiohttp
from typing import Dict, Optional
from dataclasses import dataclass
from base64 import b64encode

@dataclass
class SwapParams:
    input_mint: str
    output_mint: str
    amount: int
    slippage_bps: int = 100  # 1%

@dataclass
class BuiltTransaction:
    transaction: str  # Base64 encoded
    last_valid_block_height: int
    prioritization_fee: int

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
        
        Returns quote with swap instructions
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
            return await response.json()

    async def get_jupiter_swap_transaction(self, quote: Dict, user_public_key: str) -> BuiltTransaction:
        """
        Get the actual swap transaction from Jupiter
        
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
            
            return BuiltTransaction(
                transaction=data['swapTransaction'],
                last_valid_block_height=data.get('lastValidBlockHeight', 0),
                prioritization_fee=data.get('prioritizationFeeLamports', 0)
            )

    async def build_jito_bundle(self, transactions: list, tip_lamports: int = 10000) -> Dict:
        """
        Build a Jito bundle for MEV-protected execution
        
        Args:
            transactions: List of base64-encoded transactions
            tip_lamports: Tip amount for Jito validators
        
        Returns bundle data ready for submission
        """
        # Jito bundle format
        bundle = {
            "transactions": transactions,
            "tip_lamports": tip_lamports,
            "revert_on_fail": True
        }
        
        return bundle


async def main():
    """Test builder"""
    async with TransactionBuilder() as builder:
        # Example: SOL → USDC swap
        params = SwapParams(
            input_mint="So11111111111111111111111111111111111111112",  # SOL
            output_mint="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
            amount=1_000_000_000,  # 1 SOL in lamports
            slippage_bps=100  # 1%
        )
        
        quote = await builder.get_jupiter_quote(params)
        print("Quote:", json.dumps(quote, indent=2))

if __name__ == "__main__":
    import json
    asyncio.run(main())
