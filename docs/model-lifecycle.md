# Model lifecycle

A model can be deprecated underneath a running deployment. Nothing in our code changes, and the
client's answers do. This is the procedure that keeps that from being an incident, and the record of
who owns it.

Written 2026-10-11, when `INF-DEPRECATE-001` was the last open item in C1 and the brief still listed
model deprecation as unowned and unpriced.

## Owned

**The deployment owner owns this**, which today is Anthony Stachowitz. The cadence is **monthly**, on
the same day as the vulnerability watch, because both are the same kind of job: a supplier changed
something and we find out before a client does.

What that ownership means in practice:

- Refresh `data/model-lifecycle.json` monthly from the provider APIs, and commit the result with the
  date it was verified.
- When a model goes `LEGACY` or passes its end-of-life date, treat it as planned work with a client
  conversation, not as an outage to be absorbed.
- Keep the replacement column honest. During an incident is the wrong time to be choosing a model.

## Detected

`scripts/check_model_lifecycle.py` fails CI when a manifest names a model that is not `ACTIVE`, that
passed its end-of-life date, or that the table does not know at all. The third case is deliberate: a
new model in a manifest should be a decision someone made, not something that slipped past because
nobody wrote its lifecycle down.

The table is a checked-in file rather than a live API call, and that is the point. It gives the same
answer on every machine, it needs no credentials in CI, and refreshing it is an act with a date
attached rather than a background lookup whose result nobody read.

**Refreshing it:**

    # per model currently named by a manifest, plus any candidate replacement
    aws bedrock get-foundation-model --model-identifier amazon.nova-micro-v1:0 \
      --profile oi-credits --region us-east-1 \
      --query 'modelDetails.{status:modelLifecycle.status,streaming:responseStreamingSupported,inferenceTypes:inferenceTypesSupported}'

    # and the wider catalog, when choosing a replacement
    aws bedrock list-foundation-models --profile oi-credits --region us-east-1 \
      --query 'modelSummaries[].{id:modelId,provider:providerName,lifecycle:modelLifecycle.status}'

Then update `data/model-lifecycle.json`, set `verified_on` to today, and run
`python3 scripts/check_model_lifecycle.py`. The check is run in CI on every push.

## The models this deployment names today

Verified 2026-10-11 against `bedrock:GetFoundationModel` in us-east-1.

| Model | Provider | Status | Tier |
|---|---|---|---|
| `amazon.nova-micro-v1:0` | Amazon | ACTIVE | `front_desk`, cheap |
| `amazon.nova-lite-v1:0` | Amazon | ACTIVE | `analyst`, cheap |
| `amazon.nova-pro-v1:0` | Amazon | ACTIVE | `reader`, cheap |
| `deepseek.v3.2` | DeepSeek | ACTIVE | `coder`, cheap |
| `anthropic.claude-haiku-5-5` | Anthropic | ACTIVE | `front_desk`, frontier |
| `anthropic.claude-sonnet-5` | Anthropic | ACTIVE | `analyst` and `reader`, frontier |
| `anthropic.claude-opus-5-5` | Anthropic | ACTIVE | hybrid example, frontier |

The four cheap-tier models are `ON_DEMAND`, which means they survive the single-Region residency rule
that disqualifies every inference-profile-only model. The three Claude models are
`INFERENCE_PROFILE` only, so a client whose contract names a single Region cannot use them. That is
recorded per model in the table, because it decides which models a compliant deployment may name.

**One open question, deliberately not guessed.** The brief flags Nova Micro as past its stated
end-of-life floor. The API reports it `ACTIVE` and `ON_DEMAND` today, and nobody has read the model
card's support window, so its `eol_date` is null. Reading it is the first item of the monthly cadence.

## Priced

**A forced swap is not a configuration edit.** The manifest makes the model a line of configuration,
which is what "models are configuration" buys us, and the cost sits either side of that line:

| Item | Cost |
|---|---|
| The config change itself | Minutes: manifest, render, restart the gateway. Two lines and a `docker compose up -d` |
| The quality check | 60 golden questions, which is the real cost. It needs a full run at 92 percent retrieval and 85 percent support before the client sees the new model, and a numeric mismatch blocks the release regardless of averages |
| The client conversation | Not ours to schedule. A client whose answers change should hear it from us before they notice |
| The frontier lane | Per million tokens, the difference between tiers is roughly 30x on input between Nova Micro and Haiku 5.5. Recorded in the brief's pricing section rather than here |

An unplanned swap costs the same work plus the credibility of having been surprised. That is the whole
argument for spending an hour a month on a table.

## What a deprecation looks like when it lands

1. CI fails on `check_model_lifecycle.py`, naming the model, its status, and the replacement on file.
2. Pick the replacement from the table. If the replacement is on the frontier lane, check the
   residency rule for that client first, because an inference profile may be disqualified.
3. Update the manifest, render, restart.
4. Run the golden questions. A hard-fail category blocks the swap until it is understood.
5. Tell the client what changed and when, in plain words, with the support rate before and after.

## What this does not cover

The office build names no remote models, so the check reports zero for it and passes. Its model is a
file we choose to serve, with no vendor end-of-life to track. What it does have is a *hardware* and
*quantisation* lifecycle, which is a different problem and belongs with the office build's own
runbook rather than here.
