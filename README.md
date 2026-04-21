# Arb Execution Engine

A latency-aware execution engine with multi-exchange support:
- Solana arbitrage (Jupiter + Jito)
- Gate.io spot trading with edge detection
- Portfolio delta accounting
- Risk controls and kill switches

## ⚠️ Status

This is a truthful execution framework, not a profitable strategy out of the box.

- ✅ Deterministic execution pipeline
- ✅ Portfolio delta accounting (partial normalization)
- ✅ Dry-run mode for safe testing
- ✅ Gate.io integration with edge detection
- ❌ No guaranteed economic edge
- ❌ Requires proper environment + tuning for production

## 🧠 Architecture

```
arb_engine.py         # Solana execution engine (single-file)
gateio_execution.py   # Gate.io spot trading with edge detection
api_server.py         # FastAPI wrapper (optional control plane)
openapi.yaml          # API schema
trades.db             # SQLite trade log (gitignored)
gateio_trades.db      # Gate.io trade log (gitignored)
```

Execution pipeline:
```
quote → validate → build → sign → send → confirm → measure
```

## ⚙️ Features

### Execution
- Jupiter routing integration
- Jito bundle submission
- Retry logic (10x confirmation attempts)
- Failure classification: submitted / success / failed / dropped / timeout / slippage_exceeded / insufficient_edge

### Accounting
- Portfolio delta method (tracks all token changes)
- SOL fee extraction (base + priority)
- Latency measurement

### Risk Controls
- Minimum edge threshold
- Fee buffer filter (>2x estimated fees)
- Kill switch (per-trade basis)
- Dynamic priority fees

### API Layer (optional)
- FastAPI wrapper (api_server.py)
- OpenAPI schema included
- Endpoints: /execute, /trades, /metrics, /health

## 🚀 Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure environment
```bash
cp .env.example .env
```

Add your credentials:
```bash
BURNER_WALLET_PRIVATE_KEY=<base64_key>
SOLANA_RPC_URL=<your_rpc>
JITO_ENDPOINT=<your_jito_endpoint>
EXECUTION_MODE=sim  # "sim" for simulation, "real" for mainnet
```

### 3. Choose execution mode

**Simulation mode (default):**
- No network access required
- Deterministic market simulation
- Realistic failure modes (5% failure rate, slippage, latency)
- Perfect for testing and dashboard development

```bash
EXECUTION_MODE=sim python arb_engine.py
```

**Real mode:**
- Requires mainnet access
- Real Jupiter API calls
- Real Jito bundle submission
- Actual SOL transactions

```bash
EXECUTION_MODE=real python arb_engine.py
```

## Gate.io Execution

The engine also supports Gate.io spot trading with edge detection:

### Setup

Add Gate.io credentials to `.env`:
```bash
GATEIO_API_KEY=your_api_key
GATEIO_API_SECRET=your_api_secret
MAX_RISK_PER_TRADE=0.02  # 2% risk per trade
```

### Run Gate.io execution

```bash
python gateio_execution.py
```

This will:
- Fetch account balance
- Calculate edge from orderbook depth
- Execute trades only when edge > minimum threshold
- Track PnL with portfolio delta accounting
- Kill switch if rolling PnL drops below threshold

### Risk Controls

- Minimum edge filter (0.5% default)
- Maximum risk per trade (2% default)
- Kill switch on rolling losses
- Orderbook depth validation

## 🧪 Testing

### Dry run (recommended)
```bash
python arb_engine.py --dry-run
```

What it tests:
- Full pipeline (except send)
- Transaction building + signing
- Logging + metrics

Expected output:
- Keypair generation
- Quote attempt
- Trade result (likely failure if no network access to Jupiter)

## 🌐 API Server

### Run server
```bash
python api_server.py
# or
uvicorn api_server:app --host 0.0.0.0 --port 8000
```

### OpenAPI docs
```
http://localhost:8000/docs
```

## ⚠️ Known Limitations

- No price oracle → portfolio delta not normalized across tokens
- Scanner uses public spreads (no real edge)
- No continuous loop / strategy engine
- No multi-region latency optimization
- Jupiter API required (network-dependent)
- Not tested with real capital in this repo

## 🔐 Security

- Never commit private keys
- Use burner wallets for testing
- Do not expose execution endpoints publicly without auth + rate limiting

## 💡 Notes

This repo is intended as:
**a foundation for building execution systems, not a finished trading bot**

If you deploy this live without adding edge, you will lose money — just more accurately than before.

## 📜 License

MIT

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

**Public Endpoints (no auth required):**
- `GET /health` - Health check
- `GET /metrics` - Performance metrics
- `GET /metrics/live` - Live metrics with rolling PnL and recent trades

**Protected Endpoints (requires x-api-key header):**
- `POST /trade` - Execute a single arbitrage trade
- `GET /trades` - Get trade history
- `GET /trades/{id}` - Get specific trade by ID

**API Key Authentication:**
```bash
curl -X POST http://localhost:8000/trade \
  -H "Content-Type: application/json" \
  -H "x-api-key: your_api_key" \
  -d '{
    "inputMint": "So11111111111111111111111111111111111111112",
    "outputMint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "amountLamports": 10000000,
    "slippageBps": 50,
    "dryRun": false
  }'
```
