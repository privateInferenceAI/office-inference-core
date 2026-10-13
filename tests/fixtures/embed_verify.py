#!/usr/bin/env python3
"""Step 3.2's claim, measured: text becomes a vector, deterministically, on the private network.

The runbook's table asserted this and had no section recording it, which is the state this project keeps
getting caught in: a claim in a summary and no evidence behind it. This measures the four things the claim
rests on, so the section can be re-checked rather than believed: the model and its dimensions, whether the
same text gives the same vector, whether a batch preserves order, and whether different text gives a
different vector.

Run from the repo root on the box:

    docker run --rm --network ai-net -v /opt/oi-core:/w:ro -v /opt/oi-core/tests/fixtures/embed_verify.py:/e.py:ro \
      -w /w --entrypoint python ghcr.io/docling-project/docling-serve-cpu:v1.36.0 /e.py
"""
from __future__ import annotations

import json
import sys
import urllib.request

sys.path.insert(0, "/w/services/ingestion")
import ingest  # noqa: E402

services = ingest.Services()

with urllib.request.urlopen("http://embeddings:80/info", timeout=60) as response:
    info = json.loads(response.read())
print("=== the service ===")
for key in ("model_id", "model_dtype", "max_input_length", "max_client_batch_size", "model_sha"):
    print(f"  {key}: {info.get(key)}")

text = "The after-hours labour rate is 1.5x the standard rate."
first = ingest.embed_texts(services, [text])[0]
second = ingest.embed_texts(services, [text])[0]
print()
print("=== determinism ===")
print(f"  dimensions: {len(first)}")
print(f"  two calls, same text, identical: {first == second}")
print(f"  first six values: {[round(v, 6) for v in first[:6]]}")
print(f"  L2 norm: {sum(v * v for v in first) ** 0.5:.4f}")

# Order preserved, which matters because the worker zips vectors back onto chunks by position.
batch = ["alpha first", "beta second", "gamma third"]
vectors = ingest.embed_texts(services, batch)
solo = [ingest.embed_texts(services, [t])[0] for t in batch]
print()
print("=== batch order ===")
print(f"  batch of 3 returned {len(vectors)} vectors")
print(f"  each matches its own solo call: {all(v == s for v, s in zip(vectors, solo))}")

# Different text must not collide, or the same vector would stand for anything.
other = ingest.embed_texts(services, ["An unrelated sentence about refrigerant."])[0]
overlap = sum(a * b for a, b in zip(first, other))
print()
print("=== discrimination ===")
print(f"  cosine against an unrelated sentence: {overlap:.4f}")
