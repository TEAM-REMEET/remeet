"""요약 · 액션 아이템 추출 사용 사례 — 가짜 LLM으로 실행 (실제 API를 호출하지 않음)

요구사항 ID: specs/003-summary-action-extraction/spec.md
"""
import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from followup import settings as cfg
from followup.adapters.llm.structured_client import OpenAIStructuredClient
from followup.adapters.storage.local_files import LocalFileStore
from followup.analysis.summary.preprocessing import TranscriptFormatError
from followup.analysis.summary.schemas import (LLMAgenda, LLMDeletion, LLMDiscussionItem, LLMDiscussionUpdate, LLMMeetingSummary,
                                       LLMRevision, LLMVerification, LLMVerifyIssue)
from followup import settings as cfg
from followup.analysis.summary.steps import EmptySummaryError
from followup.application.summarize_meeting import LLMCallLimitError, _StepRunner, summarize_meeting
from followup.entrypoints import cli

TRANSCRIPT = (Path(__file__).parents[1] / "fixtures" / "sample_meeting.txt").read_text(encoding="utf-8")


def discussion(text, **kw):
    base = dict(subtopic="크기", discussion_tag="제안", decision_tag=None, text=text, assignees=[], due=None,
                evidence_utterance_indices=[2])
    return LLMDiscussionItem(**{**base, **kw})


def generated(*items):
    items = items or (discussion("홍길동A가 작은 크기를 제안함."),
                      discussion("부품 단가를 조사함.", discussion_tag="액션", decision_tag="결정", assignees=["김철수A"],
                                 due="2004-10-15", evidence_utterance_indices=[4]),
                      discussion("시제품을 제작함.", discussion_tag="액션", decision_tag="결정", assignees=["홍길동B"],
                                 evidence_utterance_indices=[5]))
    return LLMMeetingSummary(agendas=[LLMAgenda(agenda_title="리모컨 크기", discussion_background=[], major_discussions=list(items),
                                                unresolved_issues=[], next_meeting_topics=[])])


def issue(item_id="M_a1_d1", issue_type="액션 담당자 · 기한 오류"):
    return LLMVerifyIssue(issue_type=issue_type, item_ids=[item_id], agenda_id=None, description="문제",
                          evidence_utterance_indices=[1], suggestion="고칠 것")


def revision(**kw):
    return LLMRevision(**{**{k: [] for k in LLMRevision.model_fields}, **kw})


def update(item_id="M_a1_d1", text="수정된 제안을 함."):
    return LLMDiscussionUpdate(item_id=item_id, subtopic="크기", discussion_tag="제안", decision_tag=None, text=text, assignees=[],
                               due=None, evidence_utterance_indices=[2], reason="수정", resolves_issue_ids=[])


class FakeLLM:
    """응답을 순서대로 반환 (fail_at번째 호출에서 오류)"""

    def __init__(self, generations=None, verifications=([],), revisions=(), fail_at=None):
        self.generations = list(generations or [generated()])
        self.verifications, self.revisions = list(verifications), list(revisions)
        self.fail_at, self.calls = fail_at, []

    def parse(self, system, user, schema, effort):
        self.calls.append(schema.__name__)
        if self.fail_at == len(self.calls):
            raise ConnectionError("일시 오류")
        if schema is LLMMeetingSummary:
            return self.generations.pop(0)
        if schema is LLMVerification:
            return LLMVerification(issues=self.verifications.pop(0))
        return self.revisions.pop(0)


GEN, VER, REV = "LLMMeetingSummary", "LLMVerification", "LLMRevision"


