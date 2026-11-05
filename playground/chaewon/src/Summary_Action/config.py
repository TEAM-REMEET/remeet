"""Summary_Action 사용자 설정 (사람이 조정하는 값은 모두 이 파일에서 관리)

원칙
    - API 키 · 모델 이름 · LangSmith 키는 .env에만 둠 (이 파일에는 적지 않음)
        OPENAI_API_KEY      OpenAI 키
        OPENAI_MODEL        사용할 모델
        LANGSMITH_API_KEY   LangSmith 키 (없으면 기록 없이 동작)
        LANGSMITH_PROJECT   LangSmith 프로젝트 (없으면 아래 LANGSMITH_PROJECT 사용)
        LANGSMITH_TRACING   false로 두면 키가 있어도 기록 끔
    - 실행 전 validate()로 값의 범위를 확인
    - 각 모듈은 import config as cfg 후 cfg.값으로 실행 시점에 읽음 (노트북에서 cfg.값 = ... 로 바로 변경 가능)

구성
    1. 경로
    2. LLM 호출 (2-1. 검증 · 재수정 루프)
    3. LangSmith
    4. 입력 · 실행
    5. 후처리 (1-1)
    6. 저장
    7. 렌더링 (회의록 md · txt)
"""

import os
from pathlib import Path
from typing import get_args

# ══════════════════════════════════════════════════════════════════════
# 1. 경로
# ══════════════════════════════════════════════════════════════════════
APP_DIR = Path(__file__).resolve().parent                       # playground/chaewon/src/Summary_Action
PROJECT_DIR = APP_DIR.parents[1]                                # playground/chaewon
ENV_PATH = next((p / ".env" for p in APP_DIR.parents if (p / ".env").is_file()), None)   # 상위 폴더에서 .env 탐색

INPUT_DIR = PROJECT_DIR / "dataset" / "Meeting_Input_p"           # 입력: 회의 전사문 txt (메타데이터 + '발화자: 발화문')
INPUT_SUFFIX = ".txt"                                           # 회의 ID → {INPUT_DIR}/{회의 ID}{INPUT_SUFFIX}
INPUT_ENCODING = "utf-8-sig"                                    # BOM이 있어도 읽힘
OUTPUT_DIR = PROJECT_DIR / "outputs" / "summary_action_add_v"         # 결과 → {OUTPUT_DIR}/{회의 ID}/
LLM_CACHE_DIR = PROJECT_DIR / "outputs" / "llm_cache"           # LLM 응답 캐시
CACHE_TAG = "summary_action"                                    # 캐시 파일 이름: {회의 ID}_{CACHE_TAG}_{해시}.json

# ══════════════════════════════════════════════════════════════════════
# 2. LLM 호출 (모델 이름은 .env의 OPENAI_MODEL)
# ══════════════════════════════════════════════════════════════════════
ALLOWED_EFFORTS = {"medium", "high"}
REASONING_EFFORT = "high"          # 추론 강도 / None이면 모델 기본값
LLM_TEMPERATURE = None               # None이면 모델 기본값
VERIFY_EFFORT = None                 # 검증 추론 강도 / None이면 REASONING_EFFORT
REVISE_EFFORT = None                 # 재수정 추론 강도 / None이면 REASONING_EFFORT
# 회의 하나당 실제 API 호출 상한은 max_llm_calls()로 자동 계산 (단계별 최대 횟수의 합 + LLM_CALL_MARGIN, 캐시 사용은 세지 않음)
LLM_CALL_MARGIN = 2                  # 호출 상한의 여유 값 (0~3)
GENERATE_EMPTY_RETRIES = 1           # 생성 결과가 비었을 때(안건 · 항목 없음) 다시 호출하는 횟수 (0~2) — 그래도 비면 오류로 중단
LLM_MAX_RETRIES = 3                 # 네트워크 · 일시 오류 재시도 횟수 (호출 1건당, OpenAI SDK 내부 재시도 — 위 상한과 별개)
LLM_TIMEOUT = 600                    # 호출 1건당 대기 시간 (초) — 회의 전체를 한 번에 넣으므로 길게
USE_LLM_CACHE = True                 # 같은 입력(모델 · 추론 강도 · 프롬프트 · 스키마)이면 저장된 응답 사용

