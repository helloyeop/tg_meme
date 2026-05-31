import json
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.settings import get_settings
from data_sources.types import TokenMarketData, TokenSecurityData
from db.models import ChannelPerformance, EventScore, MessageAnalysis, TokenCallEvent


@dataclass
class ScoreResult:
    message_score: float
    channel_score: float
    timing_score: float
    market_cap_position_score: float
    risk_score: float
    final_signal_score: float
    breakdown: dict


class ScoringEngine:
    def __init__(self, strategy: dict | None = None):
        self.strategy = strategy or get_settings().load_strategy_config()
        self.scoring = self.strategy.get("scoring", {})

    def score(
        self,
        *,
        event: TokenCallEvent,
        analysis: MessageAnalysis,
        market_data: TokenMarketData | None,
        security_data: TokenSecurityData | None,
        channel_performance: ChannelPerformance | None = None,
        ca_count: int = 1,
        now: datetime | None = None,
    ) -> ScoreResult:
        now = now or datetime.utcnow()
        message_score = self.scoring.get("intent_base_score", {}).get(analysis.intent, 0)
        channel_score = (
            channel_performance.overall_score
            if channel_performance
            else self.scoring.get("new_channel_default_score", 50)
        )
        call_time = (
            event.latest_actionable_call_time
            or event.first_actionable_call_time
            or event.first_seen_time
        )
        timing_factor = self._timing_factor((now - call_time).total_seconds() / 60)
        market_cap_factor = self._market_cap_position_factor(event, market_data)
        chase_risk_factor = self._chase_risk_factor(event)
        ca_factor = self._ca_count_factor(ca_count)
        risk_score, risk_breakdown = self._risk_score(market_data, security_data)

        final = (
            message_score
            * (channel_score / 50)
            * timing_factor
            * market_cap_factor
            * chase_risk_factor
            * ca_factor
            * (risk_score / 100)
        )
        final = max(-100, min(100, final))
        return ScoreResult(
            message_score=message_score,
            channel_score=channel_score,
            timing_score=timing_factor * 100,
            market_cap_position_score=market_cap_factor * 100,
            risk_score=risk_score,
            final_signal_score=final,
            breakdown={
                "intent": analysis.intent,
                "timing_basis": (
                    "latest_actionable_call_time"
                    if event.latest_actionable_call_time
                    else "first_actionable_call_time"
                    if event.first_actionable_call_time
                    else "first_seen_time"
                ),
                "timing_factor": timing_factor,
                "market_cap_anchor": (
                    "latest_actionable_market_cap_usd"
                    if event.latest_actionable_market_cap_usd
                    else "first_seen_market_cap_usd"
                ),
                "market_cap_position_factor": market_cap_factor,
                "chase_risk_factor": chase_risk_factor,
                "ca_count_factor": ca_factor,
                "risk": risk_breakdown,
            },
        )

    def persist(self, session: Session, event_id: int, score: ScoreResult) -> EventScore:
        row = EventScore(
            event_id=event_id,
            score_time=datetime.utcnow(),
            message_score=score.message_score,
            channel_score=score.channel_score,
            timing_score=score.timing_score,
            market_cap_position_score=score.market_cap_position_score,
            risk_score=score.risk_score,
            final_signal_score=score.final_signal_score,
            score_breakdown_json=json.dumps(score.breakdown, ensure_ascii=False),
        )
        session.add(row)
        session.flush()
        return row

    def _timing_factor(self, minutes: float) -> float:
        factors = self.scoring.get("timing_factor", {})
        if minutes <= 1:
            return factors.get("first_1_minute", 1.0)
        if minutes <= 3:
            return factors.get("1_to_3_minutes", 0.85)
        if minutes <= 5:
            return factors.get("3_to_5_minutes", 0.70)
        if minutes <= 15:
            return factors.get("5_to_15_minutes", 0.45)
        if minutes <= 60:
            return factors.get("15_to_60_minutes", 0.25)
        return factors.get("over_60_minutes", 0.10)

    def _market_cap_position_factor(
        self, event: TokenCallEvent, market_data: TokenMarketData | None
    ) -> float:
        factors = self.scoring.get(
            "market_cap_position_factor", self.scoring.get("price_position_factor", {})
        )
        first_market_cap = event.latest_actionable_market_cap_usd or event.first_seen_market_cap_usd
        current_market_cap = market_data.market_cap_usd if market_data else None
        if not first_market_cap or not current_market_cap or current_market_cap <= first_market_cap:
            return factors.get("below_or_equal_first_call", 1.0)
        increase_pct = ((current_market_cap / first_market_cap) - 1) * 100
        if increase_pct <= 30:
            return factors.get("up_0_to_30_pct", 0.90)
        if increase_pct <= 80:
            return factors.get("up_30_to_80_pct", 0.70)
        if increase_pct <= 150:
            return factors.get("up_80_to_150_pct", 0.45)
        if increase_pct <= 300:
            return factors.get("up_150_to_300_pct", 0.20)
        return factors.get("up_over_300_pct", 0.05)

    def _chase_risk_factor(self, event: TokenCallEvent) -> float:
        factors = self.strategy.get("actionable_recall", {}).get("chase_risk_factor", {})
        first_market_cap = event.first_seen_market_cap_usd
        anchor_market_cap = event.latest_actionable_market_cap_usd
        if not first_market_cap or not anchor_market_cap or anchor_market_cap <= first_market_cap:
            return factors.get("below_or_equal_first_call", 1.0)
        increase_pct = ((anchor_market_cap / first_market_cap) - 1) * 100
        if increase_pct <= 30:
            return factors.get("up_0_to_30_pct", 0.95)
        if increase_pct <= 80:
            return factors.get("up_30_to_80_pct", 0.85)
        if increase_pct <= 150:
            return factors.get("up_80_to_150_pct", 0.85)
        if increase_pct <= 300:
            return factors.get("up_150_to_300_pct", 0.70)
        return factors.get("up_over_300_pct", 0.50)

    def _ca_count_factor(self, ca_count: int) -> float:
        factors = self.scoring.get("ca_count_factor", {})
        if ca_count >= 4:
            return factors.get("4_or_more", 0.40)
        return factors.get(ca_count, factors.get(str(ca_count), 1.0))

    def _risk_score(
        self,
        market_data: TokenMarketData | None,
        security_data: TokenSecurityData | None,
    ) -> tuple[float, dict]:
        penalties = self.scoring.get("risk_penalties", {})
        score = 100.0
        reasons: list[str] = []

        def apply(key: str) -> None:
            nonlocal score
            amount = penalties.get(key, 0)
            score -= amount
            reasons.append(key)

        if market_data is None:
            apply("missing_market_data")
        elif market_data.liquidity_usd is not None and market_data.liquidity_usd < 5000:
            apply("liquidity_under_5000_usd")

        if security_data is None:
            apply("missing_security_data")
        else:
            if security_data.top10_holder_ratio and security_data.top10_holder_ratio > 45:
                apply("top10_holder_ratio_over_45_pct")
            if security_data.dev_wallet_ratio and security_data.dev_wallet_ratio > 5:
                apply("dev_wallet_ratio_over_5_pct")
            if security_data.mint_authority_active:
                apply("mint_authority_active")
            if security_data.freeze_authority_active:
                apply("freeze_authority_active")
            if security_data.holder_count is not None and security_data.holder_count < 80:
                apply("holder_count_under_80")

        return max(0, min(100, score)), {"penalties": reasons}
