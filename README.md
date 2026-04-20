# Arb Execution Engine

Autonomous Solana arbitrage execution engine with on-chain verification and truthful accounting.

## Architecture

**This is the execution engine** - where money is made (or lost, truthfully).

- Strategy engine: Opportunity detection, simulation
- Tx builder: Jupiter swap instructions with priority fees
- Signer: Burner wallet keypair
- Jito bundle sender: MEV-protected execution
- On-chain verification: Confirmed transaction parsing
- Truthful accounting: Real profit calculation from chain data

## Key Principle

**Quote ≠ Execution**

Only confirmed on-chain transactions tell the truth. Everything else is speculation.

## Truthful Execution System

The system tracks:
- Transaction signature
- Latency (ms)
- Expected profit
- Actual profit (from on-chain portfolio delta method)
- SOL fees (extracted from transaction metadata)
- Dynamic priority fee (calculated as % of expected profit)
- Status (submitted / success / failed / dropped / killed / slippage_exceeded / timeout / insufficient_edge)

**Portfolio Delta Method**: Tracks ALL token balance changes, not just input/output. This accounts for Jupiter routing through multiple pools, intermediate tokens, SOL wrapping, and dust accounts.

Real profit = portfolio_delta - sol_fee

**Dynamic Priority Fees**: Fee scales with expected profit (default 30%), capped at maximum. This ensures competitive bidding in Jito auctions without overpaying on small edges.

**Kill Switch**: Automatically stops execution if:
- Rolling PnL over last N trades falls below threshold
- Win rate drops below 30%

## Components (Single File)

All components consolidated into `arb_engine.py`:
- Scanner - Opportunity detection (Dexscreener)
- TransactionBuilder - Jupiter quote + transaction construction
- Signer - Burner wallet keypair and signing
- JitoExecutor - Jito bundle submission
- ExecutionEngine - Main orchestration with on-chain verification
- TradeDatabase - SQLite trade tracking (truth layer)

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Generate burner wallet (if needed)
python -c "from arb_engine import Signer; s = Signer(); s.save_keypair('burner_wallet.json'); print(f'Public: {s.public_key}'); print(f'Private (base64): {s.private_key_base64}')"

# Set environment variables
cp .env.example .env
# Edit .env with your private key

# Option 1: Run as script (single trade test)
python arb_engine.py --dry-run  # Dry run mode
python arb_engine.py            # Real execution

# Option 2: Run as API server
uvicorn api_server:app --host 0.0.0.0 --port 8000
# OpenAPI docs: http://localhost:8000/docs
```

## Environment

```bash
SOLANA_RPC_URL=https://api.mainnet-beta.solana.com
JITO_ENDPOINT=https://mainnet.block-engine.jito.wtf/api/v1/bundles
BURNER_WALLET_PRIVATE_KEY=your_key_here
MIN_EDGE_PCT=0.5  # Minimum edge percentage (raised for reality)
MAX_PRIORITY_FEE=10000  # Maximum priority fee in lamports
FEE_PROFIT_RATIO=0.3  # Use 30% of expected profit for priority fee
KILL_SWITCH_THRESHOLD=-0.1  # Stop if losing more than 0.1 SOL in last N trades
KILL_SWITCH_TRADES=10  # Check last 10 trades for kill switch
```

## Known Limitations

**Critical Issues to Address:**

1. **Portfolio Delta Normalization**: Currently tracks token balance changes but does not convert to a common unit (SOL or USD). This makes profit calculation meaningless across different tokens. Needs a price oracle.

2. **Position Sizing Not Implemented**: The `calculate_position_size()` method exists but is not used in the execution path. Trades use fixed amounts (0.01 SOL). Position sizing must feed directly into quote requests.

3. **Scanner Edge is Naive**: The scanner uses simple price spreads from Dexscreener, which are public and already exploited. Real arbitrage requires more sophisticated edge detection.

4. **No Price Oracle**: Without real-time price data, you cannot normalize portfolio delta across tokens or accurately calculate profit.

5. **Kill Switch Only Works Per-Trade**: The kill switch raises an exception to stop a single trade, but in a continuous loop, you'd need to break the loop entirely.

**Status:** This is a truthful execution framework with proper measurement, but **zero economic edge**. It will accurately tell you when you lose money.

## Testing Strategy

**Test 1: Dry Run Pipeline**
```bash
python arb_engine.py --dry-run
```
Verifies:
- Jupiter quote API works
- Instructions build correctly
- Transaction serializes
- Profit calculation runs

**Test 2: Single Real Transaction**
- Use 0.001-0.005 SOL
- High priority fee to force inclusion
- Goal: Prove you can land a transaction (not profit, just landing)

**Test 3: Batch of 10 Trades**
- Log expected vs actual profit
- Track latency, fees, status
- Analyze: dropped vs landed, profitable vs unprofitable

## Execution Pipeline

1. Get Jupiter quote
2. Validate profitability with minimum edge filter
3. Apply fee buffer filter (expected profit > 2x estimated fees)
4. Get swap instructions
5. Build transaction
6. Sign with burner wallet
7. Send via Jito bundle (check for error vs result)
8. Wait for confirmation with retry logic (10 retries, 1s delay)
9. Extract ALL token balance changes (portfolio delta method)
10. Extract SOL fee from transaction metadata
11. Calculate real profit: portfolio_delta - sol_fee
12. Log to database

## Failure Classification

- `submitted` - Transaction submitted to Jito (not yet confirmed)
- `success` - Trade executed profitably on-chain
- `failed` - General execution failure
- `dropped` - Transaction submitted but not confirmed
- `slippage_exceeded` - Slippage too high
- `timeout` - Execution timed out
- `insufficient_edge` - Edge below minimum threshold

## Security

- Burner wallet with limited funds
- No private keys in frontend
- Server-side signing only
- Replaceable keypairs
- Minimum edge filter prevents bad trades

## First Profitable Trade Checklist

Before running:
- [ ] Burner wallet funded (small amount)
- [ ] Private key in .env
- [ ] solana-py installed (for on-chain verification)

After execution:
- [ ] Transaction signature confirmed
- [ ] Latency < 500ms
- [ ] Actual profit > 0
- [ ] Status = success

## API Endpoints

- `GET /signals` - Current opportunities
- `GET /trades` - Trade history
- `GET /metrics` - Performance metrics
- `POST /manual-trade` - Manual execution override
