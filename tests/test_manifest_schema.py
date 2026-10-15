"""Every manifest against the schema it claims to follow, with the strict reading.

**Why this file exists, and what it found on its first run.** `schemas/manifest.schema.json` declares
`additionalProperties: false` and nothing enforced it. `scripts/validate_manifests.py` is a thorough program
with its own rules, and it does not read the schema, so the two had drifted in both directions:

* a key added to a manifest — `oi_ui_root`, added while wiring the app's build — validated clean and was
  invalid, which is the direction that lets an undeclared field reach a deployment nobody validated;
* **the schema's `access.groups` accepted only a bare list, while every manifest in this repository and the
  manual both use `staff: {roots: [company]}`.** So every manifest was invalid against its own schema and the
  validator reported all six clean. A schema nobody enforces is documentation, and documentation that
  disagrees with the code is worse than none.

**The strict reading is the point.** A validation that ignores unknown keys is a validation that accepts a
typo: `access.egres` instead of `access.egress` would change nothing and be reported as fine. So these tests
assert the schema as written, including that closed objects stay closed.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "schemas" / "manifest.schema.json"
MANIFESTS = sorted((ROOT / "manifests").glob("*.yaml"))

jsonschema = pytest.importorskip("jsonschema", reason="jsonschema is in requirements-dev.txt")


def schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text())


def test_the_schema_is_closed_and_the_check_reads_that():
    """Without this the tests below would be vacuous: an open schema accepts anything."""
    s = schema()
    assert s.get("additionalProperties") is False, (
        "the top level of the schema is not closed, so an undeclared key would pass and these tests would "
        "prove nothing")


def test_there_are_manifests_to_check():
    assert len(MANIFESTS) >= 6, f"only found {len(MANIFESTS)} manifests; is the path right?"


@pytest.mark.parametrize("path", MANIFESTS, ids=lambda p: p.name)
def test_the_manifest_is_valid_against_the_schema(path: Path):
    """The strict read: unknown keys are errors, not warnings."""
    jsonschema.validate(yaml.safe_load(path.read_text()), schema())


def test_an_undeclared_key_is_refused():
    """**The check can fail, stated as a case rather than assumed.** This is the exact mistake that was made
    while wiring the app's build: a plausible key, added to a manifest, accepted by the repository's own
    validator, and never read by any code."""
    manifest = yaml.safe_load(MANIFESTS[0].read_text())
    manifest["oi_ui_root"] = "/opt/oic-ui"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(manifest, schema())


def test_a_misspelled_section_is_refused():
    """The case that makes the strict reading worth having: a typo that changes nothing and is caught."""
    manifest = yaml.safe_load(MANIFESTS[0].read_text())
    manifest["acess"] = manifest.pop("access", {})
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(manifest, schema())


def test_the_documented_group_form_is_accepted():
    """`staff: {roots: [company]}` is what the manual shows and what every manifest uses.

    The schema accepted only the bare list until this was found, so the documentation, the manifests and the
    renderer agreed and the schema disagreed with all three.
    """
    manifest = yaml.safe_load(MANIFESTS[0].read_text())
    manifest["access"]["groups"] = {"staff": {"roots": ["company"]}}
    jsonschema.validate(manifest, schema())


def test_a_group_with_no_roots_is_refused():
    """A group that grants nothing is a permission model that reads as if it grants something."""
    manifest = yaml.safe_load(MANIFESTS[0].read_text())
    manifest["access"]["groups"] = {"staff": {"name": "Staff"}}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(manifest, schema())
