import json
import logging
import re
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.settings import get_settings

logger = logging.getLogger(__name__)

INTENTS = {
    "BUY_CALL",
    "WATCH",
    "UPDATE_BULLISH",
    "UPDATE_BEARISH",
    "HOLD",
    "ADDING",
    "TAKE_PROFIT",
    "SOLD",
    "WARNING",
    "FLEX",
    "REPOST",
    "DISCUSSION",
    "NOISE",
    "UNKNOWN",
}
SENTIMENTS = {"BULLISH", "BEARISH", "NEUTRAL", "MIXED", "UNKNOWN"}
URGENCIES = {"LOW", "MEDIUM", "HIGH", "UNKNOWN"}


class MessageClassification(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    intent: str = "UNKNOWN"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    sentiment: str = "UNKNOWN"
    urgency: str = "UNKNOWN"
    is_new_call: bool = False
    is_follow_up: bool = False
    is_profit_flex: bool = False
    is_exit_signal: bool = False
    contains_warning: bool = False
    contains_reentry_phrase: bool = False
    mentioned_symbols: list[str] = Field(default_factory=list)
    mentioned_cas: list[str] = Field(default_factory=list)
    language: str = "unknown"
    reason: str = ""
    model_name: str | None = None
    llm_provider: str | None = None
    initial_model_name: str | None = None
    review_model_name: str | None = None
    was_reviewed: bool = False
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    review_prompt_tokens: int | None = None
    review_completion_tokens: int | None = None
    latency_ms: float | None = None
    context_linked: bool = False
    context_relation: str | None = None
    context_confidence: float | None = None
    context_message_ids: list[int] = Field(default_factory=list)

    @field_validator("intent")
    @classmethod
    def validate_intent(cls, value: str) -> str:
        return value if value in INTENTS else "UNKNOWN"

    @field_validator("sentiment")
    @classmethod
    def validate_sentiment(cls, value: str) -> str:
        return value if value in SENTIMENTS else "UNKNOWN"

    @field_validator("urgency")
    @classmethod
    def validate_urgency(cls, value: str) -> str:
        return value if value in URGENCIES else "UNKNOWN"


PROMPT_TEMPLATE = """You are a crypto Telegram message classifier.

Classify the message for a Solana meme coin paper-trading analytics system.

Do not provide trading advice.
Do not decide whether to buy or sell.
Only classify the speaker's intent and message context.

Consider English crypto slang, Korean, emojis, abbreviations, and meme coin expressions.

Return strict JSON only.

Allowed intent labels:
BUY_CALL, WATCH, UPDATE_BULLISH, UPDATE_BEARISH, HOLD, ADDING,
TAKE_PROFIT, SOLD, WARNING, FLEX, REPOST, DISCUSSION, NOISE, UNKNOWN.

{context_block}Current message:
{raw_text}

Extracted CAs:
{extracted_cas}

Return JSON with:
intent, confidence, sentiment, urgency, is_new_call, is_follow_up,
is_profit_flex, is_exit_signal, contains_warning, contains_reentry_phrase,
mentioned_symbols, mentioned_cas, language, reason.
"""

CLASSIFICATION_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "intent",
        "confidence",
        "sentiment",
        "urgency",
        "is_new_call",
        "is_follow_up",
        "is_profit_flex",
        "is_exit_signal",
        "contains_warning",
        "contains_reentry_phrase",
        "mentioned_symbols",
        "mentioned_cas",
        "language",
        "reason",
    ],
    "properties": {
        "intent": {"type": "string", "enum": sorted(INTENTS)},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "sentiment": {"type": "string", "enum": sorted(SENTIMENTS)},
        "urgency": {"type": "string", "enum": sorted(URGENCIES)},
        "is_new_call": {"type": "boolean"},
        "is_follow_up": {"type": "boolean"},
        "is_profit_flex": {"type": "boolean"},
        "is_exit_signal": {"type": "boolean"},
        "contains_warning": {"type": "boolean"},
        "contains_reentry_phrase": {"type": "boolean"},
        "mentioned_symbols": {"type": "array", "items": {"type": "string"}},
        "mentioned_cas": {"type": "array", "items": {"type": "string"}},
        "language": {"type": "string"},
        "reason": {"type": "string"},
    },
}


