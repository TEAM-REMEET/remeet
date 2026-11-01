"""요약문 생성 파이프라인 데이터 구조

구성
    0. 태그 · 섹션 · ID 규칙
    1. 입력: 주제 경계 탐지 결과 (03_text_boundary_final_code의 segments)
    2. 안건 Registry (시스템 내부 저장 / LLM에는 관련 후보만 전달)
    3. LLM 출력 스키마 (ID 없음) — [1] 1차 요약, [3][5] 내용 검증, [4] 재수정, [6] 가독성 검증, [7] 가독성 수정
    4. 내부 저장 스키마 (코드가 ID 부여) — 요약 항목, 안건, Segment별 요약
    5. 구조 검증 결과 — [8]
    6. 최종 회의록 — [9] (최소 단위 JSON → 렌더링 시 문자열로 조합)
    7. LangGraph State

원칙
    - LLM 출력 스키마(LLM*)에는 item_id를 두지 않음 → 파싱 후 코드가 ID 부여 (make_item_id)
    - LLM 출력 스키마는 structured output(strict) 호환을 위해 기본값 없이 모든 필드 필수, 추가 필드 금지
    - 근거 발화는 모두 evidence_utterance_indices(발화 번호 목록)로 통일,
      안건 Registry의 [최초 근거, 마지막 근거]만 evidence_range로 구분
"""

from typing import Literal, TypedDict, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ══════════════════════════════════════════════════════════════════════
# 0. 태그 · 섹션 · ID 규칙
# ══════════════════════════════════════════════════════════════════════
DiscussionTag = Literal["제안", "반대·우려", "질문", "정보"]           # 주요 논의 ('동의'는 사용하지 않음)
DecisionTag = Literal["결정", "조건부 결정", "보류", "공유"]           # 결정 상태
UnresolvedTag = Literal["확인 필요", "승인 필요", "세부 미정", "미논의"]  # 미해결 이슈 (선택)
FollowUpTag = Literal["결과 공유", "재논의"]                           # 다음 회의 후보 안건
ItemTag = Literal[DiscussionTag, DecisionTag, UnresolvedTag, FollowUpTag]  # 전체 태그 ([4] 재수정 출력용, 섹션별 허용 여부는 코드가 확인)

SectionName = Literal["discussion_background", "major_discussions", "decisions",
                      "unresolved_issues", "next_meeting_topics"]

# 섹션 → item_id 접두어 (ex. TS3003c_a1_d1, TS3003c_a1_dec1)
SECTION_PREFIX: dict[str, str] = {
    "discussion_background": "bg",
    "major_discussions": "d",
    "decisions": "dec",
    "unresolved_issues": "un",
    "next_meeting_topics": "next",
}
SECTIONS: tuple[str, ...] = tuple(SECTION_PREFIX)

# 섹션별 허용 태그 (None: 태그 없음 허용) — [4] 재수정 · [8] 구조 검증에서 확인
SECTION_TAGS: dict[str, tuple] = {
    "discussion_background": (None,),
    "major_discussions": get_args(DiscussionTag),
    "decisions": get_args(DecisionTag),
    "unresolved_issues": (*get_args(UnresolvedTag), None),
    "next_meeting_topics": get_args(FollowUpTag),
}

ItemStatus = Literal["ACTIVE", "REVIEW_REQUIRED", "DELETED"]
# ACTIVE: 정상 / REVIEW_REQUIRED: 재검증 상한 초과 후에도 미해결 / DELETED: 삭제됨 (이력 보존용, 렌더링 제외)


def make_agenda_id(meeting_id: str, number: int) -> str:
    return f"{meeting_id}_a{number}"


def make_item_id(agenda_id: str, section: str, number: int) -> str:
    return f"{agenda_id}_{SECTION_PREFIX[section]}{number}"


def _sorted_unique(indices: list[int]) -> list[int]:
    return sorted(set(indices))


# ══════════════════════════════════════════════════════════════════════
# 1. 입력: 주제 경계 탐지 결과
# ══════════════════════════════════════════════════════════════════════
class Utterance(BaseModel):
    model_config = ConfigDict(extra="ignore")

    utterance_index: int
    text: str


