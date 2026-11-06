"""Summary_Action 데이터 구조

구성
    0. 태그 · 섹션 · ID 규칙
    1. 입력: 회의 전사문 txt (메타데이터 + '발화자: 발화문')
    2. LLM 출력 스키마 (ID 없음) — prompt/Extract_summary_action_item.py
       2-1. 검증 출력 — prompt/Verify_summary.py / 2-2. 재수정 출력 (항목 단위 수정 명령) — prompt/Revise_summary.py
    3. 저장 스키마 (코드가 ID 부여) — 값을 최소 단위로 분리
       회의 정보 / 전사문 / 안건 → 섹션 → 항목 / 소주제 / 안건 목차 / 회의 결과 / 검증 · 재수정 기록
    4. 사용자 수정(HITL) — 수정 목록 입력 / 근거 연결 · 검증 LLM 출력 / 저장 기록 (hitl.py)

원칙
    - LLM 출력 스키마(LLM*)에는 ID를 두지 않음 → 파싱 후 코드가 agenda_id · subtopic_id · item_id 부여
    - LLM 출력 스키마는 structured output(strict) 호환: 기본값 없음 · 모든 필드 필수 · 추가 필드 금지
    - 저장 스키마의 항목 하나 = 회의록 글머리표 한 줄 = 검증 · 수정 단위 (item_id로 식별)
      "[논의 종류][결정사항] 요약문 [근거 발화]"는 discussion_tag · decision_tag · text · evidence_utterance_indices로 분리
    - 안건 목차 · 회의 결과는 항목을 복사하지 않고 item_id로 참조 (수정 시 항목 한 곳만 고치면 됨)
"""

from typing import Annotated, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field


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
#    회의 제목: …                  ┐
#    참여자: 이름_직무_userid, … (N명) │ 메타데이터 (config.METADATA_KEYS)
#    발화자: 이름_userid, …        │ (화자를 매핑한 경우에만)
#    일시: 2005-01-19             ┘
#    (빈 줄)
#    본문: '이름_userid: 발화문' / '[이름_userid]' + 발화문 / 발화문만 — 규칙은 transcript_parser.py
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
    assignees: list[str]                # 액션만 (여러 명이면 모두, 확인되지 않으면 빈 배열)
    due: str | None                     # 액션만 (YYYY-MM-DD 또는 YYYY-MM-DD HH:MM, 날짜가 특정되지 않으면 null)
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

    def section_items(self, name: str) -> list:
        return getattr(self, name)


class LLMMeetingSummary(_LLMModel):
    agendas: list[LLMAgenda]            # 처음 논의된 순서

    def has_content(self) -> bool:
        # 문장이 있는 항목이 하나라도 있는가 (없으면 후처리 후 안건이 0개)
        return any(it.text.strip() for a in self.agendas for name in SECTIONS for it in a.section_items(name))


# ── 2-1. 검증 출력 (prompt/Verify_summary.py) ──────────
IssueType = Literal[                    # 검증 기준 — prompt/Verify_summary.py '# 검증 기준'과 같은 이름 (가독성 제외)
    "가짜 근거", "원문에 없는 내용", "과장 및 왜곡", "결정 상태 오류", "액션 판단 오류", "액션 담당자 · 기한 오류",
    "액션 분리 · 병합 오류", "근거 연결 오류", "중요 항목 누락", "중복 요약", "결정 · 액션 분리 오류", "깊이 부족",
    "요약 범위 오류", "안건 구성 오류", "기타 안건 분류 오류", "결정 고려사항 누락", "결정 범위 오류", "세부 정보 누락",
    "논의 흐름 단절", "동명이인 표기 오류",
]


class LLMVerifyIssue(_LLMModel):
    issue_type: IssueType
    item_ids: list[str]                 # 문제 항목 (누락 · 회의 전체 문제는 빈 배열)
    agenda_id: str | None               # 문제가 속한 안건 (회의 전체 문제는 null)
    description: str
    evidence_utterance_indices: list[int]
    suggestion: str


class LLMVerification(_LLMModel):
    issues: list[LLMVerifyIssue]        # 빈 배열 = 통과


