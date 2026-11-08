"""AI 사용량 · 비용 기록 (adapters/llm/usage.py) · 환율 · 관리자 비용 API — DB · 실제 API 호출 없음"""
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from followup import settings as cfg
from followup.adapters import exchange_rate
from followup.adapters.llm import usage
from followup.adapters.llm.structured_client import OpenAIStructuredClient
from followup.adapters.postgres import usage as usage_table
from followup.analysis.summary.schemas import LLMVerification
from followup.entrypoints.api.main import app
from followup.entrypoints.api.routes.auth import current_user


class Sink:
    def __init__(self, fail=False):
        self.rows, self.fail = [], fail

    def __call__(self, **row):
        if self.fail:
            raise ConnectionError("db down")
        self.rows.append(row)


class UsageBase(unittest.TestCase):
    def setUp(self):
        self.sink = Sink()
        usage.enable(self.sink)
        self.addCleanup(usage.disable)


class CostTests(unittest.TestCase):
    def test_cost_uses_cached_price_for_cached_input(self):
        # gpt-6-luna: 입력 0.10 · 캐시 0.01 · 출력 0.50 (100만 토큰당)
        self.assertAlmostEqual(usage.cost_usd("gpt-6-luna", 1_000_000, 0, 0), 0.10)
        self.assertAlmostEqual(usage.cost_usd("gpt-6-luna", 1_000_000, 1_000_000, 0), 0.01)
        self.assertAlmostEqual(usage.cost_usd("gpt-6-luna", 400_000, 100_000, 200_000), 0.03 + 0.001 + 0.10)

    def test_dated_model_name_uses_base_price(self):
        self.assertEqual(usage.price("gpt-6-luna-2026-10-01"), cfg.LLM_PRICES_USD["gpt-6-luna"])

    def test_unknown_model_has_no_cost(self):
        self.assertIsNone(usage.cost_usd("other-model", 10, 0, 10))


class RecordTests(UsageBase):
    def test_nothing_is_recorded_without_sink(self):
        usage.disable()
        with usage.scope("M1", "summary"):
            usage.record("gpt-6-luna", 10, 0, 5)
        self.assertEqual(self.sink.rows, [])

    def test_scope_sets_meeting_and_feature(self):
        with usage.scope("M1", "evidence"):
            usage.record("gpt-6-luna", 1000, 200, 300)
        usage.record("gpt-6-luna", 1, 0, 1)        # 범위 밖 · 기능 없음 → 기록 안 함
        self.assertEqual(len(self.sink.rows), 1)
        row = self.sink.rows[0]
        self.assertEqual((row["meeting_id"], row["feature"], row["input_tokens"], row["cached_tokens"], row["output_tokens"]),
                         ("M1", "evidence", 1000, 200, 300))
        self.assertAlmostEqual(row["cost_usd"], usage.cost_usd("gpt-6-luna", 1000, 200, 300))

    def test_unknown_model_is_recorded_without_cost(self):
        usage.record("other-model", 10, 0, 5, feature="ask")
        self.assertIsNone(self.sink.rows[0]["cost_usd"])

    def test_sink_failure_does_not_raise_and_pauses(self):
        failing = Sink(fail=True)
        usage.enable(failing)
        usage.record("gpt-6-luna", 10, 0, 5, feature="ask")         # 예외 없음
        usage.enable(self.sink)
        usage._paused_until = usage.time.monotonic() + 60            # 실패 직후 상태
        usage.record("gpt-6-luna", 10, 0, 5, feature="ask")
        self.assertEqual(self.sink.rows, [])

    def test_responses_usage_is_read(self):
        response = SimpleNamespace(model="gpt-6-luna", usage=SimpleNamespace(
            input_tokens=500, input_tokens_details=SimpleNamespace(cached_tokens=100), output_tokens=70))
        with usage.scope("M2", "summary"):
            usage.record_responses(response)
            usage.record_responses(SimpleNamespace(output_parsed=None))     # 사용량이 없는 응답은 건너뜀
        self.assertEqual([(r["input_tokens"], r["cached_tokens"], r["output_tokens"]) for r in self.sink.rows],
                         [(500, 100, 70)])

    def test_chat_usage_is_read(self):
        usage.record_chat("gpt-6-luna", {"prompt_tokens": 40, "completion_tokens": 9,
                                         "prompt_tokens_details": {"cached_tokens": 4}}, meeting_id="M3", feature="recommend")
        usage.record_chat("gpt-6-luna", None, meeting_id="M3", feature="recommend")
        self.assertEqual([(r["meeting_id"], r["input_tokens"], r["cached_tokens"], r["output_tokens"]) for r in self.sink.rows],
                         [("M3", 40, 4, 9)])

    def test_langchain_callback_records_each_response(self):
        from langchain_core.messages import AIMessage
        from langchain_core.outputs import ChatGeneration, LLMResult
        message = AIMessage(content="{}", response_metadata={"model_name": "gpt-6-luna"},
                            usage_metadata={"input_tokens": 30, "output_tokens": 6, "total_tokens": 36,
                                            "input_token_details": {"cache_read": 10}})
        usage.langchain_callback("recommend", "M4").on_llm_end(LLMResult(generations=[[ChatGeneration(message=message)]]))
        row = self.sink.rows[0]
        self.assertEqual((row["meeting_id"], row["feature"], row["input_tokens"], row["cached_tokens"], row["output_tokens"]),
                         ("M4", "recommend", 30, 10, 6))

    def test_structured_client_records_with_runner_scope(self):
        from followup.adapters.storage.local_files import LocalFileStore
        from followup.application.summarize_meeting import _StepRunner
        import tempfile
        response = SimpleNamespace(model="gpt-6-luna", output_parsed=LLMVerification(issues=[]),
                                   usage=SimpleNamespace(input_tokens=20, input_tokens_details=None, output_tokens=3))
        client = OpenAIStructuredClient(client=SimpleNamespace(responses=SimpleNamespace(parse=lambda **kw: response)))
        with tempfile.TemporaryDirectory() as root, patch.dict("os.environ", {"OPENAI_MODEL": "gpt-6-luna"}):
            runner = _StepRunner(client, LocalFileStore(root), "M5", use_cache=False, feature="evidence")
            runner("s", "u", LLMVerification, step="verify")
        self.assertEqual([(r["meeting_id"], r["feature"], r["input_tokens"]) for r in self.sink.rows], [("M5", "evidence", 20)])


