#!/usr/bin/env python3
"""Ask the running services what they accept, instead of writing a client from documentation.

Every claim below is checked against the service rather than against a doc, because this project has
already paid for assuming an API shape: the parser call sent its options as one nested field, the service
ignored the field in silence, and the result looked like a missing feature rather than a malformed
request.

Three things are in question and each has a cheap answer from the live service.

  * TEI's embedding endpoint. `/embed` is the current one and `/embeddings` is the older batch form, and
    an earlier version of the harness may accept either or both. The probe reports which answer, what
    the response body looks like, and whether the vector is the 1024 dimensions the collection expects.
  * Whether a batch works and what it returns for each input.
  * Qdrant's upsert and payload shape, and whether a filtered count sees what was written.

Read-only with one exception: it writes points under a probe document id into the real collection and
then deletes them again. That is deliberate, because a probe that only reads cannot discover that a write
fails, and the cleanup is by a filter that names exactly what it created.

Run from the repo root on the box:

    docker run --rm --network ai-net -v /opt/oi-core:/w -w /w \\
      --entrypoint python ghcr.io/docling-project/docling-serve-cpu:v1.36.0 \\
      /w/tests/fixtures/api_contract_probe.py
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

EMBED = "http://embeddings:80"
VECTOR = "http://vector:6333"
COLLECTION = "documents"
PROBE_DOC = "contract-probe"


def call(url: str, method: str = "GET", body: dict | None = None, timeout: int = 60):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            try:
                return response.status, json.loads(raw)
            except json.JSONDecodeError:
                return response.status, raw[:200].decode(errors="replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, raw[:200].decode(errors="replace")
    except Exception as exc:  # noqa: BLE001 - the point is to report
        return None, repr(exc)


def main() -> int:
    findings: dict[str, object] = {}

    print("=== TEI: what the embed service says about itself ===")
    status, info = call(f"{EMBED}/info")
    print(f"  /info -> {status}")
    if isinstance(info, dict):
        for key in ("model_id", "model_dtype", "max_input_length", "max_client_batch_size"):
            print(f"    {key}: {info.get(key)}")

    print()
    print("=== TEI: which embedding endpoint answers ===")
    text = "The mileage rate for personal vehicles."
    for path, body in (
        ("/embed", {"inputs": text}),
        ("/embed", {"inputs": [text]}),
        ("/embeddings", {"inputs": [text]}),
        ("/v1/embeddings", {"input": [text], "model": "bge-m3"}),
    ):
        status, payload = call(f"{EMBED}{path}", "POST", body)
        shape = ""
        if isinstance(payload, list) and payload and isinstance(payload[0], list):
            shape = f"list[list[float]], inner length {len(payload[0])}"
        elif isinstance(payload, list) and payload:
            shape = f"list[{type(payload[0]).__name__}], first {str(payload[0])[:60]}"
        elif isinstance(payload, dict):
            shape = f"dict keys {sorted(payload.keys())[:6]}"
            data = payload.get("data")
            if isinstance(data, list) and data:
                first = data[0]
                if isinstance(first, dict) and isinstance(first.get("embedding"), list):
                    shape += f", embedding length {len(first['embedding'])}"
        print(f"  POST {path} {json.dumps(body)[:44]:<46} -> {status}  {shape}")
        findings[f"{path} {list(body.keys())[0]}"] = {"status": status}

    print()
    print("=== TEI: does a batch return one vector per input? ===")
    texts = ["one", "two", "three"]
    for path, body in (("/embed", {"inputs": texts}), ("/embeddings", {"inputs": texts})):
        status, payload = call(f"{EMBED}{path}", "POST", body)
        count = None
        dims = None
        if isinstance(payload, list):
            count = len(payload)
            if payload and isinstance(payload[0], list):
                dims = len(payload[0])
        elif isinstance(payload, dict) and isinstance(payload.get("data"), list):
            count = len(payload["data"])
            embedding = payload["data"][0].get("embedding") if payload["data"] else None
            dims = len(embedding) if embedding else None
        print(f"  POST {path} with {len(texts)} inputs -> {status}  vectors {count}, dims {dims}")

    print()
    print("=== Qdrant: collection shape and a write that is then removed ===")
    status, collection = call(f"{VECTOR}/collections/{COLLECTION}")
    print(f"  GET collection -> {status}")
    if isinstance(collection, dict):
        result = collection.get("result") or {}
        vectors = ((result.get("config") or {}).get("params") or {}).get("vectors") or {}
        print(f"    vectors config: {json.dumps(vectors)[:120]}")
        print(f"    points_count: {result.get('points_count')}")

    status, _ = call(f"{EMBED}/embed", "POST", {"inputs": [text]})
    vector = None
    if status == 200:
        _, payload = call(f"{EMBED}/embed", "POST", {"inputs": [text]})
        if isinstance(payload, list) and payload and isinstance(payload[0], list):
            vector = payload[0]
        elif isinstance(payload, dict) and isinstance(payload.get("data"), list):
            vector = payload["data"][0].get("embedding")

    if vector is None:
        print("  no vector available, so the write test is skipped")
    else:
        point = {
            "points": [{
                "id": "11111111-1111-1111-1111-111111111111",
                "vector": vector,
                "payload": {"doc_id": PROBE_DOC, "text": text, "page_start": 1, "page_end": 1,
                            "chunk_id": "probe", "acl": "company"},
            }]
        }
        status, response = call(f"{VECTOR}/collections/{COLLECTION}/points?wait=true", "PUT", point)
        print(f"  PUT one point with a payload -> {status}  {json.dumps(response)[:100]}")

        status, counted = call(f"{VECTOR}/collections/{COLLECTION}/points/count", "POST",
                               {"filter": {"must": [{"key": "doc_id", "match": {"value": PROBE_DOC}}]},
                                "exact": True})
        total = (counted or {}).get("result", {}).get("count") if isinstance(counted, dict) else None
        print(f"  POST count filtered by payload -> {status}, count {total}")

        status, found = call(f"{VECTOR}/collections/{COLLECTION}/points/scroll", "POST",
                             {"filter": {"must": [{"key": "doc_id", "match": {"value": PROBE_DOC}}]},
                              "limit": 1, "with_payload": True, "with_vector": False})
        payload_keys = None
        if isinstance(found, dict):
            points = (found.get("result") or {}).get("points") or []
            if points:
                payload_keys = sorted((points[0].get("payload") or {}).keys())
        print(f"  payload keys round-tripped: {payload_keys}")

        status, _ = call(f"{VECTOR}/collections/{COLLECTION}/points/delete?wait=true", "POST",
                         {"filter": {"must": [{"key": "doc_id", "match": {"value": PROBE_DOC}}]}})
        print(f"  DELETE the probe points -> {status}  (cleanup)")

    print()
    print("Findings are what the services answered, not what a document said they would.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
