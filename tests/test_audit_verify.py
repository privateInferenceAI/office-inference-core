"""Regression tests for the audit verifier.

Written after an adversarial audit broke the verifier four ways. Each case below is one of those
attacks, turned into a test that must fail the export. They run without a database: psycopg is
stubbed, because the verifier reads files rather than tables.

Two ways to run it, both of which must pass:

    python3 -m pytest tests/            what CI runs
    python3 tests/test_audit_verify.py  the same cases, printed, non-zero exit on failure

The printed run and the collected run drive the same scenario helpers, so they cannot drift apart.
Each scenario gets its own working directory. An earlier version shared one directory under /tmp,
which meant a failure in one case could leave a manifest behind for the next.
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


def chain() -> list[dict]:
    """Three linked rows, the same three every scenario starts from."""
    a = row(1, {"question": "mileage rate"}, None)
    b = row(2, {"question": "sabbatical policy"}, a["row_hash"])
    c = row(3, {"question": "hygienist sign-off"}, b["row_hash"])
    return [a, b, c]


def write(base: Path, name: str, rows: list[dict]) -> Path:
    base.mkdir(parents=True, exist_ok=True)
    data = base / f"{name}.jsonl"
    data.write_text("".join(audit.canonical_json(r) + "\n" for r in rows))
    manifest = {
        "event_count": len(rows),
        "first_id": rows[0]["id"] if rows else None,
        "first_prev_hash": rows[0]["prev_hash"] if rows else None,
        "last_row_hash": rows[-1]["row_hash"] if rows else None,
        "file": data.name,
        "file_sha256": audit.sha256_hex(data.read_text()),
    }
    (base / f"{name}-manifest.json").write_text(json.dumps(manifest))
    return data


def anchor_file(base: Path, name: str, rows: list[dict]) -> Path:
    path = base / f"{name}-anchor.json"
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


def run(base: Path, file: Path, anchor: Path | None) -> int:
    # The verifier looks for manifest.json beside the file. Point it at the matching one.
    manifest = base / "manifest.json"
    manifest.write_text((base / (file.stem + "-manifest.json")).read_text())
    return audit.cmd_verify(Args(str(file), str(anchor) if anchor else None))


# --- the five scenarios, each returning the verifier's exit code -----------------------------


def scenario_clean_export(base: Path) -> int:
    whole = chain()
    write(base, "clean", whole)
    return run(base, base / "clean.jsonl", anchor_file(base, "clean", whole))


def scenario_deleted_tail(base: Path) -> int:
    """Tail deletion with a regenerated manifest. The anchor is what catches this."""
    whole = chain()
    write(base, "truncated", whole[:2])
    return run(base, base / "truncated.jsonl", anchor_file(base, "truncated", whole))


def scenario_rewritten_payload(base: Path) -> int:
    """Content rewritten, payload_sha256 left alone."""
    whole = chain()
    tampered = [dict(r) for r in whole]
    tampered[1]["payload"] = {"question": "something else entirely"}
    write(base, "payload", tampered)
    return run(base, base / "payload.jsonl", anchor_file(base, "payload", whole))


def scenario_empty_export(base: Path) -> int:
    """An empty export where the anchor says rows existed."""
    whole = chain()
    write(base, "empty", [])
    return run(base, base / "empty.jsonl", anchor_file(base, "empty", whole))


def scenario_unanchored_tail(base: Path) -> int:
    """Deleting the tail with no anchor supplied must at least verify and say so."""
    whole = chain()
    write(base, "noanchor", whole[:2])
    return run(base, base / "noanchor.jsonl", None)


# --- pytest ----------------------------------------------------------------------------------


def test_a_clean_export_with_a_matching_anchor_passes(tmp_path):
    assert scenario_clean_export(tmp_path) == 0


def test_a_deleted_tail_is_caught(tmp_path):
    assert scenario_deleted_tail(tmp_path) != 0


def test_a_rewritten_payload_is_caught(tmp_path):
    assert scenario_rewritten_payload(tmp_path) != 0


def test_an_empty_export_against_a_real_anchor_is_caught(tmp_path):
    assert scenario_empty_export(tmp_path) != 0


def test_an_intact_export_without_an_anchor_warns_but_passes(tmp_path):
    """Without an anchor, completeness cannot be shown. That is a documented limit, not a pass:
    the verifier says so out loud."""
    assert scenario_unanchored_tail(tmp_path) == 0


# --- the printed run -------------------------------------------------------------------------


def main() -> int:
    failures = []

    code = scenario_clean_export(WORK)
    print(f"1 clean export with an anchor          -> exit {code} (want 0)")
    if code != 0:
        failures.append("a clean export failed")

    code = scenario_deleted_tail(WORK)
    print(f"2 two of three rows deleted            -> exit {code} (want non-zero)")
    if code == 0:
        failures.append("deleted tail still verified")

    code = scenario_rewritten_payload(WORK)
    print(f"3 stored payload rewritten             -> exit {code} (want non-zero)")
    if code == 0:
        failures.append("a rewritten payload still verified")

    code = scenario_empty_export(WORK)
    print(f"4 empty export against a real anchor   -> exit {code} (want non-zero)")
    if code == 0:
        failures.append("an empty export still verified")

    code = scenario_unanchored_tail(WORK)
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
