"""The answer path has two profiles and one setting."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "services" / "api"))


def test_the_accuracy_profile_is_the_measured_configuration():
    import answer_profile
    profile = answer_profile.PROFILES["accuracy"]
    assert (profile.seed_passages, profile.snippet_chars, profile.max_evidence, profile.search_limit) == \
        (12, 1600, 32, 12)


def test_the_economy_profile_is_the_older_smaller_configuration():
    import answer_profile
    profile = answer_profile.PROFILES["economy"]
    assert (profile.seed_passages, profile.snippet_chars, profile.max_evidence, profile.search_limit) == \
        (6, 700, 24, 8)


def test_the_default_is_accuracy(monkeypatch):
    import answer_profile
    monkeypatch.delenv("OI_ANSWER_PROFILE", raising=False)
    assert answer_profile.current().name == "accuracy"


def test_an_unknown_profile_falls_back_to_accuracy(monkeypatch):
    import answer_profile
    monkeypatch.setenv("OI_ANSWER_PROFILE", "turbo")
    assert answer_profile.current().name == "accuracy"


def test_the_profile_can_be_selected(monkeypatch):
    import answer_profile
    monkeypatch.setenv("OI_ANSWER_PROFILE", "economy")
    assert answer_profile.current().name == "economy"