@dataclass
class LLMClassifier:
    model: str | None = None

    def classify(
        self,
        raw_text: str | None,
        extracted_cas: list[str],
        preceding_context: str | None = None,
    ) -> MessageClassification:
        settings = get_settings()
        model = self.model or (settings.ollama_model if settings.llm_provider == "ollama" else settings.llm_model)
        if not settings.llm_enabled:
            return keyword_fallback_classification(_combined_text(raw_text or "", preceding_context), extracted_cas, model)
        if settings.llm_provider == "ollama":
            try:
                if preceding_context:
                    return self._classify_with_ollama(raw_text or "", extracted_cas, model, preceding_context)
                return self._classify_with_ollama(raw_text or "", extracted_cas, model)
            except Exception:
                return keyword_fallback_classification(_combined_text(raw_text or "", preceding_context), extracted_cas, model)

        if not settings.llm_api_key:
            return self._fallback_classification(raw_text or "", extracted_cas, settings, model, preceding_context)

        try:
            initial = self._classify_with_openai(raw_text or "", extracted_cas, model, preceding_context)
        except Exception as exc:
            logger.warning("Primary LLM classification failed for model %s: %s", model, exc)
            return self._fallback_classification(raw_text or "", extracted_cas, settings, model, preceding_context)

        initial.initial_model_name = model
        if not self._needs_review(initial, settings):
            return initial

        review_model = getattr(settings, "llm_review_model", "gpt-5.4-mini")
        try:
            reviewed = self._classify_with_openai(raw_text or "", extracted_cas, review_model, preceding_context)
        except Exception as exc:
            logger.warning("LLM review failed for model %s: %s", review_model, exc)
            return initial

        reviewed.initial_model_name = model
        reviewed.review_model_name = review_model
        reviewed.was_reviewed = True
        reviewed.review_prompt_tokens = reviewed.prompt_tokens
        reviewed.review_completion_tokens = reviewed.completion_tokens
        reviewed.prompt_tokens = (initial.prompt_tokens or 0) + (reviewed.prompt_tokens or 0)
        reviewed.completion_tokens = (initial.completion_tokens or 0) + (reviewed.completion_tokens or 0)
        reviewed.total_tokens = (initial.total_tokens or 0) + (reviewed.total_tokens or 0)
        reviewed.latency_ms = (initial.latency_ms or 0) + (reviewed.latency_ms or 0)
        return reviewed

    def _classify_with_openai(
        self,
        raw_text: str,
        extracted_cas: list[str],
        model: str,
        preceding_context: str | None = None,
    ) -> MessageClassification:
        settings = get_settings()
        from openai import OpenAI

        client = OpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url or None)
        prompt = _format_prompt(raw_text, extracted_cas, preceding_context)
        started = time.perf_counter()
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": "You classify Telegram meme-token posts. Return strict JSON only.",
                },
                {"role": "user", "content": prompt},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "telegram_message_classification",
                    "strict": True,
                    "schema": CLASSIFICATION_JSON_SCHEMA,
                },
            },
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
        content = response.choices[0].message.content or "{}"
        try:
            parsed = MessageClassification.model_validate(json.loads(content))
        except (json.JSONDecodeError, ValidationError):
            repair = _extract_json_object(content)
            parsed = MessageClassification.model_validate(repair or {"intent": "UNKNOWN"})
        parsed.model_name = model
        parsed.llm_provider = "openai"
        parsed.prompt_tokens = getattr(getattr(response, "usage", None), "prompt_tokens", None)
        parsed.completion_tokens = getattr(getattr(response, "usage", None), "completion_tokens", None)
        parsed.total_tokens = getattr(getattr(response, "usage", None), "total_tokens", None)
        parsed.latency_ms = round(elapsed_ms, 2)
        if not parsed.mentioned_cas:
            parsed.mentioned_cas = extracted_cas
        return parsed

    @staticmethod
    def _needs_review(result: MessageClassification, settings) -> bool:
        if not getattr(settings, "llm_review_enabled", False):
            return False
        review_intents = getattr(settings, "review_intents", {"BUY_CALL", "WARNING", "SOLD", "TAKE_PROFIT"})
        threshold = getattr(settings, "llm_review_confidence_threshold", 0.75)
        return result.intent in review_intents and result.confidence < threshold

    def _fallback_classification(
        self,
        raw_text: str,
        extracted_cas: list[str],
        settings,
        primary_model: str,
        preceding_context: str | None = None,
    ) -> MessageClassification:
        if getattr(settings, "llm_fallback_to_ollama", False):
            try:
                if preceding_context:
                    return self._classify_with_ollama(raw_text, extracted_cas, settings.ollama_model, preceding_context)
                return self._classify_with_ollama(raw_text, extracted_cas, settings.ollama_model)
            except Exception as exc:
                logger.warning("Ollama fallback failed after primary model %s: %s", primary_model, exc)
        return keyword_fallback_classification(_combined_text(raw_text, preceding_context), extracted_cas, primary_model)

    def _classify_with_ollama(
        self,
        raw_text: str,
        extracted_cas: list[str],
        model: str,
        preceding_context: str | None = None,
    ) -> MessageClassification:
        settings = get_settings()
        prompt = _format_prompt(raw_text, extracted_cas, preceding_context)
        payload = {
            "model": model,
            "stream": False,
            "think": False,
            "format": CLASSIFICATION_JSON_SCHEMA,
            "options": {"temperature": 0, "num_predict": 500},
            "messages": [
                {
                    "role": "system",
                    "content": "You classify Telegram meme-token posts. Return strict JSON only.",
                },
                {"role": "user", "content": prompt},
            ],
        }
        url = settings.ollama_base_url.rstrip("/") + "/api/chat"
        body = json.dumps(payload).encode("utf-8")
        request = Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        started = time.perf_counter()
        try:
            with urlopen(request, timeout=settings.ollama_timeout_seconds) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Ollama request failed: HTTP {exc.code} {detail}") from exc
        except URLError as exc:
            raise RuntimeError(f"Ollama request failed: {exc}") from exc

        content = data.get("message", {}).get("content")
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("Ollama returned an empty classification response")
        parsed = MessageClassification.model_validate(json.loads(content))
        parsed.model_name = model
        parsed.llm_provider = "ollama"
        parsed.latency_ms = round((time.perf_counter() - started) * 1000, 2)
        if not parsed.mentioned_cas:
            parsed.mentioned_cas = extracted_cas
        return parsed


