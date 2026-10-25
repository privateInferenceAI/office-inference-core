"""The temperature field is a provider capability, not a constant."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services" / "api"))


def test_unset_keeps_the_measured_default():
    import answer_params
    assert answer_params.temperature_param({}) == {"temperature": 0}


def test_empty_omits_the_field_for_a_model_that_rejects_it():
    # Bedrock Kimi K3 rejects the temperature field outright; an empty OI_TEMPERATURE is how the
    # deployment says so without a second request builder.
    import answer_params
    assert answer_params.temperature_param({"OI_TEMPERATURE": ""}) == {}
    assert answer_params.temperature_param({"OI_TEMPERATURE": "   "}) == {}


def test_a_number_sets_the_field():
    import answer_params
    assert answer_params.temperature_param({"OI_TEMPERATURE": "0.2"}) == {"temperature": 0.2}


def test_a_malformed_value_falls_back_to_the_default():
    import answer_params
    assert answer_params.temperature_param({"OI_TEMPERATURE": "warm"}) == {"temperature": 0}
