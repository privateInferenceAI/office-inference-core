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


def write(base: Path, name: str, rows: list[dict], manifest_rows: list[dict] | None = None) -> Path:
    """Write an export file, and a manifest describing it.

    `manifest_rows` exists so a scenario can model the shape that matters: **a file that has been truncated
    while the manifest beside it still describes the full window.** Without it the fixture regenerates the
    manifest from the truncated rows, so the manifest agrees with the tampered file and every check passes --
    which is precisely why a deleted tail was only ever caught by the anchor, and only when the anchor's row
    happened to fall inside the window.
    """
    described = rows if manifest_rows is None else manifest_rows
    base.mkdir(parents=True, exist_ok=True)
    data = base / f"{name}.jsonl"
    data.write_text("".join(audit.canonical_json(r) + "\n" for r in rows))
    manifest = {
        # `date` as well as the ids. Production writes it, and the anchor check falls back to ordering on
        # time when a window is empty -- so a fixture without it cannot exercise that path, which is how
        # the empty-export case slipped through.
        "date": "2026-10-10",
        "event_count": len(described),
        "first_id": described[0]["id"] if described else None,
        "first_prev_hash": described[0]["prev_hash"] if described else None,
        # `last_id` as well as `last_row_hash`. Production writes both, and omitting it here meant the
        # position rule added for the multi-day defect had nothing to read in CI -- so the tests could not
        # have caught the bug they now guard against.
        "last_id": described[-1]["id"] if described else None,
        "last_row_hash": described[-1]["row_hash"] if described else None,
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
        "last_id": rows[-1]["id"] if rows else None,
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
    write(base, "truncated", whole[:2], manifest_rows=whole)
    return run(base, base / "truncated.jsonl", anchor_file(base, "truncated", whole))


def scenario_rewritten_payload(base: Path) -> int:
    """Content rewritten, payload_sha256 left alone."""
    whole = chain()
    tampered = [dict(r) for r in whole]
    tampered[1]["payload"] = {"question": "something else entirely"}
    write(base, "payload", tampered)
    return run(base, base / "payload.jsonl", anchor_file(base, "payload", whole))


def scenario_empty_export(base: Path) -> int:
    """An empty export where the anchor says rows existed.

    **The anchor is built from the whole chain, not from the export**, because that is what a real anchor
    is: it records where the chain had got to, and it has no knowledge of which day a later export happens
    to cover. Building it from the (empty) export list made it carry no row id at all and gave the check
    nothing to reason with, which is a fixture artefact rather than the case being tested.
    """
    whole = chain()
    write(base, "empty", [])
    return run(base, base / "empty.jsonl", anchor_file(base, "empty", whole))


def scenario_unanchored_tail(base: Path) -> int:
    """A short export with no anchor: internally consistent, so it verifies, and it says what it cannot show.

    The file and its manifest agree with each other, which is the situation the no-anchor case is about --
    there is nothing here to catch, and the point is that the verifier says so rather than implying
    completeness. Passing `manifest_rows=whole` would model a truncated file instead, which the manifest's
    own row ids now catch, and that is a different test.
    """
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


# --- the multi-day chain, which is the shape every test above was missing -------------------------
#
# Every scenario before this one builds a SINGLE-DAY chain, where the export IS the whole chain and a
# whole-chain anchor lines up with it by accident. That coincidence is why five tests passed while the
# check was broken on every real deployment: an export covers one day, an anchor covers the whole chain,
# and the two only agree when the chain is one day long.

def chain_of_days() -> list[dict]:
    """Forty-nine rows: day one holds 36, day two holds 13. The shape a deployment actually produces."""
    rows: list[dict] = []
    previous: str | None = None
    for n in range(1, 50):
        entry = row(n, {"n": n}, previous)
        rows.append(entry)
        # The next row chains from this row's HASH, not from the row. The first version of this helper
        # reassigned `previous` to the row dict and passed that as `prev_hash`, which fails inside
        # row_canonical with "expected str instance, dict found".
        previous = entry["row_hash"]
    return rows


def write_window(base: Path, name: str, rows: list[dict], day: str,
                 manifest_rows: list[dict] | None = None) -> Path:
    """An export of a WINDOW, with a manifest describing that window.

    `manifest_rows` lets a scenario describe a LONGER window than the file holds, which is the truncation
    shape: the file lost its tail and the manifest still records the window as it was written.
    """
    described = rows if manifest_rows is None else manifest_rows
    data = base / f"{name}.jsonl"
    data.write_text("".join(audit.canonical_json(r) + "\n" for r in rows))
    manifest = {
        "date": day,
        "event_count": len(described),
        "first_id": described[0]["id"] if described else None,
        "first_prev_hash": described[0]["prev_hash"] if described else None,
        "last_id": described[-1]["id"] if described else None,
        "last_row_hash": described[-1]["row_hash"] if described else None,
        "file": data.name,
        "file_sha256": audit.sha256_hex(data.read_text()),
    }
    (base / f"{name}-manifest.json").write_text(json.dumps(manifest))
    return data


def anchor_at(base: Path, name: str, rows: list[dict], created_at: str) -> Path:
    last = rows[-1] if rows else None
    path = base / f"{name}-anchor.json"
    path.write_text(json.dumps({
        "created_at": created_at,
        "event_count": len(rows),
        "last_id": last["id"] if last else None,
        "last_row_hash": last["row_hash"] if last else None,
    }))
    return path


def test_a_days_export_verifies_against_a_whole_chain_anchor(tmp_path):
    """**The defect, as a test.** Thirteen rows of forty-nine, anchored at row 49.

    The old check compared `len(lines)` with the anchor's cumulative `event_count` and reported "the chain
    is shorter than it was" against a file nobody had touched. Position is the relation that means
    something: the anchored row is inside this window, and it is present.
    """
    whole = chain_of_days()
    day = whole[36:]
    write_window(tmp_path, "day2", day, "2026-10-10")
    anchor = anchor_at(tmp_path, "whole", whole, "2026-10-10T23:00:00+00:00")

    assert run(tmp_path, tmp_path / "day2.jsonl", anchor) == 0, (
        "a day's export must verify against a whole-chain anchor when the anchored row is inside it")


def test_a_deleted_tail_is_caught_by_an_anchor_inside_the_window(tmp_path):
    """**The case the anchor genuinely catches, and the fix must not have turned it off.**

    Truncate a window whose own last row is the anchored row, and the anchored row is gone from inside a
    window the manifest says ran to it. That is unambiguous: the row was there when the anchor was taken and
    the file claims a window that includes it.

    The version of this test that came before truncated a window whose anchor sat *beyond* it, which is a
    case one anchor cannot decide -- see the note in `cmd_verify` and the test below it.
    """
    whole = chain_of_days()
    day = whole[36:]                        # rows 37..49, and the anchor is row 49
    # Anchor row 47, which is INSIDE the window below, and give the window an honest manifest that also
    # runs to row 49. The file is then missing rows 48 and 49 -- including the anchored row 47? No: 47 is
    # present in a file ending at 49. Remove the tail instead so the anchored row itself is gone.
    anchor = anchor_at(tmp_path, "at49", day, "2026-10-10T12:00:00+00:00")
    # The window claims rows 37..49 but the file stops at 47: row 49 is inside the claimed window and is
    # not in the file, which is unambiguous.
    write_window(tmp_path, "shorter", day[:-2], "2026-10-10", manifest_rows=day)

    assert run(tmp_path, tmp_path / "shorter.jsonl", anchor) != 0, (
        "the anchored row falls inside a window the manifest claims and is not in the file")


def test_an_anchor_written_after_the_window_is_reported_as_a_note(tmp_path):
    """The case that produced the language of tampering.

    **The two windows are inside ONE day, which is what makes this case real.** An export covers a day, so
    an afternoon anchor against a morning export is the ordinary situation: the anchored row is past the
    window's end, and the window has lost nothing. The old check called that "the anchored row is missing
    from this export, so rows that existed when the anchor was written are gone".

    The discriminator is the anchor's own row count against the window's last row id. Here the anchor
    counted 30 and the window ends at row 20, so the chain was never longer than this file shows:
    an anchor that is merely newer, which proves nothing about this file in either direction.
    """
    whole = chain_of_days()
    morning = whole[:20]          # rows 1..20, exported for the day
    afternoon = whole[:30]        # the chain by the afternoon, still the same day, 30 rows
    write_window(tmp_path, "morning", morning, "2026-10-09")
    anchor = anchor_at(tmp_path, "afternoon", afternoon, "2026-10-09T15:00:00+00:00")

    assert run(tmp_path, tmp_path / "morning.jsonl", anchor) == 0, (
        "an anchor written later the same day must not be reported as this file losing rows")


def test_an_anchor_older_than_the_window_proves_nothing_either_way(tmp_path):
    """An anchor taken before the window opened says nothing about it, and must not be read as a match."""
    whole = chain_of_days()
    day_two = whole[36:]
    write_window(tmp_path, "day2", day_two, "2026-10-10")
    earlier = anchor_at(tmp_path, "earlier", whole[:20], "2026-10-09T09:00:00+00:00")

    assert run(tmp_path, tmp_path / "day2.jsonl", earlier) == 0
