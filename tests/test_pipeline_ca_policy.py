from app.pipeline import coerce_ca_post_to_buy_call
from llm.classifier import MessageClassification


def test_ca_post_noise_is_treated_as_buy_call() -> None:
    classification = MessageClassification(
        intent="NOISE",
        confidence=0.99,
        sentiment="NEUTRAL",
        urgency="LOW",
        reason="CA only.",
    )

    result = coerce_ca_post_to_buy_call(
        classification, ["9zwhS3b1oYuUEqWNpu2SPkEH24JMVWFEVQvHuYXZpump"]
    )

    assert result.intent == "BUY_CALL"
    assert result.confidence == 0.99
    assert result.is_new_call is True
    assert result.is_follow_up is False
    assert "CA post policy" in result.reason


def test_ca_post_warning_is_not_forced_to_buy_call() -> None:
    classification = MessageClassification(
        intent="WARNING",
        confidence=0.9,
        sentiment="BEARISH",
        urgency="HIGH",
        contains_warning=True,
        is_exit_signal=True,
    )

    result = coerce_ca_post_to_buy_call(
        classification, ["9zwhS3b1oYuUEqWNpu2SPkEH24JMVWFEVQvHuYXZpump"]
    )

    assert result.intent == "WARNING"
    assert result.is_new_call is False
