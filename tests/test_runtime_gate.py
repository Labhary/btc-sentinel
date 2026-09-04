import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "runtime_gate.py"


class RuntimeGateTests(unittest.TestCase):
    def run_gate(self, value: str) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["PAPER_ENGINE_ENABLED"] = value
        return subprocess.run(
            [sys.executable, str(SCRIPT)],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_disabled_gate_is_a_safe_noop(self) -> None:
        result = self.run_gate("false")
        self.assertEqual(result.returncode, 0)
        self.assertIn("disabled", result.stdout)

    def test_enabled_gate_refuses_unreviewed_activation(self) -> None:
        result = self.run_gate("true")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Activation refused", result.stderr)

    def test_gate_rejects_ambiguous_values(self) -> None:
        result = self.run_gate("yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("exactly true or false", result.stderr)


if __name__ == "__main__":
    unittest.main()
