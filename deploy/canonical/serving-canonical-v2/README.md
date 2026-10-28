# Canonical v2 serving pin

These are the exact serving files used for the Canonical Second Stack v2 answer tests on
2026-10-27 and 2026-10-28. They are a deployment artifact, not the product branch's default
serving code.

- `tool_loop.py` and `retriever.py` are the candidate files copied from the lab box.
- `litellm.yaml` is the model route.
- Measured with bundle `bundle-table-units-v2-20261028`, collection
  `documents-mixed-v2-20261028`, Kimi K3 on Bedrock, empty temperature, 2,000 max tokens.

The result was 36/38 on one section pass and 37, 38, 38, 38, 37 on the five full 38-role passes.
The section pass before the five-run had PUB_P03 and SYN_A02 failures; the five runs had PUB_P03
in pass 1 and SYN_X02 in pass 5.

Do not edit these files in place. If the product branch is reconciled to equivalent behavior,
replace this pin with a tested commit and record the new measurement.

Deployment steps:
1. Copy `tool_loop.py` to `services/api/tool_loop.py`.
2. Copy `retriever.py` to `services/retrieval/retriever.py`.
3. Render the manifest and environment, build the API image, and start the stack.
4. Verify the collection and run the smoke test before any demo.
