# Arb Execution Engine

Autonomous Solana arbitrage execution engine with Jito bundle support.

## Architecture

**This is the execution engine** - where money is made.

- Strategy engine: Opportunity detection, simulation
- Tx builder: Jupiter swap instructions
- Signer: Burner wallet keypair
- Jito bundle sender: MEV-protected execution
- PnL tracking: Real performance data

## Key Principle

**Execution consistency > strategy intelligence**

Same execution path every time. No human latency noise. Real data → real improvement.

## Components

- `scanner/` - Opportunity detection (Dexscreener, Jupiter)
- `builder/` - Transaction construction (Jupiter, Jito)
- `signer/` - Keypair management and signing
- `executor/` - Jito bundle submission
- `api/` - FastAPI for UI connection
- `db/` - Trade and PnL tracking

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Generate burner wallet
python scripts/generate_keypair.py

# Run execution engine
python main.py
```

## Environment

```bash
SOLANA_RPC_URL=https://api.mainnet-beta.solana.com
JITO_ENDPOINT=https://mainnet.block-engine.jito.wtf/api/v1/transactions
BURNER_WALLET_PRIVATE_KEY=your_key_here
```

## API Endpoints

- `GET /signals` - Current opportunities
- `GET /trades` - Trade history
- `GET /metrics` - Performance metrics
- `POST /manual-trade` - Manual execution override

## Security

- Burner wallet with limited funds
- No private keys in frontend
- Server-side signing only
- Replaceable keypairs
