"""The deployment-state report."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import deployment_state  # noqa: E402


def test_a_changed_image_is_drift(tmp_path):
    compose = tmp_path / "compose.yml"
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}\n")
    compose.write_text("services:\n  one:\n    image: image:1\n")
    expected = deployment_state.state(compose, manifest)
    compose.write_text("services:\n  one:\n    image: image:2\n")
    actual = deployment_state.state(compose, manifest)
    assert deployment_state.differences(expected, actual) == ["one: expected 'image:1', found 'image:2'"]


def test_the_same_pins_do_not_drift(tmp_path):
    compose = tmp_path / "compose.yml"
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}\n")
    compose.write_text("services:\n  one:\n    image: image:1\n")
    expected = deployment_state.state(compose, manifest)
    assert deployment_state.differences(expected, deployment_state.state(compose, manifest)) == []