# ── 2-2. 재수정 출력 (prompt/Revise_summary.py) — 항목 단위 수정 명령 ──
class _LLMEdit(_LLMModel):
    reason: str                         # 무엇을 왜 고쳤는지
    resolves_issue_ids: list[str]       # 해결한 검증 문제


class LLMBackgroundUpdate(LLMBackgroundItem, _LLMEdit):
    item_id: str


class LLMDiscussionUpdate(LLMDiscussionItem, _LLMEdit):
    item_id: str


class LLMUnresolvedUpdate(LLMUnresolvedItem, _LLMEdit):
    item_id: str


class LLMNextTopicUpdate(LLMNextTopicItem, _LLMEdit):
    item_id: str


class LLMBackgroundAddition(LLMBackgroundItem, _LLMEdit):
    agenda_id: str


class LLMDiscussionAddition(LLMDiscussionItem, _LLMEdit):
    agenda_id: str


class LLMUnresolvedAddition(LLMUnresolvedItem, _LLMEdit):
    agenda_id: str


class LLMNextTopicAddition(LLMNextTopicItem, _LLMEdit):
    agenda_id: str


class LLMDeletion(_LLMEdit):
    item_id: str


class LLMAgendaTitleUpdate(_LLMEdit):
    agenda_id: str
    agenda_title: str


class LLMRevision(_LLMModel):
    background_updates: list[LLMBackgroundUpdate]
    discussion_updates: list[LLMDiscussionUpdate]
    unresolved_updates: list[LLMUnresolvedUpdate]
    next_topic_updates: list[LLMNextTopicUpdate]
    background_additions: list[LLMBackgroundAddition]
    discussion_additions: list[LLMDiscussionAddition]
    unresolved_additions: list[LLMUnresolvedAddition]
    next_topic_additions: list[LLMNextTopicAddition]
    deletions: list[LLMDeletion]
    agenda_title_updates: list[LLMAgendaTitleUpdate]


# ══════════════════════════════════════════════════════════════════════
# 3. 저장 스키마 (코드가 ID 부여, 최소 단위 분리)
# ══════════════════════════════════════════════════════════════════════
SpeakerMode = Literal["speaker", "none"]    # speaker: '발화자' 있음 / none: '발화자' 없음 (발화문만)
TranscriptFormat = Literal["inline", "block", "plain"]   # inline: '이름_userid: 발화문' / block: '[이름_userid]' + 발화문 / plain: 발화문만


class SourceDocument(BaseModel):
    input_path: str                     # 입력 전사문 txt 경로
    speaker_mode: SpeakerMode = "speaker"   # 회의 정보의 '발화자' 유무로 정함 (프롬프트도 이에 따라 바뀜)
    transcript_format: TranscriptFormat = "inline"   # 본문 형식 (speaker 모드는 첫 발화 줄로 판정)
    utterance_count: int
    first_utterance_index: int | None
    last_utterance_index: int | None


class Person(BaseModel):
    user_id: str                        # 사람 식별자 (ex. u2)
    name: str                           # 입력의 성명 (같은 직무의 동명이인은 이름 자체가 홍길동A)
    job: str                            # 직무 (ex. 산업디자이너)
    display_name: str                   # 사용자 표시 이름 (ex. 김철수 / 다른 직무의 동명이인 홍길동(산업디자이너) / 같은 직무의 동명이인 홍길동A)


class MeetingInfo(BaseModel):
    title: str | None                   # metadata '회의 제목'
    date: str | None                    # metadata '일시'의 날짜 (ex. 2004-10-11)
    datetime_raw: str | None            # metadata '일시' 원문
    participants: list[Person]          # metadata '참여자'를 user_id · 성명 · 직무로 분리
    participant_count: int | None       # metadata '참여자'의 (N명), 없으면 participants 수
    participants_raw: str | None        # metadata '참여자' 원문
    speaker_user_ids: list[str] = []    # metadata '발화자'의 user_id
    speakers_raw: str | None = None     # metadata '발화자' 원문
    extra: dict[str, str] = {}          # 그 외 metadata


