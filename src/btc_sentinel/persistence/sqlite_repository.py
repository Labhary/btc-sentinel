"""SQLite reference repository using the same schema semantics as D1."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from btc_sentinel.domain.enums import (
    MarketRegime,
    OutcomeResult,
    OutcomeVariant,
    Side,
    SignalStatus,
    TrackStatus,
    TradeEventType,
)
from btc_sentinel.domain.ids import format_signal_id
from btc_sentinel.domain.models import Signal, Target, as_decimal
from btc_sentinel.domain.state_machine import assert_transition, transition_event
from btc_sentinel.errors import (
    ConcurrencyError,
    DuplicateRecordError,
    RecordNotFoundError,
    SecurityError,
)
from btc_sentinel.lifecycle.models import LifecycleSignal, TrackState
from btc_sentinel.management.models import ManagementDecision
from btc_sentinel.reports.models import ReportSignal
from btc_sentinel.statistics import OutcomeSample, calculate_statistics
from btc_sentinel.time_utils import ensure_utc, iso_utc

_CLOSE_EVENTS = {
    TradeEventType.TP1_HIT,
    TradeEventType.TP2_HIT,
    TradeEventType.TP3_HIT,
    TradeEventType.STOP_LOSS_HIT,
    TradeEventType.BREAK_EVEN,
    TradeEventType.EARLY_EXIT,
}
_SENSITIVE_SETTING_PARTS = (
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "API_KEY",
    "AUTHORIZATION",
    "SIGNATURE",
    "CHAT_ID",
    "USER_ID",
)


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _decimal_text(value: Decimal | str | int, name: str) -> str:
    return format(as_decimal(value, name), "f")


class SqliteRepository:
    """Transactional local implementation used by tests and backtests."""

    def __init__(self, database_path: str | Path, migration_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.migration_path = Path(migration_path)
        self._connection = sqlite3.connect(self.database_path, isolation_level=None)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA busy_timeout = 5000")

    def __enter__(self) -> SqliteRepository:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._connection.close()

    def migrate(self) -> None:
        self._connection.executescript(self.migration_path.read_text(encoding="utf-8"))

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            yield self._connection
        except Exception:
            self._connection.execute("ROLLBACK")
            raise
        else:
            self._connection.execute("COMMIT")

    def allocate_signal_id(self, business_date: date) -> str:
        with self._transaction() as connection:
            row = connection.execute(
                """
                INSERT INTO signal_id_counters(business_date, last_sequence)
                VALUES (?, 1)
                ON CONFLICT(business_date) DO UPDATE
                    SET last_sequence = signal_id_counters.last_sequence + 1
                RETURNING last_sequence
                """,
                (business_date.isoformat(),),
            ).fetchone()
        assert row is not None
        return format_signal_id(business_date, int(row["last_sequence"]))

    def create_signal(self, signal: Signal) -> None:
        terms = signal.terms
        now = iso_utc(terms.created_at)
        try:
            with self._transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO signals(
                        signal_id, symbol, side, lifecycle_status, setup_score, regime,
                        monthly_bias, weekly_bias, daily_bias, four_hour_bias,
                        one_hour_bias, fifteen_minute_bias, created_at, data_timestamp,
                        expires_at, entry_low, entry_high, original_stop,
                        estimated_cost_rate, minimum_planned_rr, invalidation_condition,
                        expiration_condition, recommended_risk_percent, reasons_json,
                        risks_json, strategy_version, updated_at, row_version
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        signal.signal_id,
                        terms.symbol,
                        terms.side.value,
                        signal.status.value,
                        signal.setup_score,
                        signal.regime.value,
                        signal.biases.monthly.value,
                        signal.biases.weekly.value,
                        signal.biases.daily.value,
                        signal.biases.four_hour.value,
                        signal.biases.one_hour.value,
                        signal.biases.fifteen_minute.value,
                        now,
                        iso_utc(terms.data_timestamp),
                        iso_utc(terms.expires_at),
                        _decimal_text(terms.entry_low, "entry_low"),
                        _decimal_text(terms.entry_high, "entry_high"),
                        _decimal_text(terms.original_stop, "original_stop"),
                        _decimal_text(terms.estimated_round_trip_cost_rate, "estimated_cost_rate"),
                        _decimal_text(terms.minimum_planned_rr, "minimum_planned_rr"),
                        terms.invalidation_condition,
                        terms.expiration_condition,
                        _decimal_text(terms.recommended_risk_percent, "recommended_risk_percent"),
                        _json(signal.reasons),
                        _json(signal.risks),
                        signal.strategy_version,
                        now,
                        signal.row_version,
                    ),
                )
                connection.executemany(
                    """
                    INSERT INTO signal_targets(signal_id, ordinal, price, planned_r)
                    VALUES (?, ?, ?, ?)
                    """,
                    [
                        (
                            signal.signal_id,
                            target.ordinal,
                            _decimal_text(target.price, "target_price"),
                            _decimal_text(terms.planned_r_for(target), "planned_r"),
                        )
                        for target in terms.targets
                    ],
                )
                self._insert_event(
                    connection=connection,
                    signal_id=signal.signal_id,
                    variant=None,
                    event_type=TradeEventType.SIGNAL_CREATED,
                    occurred_at=terms.created_at,
                    price=None,
                    payload={"strategy_version": signal.strategy_version},
                    dedupe_key=f"signal:{signal.signal_id}:created",
                )
        except sqlite3.IntegrityError as exc:
            raise DuplicateRecordError(f"Signal {signal.signal_id} already exists") from exc

    def get_signal_status(self, signal_id: str) -> SignalStatus:
        row = self._connection.execute(
            "SELECT lifecycle_status FROM signals WHERE signal_id = ?", (signal_id,)
        ).fetchone()
        if row is None:
            raise RecordNotFoundError(f"Signal {signal_id} was not found")
        return SignalStatus(row["lifecycle_status"])

    def get_lifecycle_signal(self, signal_id: str) -> LifecycleSignal:
        row = self._connection.execute(
            """
            SELECT s.*, t.fill_price, t.activated_at
            FROM signals AS s
            LEFT JOIN trades AS t ON t.signal_id = s.signal_id
            WHERE s.signal_id = ?
            """,
            (signal_id,),
        ).fetchone()
        if row is None:
            raise RecordNotFoundError(f"Signal {signal_id} was not found")
        targets = self._connection.execute(
            "SELECT ordinal, price FROM signal_targets WHERE signal_id = ? ORDER BY ordinal",
            (signal_id,),
        ).fetchall()
        tracks = self._connection.execute(
            """
            SELECT variant, current_stop, remaining_fraction, realized_r
            FROM trade_tracks
            WHERE signal_id = ? AND track_status = 'ACTIVE'
            ORDER BY variant
            """,
            (signal_id,),
        ).fetchall()

        def parse_time(value: object) -> datetime:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))

        return LifecycleSignal(
            signal_id=signal_id,
            status=SignalStatus(row["lifecycle_status"]),
            side=Side(row["side"]),
            created_at=parse_time(row["created_at"]),
            expires_at=parse_time(row["expires_at"]),
            entry_low=Decimal(row["entry_low"]),
            entry_high=Decimal(row["entry_high"]),
            original_stop=Decimal(row["original_stop"]),
            targets=tuple(Target(item["ordinal"], Decimal(item["price"])) for item in targets),
            estimated_cost_rate=Decimal(row["estimated_cost_rate"]),
            recommended_risk_percent=Decimal(row["recommended_risk_percent"]),
            fill_price=None if row["fill_price"] is None else Decimal(row["fill_price"]),
            activated_at=(None if row["activated_at"] is None else parse_time(row["activated_at"])),
            active_tracks=tuple(
                TrackState(
                    variant=OutcomeVariant(item["variant"]),
                    current_stop=Decimal(item["current_stop"]),
                    remaining_fraction=Decimal(item["remaining_fraction"]),
                    realized_r=Decimal(item["realized_r"]),
                )
                for item in tracks
            ),
        )

    def _signal_row(self, connection: sqlite3.Connection, signal_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM signals WHERE signal_id = ?", (signal_id,)
        ).fetchone()
        if row is None:
            raise RecordNotFoundError(f"Signal {signal_id} was not found")
        return row

    def activate_signal(
        self,
        signal_id: str,
        fill_price: Decimal,
        occurred_at: datetime,
        dedupe_key: str,
    ) -> None:
        fill = _decimal_text(fill_price, "fill_price")
        if Decimal(fill) <= 0:
            raise ValueError("fill_price must be positive")
        try:
            with self._transaction() as connection:
                row = self._signal_row(connection, signal_id)
                current = SignalStatus(row["lifecycle_status"])
                assert_transition(current, SignalStatus.ACTIVE)
                event_type = transition_event(current, SignalStatus.ACTIVE)

                updated = connection.execute(
                    """
                    UPDATE signals
                    SET lifecycle_status = 'ACTIVE', updated_at = ?, row_version = row_version + 1
                    WHERE signal_id = ? AND lifecycle_status = 'PENDING' AND row_version = ?
                    """,
                    (iso_utc(occurred_at), signal_id, row["row_version"]),
                )
                if updated.rowcount != 1:
                    raise ConcurrencyError("Signal changed during activation")

                targets = connection.execute(
                    """
                    SELECT ordinal, price, planned_r FROM signal_targets
                    WHERE signal_id = ? ORDER BY ordinal
                    """,
                    (signal_id,),
                ).fetchall()
                targets_payload = [dict(target) for target in targets]
                connection.execute(
                    """
                    INSERT INTO trades(
                        signal_id, activated_at, fill_price, original_entry_low,
                        original_entry_high, original_stop, original_targets_json,
                        strategy_version, activation_event_key
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        signal_id,
                        iso_utc(occurred_at),
                        fill,
                        row["entry_low"],
                        row["entry_high"],
                        row["original_stop"],
                        _json(targets_payload),
                        row["strategy_version"],
                        dedupe_key,
                    ),
                )
                connection.executemany(
                    """
                    INSERT INTO trade_tracks(
                        signal_id, variant, track_status, current_stop,
                        remaining_fraction, realized_r, updated_at, row_version
                    ) VALUES (?, ?, 'ACTIVE', ?, '1', '0', ?, 1)
                    """,
                    [
                        (signal_id, variant.value, row["original_stop"], iso_utc(occurred_at))
                        for variant in OutcomeVariant
                    ],
                )
                self._insert_event(
                    connection=connection,
                    signal_id=signal_id,
                    variant=None,
                    event_type=event_type,
                    occurred_at=occurred_at,
                    price=Decimal(fill),
                    payload={"fill_policy": "conservative-v1"},
                    dedupe_key=dedupe_key,
                )
        except sqlite3.IntegrityError as exc:
            raise DuplicateRecordError(
                "Activation was duplicated or another BTC signal is already active"
            ) from exc

    def expire_signal(self, signal_id: str, occurred_at: datetime, dedupe_key: str) -> None:
        self._transition_pending(signal_id, SignalStatus.EXPIRED, occurred_at, dedupe_key)

    def cancel_signal(self, signal_id: str, occurred_at: datetime, dedupe_key: str) -> None:
        self._transition_pending(signal_id, SignalStatus.CANCELLED, occurred_at, dedupe_key)

    def _transition_pending(
        self,
        signal_id: str,
        new_status: SignalStatus,
        occurred_at: datetime,
        dedupe_key: str,
    ) -> None:
        with self._transaction() as connection:
            row = self._signal_row(connection, signal_id)
            current = SignalStatus(row["lifecycle_status"])
            assert_transition(current, new_status)
            event_type = transition_event(current, new_status)
            updated = connection.execute(
                """
                UPDATE signals
                SET lifecycle_status = ?, updated_at = ?, row_version = row_version + 1
                WHERE signal_id = ? AND lifecycle_status = 'PENDING' AND row_version = ?
                """,
                (new_status.value, iso_utc(occurred_at), signal_id, row["row_version"]),
            )
            if updated.rowcount != 1:
                raise ConcurrencyError("Signal changed during lifecycle transition")
            self._insert_event(
                connection=connection,
                signal_id=signal_id,
                variant=None,
                event_type=event_type,
                occurred_at=occurred_at,
                price=None,
                payload={},
                dedupe_key=dedupe_key,
            )

    def append_trade_event(
        self,
        signal_id: str,
        variant: OutcomeVariant,
        event_type: TradeEventType,
        occurred_at: datetime,
        price: Decimal,
        dedupe_key: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        if event_type in {
            TradeEventType.SIGNAL_CREATED,
            TradeEventType.ENTRY_ACTIVATED,
            TradeEventType.ENTRY_EXPIRED,
            TradeEventType.SIGNAL_CANCELLED,
            TradeEventType.CLOSED,
        }:
            raise ValueError("This event is owned by a lifecycle operation")
        try:
            with self._transaction() as connection:
                self._require_active_track(connection, signal_id, variant)
                self._insert_event(
                    connection,
                    signal_id,
                    variant,
                    event_type,
                    occurred_at,
                    price,
                    payload or {},
                    dedupe_key,
                )
        except sqlite3.IntegrityError as exc:
            raise DuplicateRecordError(f"Event {dedupe_key} already exists") from exc

    def close_track(
        self,
        signal_id: str,
        variant: OutcomeVariant,
        result: OutcomeResult,
        result_r: Decimal,
        result_percent: Decimal,
        close_reason: str,
        close_event: TradeEventType,
        price: Decimal,
        occurred_at: datetime,
        dedupe_key: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        if close_event not in _CLOSE_EVENTS:
            raise ValueError("close_event is not a permitted terminal event")
        if not close_reason.strip():
            raise ValueError("close_reason is required")
        result_r_text = _decimal_text(result_r, "result_r")
        result_percent_text = _decimal_text(result_percent, "result_percent")
        close_price = _decimal_text(price, "close_price")

        try:
            with self._transaction() as connection:
                track = self._require_active_track(connection, signal_id, variant)
                updated = connection.execute(
                    """
                    UPDATE trade_tracks
                    SET track_status = 'CLOSED', realized_r = ?, closed_at = ?,
                        updated_at = ?, row_version = row_version + 1
                    WHERE signal_id = ? AND variant = ? AND track_status = 'ACTIVE'
                        AND row_version = ?
                    """,
                    (
                        result_r_text,
                        iso_utc(occurred_at),
                        iso_utc(occurred_at),
                        signal_id,
                        variant.value,
                        track["row_version"],
                    ),
                )
                if updated.rowcount != 1:
                    raise ConcurrencyError("Trade track changed during close")

                connection.execute(
                    """
                    INSERT INTO outcomes(
                        outcome_id, signal_id, variant, result, result_r,
                        result_percent, close_reason, closed_at, details_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid4()),
                        signal_id,
                        variant.value,
                        result.value,
                        result_r_text,
                        result_percent_text,
                        close_reason,
                        iso_utc(occurred_at),
                        _json(details or {}),
                    ),
                )
                self._insert_event(
                    connection,
                    signal_id,
                    variant,
                    close_event,
                    occurred_at,
                    Decimal(close_price),
                    {"result": result.value, "result_r": result_r_text},
                    f"{dedupe_key}:reason",
                )
                self._insert_event(
                    connection,
                    signal_id,
                    variant,
                    TradeEventType.CLOSED,
                    occurred_at,
                    Decimal(close_price),
                    {"close_reason": close_reason},
                    f"{dedupe_key}:closed",
                )

                if variant is OutcomeVariant.MANAGED:
                    signal_row = self._signal_row(connection, signal_id)
                    current = SignalStatus(signal_row["lifecycle_status"])
                    assert_transition(current, SignalStatus.CLOSED)
                    status_update = connection.execute(
                        """
                        UPDATE signals
                        SET lifecycle_status = 'CLOSED', updated_at = ?,
                            row_version = row_version + 1
                        WHERE signal_id = ? AND lifecycle_status = 'ACTIVE'
                            AND row_version = ?
                        """,
                        (iso_utc(occurred_at), signal_id, signal_row["row_version"]),
                    )
                    if status_update.rowcount != 1:
                        raise ConcurrencyError("Signal changed during managed close")
                self._insert_statistics_snapshot(
                    connection,
                    triggering_signal_id=signal_id,
                    triggering_variant=variant,
                    calculated_at=occurred_at,
                )
        except sqlite3.IntegrityError as exc:
            raise DuplicateRecordError(
                f"Track {signal_id}/{variant.value} already has this close or outcome"
            ) from exc

    def _require_active_track(
        self, connection: sqlite3.Connection, signal_id: str, variant: OutcomeVariant
    ) -> sqlite3.Row:
        row = connection.execute(
            """
            SELECT * FROM trade_tracks
            WHERE signal_id = ? AND variant = ?
            """,
            (signal_id, variant.value),
        ).fetchone()
        if row is None:
            raise RecordNotFoundError(f"Track {signal_id}/{variant.value} was not found")
        if TrackStatus(row["track_status"]) is not TrackStatus.ACTIVE:
            raise ConcurrencyError(f"Track {signal_id}/{variant.value} is already closed")
        return row

    def get_track_status(self, signal_id: str, variant: OutcomeVariant) -> TrackStatus:
        row = self._connection.execute(
            "SELECT track_status FROM trade_tracks WHERE signal_id = ? AND variant = ?",
            (signal_id, variant.value),
        ).fetchone()
        if row is None:
            raise RecordNotFoundError(f"Track {signal_id}/{variant.value} was not found")
        return TrackStatus(row["track_status"])

    def get_checkpoint(self, checkpoint_key: str) -> datetime | None:
        row = self._connection.execute(
            "SELECT last_processed_at FROM processing_checkpoints WHERE checkpoint_key = ?",
            (checkpoint_key,),
        ).fetchone()
        if row is None:
            return None
        return ensure_utc(
            datetime.fromisoformat(str(row["last_processed_at"]).replace("Z", "+00:00"))
        )

    def advance_checkpoint(
        self,
        checkpoint_key: str,
        processed_at: datetime,
        payload: dict[str, Any],
    ) -> None:
        if not checkpoint_key.strip():
            raise ValueError("checkpoint_key is required")
        timestamp = iso_utc(processed_at)
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO processing_checkpoints(
                    checkpoint_key, last_processed_at, source_cursor,
                    payload_json, updated_at, row_version
                ) VALUES (?, ?, NULL, ?, ?, 1)
                ON CONFLICT(checkpoint_key) DO UPDATE SET
                    last_processed_at = excluded.last_processed_at,
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at,
                    row_version = processing_checkpoints.row_version + 1
                WHERE excluded.last_processed_at > processing_checkpoints.last_processed_at
                """,
                (checkpoint_key, timestamp, _json(payload), timestamp),
            )

    def apply_management_decision(self, decision: ManagementDecision) -> None:
        try:
            with self._transaction() as connection:
                track = self._require_active_track(
                    connection, decision.signal_id, OutcomeVariant.MANAGED
                )
                stop = (
                    track["current_stop"]
                    if decision.updated_stop is None
                    else _decimal_text(decision.updated_stop, "updated_stop")
                )
                remaining = (
                    track["remaining_fraction"]
                    if decision.remaining_fraction_after is None
                    else _decimal_text(
                        decision.remaining_fraction_after, "remaining_fraction_after"
                    )
                )
                realized = (
                    track["realized_r"]
                    if decision.realized_r_after is None
                    else _decimal_text(decision.realized_r_after, "realized_r_after")
                )
                if decision.changes_managed_result:
                    updated = connection.execute(
                        """
                        UPDATE trade_tracks
                        SET current_stop = ?, remaining_fraction = ?, realized_r = ?,
                            updated_at = ?, row_version = row_version + 1
                        WHERE signal_id = ? AND variant = 'MANAGED'
                            AND track_status = 'ACTIVE' AND row_version = ?
                        """,
                        (
                            stop,
                            remaining,
                            realized,
                            iso_utc(decision.decided_at),
                            decision.signal_id,
                            track["row_version"],
                        ),
                    )
                    if updated.rowcount != 1:
                        raise ConcurrencyError("Managed track changed during decision")

                connection.execute(
                    """
                    INSERT INTO management_decisions(
                        decision_id, signal_id, decided_at, action, current_price,
                        unrealized_percent, unrealized_r, reason, updated_stop,
                        updated_target, changes_managed_result, strategy_version,
                        evidence_json, dedupe_key
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid4()),
                        decision.signal_id,
                        iso_utc(decision.decided_at),
                        decision.action.value,
                        _decimal_text(decision.current_price, "current_price"),
                        _decimal_text(decision.unrealized_percent, "unrealized_percent"),
                        _decimal_text(decision.unrealized_r, "unrealized_r"),
                        decision.reason,
                        None
                        if decision.updated_stop is None
                        else _decimal_text(decision.updated_stop, "updated_stop"),
                        int(decision.changes_managed_result),
                        decision.strategy_version,
                        _json(decision.evidence),
                        decision.dedupe_key,
                    ),
                )
                self._insert_event(
                    connection,
                    decision.signal_id,
                    OutcomeVariant.MANAGED,
                    TradeEventType.MANAGEMENT_DECISION,
                    decision.decided_at,
                    decision.current_price,
                    {
                        "action": decision.action.value,
                        "changes_managed_result": decision.changes_managed_result,
                        "strategy_version": decision.strategy_version,
                    },
                    f"{decision.dedupe_key}:event",
                )
        except sqlite3.IntegrityError as exc:
            raise DuplicateRecordError(
                f"Management decision {decision.dedupe_key} already exists"
            ) from exc

    def management_decision_exists(self, dedupe_key: str) -> bool:
        row = self._connection.execute(
            "SELECT 1 FROM management_decisions WHERE dedupe_key = ?",
            (dedupe_key,),
        ).fetchone()
        return row is not None

    def _insert_statistics_snapshot(
        self,
        connection: sqlite3.Connection,
        *,
        triggering_signal_id: str,
        triggering_variant: OutcomeVariant,
        calculated_at: datetime,
    ) -> None:
        rows = connection.execute(
            """
            SELECT o.signal_id, o.variant, o.result, o.result_r, o.closed_at,
                   s.strategy_version
            FROM outcomes AS o
            JOIN signals AS s ON s.signal_id = o.signal_id
            ORDER BY o.closed_at, o.outcome_id
            """
        ).fetchall()
        samples = tuple(
            OutcomeSample(
                signal_id=row["signal_id"],
                variant=OutcomeVariant(row["variant"]),
                result=OutcomeResult(row["result"]),
                result_r=Decimal(row["result_r"]),
                closed_at=datetime.fromisoformat(row["closed_at"].replace("Z", "+00:00")),
                strategy_version=row["strategy_version"],
            )
            for row in rows
        )
        report = calculate_statistics(samples, calculated_at)
        dedupe_key = (
            f"statistics:{triggering_signal_id}:{triggering_variant.value}:statistics-v0.9.0"
        )
        connection.execute(
            """
            INSERT INTO statistics_snapshots(
                snapshot_id, triggering_signal_id, triggering_variant,
                calculated_at, strategy_version, payload_json, dedupe_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid4()),
                triggering_signal_id,
                triggering_variant.value,
                iso_utc(calculated_at),
                "statistics-v0.9.0",
                _json(report.as_payload()),
                dedupe_key,
            ),
        )

    def get_latest_statistics_snapshot(self) -> dict[str, Any] | None:
        row = self._connection.execute(
            """
            SELECT snapshot_id, triggering_signal_id, triggering_variant,
                   calculated_at, strategy_version, payload_json, dedupe_key
            FROM statistics_snapshots
            ORDER BY calculated_at DESC, rowid DESC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        return {
            "snapshot_id": row["snapshot_id"],
            "triggering_signal_id": row["triggering_signal_id"],
            "triggering_variant": row["triggering_variant"],
            "calculated_at": row["calculated_at"],
            "strategy_version": row["strategy_version"],
            "payload": json.loads(row["payload_json"]),
            "dedupe_key": row["dedupe_key"],
        }

    def list_outcome_samples(
        self,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> tuple[OutcomeSample, ...]:
        """Read immutable outcome samples within a half-open UTC window."""
        clauses: list[str] = []
        parameters: list[str] = []
        if start_at is not None:
            clauses.append("o.closed_at >= ?")
            parameters.append(iso_utc(start_at))
        if end_at is not None:
            clauses.append("o.closed_at < ?")
            parameters.append(iso_utc(end_at))
        where = "" if not clauses else "WHERE " + " AND ".join(clauses)
        rows = self._connection.execute(
            f"""
            SELECT o.signal_id, o.variant, o.result, o.result_r, o.closed_at,
                   s.strategy_version
            FROM outcomes AS o
            JOIN signals AS s ON s.signal_id = o.signal_id
            {where}
            ORDER BY o.closed_at, o.outcome_id
            """,
            parameters,
        ).fetchall()
        return tuple(
            OutcomeSample(
                signal_id=row["signal_id"],
                variant=OutcomeVariant(row["variant"]),
                result=OutcomeResult(row["result"]),
                result_r=Decimal(row["result_r"]),
                closed_at=datetime.fromisoformat(row["closed_at"].replace("Z", "+00:00")),
                strategy_version=row["strategy_version"],
            )
            for row in rows
        )

    def list_report_signals(self, status: SignalStatus) -> tuple[ReportSignal, ...]:
        """Load compact active or pending state without mutating lifecycle data."""
        if status not in {SignalStatus.ACTIVE, SignalStatus.PENDING}:
            raise ValueError("Reports can list only ACTIVE or PENDING signals")
        rows = self._connection.execute(
            """
            SELECT s.*, t.fill_price, t.activated_at,
                   managed.current_stop AS managed_stop,
                   managed.track_status AS managed_track_status,
                   fixed.track_status AS fixed_track_status
            FROM signals AS s
            LEFT JOIN trades AS t ON t.signal_id = s.signal_id
            LEFT JOIN trade_tracks AS managed
              ON managed.signal_id = s.signal_id AND managed.variant = 'MANAGED'
            LEFT JOIN trade_tracks AS fixed
              ON fixed.signal_id = s.signal_id AND fixed.variant = 'FIXED'
            WHERE s.lifecycle_status = ?
            ORDER BY s.created_at, s.signal_id
            """,
            (status.value,),
        ).fetchall()

        def parse_time(value: object) -> datetime:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))

        result: list[ReportSignal] = []
        for row in rows:
            target_rows = self._connection.execute(
                "SELECT ordinal, price FROM signal_targets WHERE signal_id = ? ORDER BY ordinal",
                (row["signal_id"],),
            ).fetchall()
            result.append(
                ReportSignal(
                    signal_id=row["signal_id"],
                    status=status,
                    side=Side(row["side"]),
                    regime=MarketRegime(row["regime"]),
                    setup_score=int(row["setup_score"]),
                    created_at=parse_time(row["created_at"]),
                    expires_at=parse_time(row["expires_at"]),
                    entry_low=Decimal(row["entry_low"]),
                    entry_high=Decimal(row["entry_high"]),
                    original_stop=Decimal(row["original_stop"]),
                    targets=tuple(
                        Target(target["ordinal"], Decimal(target["price"]))
                        for target in target_rows
                    ),
                    strategy_version=row["strategy_version"],
                    fill_price=(None if row["fill_price"] is None else Decimal(row["fill_price"])),
                    activated_at=(
                        None if row["activated_at"] is None else parse_time(row["activated_at"])
                    ),
                    managed_stop=(
                        None if row["managed_stop"] is None else Decimal(row["managed_stop"])
                    ),
                    fixed_track_active=row["fixed_track_status"] == TrackStatus.ACTIVE.value,
                    managed_track_active=row["managed_track_status"] == TrackStatus.ACTIVE.value,
                )
            )
        return tuple(result)

    def _insert_event(
        self,
        connection: sqlite3.Connection,
        signal_id: str,
        variant: OutcomeVariant | None,
        event_type: TradeEventType,
        occurred_at: datetime,
        price: Decimal | None,
        payload: dict[str, Any],
        dedupe_key: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO trade_events(
                event_id, signal_id, variant, event_type, occurred_at,
                price, payload_json, dedupe_key, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid4()),
                signal_id,
                None if variant is None else variant.value,
                event_type.value,
                iso_utc(occurred_at),
                None if price is None else _decimal_text(price, "event_price"),
                _json(payload),
                dedupe_key,
                iso_utc(occurred_at),
            ),
        )

    def count_events(self, signal_id: str) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) AS count FROM trade_events WHERE signal_id = ?", (signal_id,)
        ).fetchone()
        assert row is not None
        return int(row["count"])

    def enqueue_alert(
        self,
        message_type: str,
        payload: dict[str, Any],
        dedupe_key: str,
        created_at: datetime,
        signal_id: str | None = None,
    ) -> str:
        outbox_id = str(uuid4())
        timestamp = iso_utc(created_at)
        try:
            with self._transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO outbox(
                        outbox_id, signal_id, message_type, payload_json,
                        delivery_status, dedupe_key, attempt_count, available_at,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'PENDING', ?, 0, ?, ?, ?)
                    """,
                    (
                        outbox_id,
                        signal_id,
                        message_type,
                        _json(payload),
                        dedupe_key,
                        timestamp,
                        timestamp,
                        timestamp,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise DuplicateRecordError(f"Alert {dedupe_key} already exists") from exc
        return outbox_id

    def count_outbox(self) -> int:
        row = self._connection.execute("SELECT COUNT(*) AS count FROM outbox").fetchone()
        assert row is not None
        return int(row["count"])

    def put_setting(self, key: str, value: str, updated_at: datetime) -> None:
        normalized = key.strip().upper()
        if not normalized or any(part in normalized for part in _SENSITIVE_SETTING_PARTS):
            raise SecurityError("Secrets and identity values cannot be stored in bot_settings")
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO bot_settings(setting_key, setting_value, updated_at, row_version)
                VALUES (?, ?, ?, 1)
                ON CONFLICT(setting_key) DO UPDATE SET
                    setting_value = excluded.setting_value,
                    updated_at = excluded.updated_at,
                    row_version = bot_settings.row_version + 1
                """,
                (normalized, value, iso_utc(updated_at)),
            )

    def get_setting(self, key: str) -> str | None:
        row = self._connection.execute(
            "SELECT setting_value FROM bot_settings WHERE setting_key = ?",
            (key.strip().upper(),),
        ).fetchone()
        return None if row is None else str(row["setting_value"])
