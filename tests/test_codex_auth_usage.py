import unittest

from src.adapters.codex_auth_usage import parse_usage_response
from src.adapters.types import RateLimits


class CodexAuthUsageTest(unittest.TestCase):
    def test_parse_usage_response_uses_primary_and_secondary_windows(self):
        rate_limits = parse_usage_response({
            "rate_limit": {
                "primary_window": {
                    "used_percent": 19,
                    "limit_window_seconds": 18000,
                    "reset_at": 1780418876,
                },
                "secondary_window": {
                    "used_percent": 45,
                    "limit_window_seconds": 604800,
                    "reset_at": 1780846058,
                },
            },
            "additional_rate_limits": [
                {
                    "limit_name": "GPT-5.3-Codex-Spark",
                    "rate_limit": {
                        "primary_window": {"used_percent": 0, "reset_at": 1780430111},
                        "secondary_window": {"used_percent": 0, "reset_at": 1781016911},
                    },
                }
            ],
        })

        self.assertIsNotNone(rate_limits)
        self.assertEqual(rate_limits.five_hour_pct, 19.0)
        self.assertEqual(rate_limits.five_hour_resets_at, 1780418876)
        self.assertEqual(rate_limits.seven_day_pct, 45.0)
        self.assertEqual(rate_limits.seven_day_resets_at, 1780846058)
        self.assertEqual(rate_limits.source, "chatgpt_wham_usage")

    def test_codex_loader_falls_back_to_tui_when_auth_usage_fails(self):
        from src.adapters import codex

        old_auth_loader = codex.codex_auth_usage.load_rate_limits_from_host_auth
        old_tui_loader = codex.codex_status_tui.load_rate_limits_from_host_status
        try:
            codex.codex_auth_usage.load_rate_limits_from_host_auth = lambda: (_ for _ in ()).throw(RuntimeError("no auth"))
            codex.codex_status_tui.load_rate_limits_from_host_status = lambda: RateLimits(five_hour_pct=22.0)

            rate_limits = codex.load_rate_limits()
        finally:
            codex.codex_auth_usage.load_rate_limits_from_host_auth = old_auth_loader
            codex.codex_status_tui.load_rate_limits_from_host_status = old_tui_loader

        self.assertIsNotNone(rate_limits)
        self.assertEqual(rate_limits.five_hour_pct, 22.0)


if __name__ == "__main__":
    unittest.main()
