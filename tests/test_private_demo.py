import json
from pathlib import Path
from unittest import TestCase

from scripts.build_private_demo import build_demo


class PrivateDemoTests(TestCase):
    def test_bundled_demo_is_generated_by_current_lifecycle_simulators(self):
        report = build_demo()
        bundled = Path(__file__).parents[1] / "worker/src/demo-report.json"
        self.assertEqual(json.loads(bundled.read_text()), report)
        self.assertFalse(report["performance_evidence"])
        self.assertEqual(report["closed_trades"], 24)
        self.assertEqual(sum(row["fixed"] == "WIN" for row in report["records"]), 12)
        self.assertTrue(all(float(row["planned_rr"]) >= 2 for row in report["records"]))
