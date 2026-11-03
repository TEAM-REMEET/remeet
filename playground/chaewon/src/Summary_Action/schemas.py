"""Summary_Action 데이터 구조

구성
    0. 태그 · 섹션 · ID 규칙
    1. 입력: 회의 전사문 txt (메타데이터 + '발화자: 발화문')
    2. LLM 출력 스키마 (ID 없음) — prompt/Extract_summary_action_item.py
    3. 저장 스키마 (코드가 ID 부여) — 값을 최소 단위로 분리
       회의 정보 / 전사문 / 안건 → 섹션 → 항목 / 소주제 / 안건 목차 / 회의 결과

원칙
    - LLM 출력 스키마(LLM*)에는 ID를 두지 않음 → 파싱 후 코드가 agenda_id · subtopic_id · item_id 부여
    - LLM 출력 스키마는 structured output(strict) 호환: 기본값 없음 · 모든 필드 필수 · 추가 필드 금지
    - 저장 스키마의 항목 하나 = 회의록 글머리표 한 줄 = 검증 · 수정 단위 (item_id로 식별)
      "[논의 종류][결정사항] 요약문 [근거 발화]"는 discussion_tag · decision_tag · text · evidence_utterance_indices로 분리
    - 안건 목차 · 회의 결과는 항목을 복사하지 않고 item_id로 참조 (수정 시 항목 한 곳만 고치면 됨)
"""

from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict


# ══════════════════════════════════════════════════════════════════════
# 0. 태그 · 섹션 · ID 규칙
# ══════════════════════════════════════════════════════════════════════
DiscussionTag = Literal["제안", "반대·우려", "질문", "정보", "액션"]     # 논의 종류 ('동의'는 사용하지 않음)
DecisionTag = Literal["결정", "보류", "공유"]                           # 결정사항 (결정과 연관 없으면 None)
UnresolvedTag = Literal["확인 필요", "승인 필요", "세부 미정", "미논의"]  # 미해결 이슈 (선택)
FollowUpTag = Literal["결과 공유", "재논의"]                            # 다음 회의 후보 안건

ACTION_TAG = "액션"
ACTION_DECISION = "결정"            # 액션의 결정사항 태그는 '결정' 고정
COUNTED_DECISION = "결정"           # 결정 수: decision_tag == '결정'이고 액션이 아닌 항목만 셈

SectionName = Literal["discussion_background", "major_discussions", "unresolved_issues", "next_meeting_topics"]

# 섹션 → item_id 접두어 (ex. TS3003c_a1_d3)
SECTION_PREFIX: dict[str, str] = {
    "discussion_background": "bg",
    "major_discussions": "d",
    "unresolved_issues": "un",
    "next_meeting_topics": "next",
}
SECTIONS: tuple[str, ...] = tuple(SECTION_PREFIX)
SECTION_LABEL: dict[str, str] = {
    "discussion_background": "논의 배경",
    "major_discussions": "주요 논의",
    "unresolved_issues": "미해결 이슈",
    "next_meeting_topics": "다음 회의 후보 안건",
}
DISCUSSION_TAGS = get_args(DiscussionTag)
DECISION_TAGS = get_args(DecisionTag)

ItemStatus = Literal["ACTIVE", "REVIEW_REQUIRED", "DELETED"]
# ACTIVE: 정상 / REVIEW_REQUIRED: 근거 검사 · 검증에서 문제가 남음 / DELETED: 삭제됨 (이력 보존용, 렌더링 제외)


def make_agenda_id(meeting_id: str, number: int) -> str:
    return f"{meeting_id}_a{number}"


def make_subtopic_id(agenda_id: str, number: int) -> str:
    return f"{agenda_id}_st{number}"


def make_item_id(agenda_id: str, section: str, number: int) -> str:
    return f"{agenda_id}_{SECTION_PREFIX[section]}{number}"


