import io
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from btc_sentinel.config import PublicSettings, SecretValue
from btc_sentinel.errors import ConfigurationError
from btc_sentinel.persistence.state_api_repository import StateApiRepository
from btc_sentinel.runtime.job import RuntimeJobConfig, build_orchestrator, execute, main
from btc_sentinel.runtime.orchestrator import PaperEngineOrchestrator, RunStatus, RunSummary
from btc_sentinel.runtime.state_bridge import StateApiRuntimeBridge

NOW = datetime(2026, 9, 3, 13, 30, tzinfo=UTC)


def valid_env() -> dict[str, str]:
    return {
        "PAPER_ENGINE_ENABLED": "true",
        "APP_ENV": "production",
        "DISPLAY_TIMEZONE": "Africa/Casablanca",
        "TRADING_SYMBOL": "BTCUSDT",
        "MIN_PLANNED_RR": "2",
        "DEFAULT_RISK_PERCENT": "0.50",
        "MAX_RISK_PERCENT": "1.00",
        "DISPATCH_KEY": "paper-engine:2026-09-03T13:30:00Z",
        "STATE_API_BASE_URL": "https://worker.example",
        "STATE_API_HMAC_SECRET": "h" * 40,
    }


class StaticOrchestrator:
    def __init__(self, status: RunStatus) -> None:
        self.status = status

    def run(self, run_id: str, as_of: datetime) -> RunSummary:
        return RunSummary(run_id, as_of, as_of, self.status, True, 2, 7, None, "CLEAR", ())


class RuntimeJobTests(TestCase):
    def test_config_requires_explicit_gate_and_frozen_strategy(self) -> None:
        config = RuntimeJobConfig.from_env(valid_env())
        self.assertEqual(config.dispatch_key, "paper-engine:2026-09-03T13:30:00Z")
        self.assertEqual(str(config.state_api_hmac_secret), "<redacted>")

        for updates in (
            {"PAPER_ENGINE_ENABLED": "false"},
            {"APP_ENV": "development"},
            {"MIN_PLANNED_RR": "3"},
            {"DEFAULT_RISK_PERCENT": "0.25"},
            {"STATE_API_BASE_URL": "SET_AFTER_WORKER_PREVIEW_EXISTS"},
        ):
            env = valid_env()
            env.update(updates)
            with self.subTest(updates=updates), self.assertRaises(ConfigurationError):
                RuntimeJobConfig.from_env(env)

    def test_builder_wires_real_public_and_signed_adapters_without_io(self) -> None:
        orchestrator = build_orchestrator(RuntimeJobConfig.from_env(valid_env()), lambda: NOW)
        self.assertIsInstance(orchestrator, PaperEngineOrchestrator)
        self.assertIsInstance(orchestrator.repository, StateApiRepository)
        self.assertIsInstance(orchestrator.state_provider, StateApiRuntimeBridge)
        self.assertIs(orchestrator.state_provider, orchestrator.notification_sink)
        self.assertIs(orchestrator.state_provider, orchestrator.health_sink)

    def test_execute_emits_bounded_json_and_fails_failed_runs(self) -> None:
        config = RuntimeJobConfig(
            "paper-engine:test",
            "https://worker.example",
            SecretValue("h" * 40),
            PublicSettings(app_env="production"),
        )
        for status, expected in ((RunStatus.OK, 0), (RunStatus.DEGRADED, 0), (RunStatus.FAILED, 1)):
            output = io.StringIO()
            with self.subTest(status=status), patch("sys.stdout", output):
                result = execute(
                    config,
                    clock=lambda: NOW,
                    builder=lambda _config, _clock: StaticOrchestrator(status),
                )
            self.assertEqual(result, expected)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["status"], status.value)
            self.assertNotIn("state_api", output.getvalue().lower())

    def test_main_logs_only_exception_class(self) -> None:
        env = valid_env()
        env["STATE_API_BASE_URL"] = "http://unsafe.example"
        error = io.StringIO()
        with patch.dict(os.environ, env, clear=True), patch("sys.stderr", error):
            self.assertEqual(main(), 1)
        self.assertEqual(
            json.loads(error.getvalue()),
            {"event": "paper_engine_refused", "error_name": "ConfigurationError"},
        )
        self.assertNotIn(env["STATE_API_HMAC_SECRET"], error.getvalue())

    def test_workflow_keeps_hard_gate_before_unreachable_entrypoint(self) -> None:
        workflow = (
            Path(__file__).resolve().parents[1] / ".github" / "workflows" / "paper-engine.yml"
        ).read_text(encoding="utf-8")
        self.assertLess(
            workflow.index("python scripts/runtime_gate.py"),
            workflow.index("run: btc-sentinel-paper-engine"),
        )
        self.assertIn("if: ${{ env.PAPER_ENGINE_ENABLED == 'true' }}", workflow)
        self.assertNotIn("TELEGRAM_BOT_TOKEN", workflow)
