"""Deterministic read-only Phase 10 reports for paper outcomes and state."""

from __future__ import annotations

import re
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from typing import Protocol

from btc_sentinel.domain.enums import SignalStatus
from btc_sentinel.news.models import RiskAssessment
from btc_sentinel.reports.models import (
    ReportDocument,
    ReportKind,
    ReportSignal,
    active_variant_labels,
)
from btc_sentinel.statistics import OutcomeSample, VariantStatistics, calculate_statistics
from btc_sentinel.time_utils import CASABLANCA, ensure_utc, format_casablanca, iso_utc

_MAX_TEXT = 4096
_MAX_NEWS_AGE = timedelta(minutes=30)
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


class ReportRepository(Protocol):
    def list_outcome_samples(
        self, start_at: datetime | None = None, end_at: datetime | None = None
    ) -> tuple[OutcomeSample, ...]: ...

    def list_report_signals(self, status: SignalStatus) -> tuple[ReportSignal, ...]: ...


def _clean(value: object, limit: int = 240) -> str:
    text = _CONTROL.sub("", " ".join(str(value).split()))
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def _decimal(value: Decimal | None, places: int = 2) -> str:
    if value is None:
        return "unavailable"
    quantum = Decimal(1).scaleb(-places)
    return format(value.quantize(quantum), "f")


def _bounded(lines: list[str]) -> str:
    text = "\n".join(_clean(line, 500) for line in lines).strip()
    if len(text) <= _MAX_TEXT:
        return text
    suffix = "\n[Report truncated safely]"
    return text[: _MAX_TEXT - len(suffix)].rstrip() + suffix


def _period_bounds(kind: ReportKind, as_of: datetime) -> tuple[datetime, datetime]:
    local = ensure_utc(as_of).astimezone(CASABLANCA)
    if kind is ReportKind.DAILY:
        start_date = local.date()
    elif kind is ReportKind.WEEKLY:
        start_date = local.date() - timedelta(days=local.weekday())
    elif kind is ReportKind.MONTHLY:
        start_date = local.date().replace(day=1)
    else:
        raise ValueError("Only calendar report kinds have period bounds")
    start_local = datetime.combine(start_date, time.min, tzinfo=CASABLANCA)
    return start_local.astimezone(UTC), ensure_utc(as_of)


def _rate_line(label: str, stats: VariantStatistics) -> str:
    if stats.strict_win_rate_percent is None:
        return f"{label}: no resolved outcomes (n=0); win rate unavailable"
    low = _decimal(stats.strict_win_rate_95_low_percent, 1)
    high = _decimal(stats.strict_win_rate_95_high_percent, 1)
    observed = _decimal(stats.strict_win_rate_percent, 1)
    return f"{label}: strict wins {observed}% (n={stats.resolved}, 95% Wilson {low}% to {high}%)"


def _statistics_lines(samples: tuple[OutcomeSample, ...], as_of: datetime) -> list[str]:
    report = calculate_statistics(samples, as_of)
    lines: list[str] = []
    for label, stats in (("Managed", report.managed), ("Fixed", report.fixed)):
        lines.extend(
            [
                _rate_line(label, stats),
                (
                    f"{label} outcomes: W {stats.wins} / L {stats.losses} / "
                    f"BE {stats.break_even} / early {stats.early_exits}"
                ),
                (
                    f"{label} R: net {_decimal(stats.net_r)} | avg {_decimal(stats.average_r)} | "
                    f"max drawdown {_decimal(stats.max_drawdown_r)}"
                ),
            ]
        )
    comparison = report.comparison
    delta = _decimal(comparison.average_managed_delta_r)
    lines.append(
        "Paired comparison: "
        f"n={comparison.completed_pairs}, managed better {comparison.managed_better}, "
        f"fixed better {comparison.fixed_better}, ties {comparison.ties}, avg ΔR {delta}"
    )
    lines.append(
        f"Unresolved tracks: fixed {comparison.unresolved_fixed}, "
        f"managed {comparison.unresolved_managed}"
    )
    return lines


