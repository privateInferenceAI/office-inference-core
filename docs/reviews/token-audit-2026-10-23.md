# Session token audit, 2026-10-23

Source: the DSH session log for session-a8d780c1-da54-4f2f-9725-f64100624ea2, the long-running
office-inference-core build session. Usage is aggregated from the usage block on each assistant message.

## Totals

- Assistant messages with usage: 2,324
- Turns: 64
- Total tokens: 958,002,482 (the "945M" figure is the rounded session total)
- Cache-read tokens: 952,994,048 (99.48 percent)
- Input tokens: 3,443,591 (0.36 percent)
- Output tokens: 1,564,843 (0.16 percent)
- Compaction summaries: 4

The total is dominated by re-reading the long conversation context on each model call, not by generated text.

## Why the count is so large

The session made 2,324 model calls. Each call sends the conversation context again, so the same long history is
counted repeatedly. The average is about 412,000 tokens per call, which is why one long build session reaches
most of a billion counted tokens while the visible output is only 1.56M tokens.

The tool payloads are not the bulk. Tool results total about 4.2 MB of JSONL, and user messages about 1.1 MB.
The repeated context is.

## Where it went

The top ten turns account for about 544M tokens, roughly 57 percent of the total:

| Turn | Total tokens | Calls | User instruction |
|---|---:|---:|---|
| 22 | 107,434,025 | 221 | lets keep going please |
| 6 | 71,564,206 | 148 | sounds good - please build it... let me know when finished |
| 43 | 57,709,516 | 205 | keep working until you need me, don't stop |
| 50 | 52,134,859 | 84 | backup and RTO, including restore drill |
| 42 | 49,397,373 | 82 | continue with badj.ai and DeepSeek |
| 45 | 44,156,196 | 88 | three-agent testing after a major implementation |
| 21 | 43,829,595 | 126 | are you waiting to merge, continue |
| 25 | 43,412,108 | 66 | continue |
| 64 | 37,031,378 | 106 | freeze badj.ai and patch the CVE |
| 38 | 36,998,750 | 67 | keep building until a blocker |

The pattern is clear: the long autonomous "keep going" turns produce the most calls, and every call carries the
whole history. Turn 22 alone was 221 calls.

## What to change

1. Cap autonomous turns at a step budget, then return a checkpoint instead of continuing indefinitely.
2. Use compaction before a long turn, not after it, and keep the summary shorter than the history it replaces.
3. Split research and verification into subagents with narrow prompts. Subagents still use the model, but they do
   not each carry the main session's full history.
4. Avoid re-reading large documents in later steps. Keep durable summaries in the repo and read the summary.
5. Set a per-turn token budget in the harness. The measurement itself is not expensive; the repeated context is.

## Files

- Session log: /home/anthony/.dsh/sessions/--home-anthony-projects-ai-stack--/session-a8d780c1-da54-4f2f-9725-f64100624ea2/session.v4.jsonl.zstd