# ══════════════════════════════════════════════════════════════════════
# 1. 입력: 회의 전사문 txt (dataset/Meeting_Input/{회의 ID}.txt) — postprocess.load_input에서 읽음
#    회의 대주제: …        ┐
#    참여자: … (N명)       │ 메타데이터 (config.METADATA_KEYS)
#    발화자: A=역할, …     │
#    일시: … (회의 길이 …) ┘
#    (빈 줄)
#    A: 발화문             발화 한 줄 = 발화 하나 (빈 줄 무시, 번호는 config.UTTERANCE_START부터)
# ══════════════════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════════════════
# 2. LLM 출력 스키마 (ID 없음)
# ══════════════════════════════════════════════════════════════════════
class _LLMModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LLMBackgroundItem(_LLMModel):
    text: str
    evidence_utterance_indices: list[int]


class LLMDiscussionItem(_LLMModel):
    subtopic: str                       # 10자 이내 명사구
    discussion_tag: DiscussionTag
    decision_tag: DecisionTag | None    # 결정과 연관 없으면 null / 액션은 '결정'
    text: str
    assignee: str | None                # 액션만 (확인되지 않으면 null)
    due: str | None                     # 액션만 (원문 기한 표현, 없으면 null)
    evidence_utterance_indices: list[int]


class LLMUnresolvedItem(_LLMModel):
    tag: UnresolvedTag | None
    text: str
    evidence_utterance_indices: list[int]


class LLMNextTopicItem(_LLMModel):
    tag: FollowUpTag
    text: str
    evidence_utterance_indices: list[int]


class LLMAgenda(_LLMModel):
    agenda_title: str
    discussion_background: list[LLMBackgroundItem]
    major_discussions: list[LLMDiscussionItem]
    unresolved_issues: list[LLMUnresolvedItem]
    next_meeting_topics: list[LLMNextTopicItem]


class LLMMeetingSummary(_LLMModel):
    agendas: list[LLMAgenda]            # 처음 논의된 순서


# ══════════════════════════════════════════════════════════════════════
# 3. 저장 스키마 (코드가 ID 부여, 최소 단위 분리)
# ══════════════════════════════════════════════════════════════════════
class SourceDocument(BaseModel):
    input_path: str                     # 입력 전사문 txt 경로
    utterance_count: int
    first_utterance_index: int | None
    last_utterance_index: int | None


class MeetingInfo(BaseModel):
    title: str | None                   # metadata '회의 대주제'
    date: str | None                    # metadata '일시'에서 날짜 (ex. 2004-10-11)
    duration: str | None                # metadata '일시'의 괄호 안 (ex. 회의 길이 약 43분)
    datetime_raw: str | None            # metadata '일시' 원문
    participants: list[str]             # metadata '참여자'를 쉼표로 분리 (괄호 안 인원 표기 제외)
    participant_count: int | None       # metadata '참여자'의 (N명), 없으면 participants 수
    participants_raw: str | None        # metadata '참여자' 원문
    speakers: dict[str, str] = {}       # metadata '발화자' → {화자 표기: 역할} (ex. {"A": "프로젝트 매니저"})
    speakers_raw: str | None = None     # metadata '발화자' 원문
    extra: dict[str, str] = {}          # 그 외 metadata


class TranscriptUtterance(BaseModel):
    utterance_index: int                # config.UTTERANCE_START부터 (메타데이터 · 빈 줄 제외)
    speaker: str | None                 # 전사문의 화자 표기 (ex. A)
    speaker_role: str | None = None     # '발화자' 메타데이터로 찾은 역할 (ex. 프로젝트 매니저)
    text: str
    line_number: int | None = None      # 원본 txt의 줄 번호 (1부터, 확인용)


class ItemRevision(BaseModel):
    """항목 수정 이력 (검증 · 수정 단계 추가 시 사용)"""
    stage: str                          # postprocess / content_revise / readability_revise ...
    field: str                          # 바뀐 필드 이름
    before: str | list[int] | None
    after: str | list[int] | None
    reason: str


