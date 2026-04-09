# claude-monitor

Real-time monitor for your **claude.ai Pro / Max** subscription usage — a live CLI, a terminal TUI, and a local black-and-green web dashboard, all reading the JSONL transcripts that Claude Code writes under `~/.claude/projects/`.

claude.ai has no public usage API. Since Claude Code consumes from the same 5-hour + weekly rolling windows as the web app, parsing its transcripts gives a close-enough view of what the subscription is actually spending.

## Tracks three windows

| window           | pro       | max5        | max20       |
|------------------|----------:|------------:|------------:|
| 5-hour (all)     |   19,000  |     88,000  |    220,000  |
| weekly (all)     |  300,000  |  1,900,000  |  7,600,000  |
| weekly (opus)    |        0  |    200,000  |    800,000  |

Numbers are **approximate billable-token caps** — Anthropic does not publish exact values and they drift over time. Override them per run with `--limit-5h`, `--limit-weekly`, `--limit-weekly-opus`, a `--limits` JSON file, or the `CLAUDE_MONITOR_LIMITS_JSON` env var.

## Install

```bash
cd ClaudeUsageMonitor
uv sync
uv run pytest -q
```

## Usage

```bash
# one-shot snapshot
uv run claude-monitor snapshot --plan max5

# live TUI (rich), refreshes every 2s
uv run claude-monitor live --plan max5

# local web dashboard (terminal/hacker black-green theme)
uv run claude-monitor serve --plan max5 --port 8765
# then open http://127.0.0.1:8765/

# custom limits
uv run claude-monitor --plan custom --limit-5h 50000 --limit-weekly 1000000 --limit-weekly-opus 100000 snapshot

# inspect default + resolved limits
uv run claude-monitor show-limits --plan max20
```

## How it works

1. `discovery` walks `~/.claude/projects/**/*.jsonl` (including the nested `subagents/` files).
2. `tail.FileTailer` tracks a byte offset per file, reading only what's appended since last poll.
3. `parser.parse_line` keeps only `type == "assistant"` rows with a `message.usage` payload and extracts the token counts plus `message.model`.
4. `aggregator.UsageStore` holds records sorted by timestamp; three rolling windows (5h, weekly, weekly-opus) are computed on demand via `bisect`. Dedup is keyed by `requestId` (falling back to `uuid`).
5. Cost is computed locally from `pricing.PRICE_TABLE` (approximate per-1M rates, overridable via `--prices PATH` or `CLAUDE_MONITOR_PRICES_JSON`).
6. `cache_read` tokens are tracked separately and **not** counted toward billable totals.
7. The web server polls every 2s and pushes snapshots over Server-Sent Events (`/api/stream`); the browser renders a single static HTML page with ASCII progress bars, a Unicode sparkline for burn-rate history, and no external JS dependencies.

## Notes on accuracy

- All plan caps and prices are **approximate** and user-overridable.
- `cache_read_input_tokens` is excluded from billable totals (prompt cache reads are effectively free).
- Burn rate is averaged over the last 10 minutes; ETA = `(limit - used) / burn`.
- Retention: records older than 7 days + 10 min are pruned on each poll.
