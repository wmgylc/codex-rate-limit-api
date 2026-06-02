import os
import unittest

from src.adapters.codex_status_tui import parse_status_output


STATUS_OUTPUT = """
/status
╭────────────────────────────────────────────────────────────────────────────────────────╮
│ >_ OpenAI Codex (v0.136.0-alpha.2)                                                     │
│                                                                                        │
│ Model: gpt-5.5 (reasoning medium, summaries auto)                                      │
│ Directory: /tmp                                                                        │
│ Account: user@example.com (Plus)                                                       │
│                                                                                        │
│ 5h limit: [██████████████████░░] 91% left (resets 00:47 on 3 Jun)                      │
│ Weekly limit: [███████████░░░░░░░░░] 56% left (resets 23:27 on 7 Jun)                  │
│ GPT-5.3-Codex-Spark limit:                                                             │
│ 5h limit: [████████████████████] 100% left (resets 01:31 on 3 Jun)                     │
│ Weekly limit: [████████████████████] 100% left (resets 20:31 on 9 Jun)                 │
╰────────────────────────────────────────────────────────────────────────────────────────╯
"""


class CodexStatusTuiTest(unittest.TestCase):
    def test_parse_status_output_uses_first_main_limits(self):
        old_tz = os.environ.get("CODEX_HOST_TIMEZONE")
        os.environ["CODEX_HOST_TIMEZONE"] = "Asia/Shanghai"
        try:
            rate_limits = parse_status_output(STATUS_OUTPUT)
        finally:
            if old_tz is None:
                os.environ.pop("CODEX_HOST_TIMEZONE", None)
            else:
                os.environ["CODEX_HOST_TIMEZONE"] = old_tz

        self.assertIsNotNone(rate_limits)
        self.assertEqual(rate_limits.five_hour_remaining_pct, 91.0)
        self.assertEqual(rate_limits.five_hour_pct, 9.0)
        self.assertEqual(rate_limits.seven_day_remaining_pct, 56.0)
        self.assertEqual(rate_limits.seven_day_pct, 44.0)
        self.assertEqual(rate_limits.model, "gpt-5.5")
        self.assertEqual(rate_limits.source, "codex_cli_status")


if __name__ == "__main__":
    unittest.main()