class Segment(BaseModel):
    """추정 경계 Segment — utterances(Core)는 항목 생성 기준, overlap_before/after는 맥락 참고용"""
    model_config = ConfigDict(extra="ignore")

    segment_id: int
    utterance_start: int                    # Core 범위 (양 끝 포함)
    utterance_end: int
    utterances: list[Utterance]             # Core
    overlap_start: int                      # 오버랩 포함 범위 (양 끝 포함)
    overlap_end: int
    overlap_before: list[Utterance] = []    # 이전 Segment의 끝 발화
    overlap_after: list[Utterance] = []     # 다음 Segment의 처음 발화

    @property
    def core_indices(self) -> set[int]:
        return {u.utterance_index for u in self.utterances}

    @property
    def all_utterances(self) -> list[Utterance]:
        return [*self.overlap_before, *self.utterances, *self.overlap_after]


class BoundaryResult(BaseModel):
    """{meeting_id}_boundary_candidates.json 중 요약에 필요한 부분만 읽음"""
    model_config = ConfigDict(extra="ignore")

    meeting_id: str
    metadata: dict[str, str] = {}           # 회의 대주제 / 참여자 / 일시 → 렌더링의 회의 정보
    segments: list[Segment]


# ══════════════════════════════════════════════════════════════════════
# 2. 안건 Registry
# ══════════════════════════════════════════════════════════════════════
class AgendaRef(BaseModel):
    """안건 List 한 줄 — 관련 안건 후보 선별 및 LLM 입력용 (최소 정보)"""
    agenda_id: str
    agenda_title: str
    evidence_range: list[int] = Field(min_length=2, max_length=2)
    # [최초 근거 발화, 마지막 근거 발화] — 범위 안 발화가 모두 이 안건이라는 뜻은 아님, 재등장 시 마지막 값 갱신


class AgendaRegistry(BaseModel):
    meeting_id: str
    agendas: list[AgendaRef] = []
    next_number: int = 1                    # 다음 신규 안건 번호 (삭제·병합돼도 번호 재사용 안 함)

    def get(self, agenda_id: str) -> AgendaRef | None:
        return next((a for a in self.agendas if a.agenda_id == agenda_id), None)

    def new_agenda_id(self) -> str:
        agenda_id = make_agenda_id(self.meeting_id, self.next_number)
        self.next_number += 1
        return agenda_id


# ══════════════════════════════════════════════════════════════════════
# 3. LLM 출력 스키마 (ID 없음, strict 호환: 기본값 없음 · 모든 필드 필수 · 추가 필드 금지)
# ══════════════════════════════════════════════════════════════════════
class _LLMModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ── [1] Segment별 1차 요약 ──────────────────────────────────────
class LLMBackground(_LLMModel):
    text: str
    evidence_utterance_indices: list[int]


class LLMDiscussion(_LLMModel):
    subtopic: str = Field(description="소주제 (렌더링 시 주요 논의 하위 제목, ex. '보존 기간')")
    tag: DiscussionTag
    text: str
    evidence_utterance_indices: list[int]


class LLMDecision(_LLMModel):
    tag: DecisionTag
    text: str
    evidence_utterance_indices: list[int]


class LLMUnresolved(_LLMModel):
    tag: UnresolvedTag | None
    text: str
    evidence_utterance_indices: list[int]


class LLMNextTopic(_LLMModel):
    tag: FollowUpTag
    text: str
    evidence_utterance_indices: list[int]


class LLMAgenda(_LLMModel):
    agenda_id: str | None = Field(description="관련 안건 후보 중 같은 안건이면 그 agenda_id, 신규 안건이면 null")
    agenda_title: str = Field(description="20자 이내 명사구, 큰 범주의 안건 단위")
    discussion_background: list[LLMBackground]
    major_discussions: list[LLMDiscussion]
    decisions: list[LLMDecision]
    unresolved_issues: list[LLMUnresolved]
    next_meeting_topics: list[LLMNextTopic]


class LLMSegmentSummary(_LLMModel):
    agendas: list[LLMAgenda]


# ── [3][5] 내용 검증 ────────────────────────────────────────────
ContentCriterion = Literal["가짜 근거", "원문에 없는 내용", "과장 및 왜곡", "결정 상태 오류", "근거 연결 오류",
                           "중요 항목 누락", "중복 요약", "깊이 부족", "요약 범위"]
ContentAction = Literal["수정", "삭제", "추가", "병합"]


class LLMContentIssue(_LLMModel):
    target_item_id: str = Field(description="수정 · 삭제 · 병합: 입력 요약문의 item_id 그대로 / 추가: 항목을 추가할 안건의 agenda_id")
    section: SectionName = Field(description="대상 항목의 섹션 (추가면 항목을 추가할 섹션)")
    related_item_ids: list[str] = Field(description="병합: target 항목에 합칠 항목 ID / 그 외: 판단에 함께 참고한 항목 ID (없으면 빈 배열)")
    criterion: ContentCriterion
    action: ContentAction
    current_text: str = Field(description="대상 항목의 현재 문장 (추가면 빈 문자열)")
    reason: str
    evidence_utterance_indices: list[int]
    revision_instruction: str
    uncertain: bool = Field(description="근거가 불충분해 수정 방향을 단정할 수 없으면 true")