class TranscriptUtterance(BaseModel):
    utterance_index: int                # config.UTTERANCE_START부터 (메타데이터 · 빈 줄 제외)
    speaker_user_id: str | None = None  # 화자 user_id (ex. u2) / none 모드는 화자 필드 모두 null
    speaker_name: str | None = None     # 성명 (ex. 홍길동)
    speaker_job: str | None = None      # 직무 (ex. 산업디자이너)
    speaker_display: str | None = None  # 사용자 표시 이름 (Person.display_name)
    text: str
    line_number: int | None = None      # 원본 txt의 줄 번호 (1부터, 확인용)


class ItemRevision(BaseModel):
    """항목 수정 이력 (검증 · 수정 단계 추가 시 사용)"""
    stage: str                          # postprocess / content_revise / readability_revise ...
    field: str                          # 바뀐 필드 이름
    before: str | list[int] | list[str | None] | None
    after: str | list[int] | list[str | None] | None
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
    # 담당자 — 네 배열은 같은 순서 · 같은 길이 (참여자와 대조되지 않은 담당자의 user_id · 성명 · 직무는 null)
    assignees: list[str]                        # 액션만 — 사용자 표시 이름 (그래프 진행 중에는 LLM 입력 이름, finalize에서 되돌림)
    assignee_user_ids: list[str | None] = []    # 담당자 user_id
    assignee_names: list[str | None] = []       # 담당자 성명
    assignee_jobs: list[str | None] = []        # 담당자 직무
    due: str | None                     # 액션만 (YYYY-MM-DD 또는 YYYY-MM-DD HH:MM, 날짜가 특정되지 않으면 null)

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

    @property
    def is_empty(self) -> bool:
        # 재수정으로 항목이 모두 삭제 · 이동된 안건 (이력 보존용으로 남기되 목차 · 회의록에는 표시 안 함, agenda_number=0)
        return not any(True for _ in self.iter_items())

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


# ── 검증 · 재수정 기록 ─────────────────────────────────
VerificationStatus = Literal["PENDING", "PASSED", "REVIEW_REQUIRED"]
# PENDING: 검증 전 (그래프 진행 중에만, 완료된 결과에는 없음) / PASSED: 검증 통과
# REVIEW_REQUIRED: config.MAX_VERIFY_ROUNDS회 검증해도 통과 못 함 → 남은 문제 항목에 검토 필요 표시


class VerificationIssue(BaseModel):
    issue_id: str                       # v{검증 회차}_i{n} (ex. v1_i3)
    round: int
    issue_type: str
    item_ids: list[str]                 # 존재하는 항목만 (없는 ID는 note에 기록)
    agenda_id: str | None
    description: str
    evidence_utterance_indices: list[int]
    suggestion: str
    note: str | None = None             # 코드 확인 결과 (ex. 없는 item_id 제외)


class RevisionChange(BaseModel):
    op: Literal["update", "add", "delete", "agenda_title"]
    target_id: str                      # 수정 · 삭제: item_id / 추가: 새 item_id (실패 시 agenda_id) / 제목: agenda_id
    changed_fields: list[str] = []
    reason: str
    resolves_issue_ids: list[str] = []
    applied: bool = True
    note: str | None = None             # 적용 안 된 이유 등


class VerificationRound(BaseModel):
    round: int                          # 검증 회차 (1부터)
    passed: bool                        # 처리할 문제(재수정 · 검토 대상)가 없음 — 기록만 남긴 개선 문제는 issues에 있을 수 있음
    issues: list[VerificationIssue]     # 이 회차에서 발견한 문제 전체
    revision: list[RevisionChange] = []     # 이 검증 결과로 한 재수정 (마지막 회차 · 통과 회차는 빈 배열)


class VerificationResult(BaseModel):
    status: VerificationStatus = "PENDING"
    max_rounds: int = 0
    rounds: list[VerificationRound] = []
    remaining_issue_ids: list[str] = []     # REVIEW_REQUIRED일 때 해결되지 않은 마지막 검증 문제
    message: str | None = None

    def remaining_issues(self) -> list[VerificationIssue]:
        if not self.rounds:
            return []
        ids = set(self.remaining_issue_ids)
        return [i for i in self.rounds[-1].issues if i.issue_id in ids]


