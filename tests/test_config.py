import unittest
from decimal import Decimal

from btc_sentinel.config import DeploymentSecrets, PublicSettings
from btc_sentinel.errors import ConfigurationError


class PublicSettingsTests(unittest.TestCase):
    def test_defaults_are_btc_only_and_casablanca(self) -> None:
        settings = PublicSettings.from_env({})
        self.assertEqual(settings.trading_symbol, "BTCUSDT")
        self.assertEqual(settings.display_timezone, "Africa/Casablanca")
        self.assertEqual(settings.minimum_planned_rr, Decimal("2"))

    def test_rejects_another_symbol(self) -> None:
        with self.assertRaises(ConfigurationError):
            PublicSettings.from_env({"TRADING_SYMBOL": "ETHUSDT"})

    def test_rejects_default_risk_above_cap(self) -> None:
        with self.assertRaises(ConfigurationError):
            PublicSettings.from_env({"DEFAULT_RISK_PERCENT": "0.75", "MAX_RISK_PERCENT": "0.50"})


class DeploymentSecretsTests(unittest.TestCase):
    def valid_environment(self) -> dict[str, str]:
        return {
            "TELEGRAM_BOT_TOKEN": str(123456789) + ":" + ("A" * 35),
            "TELEGRAM_ADMIN_USER_ID": "123456789",
            "TELEGRAM_WEBHOOK_SECRET": "w" * 40,
            "STATE_API_HMAC_SECRET": "h" * 40,
        }

    def test_secret_repr_and_str_are_redacted(self) -> None:
        secrets = DeploymentSecrets.from_env(self.valid_environment())
        self.assertNotIn(":" + ("A" * 10), repr(secrets.telegram_bot_token))
        self.assertEqual(str(secrets.telegram_bot_token), "<redacted>")

    def test_rejects_reused_secret(self) -> None:
        env = self.valid_environment()
        env["STATE_API_HMAC_SECRET"] = env["TELEGRAM_WEBHOOK_SECRET"]
        with self.assertRaises(ConfigurationError):
            DeploymentSecrets.from_env(env)

    def test_rejects_placeholder(self) -> None:
        env = self.valid_environment()
        env["TELEGRAM_BOT_TOKEN"] = "SET_IN_SECRET_STORE_NOT_HERE"
        with self.assertRaises(ConfigurationError):
            DeploymentSecrets.from_env(env)


if __name__ == "__main__":
    unittest.main()
