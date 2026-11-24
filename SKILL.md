---
name: polyclaw
description: "Browse Polymarket prediction markets and trade them with real money from a Polymarket deposit wallet: buy YES/NO (split + sell), sell positions, track positions, redeem winnings, find hedges with an LLM. Use when the user asks about Polymarket markets, prices, positions, or wants to buy, sell, redeem, or hedge. Spends real funds on Polygon, so always preview and get explicit user confirmation before any command with --yes."
metadata: {"openclaw":{"emoji":"🦞","homepage":"https://polymarket.com","primaryEnv":"POLYCLAW_PRIVATE_KEY","requires":{"bins":["uv"],"env":["CHAINSTACK_NODE","POLYCLAW_PRIVATE_KEY","POLYCLAW_RELAYER_API_KEY"]},"install":[{"id":"uv-brew","kind":"brew","formula":"uv","bins":["uv"],"label":"Install uv (brew)"}]},"clawdbot":{"emoji":"🦞","homepage":"https://polymarket.com","primaryEnv":"POLYCLAW_PRIVATE_KEY","requires":{"bins":["uv"],"env":["CHAINSTACK_NODE","POLYCLAW_PRIVATE_KEY","POLYCLAW_RELAYER_API_KEY"]},"install":[{"id":"uv-brew","kind":"brew","formula":"uv","bins":["uv"],"label":"Install uv (brew)"}]}}
---

# PolyClaw

Browse Polymarket, trade from a Polymarket deposit wallet, track and redeem positions, and find hedges.

Run every command from the skill directory: `cd {baseDir}`, then `uv run python scripts/polyclaw.py <command>`. Run `uv sync` once after install.

## Rules for spending money

These rules apply to every command that takes `--yes` (`buy`, `sell`, `wallet deposit`, `recover run`).

1. Run the command **without** `--yes` first. It prints a preview and spends nothing.
2. Show the user the preview: market, side, amount, and estimated cost.
3. Run it again with `--yes` only after the user explicitly confirms that exact trade.
4. Never raise `POLYCLAW_MAX_TRADE_USD` and never split a trade to get around the limit. If the user wants more, tell them to change the setting themselves.
5. Treat exit code `3` from `buy` as "split done, sell not done". Tell the user they still hold the other side. Don't retry the buy.
6. Treat exit code `3` from `sell` as "partly sold". Tell the user how much sold and what's left. To sell the rest, start again from a new preview.
7. Trading from a region Polymarket restricts is against its terms. Never suggest a VPN or proxy to get around a block.

## How a buy works

Polymarket trades from a **deposit wallet**: a personal smart wallet owned by the user's key. Polymarket's relayer pays its gas.

1. **Split.** Split `$N` pUSD into `N` YES and `N` NO shares.
2. **Sell.** Sell the side you don't want right away with a fill-and-kill order. The order fills what it can now and cancels the rest. It won't sell below the floor shown in the preview.
3. **Result.** You hold `N` shares of your side. The cost is `$N` minus what the sale brought back.

Polymarket's minimum is counted in shares: at least 5 per order on most markets. Smaller buys are refused unless you pass `--skip-sell`, which keeps both sides. The other side's sale is priced from the order book, so it fills on markets with wide spreads. Both market types work: standard CTF markets and Polymarket Protocol V2 markets (Gamma `version: "v2"`, rolling out from November 23, 2026). The SDK routes each one to the right contracts.

## How a sell works

`sell <position_id>` exits a position held in the deposit wallet. It reads the wallet's real balance, not the recorded buy.