def _format_prompt(raw_text: str, extracted_cas: list[str], preceding_context: str | None) -> str:
    context_block = ""
    if preceding_context:
        context_block = (
            "Candidate preceding message from the same channel, immediately before a CA-only post:\n"
            f"{preceding_context}\n\n"
            "Context rule: Decide whether the current CA supplies the token address for the preceding "
            "message. If it clearly announces entry or buying, classify the combined intent as "
            "BUY_CALL even when the current message is only a CA. Do not infer a buy call from mere "
            "discussion or profit flex.\n\n"
        )
    return PROMPT_TEMPLATE.format(
        context_block=context_block,
        raw_text=raw_text,
        extracted_cas=extracted_cas,
    )


def _combined_text(raw_text: str, preceding_context: str | None) -> str:
    if preceding_context:
        return f"{preceding_context}\n{raw_text}"
    return raw_text


def keyword_fallback_classification(
    raw_text: str,
    extracted_cas: list[str],
    model_name: str | None = None,
) -> MessageClassification:
    text = raw_text.lower()
    intent = "NOISE"
    sentiment = "NEUTRAL"
    urgency = "LOW"

    warning = any(word in text for word in ["rug", "scam", "danger", "warning", "조심", "사기", "덤핑"])
    sold = any(word in text for word in ["sold", "out", "exited", "전량", "매도"])
    take_profit = any(word in text for word in ["tp", "take profit", "initial", "initials", "익절"])
    flex = any(word in text for word in ["called", "now", "x", "수익", "몇배"])
    reentry = any(word in text for word in ["re-entry", "reentry", "round 2", "back in", "재진입"])

    if warning:
        intent, sentiment, urgency = "WARNING", "BEARISH", "HIGH"
    elif sold:
        intent, sentiment, urgency = "SOLD", "BEARISH", "HIGH"
    elif take_profit:
        intent, sentiment, urgency = "TAKE_PROFIT", "MIXED", "MEDIUM"
    elif extracted_cas and any(
        word in text for word in ["entry", "entering", "ape", "buy", "bid", "bidding", "send", "early", "call", "진입"]
    ):
        intent, sentiment, urgency = "BUY_CALL", "BULLISH", "HIGH"
    elif extracted_cas:
        intent, sentiment, urgency = "WATCH", "NEUTRAL", "MEDIUM"
    elif flex:
        intent, sentiment, urgency = "FLEX", "BULLISH", "LOW"

    return MessageClassification(
        intent=intent,
        confidence=0.45,
        sentiment=sentiment,
        urgency=urgency,
        is_new_call=intent == "BUY_CALL",
        is_follow_up=bool(extracted_cas) and intent != "BUY_CALL",
        is_profit_flex=intent == "FLEX",
        is_exit_signal=intent in {"SOLD", "TAKE_PROFIT", "WARNING"},
        contains_warning=warning,
        contains_reentry_phrase=reentry,
        mentioned_symbols=_extract_symbols(raw_text),
        mentioned_cas=extracted_cas,
        language="mixed" if re.search(r"[가-힣]", raw_text) else "unknown",
        reason="Keyword fallback used because LLM is disabled or not configured.",
        model_name=model_name,
        llm_provider="fallback",
    )


def _extract_symbols(raw_text: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"\$([A-Za-z][A-Za-z0-9_]{1,12})", raw_text)))


def _extract_json_object(content: str) -> dict | None:
    match = re.search(r"\{.*\}", content, flags=re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