# ── 2-1. 검증 · 재수정 루프 (graph.py) ──────────────────
#   생성 → 후처리 → 검증 ─ 통과 ────────────────────────→ 저장
#                     └ 실패 → 재수정 → 검증 … (최대 MAX_VERIFY_ROUNDS회 검증)
#                     └ MAX_VERIFY_ROUNDS번째 검증도 실패 → 남은 문제 항목에 '검토 필요' 표시 → 저장
#   생성 후에는 반드시 1회 이상 검증함 (검증을 건너뛰는 경로 없음)
MAX_VERIFY_ROUNDS = 3               # 검증 최대 횟수 (1~3) — 재수정은 최대 MAX_VERIFY_ROUNDS-1회 (무한 루프 방지)
# 개선 유형: 1회차에만 재수정, 2회차부터는 검증 기록에만 남김 (통과를 막지 않고 '검토 필요'로 표시하지 않음)
# 그 외 유형은 오류: 회차와 관계없이 재수정 대상이고, 마지막 검증까지 남으면 '검토 필요'
IMPROVEMENT_ISSUE_TYPES = {"깊이 부족", "논의 흐름 단절", "세부 정보 누락", "결정 고려사항 누락", "중복 요약",
                           "요약 범위 오류", "안건 구성 오류", "기타 안건 분류 오류"}

# ══════════════════════════════════════════════════════════════════════
# 3. LangSmith (키는 .env, 키가 없으면 기록 없이 동작)
# ══════════════════════════════════════════════════════════════════════
USE_LANGSMITH = True                 # False면 키가 있어도 기록 안 함 (LangGraph 실행 · 노드 · LLM 호출이 한 trace로 기록)
LANGSMITH_PROJECT = "REMEET"         # .env에 LANGSMITH_PROJECT가 없을 때 쓰는 프로젝트 이름
LANGSMITH_TAGS = ["summary_action"]  # trace에 붙는 태그 (LangSmith에서 필터용)

# ══════════════════════════════════════════════════════════════════════
# 4. 입력 · 실행
# ══════════════════════════════════════════════════════════════════════
DEFAULT_INPUTS = ["TS3003c"]         # 인자 없이 실행할 때 처리할 회의 ID (또는 txt 경로)
UTTERANCE_START = 1                  # 첫 발화 번호 (GoldenSet position과 같게: 메타데이터 · 빈 줄 제외, 1부터)
# 전사문 txt 앞부분의 '키: 값' 줄 중 아래 키만 메타데이터로 읽음 (그 외 '화자: 내용' 줄은 발화)
TITLE_KEYS = ("회의 제목",)                         # 회의 제목
DATETIME_KEYS = ("일시", "회의 일시")                # 일시
PARTICIPANT_KEYS = ("참여자", "참석자")              # 참석자
SPEAKER_KEYS = ("발화자",)                          # 발화자 (ex. 한유리_u1, 홍길동_u2) — 있으면 speaker 모드, 없으면 none 모드
PARTICIPANT_SEPARATOR = "_"            # 값 구분자 — 참여자 '이름_직무_userid' · 발화자 '이름_userid'
ALIAS_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"   # 다른 직무의 동명이인에게 LLM 입력에서만 붙이는 구분 글자 (저장 · 표시 안 함)
METADATA_KEYS = TITLE_KEYS + DATETIME_KEYS + PARTICIPANT_KEYS + SPEAKER_KEYS
MAX_SPEAKER_LEN = 30                 # speaker 모드 inline 형식에서 화자 표기('이름_userid')로 인정하는 최대 길이 (넘으면 구조 오류)
MAX_REPORTED_ERRORS = 20             # 구조 오류 메시지에 보여줄 최대 건수 (나머지는 건수만)
SPEAKER_LIKE_WARN_RATIO = 0.5        # none 모드: 화자 표기처럼 보이는 줄이 이 비율 이상이면 매핑 누락 의심 경고
SPEAKER_LIKE_LABEL_LEN = 10          # none 모드 경고 판정: '표기: …'의 표기가 이 길이 이하일 때만 화자 표기처럼 봄

