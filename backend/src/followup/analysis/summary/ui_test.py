"""화면 확인용 시험 모드 — LLM을 호출하지 않고 전체 단계를 진행 (사용법: docs/ui-test-mode.md)

.env에 UI_TEST_MODE=true가 있을 때만, 회의 내용이 settings.UI_TEST_TRANSCRIPT('test')이면 동작함.
    요약 · 액션 추출    tests/fixtures/ui_test_summary.json을 기다렸다가 사용 (제목 · 날짜 · 참석자 · 프로젝트는 입력값으로 바꿈)
    근거 연결 · 검증    UiTestLLM이 대신 응답 (근거 연결은 간단한 낱말 검색, 품질 확인용이 아님)
시험 결과는 config.ui_test = true로 표시되어, 확정 단계에서도 LLM을 호출하지 않음.
"""

import json
import re
import time
from datetime import datetime
from pathlib import Path

from followup import settings as cfg
from followup.analysis.summary.schemas import (LLMEvidenceLink, LLMEvidenceLinks, LLMEvidenceVerification, MeetingSummary)
from followup.analysis.summary.transcript_builder import MeetingSubmission, new_meeting_id

FIXTURE = cfg.BACKEND_DIR / "tests" / "fixtures" / "ui_test_summary.json"
_FIXTURE_ID = "UITEST"
_MAX_EVIDENCE = 3


def is_test_transcript(transcript: str) -> bool:
    return transcript.strip().lower() == cfg.UI_TEST_TRANSCRIPT


def is_test_summary(summary: MeetingSummary) -> bool:
    return bool(summary.config.get("ui_test"))


def build_summary(submission: MeetingSubmission, path: Path = FIXTURE) -> MeetingSummary:
    meeting_id = new_meeting_id()
    data = json.loads(path.read_text(encoding="utf-8").replace(_FIXTURE_ID, meeting_id))
    info = data["meeting_info"]
    people = {p["user_id"]: p for p in info["participants"]}
    for a in submission.attendees:
        people[a.user_id] = {"user_id": a.user_id, "name": a.name, "job": a.job, "display_name": a.name}
    info.update(title=submission.title, date=submission.date.isoformat(), datetime_raw=submission.date.isoformat(),
                participants=list(people.values()), participant_count=len(people))
    data.update(project_id=submission.project_id or f"P_UITEST_{meeting_id[2:]}", directory_id=submission.directory_id,
                host_user_id=submission.host_user_id, created_at=datetime.now().isoformat(timespec="seconds"), hitl=None)
    data["config"] = {**data["config"], "ui_test": True}
    return MeetingSummary.model_validate(data)


def run_summary(submission: MeetingSubmission, store) -> MeetingSummary:
    time.sleep(cfg.UI_TEST_DELAY_SECONDS)   # 진행 중 화면을 볼 수 있도록 잠깐 기다림
    summary = build_summary(submission)
    store.save_summary(summary)
    return summary


def _words(text: str) -> list[str]:
    return re.findall(r"[가-힣A-Za-z0-9]{2,}", text)


class UiTestLLM:
    """근거 연결 · 검증을 대신 응답: 기존 근거 유지 → 없으면 원문에서 낱말이 많이 겹치는 발화 / 검증은 항상 통과"""

    def __init__(self, summary: MeetingSummary):
        self.transcript = [(u.utterance_index, u.text) for u in summary.transcript]

    def _search(self, text: str) -> list[int]:
        words = _words(text)
        scored = [(sum(1 for w in words if w in said or (len(w) > 2 and w[:-1] in said)), index) for index, said in self.transcript]
        best = sorted((pair for pair in scored if pair[0]), key=lambda pair: (-pair[0], pair[1]))[:_MAX_EVIDENCE]
        return sorted(index for _, index in best)

    def parse(self, system, user, schema, effort):
        if schema is LLMEvidenceVerification:
            return LLMEvidenceVerification(issues=[])
        if schema is LLMEvidenceLinks:
            links = []
            for line in user.splitlines():
                parts = line[2:].split(" | ") if line.startswith("- ") else []
                if len(parts) >= 6 and parts[1].startswith("안건:"):
                    before = [int(x) for x in re.findall(r"\d+", parts[-1])]
                    links.append(LLMEvidenceLink(item_id=parts[0], evidence_utterance_indices=before or self._search(" | ".join(parts[4:-1])),
                                                 reason="화면 확인용 시험 모드"))
            return LLMEvidenceLinks(links=links)
        raise AssertionError(f"시험 모드에서 예상하지 못한 LLM 호출: {schema.__name__}")
