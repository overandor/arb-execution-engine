"""
Opportunity Scanner - Detects arbitrage opportunities across DEXes
"""

import asyncio
import aiohttp
from typing import List, Dict, Optional
from dataclasses import dataclass
from datetime import datetime
import json

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
        """
        Detect arbitrage opportunities across DEXes
        
        Returns opportunities where spread > min_spread_pct
        """
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

            # Find min and max prices
            prices = [(p['dexId'], float(p['priceUsd']), p.get('liquidity', {}).get('usd', 0)) for p in dex_pairs if p.get('priceUsd')]
            
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

        # Sort by spread descending
        opportunities.sort(key=lambda x: x.spread_pct, reverse=True)
        
        return opportunities

    async def scan_loop(self, interval_seconds: int = 5, callback=None):
        """
        Continuous scanning loop
        
        Args:
            interval_seconds: Time between scans
            callback: Async function to call with opportunities
        """
        while True:
            try:
                opportunities = await self.detect_opportunities()
                if callback:
                    await callback(opportunities)
            except Exception as e:
                print(f"Scan error: {e}")
            
            await asyncio.sleep(interval_seconds)


async def main():
    """Test scanner"""
    async with Scanner("https://api.mainnet-beta.solana.com") as scanner:
        opportunities = await scanner.detect_opportunities(min_spread_pct=0.5)
        
        print(f"Found {len(opportunities)} opportunities:")
        for opp in opportunities[:5]:
            print(f"  {opp.token}: {opp.spread_pct:.2f}% spread ({opp.dex_a} → {opp.dex_b})")

if __name__ == "__main__":
    asyncio.run(main())
