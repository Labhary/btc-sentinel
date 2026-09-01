from dataclasses import replace
from datetime import datetime
from unittest import TestCase

from btc_sentinel.analysis import MultiTimeframeAnalyzer
from btc_sentinel.errors import DomainValidationError
from btc_sentinel.signals.models import SignalDecision, SignalEvaluation, SignalHistory
from tests.analysis_fixtures import ANALYSIS_NOW, analysis_snapshot
from tests.signal_fixtures import news_risk


class SignalModelTests(TestCase):
    def test_history_requires_timezone(self) -> None:
        with self.assertRaises(DomainValidationError):
            SignalHistory(datetime(2026, 9, 1))

    def test_no_signal_requires_reason(self) -> None:
        analysis = MultiTimeframeAnalyzer().analyze(analysis_snapshot())
        with self.assertRaises(DomainValidationError):
            SignalEvaluation(
                ANALYSIS_NOW,
                SignalDecision.NO_SIGNAL,
                analysis,
                news_risk(),
                None,
                (),
            )

    def test_created_cannot_contain_rejection(self) -> None:
        analysis = MultiTimeframeAnalyzer().analyze(analysis_snapshot())
        result = SignalEvaluation(
            ANALYSIS_NOW,
            SignalDecision.NO_SIGNAL,
            analysis,
            news_risk(),
            None,
            ("not admitted",),
        )
        with self.assertRaises(DomainValidationError):
            replace(result, decision=SignalDecision.CREATED)
