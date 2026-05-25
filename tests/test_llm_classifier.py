from llm.classifier import keyword_fallback_classification


def test_keyword_fallback_marks_reentry_without_changing_to_new_event_logic() -> None:
    result = keyword_fallback_classification("round 2 back in", ["So11111111111111111111111111111111111111112"])

    assert result.contains_reentry_phrase
    assert result.mentioned_cas == ["So11111111111111111111111111111111111111112"]


def test_keyword_fallback_warning() -> None:
    result = keyword_fallback_classification("dev dumping rug warning", [])

    assert result.intent == "WARNING"
    assert result.contains_warning
    assert result.is_exit_signal