class LLMContentValidation(_LLMModel):
    validation_result: Literal["PASS", "FAIL"]
    issues: list[LLMContentIssue]


# ── [4] 특정 문장 재수정 ────────────────────────────────────────
class LLMRevisedItem(_LLMModel):
    target_item_id: str = Field(description="수정 요청의 요청 ID 그대로 (수정 · 병합: 대상 항목 ID / 추가: 신규 요청 ID)")
    tag: ItemTag | None = Field(description="섹션에 맞는 태그 (논의 배경 · 해당 태그 없는 미해결 이슈는 null)")
    subtopic: str = Field(description="주요 논의만 소주제, 그 외 섹션은 빈 문자열")
    revised_text: str
    evidence_utterance_indices: list[int]


class LLMRevisionResult(_LLMModel):
    revision_result: list[LLMRevisedItem]


# ── [6] 가독성 검증 (수정할 곳 · 이유 · 수정 방향만 반환, 문장은 [7]에서 수정) ──
ReadabilityCriterion = Literal["문장 연결", "반복 표현", "길이 · 복잡도", "의미 명확성", "문체 일관성",
                               "주체 · 대상 혼동", "맥락 훼손"]


class LLMReadabilityIssue(_LLMModel):
    target_item_id: str = Field(description="입력 요약 항목의 item_id 그대로")
    criterion: ReadabilityCriterion
    current_text: str = Field(description="대상 항목의 현재 문장")
    reason: str
    revision_instruction: str


class LLMReadabilityValidation(_LLMModel):
    validation_result: Literal["PASS", "FAIL"]
    issues: list[LLMReadabilityIssue]


# ── [7] 가독성 피드백 기반 부분 수정 ────────────────────────────
class LLMReadabilityRevisedItem(_LLMModel):
    target_item_id: str = Field(description="수정 요청의 항목 ID 그대로")
    revised_text: str = Field(description="가독성을 개선한 문장 전체 (의미 · 사실관계는 그대로)")


class LLMReadabilityRevision(_LLMModel):
    revision_result: list[LLMReadabilityRevisedItem]


# ══════════════════════════════════════════════════════════════════════
# 4. 내부 저장 스키마 (코드가 item_id 부여, 이후 단계에서 ID 유지)
# ══════════════════════════════════════════════════════════════════════
class ItemRevision(BaseModel):
    """항목 수정 이력 — 수정 전 값을 남김"""
    stage: Literal["merge", "content_revise", "readability_revise", "structure_fix"]
    loop: int = 0
    before_text: str
    before_evidence: list[int]
    before_tag: str | None = None
    before_subtopic: str | None = None
    reason: str = ""


class SummaryItem(BaseModel):
    item_id: str
    text: str
    evidence_utterance_indices: list[int]
    source_segment_ids: list[int] = []      # 이 항목을 만든 / 근거를 보탠 Segment
    status: ItemStatus = "ACTIVE"
    history: list[ItemRevision] = []

    @field_validator("evidence_utterance_indices")
    @classmethod
    def _normalize_evidence(cls, v: list[int]) -> list[int]:
        if any(i < 0 for i in v):
            raise ValueError("발화 번호는 0 이상이어야 함")
        return _sorted_unique(v)

    def _record(self, stage: str, loop: int, reason: str) -> None:
        # 수정 전 값을 history에 남김 (태그 · 소주제가 없는 섹션은 None)
        self.history.append(ItemRevision(stage=stage, loop=loop, before_text=self.text,
                                         before_evidence=self.evidence_utterance_indices,
                                         before_tag=getattr(self, "tag", None),
                                         before_subtopic=getattr(self, "subtopic", None), reason=reason))

    def revise(self, text: str, evidence: list[int], stage: str, loop: int = 0, reason: str = "") -> None:
        self._record(stage, loop, reason)
        self.text = text
        self.evidence_utterance_indices = _sorted_unique(evidence)

    def delete(self, stage: str, loop: int = 0, reason: str = "") -> None:
        self._record(stage, loop, reason)
        self.status = "DELETED"


class BackgroundItem(SummaryItem):
    pass


class DiscussionItem(SummaryItem):
    subtopic: str = ""
    tag: DiscussionTag


