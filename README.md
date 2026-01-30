# PolyClaw

**Trading-enabled Polymarket skill for OpenClaw.**

Browse prediction markets, execute trades on-chain, and discover hedging opportunities using LLM-powered analysis. Full trading capability via split + CLOB execution on Polygon.

> **Disclaimer:** This software is provided as-is for educational and experimental purposes. It is not financial advice. Trading prediction markets involves risk of loss. This code has not been audited. Use at your own risk and only with funds you can afford to lose.

## Features

### Market Browsing
- `polyclaw markets trending` — Top markets by 24h volume
- `polyclaw markets search "query"` — Search markets by keyword
- `polyclaw market <id>` — Market details with prices

### Trading
- `polyclaw buy <market_id> YES <amount>` — Buy YES position
- `polyclaw buy <market_id> NO <amount>` — Buy NO position
- Split + CLOB execution (split USDC → YES+NO, sell unwanted side)

### Position Tracking
- `polyclaw positions` — List open positions with live P&L
- `polyclaw position <id>` — Detailed position view
- Positions tracked locally in `~/.openclaw/polyclaw/positions.json`

### Wallet Management
- `polyclaw wallet status` — Show address, POL/USDC.e balances
- `polyclaw wallet approve` — Set Polymarket contract approvals (one-time)

### Hedge Discovery
- `polyclaw hedge scan` — Scan trending markets for hedging opportunities
- `polyclaw hedge scan --query "topic"` — Scan markets matching a query
- `polyclaw hedge analyze <id1> <id2>` — Analyze specific market pair

Uses LLM-powered contrapositive logic to find covering portfolios. Only logically necessary implications are accepted — correlations and "likely" relationships are rejected.

**Coverage tiers:** T1 (≥95%), T2 (90-95%), T3 (85-90%)

## Quick Start

### 1. Install Skill

Copy the skill to your OpenClaw skills directory:

```bash
cp -r polyclaw ~/.openclaw/skills/
```

Then install dependencies:

```bash
cd ~/.openclaw/skills/polyclaw
uv sync
```

### 2. Configure Environment Variables

Add the following to your `openclaw.json` under `skills.entries.polyclaw.env`:

```json
"polyclaw": {
  "enabled": true,
  "env": {
    "CHAINSTACK_NODE": "https://polygon-mainnet.core.chainstack.com/YOUR_KEY",
    "POLYCLAW_PRIVATE_KEY": "0x...",
    "OPENROUTER_API_KEY": "sk-or-v1-...",
    "HTTPS_PROXY": "http://user:pass@proxy:port"
  }
}
```

**Security Warning:** Keep only small amounts in this wallet. Withdraw regularly to a secure wallet.

