import json
import time
import unittest
from dataclasses import dataclass


@dataclass
class FakeRateLimits:
    five_hour_pct: float | None = 12.5
    five_hour_resets_at: int | None = 1780312510
    five_hour_remaining_pct: float | None = None
    seven_day_pct: float | None = 34.0
    seven_day_resets_at: int | None = 1780846058
    seven_day_remaining_pct: float | None = None
    model: str = "gpt-5.5"
    updated_at: str = "2026-06-01T06:20:32.061Z"
    source: str = "test"


class HttpApiTest(unittest.TestCase):
    def test_codex_rate_limit_payload_has_expected_percentages(self):
        from src.http_api import build_codex_rate_limit_payload

        payload = build_codex_rate_limit_payload(lambda: FakeRateLimits())

        self.assertEqual(payload["agent"], "codex")
        self.assertTrue(payload["available"])
        self.assertEqual(payload["five_hour"]["used_percent"], 12.5)
        self.assertEqual(payload["weekly"]["used_percent"], 34.0)
        self.assertEqual(payload["5h"]["used_percent"], 12.5)
        self.assertEqual(payload["wk"]["used_percent"], 34.0)
        self.assertEqual(payload["5h"]["reset_at"], 1780312510)
        self.assertIn("reset_at_iso", payload["wk"])
        self.assertEqual(payload["model"], "gpt-5.5")
        self.assertNotIn("remaining_percent", payload["5h"])
        self.assertNotIn("expired", payload["5h"])
        self.assertNotIn("is_stale", payload["5h"])
        self.assertNotIn("source", payload)

    def test_handler_returns_json_for_codex_usage_alias(self):
        from src.http_api import encode_json_response

        status, headers, body = encode_json_response({"ok": True})

        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/json; charset=utf-8")
        self.assertEqual(json.loads(body.decode("utf-8")), {"ok": True})

    def test_rate_limit_cache_serves_cached_value_without_reloading(self):
        from src.http_api import RateLimitCache

        calls = 0

        def load_once():
            nonlocal calls
            calls += 1
            return FakeRateLimits(five_hour_pct=22.0)

        cache = RateLimitCache(load_once, refresh_seconds=300)
        cache.refresh()

        self.assertEqual(cache.get().five_hour_pct, 22.0)
        self.assertEqual(cache.get().five_hour_pct, 22.0)
        self.assertEqual(calls, 1)

    def test_rate_limit_cache_keeps_last_successful_value_on_refresh_failure(self):
        from src.http_api import RateLimitCache

        values = [FakeRateLimits(five_hour_pct=22.0), None]

        def load_next():
            return values.pop(0)

        cache = RateLimitCache(load_next, refresh_seconds=300)
        cache.refresh()
        cache.refresh()

        self.assertEqual(cache.get().five_hour_pct, 22.0)

    def test_measure_usage_latency_returns_probe_value(self):
        from src.http_api import measure_usage_latency_ms

        self.assertEqual(measure_usage_latency_ms(lambda timeout: 123, timeout_seconds=0.1), 123)

    def test_measure_usage_latency_returns_timeout_ms_on_failure(self):
        from src.http_api import measure_usage_latency_ms

        def fail(timeout):
            raise RuntimeError("boom")

        self.assertEqual(measure_usage_latency_ms(fail, timeout_seconds=0.1), 100)

    def test_measure_usage_latency_returns_timeout_ms_on_timeout(self):
        from src.http_api import measure_usage_latency_ms

        def slow(timeout):
            time.sleep(0.2)
            return 123

        self.assertEqual(measure_usage_latency_ms(slow, timeout_seconds=0.01), 10)

    def test_parse_latency_timeout_uses_default_and_query_param(self):
        from src.http_api import parse_latency_timeout

        self.assertEqual(parse_latency_timeout("/api/codex/usage/latency"), 5.0)
        self.assertEqual(parse_latency_timeout("/api/codex/usage/latency?timeout=3"), 3.0)
        self.assertEqual(parse_latency_timeout("/api/codex/usage/latency?timeout=bad"), 5.0)


if __name__ == "__main__":
    unittest.main()
