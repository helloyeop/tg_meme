import json

from llm.classifier import LLMClassifier


def test_ollama_classifier_uses_structured_chat(monkeypatch) -> None:
    class FakeSettings:
        llm_enabled = True
        llm_provider = "ollama"
        llm_model = "unused"
        llm_api_key = None
        llm_base_url = None
        ollama_base_url = "http://localhost:11434"
        ollama_model = "qwen"
        ollama_timeout_seconds = 1

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "intent": "BUY_CALL",
                                "confidence": 0.9,
                                "sentiment": "BULLISH",
                                "urgency": "HIGH",
                                "is_new_call": True,
                                "is_follow_up": False,
                                "is_profit_flex": False,
                                "is_exit_signal": False,
                                "contains_warning": False,
                                "contains_reentry_phrase": False,
                                "mentioned_symbols": ["ABC"],
                                "mentioned_cas": ["So11111111111111111111111111111111111111112"],
                                "language": "en",
                                "reason": "Fresh call wording.",
                            }
                        )
                    }
                }
            ).encode()

    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["payload"] = json.loads(request.data.decode())
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("llm.classifier.get_settings", lambda: FakeSettings())
    monkeypatch.setattr("llm.classifier.urlopen", fake_urlopen)

    result = LLMClassifier().classify("CA ape now", ["So11111111111111111111111111111111111111112"])

    assert result.intent == "BUY_CALL"
    assert result.model_name == "qwen"
    assert captured["url"] == "http://localhost:11434/api/chat"
    assert captured["payload"]["format"]["type"] == "object"
    assert captured["payload"]["think"] is False