> **Looking for standalone CLI usage?** This skill is designed for OpenClaw. For standalone CLI usage without OpenClaw, see [polymarket-alpha-bot](https://github.com/chainstacklabs/polymarket-alpha-bot).

### 3. First-Time Setup (Required for Trading)

Before your first trade, set Polymarket contract approvals (one-time, costs ~0.01 POL in gas):

```bash
uv run python scripts/polyclaw.py wallet approve
```

This submits 6 approval transactions to Polygon. You only need to do this once per wallet.

### 4. Run Commands

```bash
# Browse markets
uv run python scripts/polyclaw.py markets trending
uv run python scripts/polyclaw.py markets search "election"

# Find hedging opportunities
uv run python scripts/polyclaw.py hedge scan --limit 10

# Check wallet and trade
uv run python scripts/polyclaw.py wallet status
uv run python scripts/polyclaw.py buy <market_id> YES 50
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `CHAINSTACK_NODE` | Yes (trading) | Polygon RPC URL |
| `OPENROUTER_API_KEY` | Yes (hedge) | OpenRouter API key for LLM |
| `POLYCLAW_PRIVATE_KEY` | Yes (trading) | EVM private key (hex) |
| `HTTPS_PROXY` | Recommended | Rotating residential proxy for CLOB API |
| `CLOB_MAX_RETRIES` | No | Max retries for CLOB orders (default: 5) |

## Directory Structure

```
polyclaw/
├── SKILL.md                     # OpenClaw skill manifest
├── README.md                    # This file
├── pyproject.toml               # Python dependencies (uv)
│
├── scripts/
│   ├── polyclaw.py              # CLI dispatcher
│   ├── markets.py               # Market browsing (Gamma API)
│   ├── wallet.py                # Wallet management
│   ├── trade.py                 # Split + CLOB execution
│   ├── positions.py             # Position tracking + P&L
│   └── hedge.py                 # LLM hedge discovery
│
└── lib/
    ├── __init__.py              # Package marker
    ├── clob_client.py           # py-clob-client wrapper
    ├── contracts.py             # CTF ABI + addresses
    ├── coverage.py              # Coverage calculation + tiers
    ├── gamma_client.py          # Polymarket Gamma API client
    ├── llm_client.py            # OpenRouter LLM client
    ├── position_storage.py      # Position JSON storage
    └── wallet_manager.py        # Wallet lifecycle
```

## Trading Flow

1. **Set approvals** (one-time): `polyclaw wallet approve`
2. **Execute trade**: `polyclaw buy <market_id> YES 50`
   - Split $50 USDC.e → 50 YES + 50 NO tokens
   - Sell 50 NO tokens via CLOB → recover ~$15 (at 30¢)
   - Result: 50 YES tokens, net cost ~$35
3. **Track position**: `polyclaw positions`

### Understanding the Split Mechanism

Polymarket uses a **Conditional Token Framework (CTF)**. You can't directly "buy YES tokens" — instead:

1. **Split**: Deposit USDC.e into the CTF contract, which mints equal amounts of YES + NO tokens
2. **Sell unwanted**: Sell the side you don't want via the CLOB order book
3. **Result**: You hold your desired position, having recovered partial cost from selling the other side

**Example** (buying YES at $0.65):
```
Split:  $2 USDC.e → 2 YES + 2 NO tokens
Sell:   2 NO tokens @ $0.35 → recover ~$0.70
Net:    Paid ~$1.30 for 2 YES tokens (effective price: $0.65)
```

### CLOB Order IDs

When you execute a trade, the CLOB sell returns an **order ID** like:
```
0xc93d6214515b2436feb684854c98d314ad19111d7ab822a9c885d61588d5beaa
```

This is **not a blockchain transaction hash** — it's an off-chain Polymarket order book identifier. CLOB orders are matched off-chain and settled in batches on-chain.

**What you can do with the order ID:**

Query order details via the CLOB API (requires wallet authentication):
```python
from py_clob_client.client import ClobClient

client = ClobClient("https://clob.polymarket.com", key=private_key, chain_id=137)
creds = client.create_or_derive_api_creds()
client.set_api_creds(creds)

order = client.get_order("0xc93d6214...")
# Returns: id, market, side, price, size_matched, status, created_at, etc.
```

**API endpoint:** `GET https://clob.polymarket.com/data/order/<order_hash>`

**Response fields:** `id`, `market`, `asset_id`, `side`, `price`, `original_size`, `size_matched`, `status` (MATCHED/LIVE/CANCELLED), `type` (FOK/GTC), `created_at`, `maker_address`, `associate_trades`

**Note:** There's no public explorer for CLOB order IDs. To view your trade history, connect your wallet at polymarket.com → Portfolio → Activity.

## Hedge Discovery Flow

1. **Scan markets**: `polyclaw hedge scan --query "election"`
2. **Review output**: Table shows Tier, Coverage, Cost, Target, Cover
3. **Analyze pair**: `polyclaw hedge analyze <id1> <id2>`
4. **Execute if profitable**: Buy both positions manually

**Coverage tiers:**
- **Tier 1 (HIGH):** ≥95% coverage — near-arbitrage
- **Tier 2 (GOOD):** 90-95% — strong hedges
- **Tier 3 (MODERATE):** 85-90% — decent but noticeable risk
- **Tier 4 (LOW):** <85% — speculative (filtered by default)

## Polymarket Contracts (Polygon Mainnet)

| Contract | Address |
|----------|---------|
| USDC.e | `0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174` |
| CTF | `0x4D97DCd97eC945f40cF65F87097ACe5EA0476045` |
| CTF Exchange | `0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E` |
| Neg Risk CTF Exchange | `0xC5d563A36AE78145C45a50134d48A1215220f80a` |
| Neg Risk Adapter | `0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296` |

## Troubleshooting

### "No wallet available"
Set the `POLYCLAW_PRIVATE_KEY` environment variable:
```bash
export POLYCLAW_PRIVATE_KEY="0x..."
```

### "CHAINSTACK_NODE not set"
Set the Polygon RPC URL:
```bash
export CHAINSTACK_NODE="https://polygon-mainnet.core.chainstack.com/YOUR_KEY"
```

### "OPENROUTER_API_KEY not set"
Required for hedge commands. Get a free key at https://openrouter.ai/keys:
```bash
export OPENROUTER_API_KEY="sk-or-v1-..."
```

### Hedge scan finds 0 results or spurious results
Model quality matters. The default `nvidia/nemotron-nano-9b-v2:free` works well. If using a different model:
- Some models find spurious correlations (false positives)
- Some models return empty responses (DeepSeek R1 uses `reasoning_content`)
- Try `--model nvidia/nemotron-nano-9b-v2:free` explicitly

### "Insufficient USDC.e"
Check balance — you need USDC.e (bridged USDC) on Polygon:
```bash
uv run python scripts/polyclaw.py wallet status
```

### "CLOB order failed" / "IP blocked by Cloudflare"

Polymarket's CLOB API uses Cloudflare protection that blocks POST requests from many IPs. The solution is a **rotating residential proxy** with retry logic.

**Recommended setup (IPRoyal or similar):**
```bash
export HTTPS_PROXY="http://user:pass@geo.iproyal.com:12321"
export CLOB_MAX_RETRIES=10
```

The CLOB client automatically retries with new IPs until finding an unblocked one. Typically succeeds within 5-10 attempts.

**Alternative options:**
1. **Sell manually** — Your split succeeded. Go to polymarket.com to sell tokens
2. **Use `--skip-sell`** — Keep both tokens: `polyclaw buy <id> YES 50 --skip-sell`

### "Approvals not set"
Run the one-time approval setup:
```bash
uv run python scripts/polyclaw.py wallet approve
```

## License

MIT

## Credits

Based on [polymarket-alpha-bot](https://github.com/chainstacklabs/polymarket-alpha-bot) by Chainstack.

- **Chainstack** — Polygon RPC infrastructure
- **Polymarket** — Prediction market platform
- **OpenRouter** — LLM API for hedge discovery
