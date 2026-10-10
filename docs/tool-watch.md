# Keeping up with new tools

The fear is real but the risk is smaller than it feels, as long as the seams stay standard.

## Why missing a tool is not fatal

A new tool can only hurt us if it makes something we built obsolete, and it can only help us if it
plugs into a seam we already have. Our seams:

| Seam | Standard | Swapping means |
|---|---|---|
| Models | LiteLLM config | One line per role |
| Tools and integrations | MCP | Adding a server |
| Services | Container images | A compose entry |
| Data | Postgres, Qdrant | A migration |
| Configuration | The client manifest | A field |

Because of those, adopting a better tool is a two-week catch-up, not a rebuild. **The parity suite
is the insurance policy**: if a swap can be measured, arriving late costs little. If a swap can't
be measured, arriving early doesn't help either.

## What we do instead of worrying

**Cadence:** once a month, 45 minutes, on the first Monday, alongside the existing tech watch.
Anything that looks important becomes a watchlist row with a verdict, not a task.

**Sources that are worth the time**

| Source | Why |
|---|---|
| MCP Sweep (corpusiq) | Tracks MCP server changes and additions, which is where integrations come from |
| The MCP registry and awesome-mcp lists | Raw supply of connectors |
| Agent-runtime release feeds (OpenAI Agents SDK, Agent Zero, nanoclaw, OpenClaw, openJiuwen) | Where the loop itself improves |
| Model release notes and tool-calling benchmarks | Decides what the office box can promise |
| Licensing changes for anything we ship (n8n, LibreChat, Docling, LiteLLM) | A licence change is a product change |
| Our own support inbox | The most honest signal about what actually breaks |

**Adoption gates.** A tool gets in only if all five pass:

1. Speaks a standard seam (MCP, OpenAI-compatible API, or plain Docker).
2. Licensed for commercial resale and for running on a client's premises.
3. Maintenance signal: a release in the last 90 days, and either a company or more than one
   maintainer behind it.
4. Fills a named gap or replaces something we run today. "Interesting" is not a gap.
5. Survives a two-week trial scored by the parity suite, including a licence review.

**The two-week rule.** Nothing enters a client deployment until it has run in the office reference
build for two weeks and passed the suite. Clients are not a test environment.

## Current watchlist

| Candidate | Category | Status | Note |
|---|---|---|---|
| **openJiuwen** | Enterprise agent OS: swarms, sandboxes, multi-tenant control | Watch | Open-sourced October 2026. Multi-tenant control is what a hosted tier would need. Verify licence and maturity before anything else |
| **OpenClaw** | Agent runtime, MIT, very large community | Watch | Fast-moving and popular. Maturity and security posture unverified |
| **nanoclaw / nanobot** | Container-per-agent runtime, MIT | Watch | Interesting isolation model. Young |
| **Agent Zero** | Agent runtime, MIT, Docker-native | Candidate | Fallback if the OpenAI Agents SDK proves limiting |
| **IBM ContextForge** | MCP gateway, Apache-2.0 | Candidate | Front-runner for the tool registry and per-tool policy |
| **Docling** | Document parsing and OCR | Candidate | Replaces the current extractors, better on scanned documents |
| **Langfuse** | LLM observability, self-hosted | Candidate | Verify licence terms and self-hosting conditions |
| **Lambda MicroVMs** | Sandboxed execution in the cloud build | Candidate | For agent code execution where Docker is not enough |
| **Pi** | Coding agent, Austria | Not relevant | Competes with coding assistants, not with us |

Every row is a decision to keep watching, not a commitment.

## What we deliberately do not do

- **We do not chase frameworks.** A new orchestration library every quarter is how teams end up
  with four half-migrations.
- **We do not adopt anything on the office box that phones home.** That check comes before
  features.
- **We do not rewrite for a benchmark.** A better score is not a client outcome.



