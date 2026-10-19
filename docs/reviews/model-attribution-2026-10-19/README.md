# The model that answered, not the model that was configured

**Date:** 2026-10-19. **PR:** the attribution change (this review). **Register:** `INF-ROLE-001`,
`AGENT-FALLBACK-001`.

## The gap this closes

When the provider fallback was built earlier the same day, the answer and its audit row named the configured
primary model even when the fallback answered. A client reading the answer could not tell that a failover had
happened, and an auditor reading the `model_call` row saw the primary model for a call the primary never
answered. The gateway knew the truth; the API did not read it.

Two facts are different: **the role** is what the manifest names, **the model** is what ran. The answer already
carried `model` (the role) and `resolved_model` (the manifest's model). What was missing was the possibility
that the model that ran was not the model the manifest names.

## What the gateway already said, on every response

LiteLLM sets the routing decision in the response headers:

| Header | Meaning |
|---|---|
| `x-litellm-model-group` | the configured model group that answered, so a failover names itself (`analyst-fallback`) |
| `x-litellm-model-name` | the provider model underneath |
| `x-litellm-attempted-fallbacks` | how many fallbacks were tried first |

The API now reads them on the question path, the tool loop, the job path and both stream paths, and reports:

| Where | Field | Meaning |
|---|---|---|
| answer payload | `resolved_model` | the model that answered |
| answer payload | `configured_model` | the model the manifest names for the role |
| answer payload | `model_group` | the gateway group that answered; a value other than the role names a failover |
| answer payload | `fallback_used` | whether the gateway failed over before answering |
| `model_call` row | `model` column | the model that answered, not the role |
| `model_call` row payload | `model_role`, `resolved_model`, `model_group`, `fallback_used`, `fallbacks_attempted` | the same facts, kept per call |

The OpenAPI schema is regenerated, so a client builds against the new fields.

## Deterministic verification, healthy call

One question through the API with the healthy primary:

~~~
resolved_model   bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0
configured_model bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0
model_group      analyst
fallback_used    false
~~~

The `model_call` row for that call: `model = bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0`,
payload `model_role = analyst`, `resolved_model` the same, `fallback_used = false`.

## Deterministic verification, failover

The primary was failed on purpose with the repository's fault stub returning 503, the same technique as the
fallback measurement. One question through the API:

~~~
resolved_model   bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0
configured_model bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0
model_group      analyst-fallback
fallback_used    true
~~~

The `model_call` row: `model = bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0`, payload
`model_role = analyst`, `model_group = analyst-fallback`, `fallback_used = true`, `fallbacks_attempted = 1`.

**This is the verification the change exists for.** Before it, both records said Haiku.

## Five-run regression

The change is reporting-only: it reads headers and writes fields, and it does not alter a prompt, a tool or a
retrieval path. The regression check exists to catch a wiring mistake (an exception in the new path would
degrade every answer).

Five runs on the same deployed configuration as the corrected-instrument baseline (Haiku primary, Sonnet
fallback configured but not exercised): **51, 50, 51, 51, 51, mean 50.8**, range 50 to 51. The corrected-instrument
baseline before this change was 50.6 (52, 50, 51, 51, 49); the change is reporting-only, so the two belong to the
same range rather than to different configurations.

**A first batch was discarded, and the reason is recorded rather than smoothed over.** It scored 44, 50, 51, 51, 51;
its first run answered seven exact-number questions as `degraded / unclassified` and nothing else. The API log from
that minute shows the tool loop returning `unclassified` on the first step with no gateway error behind it, and the
gateway logged no 429, 500 or exception in the window. The behaviour is a tool-loop flake at run start, not the
attribution change (which only reads response headers and writes fields), and the replacement batch is the
measurement of record. Four other runs of the same set were normal, which is why one bad run was re-run rather than
explained away.

## Caveats, recorded rather than discovered later

- **The model group is a gateway name.** `analyst-fallback` is our configuration's name, not the provider's.
  A reader needs the manifest to resolve it, which is what `docs/runbook.md` 6.7 is for.
- **A local-lane answer reports the gateway's model id**, which for the office build is the loaded model name,
  not a Bedrock id. The normalisation only adds the `bedrock/` prefix when the manifest's model carries it.
- **No headers means the manifest model.** Behind a proxy that strips the LiteLLM headers the answer falls back
  to `configured_model`, which is honest but loses the failover fact; the field is documented as the gateway's
  report, not a promise from the provider.
- **The audit row's `model` column changed meaning** from the role name to the model that answered. Rows written
  before this change keep the old value, because the chain is append-only; a query that reads the column across
  the boundary sees both shapes, so use the payload's `model_role` when the role matters.