# ══════════════════════════════════════════════════════════════════════
# 4. 사용자 수정(HITL) — 흐름은 hitl_graph.py
# ══════════════════════════════════════════════════════════════════════
# ── 4-1. 사용자 수정 목록 (입력) ───────────────────────
class HitlCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["create"]
    agenda_id: str                      # 사용자가 선택한 안건
    text: str                           # 액션명
    assignee_user_ids: list[str] = []
    due: str | None = None              # 날짜로 확인되지 않으면 빈 일정(null)으로 저장


class HitlUpdate(BaseModel):
    # 쓴 값만 변경 (값을 쓰지 않은 필드는 그대로, due: null은 일정을 비움)
    model_config = ConfigDict(extra="forbid")
    op: Literal["update"]
    item_id: str
    text: str | None = None
    assignee_user_ids: list[str] | None = None
    due: str | None = None


class HitlDelete(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["delete"]
    item_id: str


class HitlEdits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    edits: list[Annotated[HitlCreate | HitlUpdate | HitlDelete, Field(discriminator="op")]]


# ── 4-2. 근거 연결 · 검증 출력 (prompt/Link_evidence.py · Verify_evidence.py) ──
class LLMEvidenceLink(_LLMModel):
    item_id: str
    evidence_utterance_indices: list[int]
    reason: str


class LLMEvidenceLinks(_LLMModel):
    links: list[LLMEvidenceLink]


EvidenceIssueType = Literal["관련 없는 근거", "핵심 근거 누락"]


class LLMEvidenceIssue(_LLMModel):
    item_id: str
    issue_type: EvidenceIssueType
    description: str
    utterance_indices: list[int]


class LLMEvidenceVerification(_LLMModel):
    issues: list[LLMEvidenceIssue]      # 빈 배열 = 통과


# ── 4-3. 저장 기록 ─────────────────────────────────────
EvidenceStatus = Literal["NOT_NEEDED", "LINKED", "NO_EVIDENCE", "UNVERIFIED"]
# NOT_NEEDED: 삭제 · 변경 없음 / LINKED: 연결 · 검증 완료 / NO_EVIDENCE: 전사문에 근거 없음 (빈 배열, 사용자 값은 유지)
# UNVERIFIED: 마지막 검증까지 문제가 남아 근거를 비움 (빈 배열, 사용자 값은 유지, 비우기 전 근거는 항목 history)
# NO_EVIDENCE · UNVERIFIED는 REVIEW_REQUIRED로 표시하지 않음


class HitlEditRecord(BaseModel):
    op: Literal["create", "update", "delete"]
    item_id: str
    agenda_id: str
    changed_fields: list[str] = []
    evidence_before: list[int] = []
    evidence_after: list[int] = []
    evidence_status: EvidenceStatus = "LINKED"
    note: str | None = None             # ex. 날짜로 확인되지 않아 빈 일정으로 저장


class HitlEvidenceIssue(BaseModel):
    issue_id: str                       # h{검증 회차}_i{n}
    round: int
    item_id: str
    issue_type: str
    description: str
    utterance_indices: list[int]


class HitlEvidenceRound(BaseModel):
    round: int
    target_item_ids: list[str]
    passed: bool
    issues: list[HitlEvidenceIssue] = []


class HitlResult(BaseModel):
    status: Literal["NO_CHANGES", "COMPLETED"]
    applied_at: str
    max_rounds: int
    edits: list[HitlEditRecord] = []
    rounds: list[HitlEvidenceRound] = []


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
    verification: VerificationResult = VerificationResult()     # 검증 · 재수정 기록 (graph.py)
    hitl: HitlResult | None = None      # 사용자 수정 기록 ({회의 ID}_summary_final.json에만, hitl_graph.py)

    def find_item(self, item_id: str) -> BaseItem | None:
        for agenda in self.agendas:
            for item in agenda.iter_items(include_deleted=True):
                if item.item_id == item_id:
                    return item
        return None

    def active_agendas(self) -> list[Agenda]:
        return [a for a in self.agendas if not a.is_empty]

    def agenda_of(self, item_id: str) -> Agenda | None:
        return next((a for a in self.agendas if item_id.startswith(a.agenda_id + "_")), None)