class Base(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.store = LocalFileStore(self.root)
        env = patch.dict(os.environ, {"OPENAI_MODEL": "fake-model", "LANGSMITH_TRACING": "false"})
        env.start()
        self.addCleanup(env.stop)

    def run_fake(self, llm, text=TRANSCRIPT, meeting_id="M", **kw):
        return summarize_meeting(meeting_id, text, llm=llm, store=self.store, **kw)

    def saved_json(self, meeting_id="M"):
        return json.loads((self.root / meeting_id / f"{meeting_id}_summary.json").read_text(encoding="utf-8"))


class FlowTests(Base):
    def test_sa001_passes_and_saves_only_summary_json(self):
        llm = FakeLLM()
        summary = self.run_fake(llm, source_name="회의.txt")
        self.assertEqual(llm.calls, [GEN, VER])
        self.assertEqual(summary.verification.status, "PASSED")
        self.assertEqual([p.name for p in (self.root / "M").iterdir()], ["M_summary.json"])
        saved = self.saved_json()
        self.assertEqual(saved["agendas"][0]["major_discussions"][1]["item_id"], summary.agendas[0].major_discussions[1].item_id)
        self.assertEqual(saved["source_document"]["input_path"], "회의.txt")

    def test_sa002_actions_are_in_results_with_assignee_and_due(self):
        summary = self.run_fake(FakeLLM())
        self.assertEqual(len(summary.meeting_results.action_item_ids), 2)
        action = summary.find_item(summary.meeting_results.action_item_ids[0])
        self.assertEqual((action.assignee_user_ids, action.assignee_jobs, action.due), (["u4"], ["개발자"], "2004-10-15"))

    def test_sa002_multiple_assignees_keep_order_and_aligned_fields(self):
        item = discussion("부품 단가를 함께 조사함.", discussion_tag="액션", decision_tag="결정",
                          assignees=["김철수A", "홍길동A", "-", "김철수A", "홍길동(산업디자이너)"], evidence_utterance_indices=[4])
        action = self.run_fake(FakeLLM(generations=[generated(item)])).agendas[0].major_discussions[0]
        self.assertEqual(action.assignees, ["김철수A", "홍길동(산업디자이너)"])     # 빈 값 · 중복 제외, 표시 이름으로 복원
        self.assertEqual(action.assignee_user_ids, ["u4", "u2"])
        self.assertEqual(action.assignee_names, ["김철수A", "홍길동"])
        self.assertEqual(action.assignee_jobs, ["개발자", "산업디자이너"])
        self.assertEqual(action.status, "ACTIVE")

    def test_sa002_assignee_written_differently_is_shown_with_display_name(self):
        item = discussion("부품 단가를 조사함.", discussion_tag="액션", decision_tag="결정", assignees=["홍길동_산업디자이너"],
                          evidence_utterance_indices=[4])
        action = self.run_fake(FakeLLM(generations=[generated(item)])).agendas[0].major_discussions[0]
        self.assertEqual((action.assignees, action.assignee_user_ids), (["홍길동(산업디자이너)"], ["u2"]))

    def test_sa002_unmatched_assignee_is_kept_with_null_fields_for_review(self):
        item = discussion("부품 단가를 조사함.", discussion_tag="액션", decision_tag="결정", assignees=["김철수A", "없는사람"],
                          evidence_utterance_indices=[4])
        action = self.run_fake(FakeLLM(generations=[generated(item)])).agendas[0].major_discussions[0]
        self.assertEqual((action.assignees, action.assignee_user_ids), (["김철수A", "없는사람"], ["u4", None]))
        self.assertEqual(action.status, "REVIEW_REQUIRED")
        self.assertIn("없는사람", action.review_reasons[0])

    def test_sa002_non_action_assignees_are_cleared_with_history(self):
        summary = self.run_fake(FakeLLM(generations=[generated(discussion("제안함.", assignees=["김철수A"]))]))
        item = summary.agendas[0].major_discussions[0]
        self.assertEqual((item.assignees, item.assignee_user_ids), ([], []))
        self.assertEqual(item.history[0].before, ["김철수A"])

    def test_sa002_revision_can_change_assignees(self):
        change = update(text="부품 단가를 함께 조사함.").model_copy(update=dict(
            discussion_tag="액션", decision_tag="결정", assignees=["김철수A", "김철수B"], due=None))
        llm = FakeLLM(verifications=[[issue()], []], revisions=[revision(discussion_updates=[change])])
        item = self.run_fake(llm).find_item("M_a1_d1")
        self.assertEqual((item.assignees, item.assignee_user_ids), (["김철수A", "김철수B"], ["u4", "u5"]))

    def test_sa003_homonym_alias_is_restored_before_save(self):
        self.run_fake(FakeLLM())
        raw = json.dumps(self.saved_json(), ensure_ascii=False)
        self.assertNotIn("홍길동A", raw)
        self.assertNotIn("홍길동B", raw)
        self.assertIn("홍길동(산업디자이너)가 작은 크기를 제안함.", raw)
        self.assertIn("김철수A", raw)       # 실제 이름은 유지

    def test_sa004_revises_then_passes(self):
        llm = FakeLLM(verifications=[[issue()], []], revisions=[revision(discussion_updates=[update()])])
        summary = self.run_fake(llm)
        self.assertEqual(llm.calls, [GEN, VER, REV, VER])
        self.assertEqual(summary.verification.status, "PASSED")
        self.assertEqual(summary.find_item("M_a1_d1").text, "수정된 제안을 함.")

    def test_sa005_stops_after_three_verifications(self):
        llm = FakeLLM(verifications=[[issue()]] * 3, revisions=[revision(), revision()])
        summary = self.run_fake(llm)
        self.assertEqual(llm.calls, [GEN, VER, REV, VER, REV, VER])
        self.assertEqual(summary.verification.status, "REVIEW_REQUIRED")
        self.assertEqual(summary.find_item("M_a1_d1").status, "REVIEW_REQUIRED")

    def test_sa005_improvement_is_revised_only_in_first_round(self):
        improve = issue(issue_type="깊이 부족")
        llm = FakeLLM(verifications=[[improve], [improve]], revisions=[revision()])
        summary = self.run_fake(llm)
        self.assertEqual(llm.calls, [GEN, VER, REV, VER])
        self.assertEqual(summary.verification.status, "PASSED")
        self.assertEqual(summary.find_item("M_a1_d1").status, "ACTIVE")
        last = summary.verification.rounds[-1]
        self.assertTrue(last.passed)
        self.assertEqual([i.issue_type for i in last.issues], ["깊이 부족"])    # 기록은 남음

    def test_sa005_only_errors_block_after_first_round(self):
        improve, error = issue("M_a1_d1", "깊이 부족"), issue("M_a1_d2")
        llm = FakeLLM(verifications=[[improve], [improve, error], [improve, error]], revisions=[revision(), revision()])
        summary = self.run_fake(llm)
        self.assertEqual(llm.calls, [GEN, VER, REV, VER, REV, VER])
        self.assertEqual(summary.verification.status, "REVIEW_REQUIRED")
        self.assertEqual(summary.verification.remaining_issue_ids, ["v3_i2"])   # 오류만 남은 문제
        self.assertEqual(summary.find_item("M_a1_d1").status, "ACTIVE")         # 개선만 지적된 항목은 그대로
        self.assertEqual(summary.find_item("M_a1_d2").status, "REVIEW_REQUIRED")

    def test_sa005_revise_receives_only_errors_after_first_round(self):
        improve, error = issue("M_a1_d1", "깊이 부족"), issue("M_a1_d2")
        seen = []

        class Recording(FakeLLM):
            def parse(self, system, user, schema, effort):
                if schema is LLMRevision:
                    seen.append(user)
                return super().parse(system, user, schema, effort)

        self.run_fake(Recording(verifications=[[improve], [improve, error], []], revisions=[revision(), revision()]))
        self.assertIn("v1_i1", seen[0])
        self.assertNotIn("v2_i1", seen[1])
        self.assertIn("v2_i2", seen[1])

    def test_sa006_empty_generation_retries_once_then_fails_without_saving(self):
        empty = LLMMeetingSummary(agendas=[])
        llm = FakeLLM(generations=[empty, generated()])
        self.assertEqual(self.run_fake(llm).verification.status, "PASSED")
        self.assertEqual(llm.calls, [GEN, GEN, VER])
        llm = FakeLLM(generations=[empty, empty])
        with self.assertRaises(EmptySummaryError):
            self.run_fake(llm, meeting_id="E")
        self.assertFalse((self.root / "E" / "E_summary.json").exists())

    def test_sa006_all_items_deleted_is_not_passed(self):
        deletion = LLMDeletion(item_id="M_a1_d1", reason="삭제", resolves_issue_ids=[])
        llm = FakeLLM(generations=[generated(discussion("하나뿐인 항목."))], verifications=[[issue()], []],
                      revisions=[revision(deletions=[deletion])])
        self.assertEqual(self.run_fake(llm).verification.status, "REVIEW_REQUIRED")

    def test_sa007_invalid_input_stops_before_llm(self):
        llm = FakeLLM()
        with self.assertRaises(TranscriptFormatError):
            self.run_fake(llm, text="회의 제목 없음\n발화\n")
        self.assertEqual(llm.calls, [])

    def test_sa007_invalid_meeting_id_stops_before_llm(self):
        llm = FakeLLM()
        for meeting_id in ("../x", "a/b", "", ".."):
            with self.subTest(meeting_id=meeting_id), self.assertRaises(ValueError):
                self.run_fake(llm, meeting_id=meeting_id)
        self.assertEqual(llm.calls, [])

    def test_sa008_due_must_be_a_date(self):
        items = [discussion(f"작업 {i}", discussion_tag="액션", decision_tag="결정", due=due, evidence_utterance_indices=[4])
                 for i, due in enumerate(("2004-10-8", "2004-10-18 14:30", "다음 회의", "2004-13-40"))]
        summary = self.run_fake(FakeLLM(generations=[generated(*items)]))
        dues = [d.due for d in summary.agendas[0].major_discussions]
        self.assertEqual(dues, ["2004-10-08", "2004-10-18 14:30", None, None])
        self.assertTrue(any(h.field == "due" and h.before == "다음 회의" for h in summary.agendas[0].major_discussions[2].history))

    def test_sa009_no_key_or_usage_in_saved_json(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "secret-value"}):
            self.run_fake(FakeLLM())
        raw = (self.root / "M" / "M_summary.json").read_text(encoding="utf-8")
        self.assertNotIn("secret-value", raw)
        for word in ("usage", "total_tokens", "cost"):
            self.assertNotIn(word, raw)
        self.assertEqual(len(self.saved_json()["config"]["prompt_version"]), 12)