class DecisionItem(SummaryItem):
    tag: DecisionTag


class UnresolvedItem(SummaryItem):
    tag: UnresolvedTag | None = None


class NextTopicItem(SummaryItem):
    tag: FollowUpTag


SECTION_ITEM_MODEL: dict[str, type[SummaryItem]] = {
    "discussion_background": BackgroundItem,
    "major_discussions": DiscussionItem,
    "decisions": DecisionItem,
    "unresolved_issues": UnresolvedItem,
    "next_meeting_topics": NextTopicItem,
}


class Agenda(BaseModel):
    agenda_id: str
    agenda_title: str
    source_segment_ids: list[int] = []
    discussion_background: list[BackgroundItem] = []
    major_discussions: list[DiscussionItem] = []
    decisions: list[DecisionItem] = []
    unresolved_issues: list[UnresolvedItem] = []
    next_meeting_topics: list[NextTopicItem] = []
    item_counters: dict[str, int] = {}      # 섹션별 마지막 부여 번호 (삭제돼도 번호 재사용 안 함)

    def section(self, name: str) -> list[SummaryItem]:
        return getattr(self, name)

    def iter_items(self, include_deleted: bool = False):
        # (섹션 이름, 항목) 순회
        for name in SECTIONS:
            for item in self.section(name):
                if include_deleted or item.status != "DELETED":
                    yield name, item

    def find_item(self, item_id: str) -> SummaryItem | None:
        return next((item for _, item in self.iter_items(include_deleted=True) if item.item_id == item_id), None)

    def add_item(self, section: str, **fields) -> SummaryItem:
        # 새 item_id를 부여해 섹션 끝에 추가
        number = self.item_counters.get(section, 0) + 1
        self.item_counters[section] = number
        item = SECTION_ITEM_MODEL[section](item_id=make_item_id(self.agenda_id, section, number), **fields)
        self.section(section).append(item)
        return item

    @property
    def evidence_range(self) -> list[int]:
        indices = [i for _, item in self.iter_items() for i in item.evidence_utterance_indices]
        return [min(indices), max(indices)] if indices else []


class SegmentSummary(BaseModel):
    """[1] 결과: Segment 하나의 1차 요약 (ID 부여 후)"""
    segment_id: int
    agendas: list[Agenda]


# ── 검증 이슈 (LLM 결과 + 처리 상태) ────────────────────────────
class ContentIssue(LLMContentIssue):
    model_config = ConfigDict(extra="ignore")

    issue_id: str = ""                      # {agenda_id}_L{loop}_i{n} (코드 부여)
    agenda_id: str = ""
    loop: int = 0                           # 몇 번째 검증에서 나온 이슈인지
    resolved: bool = False                  # [4] 재수정에서 반영됨
    revised_item_id: str = ""               # 반영된 항목 ID (수정 · 병합 · 삭제: 대상 항목 / 추가: 새로 만든 항목)


class ReadabilityIssue(LLMReadabilityIssue):
    model_config = ConfigDict(extra="ignore")

    issue_id: str = ""                      # {agenda_id}_R{loop}_i{n} (코드 부여)
    agenda_id: str = ""
    loop: int = 0                           # 몇 번째 가독성 검증에서 나온 이슈인지
    applied: bool = False                   # [7] 가독성 수정에서 반영됨
    revised_text: str = ""                  # [7]에서 반영한 문장


# ══════════════════════════════════════════════════════════════════════
# 5. 구조 검증 결과 [8]
# ══════════════════════════════════════════════════════════════════════
StructureErrorType = Literal["JSON_PARSE_ERROR", "SCHEMA_VALIDATION_ERROR", "INVALID_TAG", "DUPLICATE_ID",
                             "INVALID_ID_REFERENCE", "EVIDENCE_OUT_OF_RANGE", "MISSING_SECTION"]


class StructureErrorDetail(BaseModel):
    error_type: StructureErrorType
    target_id: str = ""                     # 오류가 난 agenda_id / item_id
    field: str = ""
    expected: str = ""
    actual: str = ""
    auto_fixed: bool = False                # 코드로 보정했는지


class StructureValidationReport(BaseModel):
    validation_status: Literal["PASSED", "FAILED"]
    retry_count: int = 0
    error_details: list[StructureErrorDetail] = []
    quarantined_ids: list[str] = []         # 복구 실패로 격리된 agenda_id / item_id (다음 단계에 전달 안 함)
    requires_manual_review: bool = False


