# PolyClaw

**Trading-enabled Polymarket skill for OpenClaw.**

Browse prediction markets, trade from your Polymarket deposit wallet, redeem winnings, and find hedging opportunities with LLM-powered analysis. Trades run on Polygon through Polymarket's official SDK.

> **Disclaimer:** This software is provided as-is for educational and experimental purposes. It is not financial advice. Trading prediction markets involves risk of loss. This code has not been audited. Use at your own risk and only with funds you can afford to lose. Polymarket restricts trading in some regions; check that you're allowed to trade where you are.

📺 **[Watch the video explainer](https://www.youtube.com/watch?v=s_uP802NVTE)**

## Features

### Market browsing
- `polyclaw markets trending`: top markets by 24h volume
- `polyclaw markets search "query"`: search markets by keyword
- `polyclaw market <id>`: market details with prices

### Trading
- `polyclaw buy <market_id> YES|NO <amount>`: preview a buy. Add `--yes` to execute it.
- Split pUSD into YES+NO shares, then sell the side you don't want.
- `polyclaw sell <position_id>`: preview selling a position. Add `--yes` to execute it, or `--shares N` to sell part.
- Guardrails:
  - The default run is a preview.
  - A spend limit (`POLYCLAW_MAX_TRADE_USD`, default $50).
  - Polymarket's minimum order sizes are checked before anything is spent.

### Positions
- `polyclaw positions`: open positions with live P&L
- `polyclaw redeem list` / `redeem all`: claim pUSD from resolved markets (gasless)
- Positions are tracked locally in `~/.openclaw/polyclaw/positions.json`

### Wallet
- `polyclaw wallet setup`: create your deposit wallet and set trading approvals (free)
- `polyclaw wallet status`: addresses, balances, approval state
- `polyclaw wallet deposit <amount>`: move pUSD from the signing wallet to the deposit wallet

### Hedge discovery
- `polyclaw hedge scan`: scan trending markets for hedging opportunities
- `polyclaw hedge scan --query "topic"`: scan markets matching a query
- `polyclaw hedge analyze <id1> <id2>`: analyze a specific market pair

The hedge scan uses LLM-powered contrapositive logic to find covering portfolios. It accepts only logically necessary implications and rejects correlations and "likely" relationships.

**Coverage tiers:** T1 (≥95%), T2 (90–95%), T3 (85–90%)

## How trading works

Polymarket requires API traders to use a **deposit wallet**. It's a personal smart wallet owned by your signing key, and Polymarket's relayer pays its gas. PolyClaw creates it for you on `wallet setup`.

A buy has two steps:

1. **Split.** `$N` pUSD becomes `N` YES and `N` NO shares.
2. **Sell.** The side you don't want is sold right away with a fill-and-kill order, which won't sell below the floor shown in the preview.

**Example: buying YES at $0.65**

```
Split:  $10 pUSD → 10 YES + 10 NO
Sell:   10 NO at ≥ $0.31 (10% under the $0.35 price) → about $3.10–3.50 back
Net:    10 YES for about $6.50–6.90
```

If the sell doesn't fill, `buy` exits with code `3`. You then hold both sides, which is hedged, not lost.

**Selling a position.** `polyclaw sell <position_id>` reads what the deposit wallet really holds. Leftover YES + NO pairs are merged back into pUSD first, at exactly $1 per pair. The rest is sold with a fill-and-kill order, at no less than 5% under the price the order book fills at now. `--slippage` changes that margin, and `--shares N` sells only part.

## Quick start

### 1. Install the skill

**Option A: install from ClawHub (recommended)**

```bash
clawhub install polyclaw
cd ~/.openclaw/skills/polyclaw
uv sync
```

**Option B: manual install**

```bash
cp -r polyclaw ~/.openclaw/skills/
cd ~/.openclaw/skills/polyclaw
uv sync
```

### 2. Configure environment variables

Add these to your `openclaw.json` under `skills.entries.polyclaw.env`:

```json
"polyclaw": {
  "enabled": true,
  "env": {
    "CHAINSTACK_NODE": "https://polygon-mainnet.core.chainstack.com/YOUR_KEY",
    "POLYCLAW_PRIVATE_KEY": "0x...",
    "POLYCLAW_RELAYER_API_KEY": "...",
    "OPENROUTER_API_KEY": "sk-or-v1-..."
  }
}
```

**Where to get the keys:**
- **Polygon RPC endpoint**: deploy a Polygon node on [Chainstack](https://chainstack.com). A free plan is available, and you can sign up with GitHub, X, or Google.
- **Relayer API key**: sign in to polymarket.com with the wallet that owns `POLYCLAW_PRIVATE_KEY`, then create a key at polymarket.com/settings?tab=api-keys. The key works only for that wallet.
- **OpenRouter API key**: [create a key at OpenRouter](https://openrouter.ai/settings/keys). You need it only for `hedge`.

**Security:** the signing key sits in an environment variable. Keep only small amounts in this wallet.

> **Looking for a full app with a dashboard?** See [polymarket-alpha-bot](https://github.com/chainstacklabs/polymarket-alpha-bot).

### 3. Set up and fund the deposit wallet

```bash
uv run python scripts/polyclaw.py wallet setup     # creates the deposit wallet + approvals
uv run python scripts/polyclaw.py wallet status    # shows the deposit wallet address
```

Send pUSD to the **deposit wallet** address. If your pUSD is on the signing wallet, move it:

```bash
uv run python scripts/polyclaw.py wallet deposit 20          # preview
uv run python scripts/polyclaw.py wallet deposit 20 --yes    # send; needs a little POL for gas
```

### 4. Trade

```bash
uv run python scripts/polyclaw.py markets trending
uv run python scripts/polyclaw.py buy <market_id> YES 20          # preview
uv run python scripts/polyclaw.py buy <market_id> YES 20 --yes    # execute
uv run python scripts/polyclaw.py positions
uv run python scripts/polyclaw.py sell <position_id>              # preview
uv run python scripts/polyclaw.py sell <position_id> --yes        # execute
```

## Example prompts

Natural-language prompts you can use with OpenClaw:

- **"What's trending on Polymarket?"**: market IDs, questions, prices, and volume.
- **"Show me details for market `<market_id>`"**: full market info.
- **"What's my PolyClaw wallet balance?"**: deposit wallet and signing wallet balances.
- **"Buy $20 YES on market `<market_id>`"**: the agent shows a preview and asks you to confirm before it spends anything.
- **"Find me some hedging opportunities on Polymarket"**: LLM analysis of market pairs. This takes a few minutes.
- **"Show my PolyClaw positions"**: entry price, current price, and P&L.
- **"Sell my Xi Jinping position"**: the agent shows what it would sell and for about how much, then asks you to confirm.
- **"Claim my resolved Polymarket positions"**: redeems resolved positions into pUSD.

## Upgrading from PolyClaw 0.1.0

Version 0.1.0 traded from the signing wallet directly. After Polymarket's April 28, 2026 upgrade, its `buy` split pUSD in a way that produced tokens no order book accepts, and then the sell failed. Your money is still there:

```bash
uv run python scripts/polyclaw.py recover scan        # read-only: what's left on the signing wallet
uv run python scripts/polyclaw.py recover run         # preview
uv run python scripts/polyclaw.py recover run --yes   # merge back to pUSD, or redeem if resolved
```

`recover` sends transactions from the signing wallet, so it needs a little POL for gas. It also handles positions bought before the upgrade.

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `CHAINSTACK_NODE` | Wallet, trading | Polygon RPC endpoint URL |
| `POLYCLAW_PRIVATE_KEY` | Trading | Signing key that owns the deposit wallet |
| `POLYCLAW_RELAYER_API_KEY` | Trading | Relayer API key minted for the signing wallet |
| `POLYCLAW_RELAYER_ADDRESS` | No | Address the relayer key belongs to; defaults to the signing wallet |
| `POLYCLAW_MAX_TRADE_USD` | No | Largest single buy, in USD; defaults to 50 |
| `POLY_BUILDER_CODE` | No | Polymarket builder code (bytes32) attached to orders |
| `OPENROUTER_API_KEY` | Hedge | OpenRouter API key |
| `POLYCLAW_HEDGE_MODEL` | No | OpenRouter model for `hedge`; defaults to `qwen/qwen3-235b-a22b-2507` |

## Directory structure

```
polyclaw/
├── SKILL.md                 # OpenClaw skill manifest and agent rules
├── pyproject.toml / uv.lock # Python dependencies (uv)
├── scripts/
│   ├── polyclaw.py          # CLI dispatcher
│   ├── markets.py           # Market browsing (Gamma API)
│   ├── wallet.py            # Deposit wallet setup, status, deposit
│   ├── trade.py             # Buy: split + sell, with preview and limits
│   ├── sell.py              # Sell: merge leftover pairs + sell, with preview
│   ├── positions.py         # Position tracking + P&L
│   ├── redeem.py            # Redeem resolved positions
│   ├── recover.py           # Clean up tokens from PolyClaw 0.1.0
│   └── hedge.py             # LLM hedge discovery
├── lib/
│   ├── trading.py           # Polymarket SDK client and guardrail settings
│   ├── wallet_manager.py    # Balances and approval state
│   ├── legacy.py            # Signing-wallet token scan, merge, redeem
│   ├── gamma_client.py      # Gamma API client
│   ├── contracts.py         # Addresses + ABIs for direct reads
│   ├── coverage.py          # Coverage calculation + tiers
│   ├── llm_client.py        # OpenRouter client
│   └── position_storage.py  # Position JSON storage
└── tests/                   # Offline tests: uv run pytest
```

## Troubleshooting

### "Set POLYCLAW_RELAYER_API_KEY"
Create a Relayer API key at polymarket.com/settings?tab=api-keys while signed in with the signing wallet.

### "maker address not allowed"
Polymarket rejects orders from bare wallets. This version always trades from the deposit wallet. Make sure you're on the latest version and have run `wallet setup`.

### "Insufficient pUSD"
Run `wallet status`. Your buying power is the deposit wallet's pUSD. If you only hold USDC.e, wrap it 1:1 into pUSD first with Polymarket's CollateralOnramp (`0x93070a847efEf7F70739046A929D47a521F5B8ee`).

### "below this market's 5-share minimum"
Polymarket's minimum order is 5 shares on most markets. Buy at least $5, or pass `--skip-sell` to keep both sides.

### `buy` exited with code 3
The split worked but the sell didn't fill, usually because of thin liquidity. You hold both sides. Keep them until the market resolves, then run `redeem`.

### Hedge scan finds no results or odd results
Model quality matters. Try another model with `--model` or `POLYCLAW_HEDGE_MODEL`.

## Development

```bash
uv sync
uv run pytest
```

## License

Apache 2.0

## Credits

Based on [polymarket-alpha-bot](https://github.com/chainstacklabs/polymarket-alpha-bot) by Chainstack.

- **Chainstack**: Polygon RPC infrastructure
- **Polymarket**: prediction market platform and official SDK
- **OpenRouter**: LLM API for hedge discovery
