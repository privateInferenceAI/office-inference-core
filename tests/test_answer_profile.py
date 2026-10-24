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
    # **The loop is part of the measured configuration.** The accuracy profile is the one that scored 37 of 37,
    # and that score is on the tool-assisted path, so the profile has to say so rather than leaving it to a
    # transport flag.
    assert profile.tools is True


def test_the_economy_profile_is_the_older_smaller_configuration():
    import answer_profile
    profile = answer_profile.PROFILES["economy"]
    assert (profile.seed_passages, profile.snippet_chars, profile.max_evidence, profile.search_limit) == \
        (6, 700, 24, 8)
    assert profile.tools is False


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


def test_the_profile_decides_whether_the_loop_runs(monkeypatch):
    import answer_profile
    monkeypatch.delenv("OI_ANSWER_PROFILE", raising=False)
    assert answer_profile.tools_enabled(None) is True
    monkeypatch.setenv("OI_ANSWER_PROFILE", "economy")
    assert answer_profile.tools_enabled(None) is False


def test_an_explicit_request_still_overrides_the_profile(monkeypatch):
    # The probes and the integration tests need to run either path on a deployment whose profile chooses the
    # other one. Absent means the deployment decides; an explicit value is the caller's request.
    import answer_profile
    monkeypatch.delenv("OI_ANSWER_PROFILE", raising=False)
    assert answer_profile.tools_enabled(False) is False
    monkeypatch.setenv("OI_ANSWER_PROFILE", "economy")
    assert answer_profile.tools_enabled(True) is True


def test_both_routes_read_the_profile():
    """A setting that one handler reads and the other ignores is the class of defect this repository keeps
    recording. The stream asks the helper, and the JSON path gates its loop on the profile directly."""
    source = (ROOT / "services" / "api" / "app.py").read_text()
    assert "answer_profile.tools_enabled(body.get(\"tools\"))" in source
    assert "if answer_profile.current().tools:" in source
