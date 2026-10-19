# Acceptance panel, 2026-10-19

**What this is.** One page a client can decide from: what the system answered correctly, how long the client
waited, and what the deployment spent to answer. Every number below comes from the same five-run suites, and the
cost join is the gateway's own spend records over the same wall-clock window.

**Deployment:** `oi-core-dev-cloud`, Claude Haiku 4.5 primary, tool loop on, query tool on. **Instrument:** the
redesigned five-probe set (PR #192), 45 questions and 53 role-runs. **Corpus:** unchanged, 539 documents.

## The numbers

| Configuration | Five runs | Mean | p50 | p95 | Cost per answer | Provider calls per answer | Degraded |
|---|---|---|---|---|---|---|---|
| Corrected instrument, before this work (52 runs) | 52, 50, 51, 51, 49 | 50.6 of 52 | - | - | - | - | 0 |
| New instrument, before the chain | 52, 52, 52, 51, 52 | 51.8 of 53 | 3.44 s | 12.30 s | 0.01343 USD | 2.92 | 0 |
| Chain configured, healthy | 52, 52, 52, 52, 52 | 52.0 of 53 | 3.20 s | 12.74 s | 0.01339 USD | 2.91 | 0 |
| Chain, primary provider failed on purpose | 52, 52, 52, 52, 52 | 52.0 of 53 | 3.43 s | 11.82 s | 0.01345 USD | 2.93 | 0 |

The only failing run in every configuration is **U08**, the known honesty failure (the corpus has no
parental-leave policy and the model assembles one from generic law). Its prompt fix is gated on a second-corpus
measurement; it is not covered up by these averages. One N04 failure appears in the new-instrument baseline run,
the known exact-number flake class.

## What the client is buying, in their terms

- **Answers.** 52 of 53 expected outcomes per run, with the five probes (row pinpoint in an ordinary table, a
  row in a 41,000-row table, an honest refusal for an unindexed data set, numeric-dump competition, and the PII
  redaction gate) passing in every run.
- **Wait.** Half the answers arrive in about 3.4 seconds; 95 percent within about 12.7 seconds. The tool loop,
  not retrieval, is the long tail: it makes up to six model turns to chase exact numbers. That is the number to
  compare against a single-shot configuration if a client cares more about latency than exactness.
- **Cost.** About 0.0134 USD per answered question at about three provider calls per answer, on the credits
  account's Bedrock pricing. The infrastructure is separate: the box runs at about 0.81 USD an hour, which is a
  fixed cost that depends on utilisation rather than on answers.
- **A provider failure does not become a client failure.** With the primary provider failed on purpose, every
  answer came through the same model in another Region at the same quality, latency and cost, in five runs of
  five. When both Regions are failed, a different model answers rather than degrading the question; the small
  subset shows the expected quality drop, which is why that leg is a last resort.

## How it was measured, and how to repeat it

    # five runs on the deployment
    scripts/run_answer_suite.sh 5 /tmp/oic-suite
    # accuracy and latency from the runs, cost from the gateway's spend records for the same window
    python3 scripts/acceptance_panel.py /tmp/oic-suite/run-1.json ... /tmp/oic-suite/run-5.json

The runner records `elapsed_ms` around the same HTTP call it scores, retrieval and tool loop included, and writes
p50/p95/max per run. `acceptance_panel.py` pools the answers across runs and queries `LiteLLM_SpendLogs` for
the wall-clock window the runs occupied. Cost is **per answered question, tool calls included**, because a loop
makes several provider calls for one answer and a per-call number would flatter the deployment. The script says so
when it cannot reach Postgres rather than estimating a cost.

## Caveats

- One corpus, one deployment, one model. These are this deployment's numbers, not a benchmark.
- The pre-chain row's latency and cost were not captured with the panel script (the instrument only started
  recording per-answer latency at the same time); the accuracy figure is from the instrument measurement.
- p95 over 265 answers still has a wide interval. Read the range across runs, not one number.
- The cost figure is provider spend only; it excludes the box, the audit sink and support, which belong in a quote
  rather than in this table.