class ResumeTests(Base):
    def test_sa010_rerun_continues_from_failed_step(self):
        for fail_at, expected in ((1, [GEN, VER, REV, VER]), (2, [VER, REV, VER]), (3, [REV, VER]), (4, [VER])):
            meeting_id = f"F{fail_at}"
            item_id = f"{meeting_id}_a1_d1"

            def llm(**kw):
                return FakeLLM(verifications=[[issue(item_id)], []], revisions=[revision(discussion_updates=[update(item_id)])], **kw)
            with self.subTest(fail_at=fail_at):
                with self.assertRaises(ConnectionError):
                    self.run_fake(llm(fail_at=fail_at), meeting_id=meeting_id)
                self.assertFalse((self.root / meeting_id / f"{meeting_id}_summary.json").exists())
                retry = llm()
                retry.verifications = retry.verifications[-expected.count(VER):]    # 이미 끝난 검증 응답은 저장소에서 재사용
                summary = self.run_fake(retry, meeting_id=meeting_id)
                self.assertEqual(retry.calls, expected)
                self.assertEqual(summary.verification.status, "PASSED")
                self.assertEqual(summary.find_item(item_id).text, "수정된 제안을 함.")
                self.assertFalse((self.root / meeting_id / "steps").exists())

    def test_sa010_changed_input_is_not_reused(self):
        with self.assertRaises(ConnectionError):
            self.run_fake(FakeLLM(fail_at=2))
        retry = FakeLLM()
        self.run_fake(retry, text=TRANSCRIPT + "한유리_u1: 추가 발화입니다\n")
        self.assertEqual(retry.calls, [GEN, VER])

    def test_sa010_no_cache_ignores_saved_steps(self):
        with self.assertRaises(ConnectionError):
            self.run_fake(FakeLLM(fail_at=2))
        retry = FakeLLM()
        summary = self.run_fake(retry, use_cache=False)
        self.assertEqual(retry.calls, [GEN, VER])
        self.assertFalse(summary.config["use_llm_cache"])

    def test_sa010_corrupted_step_is_called_again(self):
        with self.assertRaises(ConnectionError):
            self.run_fake(FakeLLM(fail_at=2))
        for path in (self.root / "M" / "steps").iterdir():
            path.write_text("{손상", encoding="utf-8")
        retry = FakeLLM()
        self.run_fake(retry)
        self.assertEqual(retry.calls, [GEN, VER])

    def test_sa011_call_limit_is_enforced(self):
        llm = FakeLLM(verifications=[[]] * 20)
        runner = _StepRunner(llm, self.store, "M", use_cache=False)
        for i in range(cfg.max_llm_calls()):
            runner(f"s{i}", "u", LLMVerification, step="verify")
        with self.assertRaises(LLMCallLimitError):
            runner("over", "u", LLMVerification, step="verify")
        self.assertEqual(len(llm.calls), cfg.max_llm_calls())

    def test_sa011_concurrent_runs_do_not_share_state(self):
        def run(i):
            meeting_id = f"C{i}"
            llm = FakeLLM(verifications=[[issue(f"{meeting_id}_a1_d1")], []],
                          revisions=[revision(discussion_updates=[update(f"{meeting_id}_a1_d1", f"수정 {i}")])])
            return self.run_fake(llm, meeting_id=meeting_id), llm
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(run, range(8)))
        for i, (summary, llm) in enumerate(results):
            self.assertEqual(summary.find_item(f"C{i}_a1_d1").text, f"수정 {i}")
            self.assertEqual(llm.calls, [GEN, VER, REV, VER])
            self.assertEqual(self.saved_json(f"C{i}")["meeting_id"], f"C{i}")


