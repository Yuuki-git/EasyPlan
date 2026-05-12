import pytest
from pydantic import ValidationError

from app.api.schemas import IntentProfile


def test_intent_profile_accepts_supported_profile_values():
    profile = IntentProfile(
        intent_type="short_term_delivery",
        time_horizon="hours",
        confidence_score=0.87,
    )

    assert profile.model_dump() == {
        "intent_type": "short_term_delivery",
        "time_horizon": "hours",
        "confidence_score": 0.87,
    }


def test_intent_profile_rejects_unknown_type_and_out_of_range_confidence():
    with pytest.raises(ValidationError):
        IntentProfile(
            intent_type="random",
            time_horizon="hours",
            confidence_score=1.5,
        )