def _signal_lines(signals: tuple[ReportSignal, ...], status: SignalStatus) -> list[str]:
    if not signals:
        return [f"No {status.value.lower()} BTCUSDT signals."]
    lines: list[str] = []
    for signal in signals:
        targets = ", ".join(f"TP{item.ordinal} {item.price}" for item in signal.targets)
        if status is SignalStatus.PENDING:
            lines.append(
                f"{signal.signal_id} {signal.side.value} score {signal.setup_score}: "
                f"entry {signal.entry_low} to {signal.entry_high}, stop {signal.original_stop}, "
                f"{targets}; expires {format_casablanca(signal.expires_at)}"
            )
        else:
            tracks = "/".join(active_variant_labels(signal)) or "none"
            managed_stop = signal.managed_stop or signal.original_stop
            lines.append(
                f"{signal.signal_id} {signal.side.value}: fill {signal.fill_price}, "
                f"managed stop {managed_stop}, {targets}; active tracks {tracks}"
            )
    return lines


class ReportEngine:
    """Build report documents only; never writes an outbox row or sends a message."""

    def __init__(self, repository: ReportRepository) -> None:
        self.repository = repository

    def generate(self, kind: ReportKind, as_of: datetime) -> ReportDocument:
        now = ensure_utc(as_of)
        if kind in {ReportKind.DAILY, ReportKind.WEEKLY, ReportKind.MONTHLY}:
            start, end = _period_bounds(kind, now)
            samples = self.repository.list_outcome_samples(start, end)
            active = self.repository.list_report_signals(SignalStatus.ACTIVE)
            pending = self.repository.list_report_signals(SignalStatus.PENDING)
            lines = [
                f"BTC Sentinel {kind.value.lower()} paper report",
                f"Window: {format_casablanca(start)} to {format_casablanca(end)}",
                "Market: BTC/USDT | Mode: PAPER ONLY",
                "",
                *_statistics_lines(samples, now),
                "",
                f"Current state: {len(active)} active, {len(pending)} pending",
                "Observed outcomes are descriptive, not a forecast or win-rate guarantee.",
            ]
            local_period = start.astimezone(CASABLANCA).date().isoformat()
            key = f"report:{kind.value.lower()}:{local_period}:v0.10.0"
            return ReportDocument(kind, now, _bounded(lines), key, start, end)

        if kind not in {ReportKind.ACTIVE, ReportKind.PENDING}:
            raise ValueError("NEWS_RISK requires generate_news_risk")
        status = SignalStatus.ACTIVE if kind is ReportKind.ACTIVE else SignalStatus.PENDING
        signals = self.repository.list_report_signals(status)
        lines = [
            f"BTC Sentinel {kind.value.lower()} paper status",
            f"As of: {format_casablanca(now)}",
            "Market: BTC/USDT | Mode: PAPER ONLY",
            "",
            *_signal_lines(signals, status),
        ]
        key = f"report:{kind.value.lower()}:{iso_utc(now)}:v0.10.0"
        return ReportDocument(kind, now, _bounded(lines), key)

    def generate_news_risk(
        self, as_of: datetime, assessment: RiskAssessment | None
    ) -> ReportDocument:
        now = ensure_utc(as_of)
        lines = [
            "BTC Sentinel news-risk report",
            f"As of: {format_casablanca(now)}",
            "Market: BTC/USDT | News can restrict signals, never create one.",
            "",
        ]
        if assessment is None:
            lines.extend(["Status: UNAVAILABLE", "No current risk assessment; treat as not clear."])
        else:
            age = now - assessment.evaluated_at
            if age < timedelta(0):
                lines.extend(
                    ["Status: UNAVAILABLE", "Assessment is future-dated; treat as not clear."]
                )
            elif age > _MAX_NEWS_AGE:
                lines.extend(
                    [
                        "Status: STALE",
                        "Assessment age: "
                        f"{int(age.total_seconds() // 60)} minutes; treat as not clear.",
                    ]
                )
            else:
                lines.append(f"Status: {assessment.decision.value}")
                if assessment.block_until is not None:
                    lines.append(f"Block until: {format_casablanca(assessment.block_until)}")
                lines.extend(f"Reason: {_clean(reason)}" for reason in assessment.reasons[:8])
                lines.extend(
                    f"Event: {_clean(event.title, 180)} [{event.volatility.value}]"
                    for event in assessment.events[:8]
                )
                lines.extend(
                    f"Scheduled: {_clean(event.title, 180)} at {format_casablanca(event.starts_at)}"
                    for event in assessment.scheduled_events[:8]
                )
                if assessment.coverage_issues:
                    lines.append(f"Coverage issues: {len(assessment.coverage_issues)}")
        key = f"report:news-risk:{iso_utc(now)}:v0.10.0"
        return ReportDocument(ReportKind.NEWS_RISK, now, _bounded(lines), key)
