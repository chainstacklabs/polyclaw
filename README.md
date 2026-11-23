<img width="1200" alt="Labs" src="https://user-images.githubusercontent.com/99700157/213291931-5a822628-5b8a-4768-980d-65f324985d32.png">

<p>
 <h3 align="center">Chainstack is the leading suite of services connecting developers with Web3 infrastructure</h3>
</p>

<p align="center">
  • <a target="_blank" href="https://chainstack.com/">Homepage</a> •
  <a target="_blank" href="https://chainstack.com/protocols/">Supported protocols</a> •
  <a target="_blank" href="https://chainstack.com/blog/">Chainstack blog</a> •
  <a target="_blank" href="https://docs.chainstack.com/reference/blockchain-apis">Blockchain API reference</a> • <br>
  • <a target="_blank" href="https://console.chainstack.com/user/account/create">Start for free</a> •
</p>

An [OpenClaw](https://github.com/openclaw/openclaw) agent skill for trading on **Polymarket**. It lets your AI agent browse prediction markets, buy and sell YES or NO, track P&L, redeem winnings, and find hedging pairs with an LLM. Trades run from your Polymarket deposit wallet on Polygon through Polymarket's official SDK, with a preview before any money moves.

📺 [Watch the video explainer](https://www.youtube.com/watch?v=s_uP802NVTE)

> **Also by Chainstack**
> - [**polymarket-alpha-bot**](https://github.com/chainstacklabs/polymarket-alpha-bot) — dashboard that finds hedging pairs across correlated markets and trades them.

---

**🚨 Scam alert:** scam bots post in Issues with links to sites that drain your wallet. Never share your private key, and don't follow links in issue comments. Deleted comments were scams; genuine contributors are welcome.

**⚠️ Not for production:** this code is for learning, it isn't audited, and it isn't financial advice. An agent using it trades real funds; keep only what you can afford to lose in the wallet. Polymarket restricts trading in some regions; check that you're allowed to trade where you are.

---

## Getting started

### 1. Prerequisites

- [uv](https://docs.astral.sh/uv/getting-started/installation/) — it handles Python, no separate install needed
- A Polygon RPC endpoint — deploy a Polygon node on [Chainstack](https://console.chainstack.com/user/account/create)
- A Relayer API key — sign in to polymarket.com with your signing wallet and create one at polymarket.com/settings?tab=api-keys. It works only for that wallet.
- An [OpenRouter API key](https://openrouter.ai/settings/keys), only for hedge discovery

### 2. Install the skill

```bash
clawhub install polyclaw                    # or: cp -r polyclaw ~/.openclaw/skills/
cd ~/.openclaw/skills/polyclaw && uv sync
```

### 3. Configure

Add the keys to `openclaw.json` under `skills.entries.polyclaw.env`:

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

### 4. Set up and fund the deposit wallet

Polymarket trades from a **deposit wallet**: a smart wallet owned by your signing key, with gas paid by Polymarket's relayer.

```bash
uv run python scripts/polyclaw.py wallet setup     # create the deposit wallet and approvals (free)
uv run python scripts/polyclaw.py wallet status    # its address and balances
```

Send pUSD to the deposit wallet address. pUSD on the signing wallet isn't buying power: move it with `wallet deposit <amount> --yes`, which needs a little POL for gas.

### 5. Ask your agent

"What's trending on Polymarket?", "Buy $20 YES on `<market_id>`", "Sell my Xi Jinping position", "Claim my resolved positions". The skill has the agent show a preview and wait for your confirmation before anything is spent.

## What it covers

| Area | What the agent can do |
|---|---|
| Markets | Trending by 24h volume, keyword search, market details; standard and Protocol V2 markets |
| Buy | Split pUSD into YES + NO, then sell the side you don't want with a fill-and-kill order and a price floor |
| Sell | Merge leftover YES + NO pairs back to pUSD at $1 each, then sell the rest; `--shares` sells part |
| Positions | P&L from what each position cost, what the wallet holds now, and pUSD already back |
| Redeem | Claim pUSD from resolved markets, gasless |
| Hedges | LLM scan for pairs where one market logically implies another, with coverage tiers |
| Safety | Preview by default, a spend limit per buy, Polymarket's minimum order sizes checked before spending |

## Commands

Run from the skill directory with `uv run python scripts/polyclaw.py <command>`:

| Command | Does |
|---|---|
| `markets trending` / `markets search "<query>"` / `market <id>` | Browse markets |
| `wallet setup` / `wallet status` / `wallet deposit <amount>` | Deposit wallet |
| `buy <market_id> YES\|NO <amount>` | Preview a buy; add `--yes` to run it |
| `sell <position_id>` | Preview a full exit; add `--yes` to run it |
| `positions` / `position <id>` | P&L; `--all` includes closed positions |
| `redeem list` / `redeem all` | Claim resolved positions |
| `hedge scan` / `hedge analyze <id1> <id2>` | Find or check hedging pairs |
| `recover scan` / `recover run` | Tokens left on the signing wallet by PolyClaw 0.1.0 |

**How a buy works:** `$10` of pUSD splits into 10 YES + 10 NO. Buying YES at $0.65 sells the 10 NO at no less than $0.32 (10% under their $0.35 price, rounded to the cent; `--slippage` to change), so you hold 10 YES for $6.50–$6.80. If that sale doesn't fill, `buy` exits with code `3`: you hold both sides, which is hedged, not lost.

## Environment variables

| Variable | Needed for | Purpose |
|---|---|---|
| `CHAINSTACK_NODE` | Wallet, trading | Polygon RPC endpoint |
| `POLYCLAW_PRIVATE_KEY` | Trading | Signing key that owns the deposit wallet |
| `POLYCLAW_RELAYER_API_KEY` | Trading | Relayer API key for the signing wallet |
| `POLYCLAW_RELAYER_ADDRESS` | Optional | Address the relayer key belongs to; defaults to the signing wallet |
| `POLYCLAW_MAX_TRADE_USD` | Optional | Largest single buy in USD; defaults to 50 |
| `POLY_BUILDER_CODE` | Optional | Polymarket builder code attached to orders |
| `OPENROUTER_API_KEY` | Hedges | OpenRouter API key |
| `POLYCLAW_HEDGE_MODEL` | Optional | Model for `hedge`; defaults to `qwen/qwen3-235b-a22b-2507` |

The signing key sits in an environment variable, so keep only small amounts in this wallet.

## Troubleshooting

| Message | Fix |
|---|---|
| `Set POLYCLAW_RELAYER_API_KEY` | Create a Relayer API key at polymarket.com while signed in with the signing wallet |
| `maker address not allowed` | Polymarket rejects orders from bare wallets; update PolyClaw and run `wallet setup` |
| `Insufficient pUSD` | Run `wallet status`: buying power is the deposit wallet's pUSD. Wrap USDC.e 1:1 into pUSD with Polymarket's CollateralOnramp (`0x93070a847efEf7F70739046A929D47a521F5B8ee`) |
| `below this market's 5-share minimum` | Buy at least $5, or pass `--skip-sell` to keep both sides |
| `buy` exit code `3` | The sale didn't fill, usually from thin liquidity; run `sell <position_id>` or hold both sides until `redeem` |
| Hedge scan finds nothing useful | Try another model with `--model` or `POLYCLAW_HEDGE_MODEL` |

**Upgrading from PolyClaw 0.1.0:** it traded from the signing wallet, and after Polymarket's April 28, 2026 upgrade its buys left tokens no order book accepts. Run `recover scan`, then `recover run --yes` to merge them back to pUSD or redeem them; it needs a little POL for gas.

## Structure

```
polyclaw/
├── SKILL.md            # What the agent reads: commands and rules for spending money
├── scripts/            # One script per command group (polyclaw.py dispatches)
├── lib/                # SDK client, Gamma API, balances, P&L, position storage
└── tests/              # Offline tests
```

Positions are stored in `~/.openclaw/polyclaw/positions.json`.

## Contributing

Open an **Issue** for feedback or bugs. Run `uv sync && uv run pytest` before a pull request; keep `SKILL.md` in step with the commands.

## License

[Apache 2.0](LICENSE)
