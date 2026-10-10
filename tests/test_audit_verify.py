"""Regression tests for the audit verifier.

Written after an adversarial audit broke the verifier four ways. Each case below is one of those
attacks, turned into a test that must fail the export. They run without a database: psycopg is
stubbed, because the verifier reads files rather than tables.

Run: python3 tests/test_audit_verify.py
"""
import json
import sys
import types
from pathlib import Path

# The service imports psycopg at module level. Stub it before importing, so the file-level checks
# can be exercised anywhere, including CI, without a database.
if "psycopg" not in sys.modules:
    psycopg = types.ModuleType("psycopg")
    psycopg.connect = lambda *a, **k: None
    psycopg.types = types.ModuleType("psycopg.types")
    psycopg.types.json = types.ModuleType("psycopg.types.json")
    psycopg.types.json.Jsonb = lambda value: value
    sys.modules["psycopg"] = psycopg
    sys.modules["psycopg.types"] = psycopg.types
    sys.modules["psycopg.types.json"] = psycopg.types.json

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "audit"))
import audit  # noqa: E402

WORK = Path("/tmp/oi-verify-test")


def row(index: int, payload: dict, previous: str | None) -> dict:
    entry = {
        "id": index,
        "occurred_at": f"2026-10-10T12:{index:02d}:00+00:00",
        "ts_iso": f"2026-10-10T12:{index:02d}:00.000000+00:00",
        "event_type": "access",
        "actor": "anthony",
        "actor_role": "admin",
        "deployment_id": "test",
        "data_class": "internal",
        "outcome": "ok",
        "tool": "docs",
        "model": None,
        "request_id": None,
        "approval": None,
        "payload": payload,
        "payload_sha256": audit.sha256_hex(audit.canonical_json(payload)),
        "payload_bytes": len(audit.canonical_json(payload)),
        "prev_hash": previous,
    }
    entry["row_hash"] = audit.sha256_hex(audit.row_canonical(entry))
    return entry


def write(name: str, rows: list[dict]) -> Path:
    WORK.mkdir(parents=True, exist_ok=True)
    data = WORK / f"{name}.jsonl"
    data.write_text("".join(audit.canonical_json(r) + "\n" for r in rows))
    manifest = {
        "event_count": len(rows),
        "first_id": rows[0]["id"] if rows else None,
        "first_prev_hash": rows[0]["prev_hash"] if rows else None,
        "last_row_hash": rows[-1]["row_hash"] if rows else None,
        "file": data.name,
        "file_sha256": audit.sha256_hex(data.read_text()),
    }
    (WORK / f"{name}-manifest.json").write_text(json.dumps(manifest))
    return data


def anchor_file(name: str, rows: list[dict]) -> Path:
    path = WORK / f"{name}-anchor.json"
    path.write_text(json.dumps({
        "created_at": "2026-10-10T13:00:00+00:00",
        "event_count": len(rows),
        "last_row_hash": rows[-1]["row_hash"],
    }))
    return path


class Args:
    def __init__(self, file, anchor=None):
        self.file = file
        self.anchor = anchor


def run(file: Path, anchor: Path | None) -> int:
    # The verifier looks for manifest.json beside the file. Point it at the matching one.
    manifest = file.parent / "manifest.json"
    manifest.write_text((file.parent / (file.stem + "-manifest.json")).read_text())
    return audit.cmd_verify(Args(str(file), str(anchor) if anchor else None))


def main() -> int:
    a = row(1, {"question": "mileage rate"}, None)
    b = row(2, {"question": "sabbatical policy"}, a["row_hash"])
    c = row(3, {"question": "hygienist sign-off"}, b["row_hash"])
    whole = [a, b, c]
    failures = []

    # 1. A clean export with a matching anchor must pass.
    write("clean", whole)
    code = run(WORK / "clean.jsonl", anchor_file("clean", whole))
    print(f"1 clean export with an anchor          -> exit {code} (want 0)")
    if code != 0:
        failures.append("a clean export failed")

    # 2. Tail deletion with a regenerated manifest. The anchor is what catches this.
    write("truncated", [a, b])
    code = run(WORK / "truncated.jsonl", anchor_file("truncated", whole))
    print(f"2 two of three rows deleted            -> exit {code} (want non-zero)")
    if code == 0:
        failures.append("deleted tail still verified")

    # 3. Content rewritten, payload_sha256 left alone.
    tampered = [dict(a), dict(b), dict(c)]
    tampered[1]["payload"] = {"question": "something else entirely"}
    write("payload", tampered)
    code = run(WORK / "payload.jsonl", anchor_file("payload", whole))
    print(f"3 stored payload rewritten             -> exit {code} (want non-zero)")
    if code == 0:
        failures.append("a rewritten payload still verified")

    # 4. An empty export where the anchor says rows existed.
    write("empty", [])
    code = run(WORK / "empty.jsonl", anchor_file("empty", whole))
    print(f"4 empty export against a real anchor   -> exit {code} (want non-zero)")
    if code == 0:
        failures.append("an empty export still verified")

    # 5. Deleting the tail with no anchor supplied must at least say so.
    write("noanchor", [a, b])
    code = run(WORK / "noanchor.jsonl", None)
    print(f"5 deleted tail, no anchor supplied     -> exit {code} (documented as unprovable)")
    if code != 0:
        failures.append("an intact but unanchored export should not fail, only warn")

    print()
    if failures:
        for item in failures:
            print(f"FAIL: {item}")
        return 1
    print("all four attacks are now caught")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
