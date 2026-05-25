import json
from types import SimpleNamespace

import openai

from llm.classifier import LLMClassifier


def _content(intent: str, confidence: float) -> str:
    return json.dumps(
        {
            "intent": intent,
            "confidence": confidence,
            "sentiment": "BULLISH",
            "urgency": "HIGH",
            "is_new_call": intent == "BUY_CALL",
            "is_follow_up": False,
            "is_profit_flex": False,
            "is_exit_signal": intent in {"WARNING", "SOLD", "TAKE_PROFIT"},
            "contains_warning": intent == "WARNING",
            "contains_reentry_phrase": False,
            "mentioned_symbols": ["ABC"],
            "mentioned_cas": [],
            "language": "en",
            "reason": "Short classification reason.",
        }
    )


def _settings():
    return SimpleNamespace(
        llm_enabled=True,
        llm_provider="openai",
        llm_model="gpt-5.4-nano",
        llm_api_key="test-key",
        llm_base_url=None,
        llm_review_enabled=True,
        llm_review_model="gpt-5.4-mini",
        llm_review_confidence_threshold=0.75,
        review_intents={"BUY_CALL", "WARNING", "SOLD", "TAKE_PROFIT"},
        llm_fallback_to_ollama=False,
    )


def test_openai_low_confidence_critical_intent_is_reviewed(monkeypatch) -> None:
    responses = [
        SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=_content("BUY_CALL", 0.55)))],
            usage=SimpleNamespace(prompt_tokens=300, completion_tokens=160, total_tokens=460),
        ),
        SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=_content("BUY_CALL", 0.93)))],
            usage=SimpleNamespace(prompt_tokens=300, completion_tokens=145, total_tokens=445),
        ),
    ]
    calls = []

    class FakeCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return responses.pop(0)

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr("llm.classifier.get_settings", _settings)
    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)

    result = LLMClassifier().classify("ape now", ["So11111111111111111111111111111111111111112"])

    assert [call["model"] for call in calls] == ["gpt-5.4-nano", "gpt-5.4-mini"]
    assert calls[0]["response_format"]["type"] == "json_schema"
    assert calls[0]["response_format"]["json_schema"]["strict"] is True
    assert result.model_name == "gpt-5.4-mini"
    assert result.initial_model_name == "gpt-5.4-nano"
    assert result.review_model_name == "gpt-5.4-mini"
    assert result.was_reviewed
    assert result.prompt_tokens == 600
    assert result.completion_tokens == 305
    assert result.total_tokens == 905
    assert result.review_prompt_tokens == 300
    assert result.mentioned_cas == ["So11111111111111111111111111111111111111112"]


def test_openai_high_confidence_result_skips_review(monkeypatch) -> None:
    calls = []

    class FakeCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=_content("BUY_CALL", 0.95)))],
                usage=SimpleNamespace(prompt_tokens=300, completion_tokens=150, total_tokens=450),
            )

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr("llm.classifier.get_settings", _settings)
    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)

    result = LLMClassifier().classify("ape now", [])

    assert len(calls) == 1
    assert result.model_name == "gpt-5.4-nano"
    assert result.initial_model_name == "gpt-5.4-nano"
    assert not result.was_reviewed


def test_openai_contextual_ca_prompt_includes_preceding_entry_message(monkeypatch) -> None:
    calls = []

    class FakeCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=_content("BUY_CALL", 0.9)))],
                usage=SimpleNamespace(prompt_tokens=400, completion_tokens=150, total_tokens=550),
            )

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr("llm.classifier.get_settings", _settings)
    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)

    LLMClassifier().classify(
        "5s7tf6ih2CEZf7ZPNkJAtcknAq9DL5GsWHMMT3Jdpump",
        ["5s7tf6ih2CEZf7ZPNkJAtcknAq9DL5GsWHMMT3Jdpump"],
        preceding_context="entry. tek is tekking",
    )

    prompt = calls[0]["messages"][1]["content"]
    assert "Candidate preceding message" in prompt
    assert "entry. tek is tekking" in prompt
    assert "classify the combined intent as BUY_CALL" in prompt


def test_openai_without_key_can_use_local_fallback(monkeypatch) -> None:
    settings = _settings()
    settings.llm_api_key = None
    settings.llm_fallback_to_ollama = True
    settings.ollama_model = "qwen3.5:9b"
    monkeypatch.setattr("llm.classifier.get_settings", lambda: settings)
    monkeypatch.setattr(
        LLMClassifier,
        "_classify_with_ollama",
        lambda self, raw_text, extracted_cas, model: SimpleNamespace(model_name=model),
    )

    result = LLMClassifier().classify("watch", [])

    assert result.model_name == "qwen3.5:9b"
