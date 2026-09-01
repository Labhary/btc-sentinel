from __future__ import annotations

from btc_sentinel.news.models import RiskAssessment, RiskDecision
from tests.analysis_fixtures import ANALYSIS_NOW


def news_risk(
    decision: RiskDecision = RiskDecision.CLEAR,
    *,
    reasons: tuple[str, ...] = (),
) -> RiskAssessment:
    return RiskAssessment(
        evaluated_at=ANALYSIS_NOW,
        decision=decision,
        block_until=None,
        events=(),
        scheduled_events=(),
        reasons=reasons,
        coverage_issues=(),
    )