# ══════════════════════════════════════════════════════════════════════
# 6. 최종 회의록 [9] — 화면 · 문서 구성에 필요한 최소 단위로 나눈 JSON
#    회의 정보 / 안건 목차 / 회의 결과 / 안건별 상세 (+ 원본 전사문)
# ══════════════════════════════════════════════════════════════════════
class SourceDocument(BaseModel):
    """원본 문서: 회의 전사문 전문"""
    utterance_count: int
    utterances: list[Utterance]             # 발화 단위 (근거 발화 연결용)
    full_text: str                          # 전문 (발화를 줄바꿈으로 연결)


class MeetingInfo(BaseModel):
    title: str                              # 제목 (metadata)
    date: str                               # 일시 (metadata)
    participants: str                       # 참석자 (metadata)
    source_document: SourceDocument         # 원본 문서


class AgendaIndexEntry(BaseModel):
    """안건 목차 한 줄: {number}. {agenda_title} [결정 {decision_count} · 미해결 {unresolved_count}]"""
    number: int
    agenda_id: str
    agenda_title: str
    decision_count: int
    unresolved_count: int


class FinalItem(BaseModel):
    """요약 항목 한 줄: · [{tag}] {text} (근거 발화는 내부 참조값, 화면에는 표시 안 함)"""
    item_id: str
    tag: str | None = None
    text: str
    evidence_utterance_indices: list[int]
    review_required: bool = False           # 내용 검증에서 해결되지 않은 항목 (REVIEW_REQUIRED)


class MeetingResultEntry(FinalItem):
    """회의 결과 한 줄: · [{tag}] {text} [안건{agenda_number}]"""
    section: Literal["decisions", "unresolved_issues", "next_meeting_topics"]
    agenda_id: str
    agenda_number: int


class SubtopicGroup(BaseModel):
    """주요 논의의 소주제 묶음: {number}. {subtopic} (ex. 2-1. 보존 기간)"""
    number: str
    subtopic: str
    items: list[FinalItem]


class AgendaDetail(BaseModel):
    number: int
    agenda_id: str
    agenda_title: str
    discussion_background: list[FinalItem]
    major_discussions: list[SubtopicGroup]
    decisions: list[FinalItem]
    unresolved_issues: list[FinalItem]
    next_meeting_topics: list[FinalItem]


class FinalSummary(BaseModel):
    meeting_id: str
    meeting_info: MeetingInfo
    agenda_index: list[AgendaIndexEntry]
    meeting_results: list[MeetingResultEntry]
    agenda_details: list[AgendaDetail]


# ══════════════════════════════════════════════════════════════════════
# 7. LangGraph State
# ══════════════════════════════════════════════════════════════════════
class SummaryState(TypedDict, total=False):
    # ── 입력 ──
    input_path: str                         # 주제 경계 탐지 결과 JSON 경로 (그래프 시작 시 전달)
    meeting_id: str
    metadata: dict[str, str]
    segments: list[Segment]
    utterance_texts: dict[int, str]         # 발화 번호 → 원문 (근거 확인 · 범위 검증용)

    # ── [1] 1차 요약 ──
    registry: AgendaRegistry
    segment_summaries: list[SegmentSummary]

    # ── [2] 병합 결과 (이후 단계는 이 값을 부분 수정) ──
    agendas: list[Agenda]

    # ── [3]~[5] 내용 검증 · 재수정 루프 ──
    content_loop: int                       # 내용 검증 수행 횟수
    content_issues: list[ContentIssue]      # 현재 루프의 미해결 이슈
    content_issue_log: list[ContentIssue]   # 전체 이력
    recheck_item_ids: list[str]             # 재검증 대상 (수정된 항목 + 연관 항목)

    # ── [6][7] 가독성 검증 · 수정 루프 ──
    readability_loop: int                   # 가독성 검증 수행 횟수
    readability_issues: list[ReadabilityIssue]      # 현재 루프의 미반영 이슈
    readability_issue_log: list[ReadabilityIssue]   # 전체 이력
    readability_recheck_ids: list[str]      # 재검증 대상 (가독성 수정된 항목)

    # ── 구조 검증 ──
    structure_report: StructureValidationReport
    quarantine: list[dict]                  # 구조 검증 실패로 격리된 안건 · 항목 (원본 JSON + 오류, 다음 단계에 전달 안 함)

    # ── [9] 렌더링 ──
    final_summary: FinalSummary             # 회의 정보 / 안건 목차 / 회의 결과 / 안건별 상세 (최소 단위 JSON)
    final_markdown: str                     # final_summary를 회의록 구조 문자열로 조합한 결과