# ══════════════════════════════════════════════════════════════════════
# 5. 후처리 (1-1 근거 발화 검사 · ID 부여)
# ══════════════════════════════════════════════════════════════════════
DEFAULT_SUBTOPIC = "기타"            # 주요 논의 소주제가 비었을 때
MISC_AGENDA_TITLE = "기타"           # 핵심 안건이 아닌 논의 · 액션을 모으는 안건 제목 (회의당 하나, 목록 맨 뒤 — 프롬프트 문구와 같게 유지)
EMPTY_VALUES = {"", "-", "null", "none", "없음"}   # assignees에서 제외하고 due에서 null로 처리할 값 (소문자 비교)
DUE_FORMATS = ("%Y-%m-%d", "%Y-%m-%d %H:%M")      # due로 인정하는 형식 (프롬프트 문구와 같게 유지) — 그 외 값은 null로 저장

# ══════════════════════════════════════════════════════════════════════
# 6. 저장
# ══════════════════════════════════════════════════════════════════════
SAVE_SUMMARY_JSON = True             # {회의 ID}_summary.json (최소 단위 분리 + item_id)
SAVE_MINUTES_MD = True               # {회의 ID}_minutes.md (저장된 summary JSON을 읽어 생성 — render_md.py)
SAVE_MINUTES_TEXT = True             # {회의 ID}_minutes.txt (사람이 읽는 회의록, 텍스트)
SAVE_LLM_OUTPUT = True               # {회의 ID}_llm_output.json (LLM 원본 출력, ID 부여 전, 내부 확인용 — 코드가 붙인 A · B가 그대로 남음)
# 저장 JSON에는 API 사용량 · 비용 정보 · 키를 넣지 않음

# ══════════════════════════════════════════════════════════════════════
# 7. 렌더링 (회의록 md · txt)
# ══════════════════════════════════════════════════════════════════════
SHOW_EVIDENCE = True                 # 항목 끝에 [근거 발화 …] 표시
SHOW_ITEM_ID = False                 # 항목 앞에 item_id 표시 (검토용)
SHOW_TRANSCRIPT = True               # 회의록 끝에 원본 전사문 첨부 (md에서는 근거 발화 번호가 전사문 링크가 됨)
HIDE_EMPTY = True                    # 빈 섹션 (미해결 이슈 · 다음 회의 후보 안건 등) 출력 안 함
EMPTY_TEXT = ""                # HIDE_EMPTY=False일 때 빈 섹션 표시
REVIEW_MARK = " (!!검토 필요!!)"          # REVIEW_REQUIRED 항목 표시 (근거 검사 · 검증 미통과)
REVIEW_BANNER = "⚠ 검토 필요"        # 검증을 MAX_VERIFY_ROUNDS회 해도 통과하지 못한 회의록 상단 경고
SHOW_REVIEW_REASONS = True           # 회의록 상단 경고 아래에 남은 검증 문제 목록 표시
INDENT = "  "


def summary_dir(meeting_id: str) -> Path:
    return OUTPUT_DIR / meeting_id


def model_name() -> str:
    # .env의 OPENAI_MODEL (모델 이름만 읽음, 키는 읽지 않음)
    model = (os.getenv("OPENAI_MODEL") or "").strip()
    if not model:
        raise RuntimeError(f"OPENAI_MODEL이 설정되지 않음 (.env 위치: {ENV_PATH})")
    return model


def llm_call_plan() -> dict[str, int]:
    # 단계별 최대 LLM 호출 횟수 (LLM 단계를 추가하면 여기에 한 줄 추가)
    return {"생성": 1, "생성 재호출": GENERATE_EMPTY_RETRIES, "검증": MAX_VERIFY_ROUNDS, "재수정": MAX_VERIFY_ROUNDS - 1}


