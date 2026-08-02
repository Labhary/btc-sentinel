import unittest

from btc_sentinel.security.redaction import redact


class RedactionTests(unittest.TestCase):
    def test_redacts_nested_sensitive_keys(self) -> None:
        cleaned = redact(
            {
                "status": "failed",
                "authorization": "Bearer should-not-appear",
                "nested": {"telegram_admin_user_id": 123456},
            }
        )
        self.assertEqual(cleaned["status"], "failed")
        self.assertEqual(cleaned["authorization"], "<redacted>")
        self.assertEqual(cleaned["nested"]["telegram_admin_user_id"], "<redacted>")

    def test_redacts_token_inside_text(self) -> None:
        token = str(123456789) + ":" + ("A" * 35)
        cleaned = redact(f"request failed for {token}")
        self.assertNotIn(token, cleaned)
        self.assertIn("redacted", cleaned)

    def test_redacts_environment_assignment(self) -> None:
        cleaned = redact("STATE_API_HMAC_SECRET=do-not-log-this")
        self.assertEqual(cleaned, "STATE_API_HMAC_SECRET=<redacted>")


if __name__ == "__main__":
    unittest.main()
