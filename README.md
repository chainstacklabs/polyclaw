# PolyClaw

**Trading-enabled Polymarket skill for OpenClaw.**

Browse prediction markets, execute trades on-chain, and discover hedging opportunities using LLM-powered analysis. Full trading capability via split + CLOB execution on Polygon.

> **Disclaimer:** This software is provided as-is for educational and experimental purposes. It is not financial advice. Trading prediction markets involves risk of loss. This code has not been audited. Use at your own risk and only with funds you can afford to lose.

📺 **[Watch the video explainer](https://www.youtube.com/watch?v=s_uP802NVTE)**

## Features

### Market browsing
- `polyclaw markets trending` — Top markets by 24h volume
- `polyclaw markets search "query"` — Search markets by keyword
- `polyclaw market <id>` — Market details with prices

### Trading
- `polyclaw buy <market_id> YES <amount>` — Buy YES position
- `polyclaw buy <market_id> NO <amount>` — Buy NO position
- Split + CLOB execution (split USDC → YES+NO, sell unwanted side)

### Position tracking
- `polyclaw positions list` — List open positions with live P&L from chain data
- `polyclaw positions list --all` — Include closed positions
- `polyclaw positions show <token_id>` — Detailed position view by token ID prefix

### Wallet management
- `polyclaw wallet status` — Show address, POL/USDC/USDC.e balances
- `polyclaw wallet approve` — Set Polymarket contract approvals (one-time)

### Token merge recovery
- `polyclaw merge <condition_id>` — Merge YES+NO tokens back to USDC.e
- `polyclaw merge <condition_id> 50` — Merge specific amount
- Supports both standard and neg-risk markets

### Auto-redeem resolved positions
- `polyclaw redeem` — Redeem all winning positions on resolved markets
- `polyclaw redeem --dry-run` — Preview without executing

### USDC swapping
- `polyclaw swap balances` — Show USDC and USDC.e balances
- `polyclaw swap to-bridged` — Swap USDC to USDC.e via QuickSwap V2
- `polyclaw swap to-native` — Swap USDC.e to USDC
- Supports `--amount N` and `--dry-run` flags

### Portfolio management
- `polyclaw portfolio status` — Portfolio overview with allocation check
- `polyclaw portfolio rules` — Show portfolio rules (informational)
- `polyclaw portfolio history` — On-chain trade history (splits, merges, redemptions)
- `polyclaw portfolio snapshot` — Save portfolio snapshot for trend tracking

### Performance analytics
- `polyclaw performance summary` — Win rate, P&L, profit factor (from on-chain data)
- `polyclaw performance trades` — Per-trade breakdown (from on-chain events)
- `polyclaw performance chart` — ASCII portfolio value chart (from snapshots)

### Hedge discovery
- `polyclaw hedge scan` — Scan trending markets for hedging opportunities
- `polyclaw hedge scan --query "topic"` — Scan markets matching a query
- `polyclaw hedge analyze <id1> <id2>` — Analyze specific market pair

Uses LLM-powered contrapositive logic to find covering portfolios. Only logically necessary implications are accepted — correlations and "likely" relationships are rejected.

**Coverage tiers:** T1 (≥95%), T2 (90-95%), T3 (85-90%)

## Quick start

### 1. Install skill

**Option A: Install from ClawHub (recommended)**

```bash
clawhub install polyclaw
cd ~/.openclaw/skills/polyclaw
uv sync
```

**Option B: Manual install**

```bash
cp -r polyclaw ~/.openclaw/skills/
cd ~/.openclaw/skills/polyclaw
uv sync
```

### 2. Configure environment variables

Add the following to your `openclaw.json` under `skills.entries.polyclaw.env`:

```json
"polyclaw": {
  "enabled": true,
  "env": {
    "POLYGON_RPC_URL": "https://polygon-mainnet.infura.io/v3/YOUR_KEY",
    "POLYCLAW_PRIVATE_KEY": "0x...",
    "OPENROUTER_API_KEY": "sk-or-v1-..."
  }
}
```

**Where to get the keys:**
- **Polygon RPC** — Any Polygon RPC provider (Infura, Alchemy, [Chainstack](https://console.chainstack.com), public polygon-rpc.com, etc.)
- **OpenRouter API key** — [Create key at OpenRouter](https://openrouter.ai/settings/keys)

**Security warning:** Keep only small amounts in this wallet. Withdraw regularly to a secure wallet.

> **Looking for standalone CLI usage?** This skill is designed for OpenClaw. For standalone CLI usage without OpenClaw, see [polymarket-alpha-bot](https://github.com/chainstacklabs/polymarket-alpha-bot).

### 3. First-time setup (required for trading)

Before your first trade, set Polymarket contract approvals (one-time, costs ~0.01 POL in gas):

```bash
uv run python scripts/polyclaw.py wallet approve
```

This submits 6 approval transactions to Polygon. You only need to do this once per wallet.

### 4. Run commands

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

## Example prompts

Natural language prompts you can use with OpenClaw:

### 1. Browse trending markets
```
What's trending on Polymarket?
```
Returns market IDs, questions, prices, and volume.

### 2. Get market details
```
Show me details for market <market_id>
```
Use the market ID from Polymarket URL or from the trending markets response above.

Returns full market info with link to Polymarket.

### 3. Check wallet status
```
What's my PolyClaw wallet balance?
```
Shows address, POL balance (for gas), and USDC.e balance.

### 4. Direct trading
If you have your own conviction on a market:
```
Buy $50 YES on market <market_id>
```
Executes split + CLOB flow. Position appears automatically in on-chain history.

### 5. Hedge discovery flow
Find LLM-analyzed arbitrage opportunities:
```
Find me some hedging opportunities on Polymarket
```
or more specifically:
```
Run hedge scan limit 10
```
> **Note:** This takes a few minutes. The skill fetches open markets and sends pairs to the LLM for logical implication analysis.

Review the results — you'll see coverage tiers (T1 = 95%+, T2 = 90-95%, T3 = 85-90%) and the market pairs where you can take hedged positions.

### 6. Check positions
```
Show my PolyClaw positions
```
Lists open positions with entry price, current price, and P&L.

### 7. Sell early
To exit a position before the market resolves:
```
Sell my YES position on market <market_id>
```
Sells your tokens on the CLOB order book at current market price.

### 8. Recover stuck tokens
If a CLOB sell failed and you have both YES and NO tokens:
```
Merge my tokens for condition 0xabc123...
```
Merges YES+NO pairs back into USDC.e.

### 9. Redeem resolved markets
```
Redeem my resolved positions (dry run first)
```
Scans open positions for resolved markets and redeems winners.

### 10. Swap USDC
```
Swap my USDC to USDC.e for trading
```
Converts native USDC to bridged USDC.e via QuickSwap V2.

### 11. Portfolio overview
```
What's my portfolio status?
```
Shows total value, cash allocation, position exposure, and rule compliance.

### 12. Performance review
```
How are my trades performing?
```
Shows win rate, P&L, and profit factor from on-chain data.

### Full flow example

1. **"What's trending on Polymarket?"** → Get market IDs
2. **"Run hedge scan limit 10"** → Wait for LLM analysis
3. Review hedge opportunities with coverage tiers
4. **"Buy $25 YES on market abc123"** → Take position on target market
5. **"Buy $25 NO on market xyz789"** → Take position on covering market
6. **"Show my PolyClaw positions"** → Verify entries and track P&L
7. **"What's my portfolio status?"** → Check allocation and value
8. **"Redeem my resolved positions"** → Collect winnings from resolved markets

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `POLYGON_RPC_URL` | Yes (trading) | Polygon RPC URL (any provider) |
| `CHAINSTACK_NODE` | No | Legacy fallback if POLYGON_RPC_URL not set |
| `OPENROUTER_API_KEY` | Yes (hedge) | OpenRouter API key for LLM |
| `POLYCLAW_PRIVATE_KEY` | Yes (trading) | EVM private key (hex) |
| `HTTPS_PROXY` | No | Only needed if CLOB orders fail (see [troubleshooting](#clob-order-failed--ip-blocked-by-cloudflare)) |
| `CLOB_MAX_RETRIES` | No | Max retries for CLOB orders (default: 5) |

## Directory structure

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
│   ├── positions.py             # On-chain position tracking + P&L
│   ├── merge_tokens.py          # Token merge/recovery
│   ├── redeem.py                # Auto-redeem resolved positions
│   ├── swap_usdc.py             # USDC <-> USDC.e swapping
│   ├── portfolio.py             # Portfolio status, rules, history, snapshot
│   ├── performance.py           # Performance analytics (on-chain)
│   └── hedge.py                 # LLM hedge discovery
│
└── lib/
    ├── __init__.py              # Package marker
    ├── clob_client.py           # py-clob-client wrapper
    ├── contracts.py             # CTF ABI + addresses
    ├── coverage.py              # Coverage calculation + tiers
    ├── gamma_client.py          # Polymarket Gamma API client
    ├── llm_client.py            # OpenRouter LLM client
    ├── market_cache.py          # conditionId → market metadata cache
    ├── portfolio_storage.py     # Portfolio snapshots + rules
    ├── storage.py               # Shared storage utilities
    ├── subgraph_client.py       # Goldsky Activity + PnL subgraph client
    └── wallet_manager.py        # Wallet lifecycle
```

## Trading flow

1. **Set approvals** (one-time): `polyclaw wallet approve`
2. **Execute trade**: `polyclaw buy <market_id> YES 50`
   - Split $50 USDC.e → 50 YES + 50 NO tokens
   - Sell 50 NO tokens via CLOB → recover ~$15 (at 30¢)
   - Result: 50 YES tokens, net cost ~$35
3. **Track position**: `polyclaw positions`

### Understanding the split mechanism

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

### CLOB order IDs

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

**Note:** There's no public explorer for CLOB order IDs. To view your on-chain trade history, use `polyclaw portfolio history` (queries the Goldsky Activity Subgraph) or connect your wallet at polymarket.com → Portfolio → Activity.

## Hedge discovery flow

1. **Scan markets**: `polyclaw hedge scan --query "election"`
2. **Review output**: Table shows Tier, Coverage, Cost, Target, Cover
3. **Analyze pair**: `polyclaw hedge analyze <id1> <id2>`
4. **Execute if profitable**: Buy both positions manually

**Coverage tiers:**
- **Tier 1 (HIGH):** ≥95% coverage — near-arbitrage
- **Tier 2 (GOOD):** 90-95% — strong hedges
- **Tier 3 (MODERATE):** 85-90% — decent but noticeable risk
- **Tier 4 (LOW):** <85% — speculative (filtered by default)

## Polymarket contracts (Polygon mainnet)

| Contract | Address |
|----------|---------|
| USDC (native) | `0x3c499c542cEf5E3811e1192ce70d8cC03d5c3359` |
| USDC.e (bridged) | `0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174` |
| CTF | `0x4D97DCd97eC945f40cF65F87097ACe5EA0476045` |
| CTF Exchange | `0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E` |
| Neg Risk CTF Exchange | `0xC5d563A36AE78145C45a50134d48A1215220f80a` |
| Neg Risk Adapter | `0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296` |
| QuickSwap V2 Router | `0xa5E0829CaCEd8fFDD4De3c43696c57F7D7A678ff` |

## Troubleshooting

### "No wallet available"
Set the `POLYCLAW_PRIVATE_KEY` environment variable:
```bash
export POLYCLAW_PRIVATE_KEY="0x..."
```

### "POLYGON_RPC_URL not set"
Set the Polygon RPC URL (any provider):
```bash
export POLYGON_RPC_URL="https://polygon-mainnet.infura.io/v3/YOUR_KEY"
```

### "OPENROUTER_API_KEY not set"
Required for hedge commands. Get a free key at https://openrouter.ai/settings/keys:
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

Apache 2.0

## Credits

Based on [polymarket-alpha-bot](https://github.com/chainstacklabs/polymarket-alpha-bot) by Chainstack.

- **Chainstack** — Polygon RPC infrastructure
- **Polymarket** — Prediction market platform
- **OpenRouter** — LLM API for hedge discovery
