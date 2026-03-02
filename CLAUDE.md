# CLAUDE.md

## Project Overview

PolyClaw is a trading-enabled Polymarket skill for OpenClaw. It enables browsing prediction markets, executing trades on-chain via split + CLOB execution on Polygon, and discovering hedging opportunities using LLM-powered contrapositive analysis.

## Commands

```bash
uv sync

# Run the CLI
uv run python scripts/polyclaw.py <command>
```

## Architecture

- `scripts/` — CLI commands, one per file. `polyclaw.py` is the main dispatcher.
- `lib/` — Reusable modules: API clients, storage, wallet management, contracts.

**Data flow:** CLI dispatcher → script command → lib modules → Polygon/Gamma/CLOB APIs

## Tech Stack

- **Runtime**: Python 3.11+
- **Package Manager**: uv
- **Blockchain**: Web3.py, eth-account, py-clob-client
- **HTTP**: httpx with SOCKS proxy support
- **Network**: Polygon mainnet (Polymarket contracts)
- **LLM**: OpenRouter API (hedge discovery)

## Code Style

- Python 3.11+, double quotes
- Type hints in function signatures and dataclasses
- Async patterns with httpx for API calls
- Dataclasses for structured data
- One command per script in `scripts/`, reusable logic in `lib/`