class BaseItem(BaseModel):
    item_id: str                        # 검증 · 수정 대상 식별자
    agenda_id: str
    section: SectionName
    order: int                          # 섹션 안 순서 (1부터)
    text: str                           # 요약문
    evidence_utterance_indices: list[int]   # 근거 발화 번호 (검사 후 유효한 번호만, 오름차순)
    status: ItemStatus = "ACTIVE"
    review_reasons: list[str] = []      # REVIEW_REQUIRED 사유
    history: list[ItemRevision] = []


class BackgroundItem(BaseItem):
    section: Literal["discussion_background"] = "discussion_background"


class DiscussionItem(BaseItem):
    section: Literal["major_discussions"] = "major_discussions"
    subtopic_id: str
    subtopic: str
    discussion_tag: DiscussionTag
    decision_tag: DecisionTag | None
    assignee: str | None                # 액션만
    due: str | None                     # 액션만

    @property
    def is_action(self) -> bool:
        return self.discussion_tag == ACTION_TAG

    @property
    def is_counted_decision(self) -> bool:
        return self.decision_tag == COUNTED_DECISION and not self.is_action


class UnresolvedItem(BaseItem):
    section: Literal["unresolved_issues"] = "unresolved_issues"
    tag: UnresolvedTag | None


class NextTopicItem(BaseItem):
    section: Literal["next_meeting_topics"] = "next_meeting_topics"
    tag: FollowUpTag


class Subtopic(BaseModel):
    subtopic_id: str
    agenda_id: str
    order: int
    title: str
    item_ids: list[str]                 # 이 소주제의 주요 논의 항목 (논의 순서)


class Agenda(BaseModel):
    agenda_id: str
    agenda_number: int                  # 안건 목차 번호 (1부터)
    agenda_title: str
    evidence_range: list[int]           # [최초 근거 발화, 마지막 근거 발화] (항목 근거로 계산)
    subtopics: list[Subtopic]
    discussion_background: list[BackgroundItem]
    major_discussions: list[DiscussionItem]
    unresolved_issues: list[UnresolvedItem]
    next_meeting_topics: list[NextTopicItem]

    def section(self, name: str) -> list[BaseItem]:
        return getattr(self, name)

    def iter_items(self, include_deleted: bool = False):
        for name in SECTIONS:
            for item in self.section(name):
                if include_deleted or item.status != "DELETED":
                    yield item


class AgendaIndexEntry(BaseModel):
    agenda_id: str
    agenda_number: int
    agenda_title: str
    decision_count: int                 # decision_tag == '결정' (액션 제외)
    unresolved_count: int
    action_count: int


class MeetingResults(BaseModel):
    """회의 결과 — 항목을 복사하지 않고 item_id로 참조"""
    decision_item_ids: list[str]        # decision_tag == '결정' (액션 제외)
    action_item_ids: list[str]          # discussion_tag == '액션'
    unresolved_item_ids: list[str]
    next_topic_item_ids: list[str]


class PostprocessLog(BaseModel):
    item_id: str | None                 # None이면 안건 · 전체 단위
    message: str


class MeetingSummary(BaseModel):
    """최종 저장 JSON — {회의 ID}_summary.json"""
    meeting_id: str
    created_at: str
    config: dict                        # config.snapshot() (비밀 값 · 비용 정보 없음)
    source_document: SourceDocument
    meeting_info: MeetingInfo
    transcript: list[TranscriptUtterance]
    agenda_index: list[AgendaIndexEntry]
    meeting_results: MeetingResults
    agendas: list[Agenda]
    postprocess_log: list[PostprocessLog]

    def find_item(self, item_id: str) -> BaseItem | None:
        for agenda in self.agendas:
            for item in agenda.iter_items(include_deleted=True):
                if item.item_id == item_id:
                    return item
        return None

    def agenda_of(self, item_id: str) -> Agenda | None:
        return next((a for a in self.agendas if item_id.startswith(a.agenda_id + "_")), None)