class ExchangeRateTests(unittest.TestCase):
    def setUp(self):
        exchange_rate.reset()
        self.addCleanup(exchange_rate.reset)

    def test_fetched_rate_is_cached(self):
        with patch.object(exchange_rate, "_fetch", return_value={"rate": 1358.73, "date": "2026-11-06", "fallback": False}) as f:
            self.assertEqual(exchange_rate.usd_krw(lambda: 1400.0)["rate"], 1358.73)
            exchange_rate.usd_krw(lambda: 1400.0)
        f.assert_called_once()

    def test_failure_uses_fallback(self):
        with patch.object(exchange_rate, "_fetch", side_effect=OSError("blocked")):
            rate = exchange_rate.usd_krw(lambda: 1400.0)
        self.assertEqual((rate["rate"], rate["fallback"]), (1400.0, True))


class MonthKeyTests(unittest.TestCase):
    def test_months_cross_year_oldest_first(self):
        self.assertEqual(usage_table._month_keys(date(2026, 2, 10), 4), ["2025-11", "2025-12", "2026-01", "2026-02"])


class AdminCostRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.addCleanup(app.dependency_overrides.clear)

    def test_member_is_forbidden(self):
        app.dependency_overrides[current_user] = lambda: {"user_id": "u1", "permission": "member"}
        self.assertEqual(self.client.get("/api/admin/cost").status_code, 403)
        self.assertEqual(self.client.put("/api/admin/budget", json={"amount_krw": 1, "alert_pct": 80}).status_code, 403)

    def test_admin_gets_months_budget_and_rate(self):
        app.dependency_overrides[current_user] = lambda: {"user_id": "a1", "permission": "admin"}
        months = [{"key": "2026-11", "year": 2026, "month": 11, "cost_usd": 1.5, "notes": 3, "unpriced": 0, "features": {}}]
        with patch.object(usage_table, "monthly_usage", return_value=months), \
                patch.object(usage_table, "budget", return_value={"amount_krw": 50000, "alert_pct": 80}), \
                patch.object(exchange_rate, "usd_krw", return_value={"rate": 1358.73, "date": "2026-11-06", "fallback": False}):
            body = self.client.get("/api/admin/cost").json()
        self.assertEqual((body["months"], body["budget"]["amount_krw"], body["rate"]["rate"]), (months, 50000, 1358.73))

    def test_budget_is_validated_and_saved(self):
        app.dependency_overrides[current_user] = lambda: {"user_id": "a1", "permission": "admin"}
        self.assertEqual(self.client.put("/api/admin/budget", json={"amount_krw": 0, "alert_pct": 80}).status_code, 422)
        with patch.object(usage_table, "save_budget", return_value={"amount_krw": 60000, "alert_pct": 90}) as save:
            res = self.client.put("/api/admin/budget", json={"amount_krw": 60000, "alert_pct": 90})
        self.assertEqual(res.json(), {"amount_krw": 60000, "alert_pct": 90})
        save.assert_called_once_with(actor_id="a1", amount_krw=60000, alert_pct=90)

    def test_defaults(self):
        self.assertEqual((cfg.DEFAULT_BUDGET_KRW, cfg.DEFAULT_BUDGET_ALERT_PCT, cfg.DEFAULT_USD_KRW), (50000, 80, 1400))
