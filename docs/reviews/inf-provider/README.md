# OpenAI-compatible provider: DeepSeek

**Date:** 2026-10-23. **Status:** INF-PROVIDER-001 is verified.

## What was built

A model role may declare an OpenAI-compatible provider:

```yaml
provider:
  base_url: https://api.deepseek.com
  model: deepseek-flash
  api_key_env: DEEPSEEK_API_KEY
```

The renderer emits a LiteLLM entry with `model: openai/deepseek-flash`, the base URL, and
`api_key: os.environ/DEEPSEEK_API_KEY`. The key is never written into the rendered gateway config.

The validator refuses a provider on any profile other than `cloud`, because an office build must not send a
generation off the box. The schema, renderer and tests are in `scripts` and `tests/test_render_manifest.py`.

The key is in the box's mode-600 rendered env only. The compose file passes it to LiteLLM as
`DEEPSEEK_API_KEY`; it appears nowhere in this evidence or the repository.

## Live comparison

The same three questions ran through the API with the DeepSeek provider config, then again with the Bedrock
config restored.

| Question | DeepSeek | Bedrock |
|---|---|---|
| mileage rate | 0.67 USD per mile with citations | 0.67 USD per mile with citations |
| travel approval under 500 | direct manager | direct manager |
| receipt threshold | 25 USD or more | 25 USD or more |

Both runs returned successful answers with citations. The models are labelled `analyst` in both configs; the
provider lane is visible in the rendered config, not in the answer payload.

## Office refusal

A provider on the office profile fails validation with "must not send generations off the box". The render test
asserts the refusal.

## Files

- `deepseek-suite.json`: the three DeepSeek answers.
- `bedrock-suite.json`: the same three questions on Bedrock.
- `litellm.yaml`: the rendered provider config; it contains the environment reference only.
- `example-deepseek.yaml`: the temporary manifest used for the comparison.
- `SHA256SUMS`.