def max_llm_calls() -> int:
    # 회의 하나당 실제 API 호출 상한 = 단계별 최대 횟수의 합 + 여유 값 (기본 1+1+3+2+2=9)
    return sum(llm_call_plan().values()) + LLM_CALL_MARGIN


def llm_call_plan_text() -> str:
    # '생성 1 + 생성 재호출 1 + 검증 3 + 재수정 2 + 여유 2 = 9'
    steps = " + ".join(f"{name} {count}" for name, count in llm_call_plan().items())
    return f"{steps} + 여유 {LLM_CALL_MARGIN} = {max_llm_calls()}"


def validate() -> None:
    # 실행 전 설정값 범위 확인 — 문제가 있으면 모두 모아 한 번에 알림
    errors = []
    if REASONING_EFFORT is not None and REASONING_EFFORT not in ALLOWED_EFFORTS:
        errors.append(f"REASONING_EFFORT={REASONING_EFFORT!r} — {sorted(ALLOWED_EFFORTS)} 또는 None")
    if not isinstance(MAX_VERIFY_ROUNDS, int) or not 1 <= MAX_VERIFY_ROUNDS <= 3:
        errors.append(f"MAX_VERIFY_ROUNDS={MAX_VERIFY_ROUNDS!r} — 1~3 (LLM 반복은 최대 3회)")
    from schemas import IssueType       # 여기서만 필요 (schemas는 config를 읽지 않음)
    unknown = IMPROVEMENT_ISSUE_TYPES - set(get_args(IssueType))
    if unknown:
        errors.append(f"IMPROVEMENT_ISSUE_TYPES에 없는 유형: {sorted(unknown)} — schemas.IssueType의 이름과 같아야 함")
    for name, value in (("VERIFY_EFFORT", VERIFY_EFFORT), ("REVISE_EFFORT", REVISE_EFFORT)):
        if value is not None and value not in ALLOWED_EFFORTS:
            errors.append(f"{name}={value!r} — {sorted(ALLOWED_EFFORTS)} 또는 None")
    if not isinstance(LLM_CALL_MARGIN, int) or not 0 <= LLM_CALL_MARGIN <= 3:
        errors.append(f"LLM_CALL_MARGIN={LLM_CALL_MARGIN!r} — 0~3")
    if not isinstance(GENERATE_EMPTY_RETRIES, int) or not 0 <= GENERATE_EMPTY_RETRIES <= 2:
        errors.append(f"GENERATE_EMPTY_RETRIES={GENERATE_EMPTY_RETRIES!r} — 0~2 (LLM 반복은 최대 3회)")
    if not isinstance(LLM_MAX_RETRIES, int) or not 0 <= LLM_MAX_RETRIES <= 5:
        errors.append(f"LLM_MAX_RETRIES={LLM_MAX_RETRIES!r} — 0~5")
    if not isinstance(LLM_TIMEOUT, (int, float)) or LLM_TIMEOUT <= 0:
        errors.append(f"LLM_TIMEOUT={LLM_TIMEOUT!r} — 0보다 커야 함")
    if not INPUT_DIR.is_dir():
        errors.append(f"INPUT_DIR={INPUT_DIR} — 폴더 없음")
    if not isinstance(UTTERANCE_START, int) or UTTERANCE_START < 0:
        errors.append(f"UTTERANCE_START={UTTERANCE_START!r} — 0 이상 정수")
    if errors:
        raise ValueError("config 설정 오류:\n  - " + "\n  - ".join(errors))


def snapshot() -> dict:
    # 실행 설정 기록용 (키 · 비용 정보 없음)
    return {"model": os.getenv("OPENAI_MODEL"), "reasoning_effort": REASONING_EFFORT,
            "verify_effort": VERIFY_EFFORT or REASONING_EFFORT, "revise_effort": REVISE_EFFORT or REASONING_EFFORT,
            "temperature": LLM_TEMPERATURE, "use_llm_cache": USE_LLM_CACHE,
            "max_verify_rounds": MAX_VERIFY_ROUNDS}