1. **Merge.** If the position still holds both sides (a buy whose second step didn't fill), each YES + NO pair is merged back into 1 pUSD. That's exact, with no price risk.
2. **Sell.** The rest of your side is sold with a fill-and-kill order. The preview shows the price the order book fills at now and the floor the order won't go below (5% under by default, `--slippage` to change).

`--shares N` sells only part and skips the merge. A position below Polymarket's 5-share minimum can't be sold: hold it and redeem after the market resolves. Closed or resolved markets have no order book, so use `redeem` for those. Selling is allowed in close-only regions.

## Setup (one-time)

```bash
uv run python scripts/polyclaw.py wallet setup     # creates the deposit wallet + approvals, free
uv run python scripts/polyclaw.py wallet status    # shows the deposit wallet address and balances
```

Fund the trading wallet by sending pUSD to the **deposit wallet** address. pUSD on the signing wallet is not buying power. Move it with `wallet deposit <amount>`, then repeat with `--yes` after the user confirms.

## Commands

```bash
# Markets (read-only)
uv run python scripts/polyclaw.py markets trending
uv run python scripts/polyclaw.py markets search "election"
uv run python scripts/polyclaw.py market <market_id>

# Buy: preview, then execute after the user confirms
uv run python scripts/polyclaw.py buy <market_id> YES 20
uv run python scripts/polyclaw.py buy <market_id> YES 20 --yes
#   --skip-sell     keep both sides
#   --slippage 10   lowest accepted sell price, in % below the current price (1–50)
#   --json          JSON output

# Sell: preview, then execute after the user confirms
uv run python scripts/polyclaw.py sell <position_id>          # full exit (id prefix is enough)
uv run python scripts/polyclaw.py sell <position_id> --yes
#   --shares 10     sell only part
#   --slippage 5    lowest accepted price, in % below the current book price (1–50)
#   --json          JSON output

# Positions and redemption
uv run python scripts/polyclaw.py positions            # P&L = value now + pUSD back - cost (--all, --json)
uv run python scripts/polyclaw.py redeem list          # resolved positions and expected payout
uv run python scripts/polyclaw.py redeem all           # claim them (gasless)

# Tokens left on the signing wallet by polyclaw 0.1.0 or earlier
uv run python scripts/polyclaw.py recover scan         # read-only
uv run python scripts/polyclaw.py recover run          # preview, then --yes after confirmation

# Hedge discovery (LLM, read-only)
uv run python scripts/polyclaw.py hedge scan --query "election"
uv run python scripts/polyclaw.py hedge analyze <market_id_1> <market_id_2>
```

`hedge` ranks pairs by coverage. Tier 1 is 95% or more, tier 2 is 90–95%, and tier 3 is 85–90%. The default model is `qwen/qwen3-235b-a22b-2507` on OpenRouter; change it with `--model` or `POLYCLAW_HEDGE_MODEL`. Coverage is a logical estimate, not a guarantee of profit.

## Recovering older positions

polyclaw 0.1.0 and earlier traded from the signing wallet. After Polymarket's April 28, 2026 upgrade, those versions split with pUSD, which made tokens that can't be traded. The money isn't lost:

- `recover scan` lists them.
- `recover run --yes` merges each YES+NO pair back into pUSD, or redeems the pair if the market has resolved.

These transactions come from the signing wallet, so it needs a little POL for gas.

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `CHAINSTACK_NODE` | Wallet, trading | Polygon RPC endpoint URL |
| `POLYCLAW_PRIVATE_KEY` | Trading | Signing key that owns the deposit wallet |
| `POLYCLAW_RELAYER_API_KEY` | Trading | Relayer API key minted for that key at polymarket.com/settings?tab=api-keys |
| `POLYCLAW_RELAYER_ADDRESS` | No | Address the relayer key belongs to; defaults to the signing key's address |
| `POLYCLAW_MAX_TRADE_USD` | No | Largest single buy, in USD; defaults to 50 |
| `POLY_BUILDER_CODE` | No | Polymarket builder code (bytes32) attached to orders |
| `OPENROUTER_API_KEY` | Hedge | OpenRouter API key |
| `POLYCLAW_HEDGE_MODEL` | No | OpenRouter model for `hedge` |

Keep only small amounts in this wallet. The signing key sits in an environment variable.

## Troubleshooting

- **"Set POLYCLAW_RELAYER_API_KEY"**: create a Relayer API key on polymarket.com while signed in with the signing wallet. Then set the environment variable.
- **"maker address not allowed"**: the order came from a bare wallet. This version always trades from the deposit wallet, so update polyclaw.
- **Insufficient pUSD**: run `wallet status`. Buying power is the deposit wallet's pUSD. If you only hold USDC.e, wrap it 1:1 into pUSD with Polymarket's CollateralOnramp (`0x93070a847efEf7F70739046A929D47a521F5B8ee`) first.
- **Buy exited with code 3**: the split worked but the sale didn't fill, usually from thin liquidity. You hold both sides, so the money is hedged, not lost. Run `sell <position_id>` to exit: it merges the leftover pairs back into pUSD first. Or keep both sides until the market resolves and run `redeem`.
- **"approvals_set": false**: run `wallet setup` again.