class AdapterTests(Base):
    def test_structured_client_sends_schema_and_effort(self):
        seen = {}

        def parse(**kw):
            seen.update(kw)
            return SimpleNamespace(output_parsed=LLMVerification(issues=[]))
        client = OpenAIStructuredClient(client=SimpleNamespace(responses=SimpleNamespace(parse=parse)))
        self.assertEqual(client.parse("시스템", "입력", LLMVerification, "high").issues, [])
        self.assertEqual((seen["model"], seen["instructions"], seen["input"], seen["text_format"], seen["reasoning"]),
                         ("fake-model", "시스템", "입력", LLMVerification, {"effort": "high"}))

    def test_structured_client_rejects_unparsed_response(self):
        parse = lambda **kw: SimpleNamespace(output_parsed=None)
        client = OpenAIStructuredClient(client=SimpleNamespace(responses=SimpleNamespace(parse=parse)))
        with self.assertRaises(RuntimeError):
            client.parse("s", "u", LLMVerification, None)

    def test_unknown_improvement_type_stops_before_llm(self):
        llm = FakeLLM()
        with patch.object(cfg, "IMPROVEMENT_ISSUE_TYPES", {"없는 유형"}), self.assertRaises(Exception) as caught:
            self.run_fake(llm)
        self.assertIn("없는 유형", str(caught.exception))
        self.assertEqual(llm.calls, [])

    def test_missing_model_stops_before_llm(self):
        llm = FakeLLM()
        with patch.dict(os.environ, {"OPENAI_MODEL": ""}), self.assertRaises(RuntimeError):
            self.run_fake(llm)
        self.assertEqual(llm.calls, [])


class CliTests(Base):
    def run_cli(self, llm, *args):
        out = StringIO()
        with patch.object(cli, "build_llm", return_value=llm), patch.object(cli, "build_store", return_value=self.store), \
                patch("sys.stdout", out):
            return cli.main(list(args)), out.getvalue()

    def test_saves_summary_and_uses_file_name_as_meeting_id(self):
        source = self.root / "회의A.txt"
        source.write_text(TRANSCRIPT, encoding="utf-8")
        code, out = self.run_cli(FakeLLM(), str(source))
        self.assertEqual(code, 0)
        self.assertIn("[완료] 회의A: PASSED", out)
        self.assertEqual(self.saved_json("회의A")["source_document"]["input_path"], "회의A.txt")

    def test_failure_returns_1_and_continues(self):
        bad, good = self.root / "bad.txt", self.root / "good.txt"
        bad.write_text("형식 오류\n", encoding="utf-8")
        good.write_text(TRANSCRIPT, encoding="utf-8")
        code, out = self.run_cli(FakeLLM(), str(bad), str(good))
        self.assertEqual(code, 1)
        self.assertIn("[완료] good: PASSED", out)
        self.assertIn("[실패]", out)


if __name__ == "__main__":
    unittest.main()
