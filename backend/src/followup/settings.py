"""요약 · 액션 아이템 추출 설정 (사람이 조정하는 값은 모두 이 파일에서 관리)

원칙
    - API 키 · 모델 이름 · LangSmith 키는 환경변수(.env)에만 둠 (이 파일에는 적지 않음)
        OPENAI_API_KEY        OpenAI 키
        OPENAI_MODEL          사용할 모델
        LANGSMITH_API_KEY     LangSmith 키
        LANGSMITH_TRACING     true일 때만 기록 (실명 · 발화가 외부로 전송되므로 기본은 꺼짐)
        LANGSMITH_PROJECT     LangSmith 프로젝트 (없으면 아래 LANGSMITH_PROJECT 사용)
        SUMMARY_OUTPUT_DIR    summary.json · 단계별 LLM 응답 저장 위치 (없으면 backend/outputs/summary_action)
        NEO4J_URI / NEO4J_USERNAME / NEO4J_PASSWORD   Neo4j 접속 정보
        DATABASE_URL          PostgreSQL 접속 주소
        AUTH_SECRET           로그인 토큰 서명 키 (길고 무작위인 값)
        ATLASSIAN_EMAIL / ATLASSIAN_API_TOKEN / JIRA_CLOUD_ID / JIRA_PROJECT_KEY / JIRA_ISSUE_TYPE   Jira 전송
        SMTP_HOST / SMTP_PORT / SMTP_USERNAME / SMTP_PASSWORD / MAIL_FROM   메일 전송 (요약본 → 참석자)
    - 환경변수는 이 파일에서만 읽음 (openai_client.py 제외)
    - 실행 전 validate()로 값의 범위를 확인

구성
    0. 서버 (Neo4j · PostgreSQL · 로그인)
    1. 저장
    2. LLM 호출 (2-1. 검증 · 재수정 루프)
    3. LangSmith
    4. 입력
    5. 후처리
"""

import os
from pathlib import Path
from typing import get_args

from dotenv import find_dotenv, load_dotenv

# 이 파일을 불러올 때 바로 읽음 (아래 NEO4J · AUTH 값이 바로 os.getenv로 읽히므로 load_env() 호출을 기다릴 수 없음)
load_dotenv(Path(__file__).resolve().parents[3] / ".env", override=False, encoding="utf-8-sig")

# ══════════════════════════════════════════════════════════════════════
# 0. 서버 (Neo4j · 로그인)
# ══════════════════════════════════════════════════════════════════════
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")

# PostgreSQL (회의 목록 · 권한 · 상태 · 공유 · 알림 · 검색). 로컬 기본값은 backend/docker-compose.postgres.yml
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://remeet:remeet@localhost:5432/remeet")

# 로그인 출입증(토큰) 서명 키. 길고 무작위인 값이어야 하고, 바뀌면 모든 사람이 다시 로그인해야 한다.
AUTH_SECRET = os.getenv("AUTH_SECRET", "")
AUTH_TOKEN_DAYS = 7


def missing() -> list[str]:
    """서버가 동작하는 데 꼭 필요한데 비어 있는 설정 이름."""
    return [name for name in ("NEO4J_PASSWORD", "AUTH_SECRET") if not globals()[name]]


# 외부 도구 전송 · Jira (Atlassian MCP). 토큰은 id.atlassian.com 에서 앱을 「Atlassian MCP V2」로 발급
JIRA_KEYS = ("ATLASSIAN_EMAIL", "ATLASSIAN_API_TOKEN", "JIRA_CLOUD_ID", "JIRA_PROJECT_KEY")
JIRA_ISSUE_TYPE = "작업"  # JIRA_ISSUE_TYPE 이 없을 때 · Jira 프로젝트에 실제로 있는 유형 이름이어야 함
EXPORT_CALL_TIMEOUT = 30  # 외부 도구(Jira MCP · SMTP) 호출 하나를 기다리는 최대 시간 (초)
# 이 시간이 지나면 남은 아이템은 보내지 않고 '시간 초과'로 돌려줌 (다시 보내기 대상).
# 접속(30) + 이 시간 + 마지막 아이템(호출 2번 · 60)이 화면이 기다리는 시간(apps/web config.EXPORT_TIMEOUT=180) 안에 들게
EXPORT_BUDGET_SECONDS = 80

# 외부 도구 전송 · 메일 (SMTP). Gmail 이면 smtp.gmail.com:587 + 앱 비밀번호
MAIL_KEYS = ("SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD", "MAIL_FROM")
SMTP_PORT = 587  # SMTP_PORT 가 없을 때 · 465 면 SSL, 그 밖은 STARTTLS


def jira_config() -> dict[str, str] | None:
    """Jira 연동 값 (JIRA_KEYS + JIRA_ISSUE_TYPE). 하나라도 비면 None (연동 안 됨)."""
    values = {k: os.getenv(k, "").strip() for k in JIRA_KEYS}
    if not all(values.values()):
        return None
    return values | {"JIRA_ISSUE_TYPE": os.getenv("JIRA_ISSUE_TYPE", "").strip() or JIRA_ISSUE_TYPE}


def mail_config() -> dict[str, str] | None:
    """메일 연동 값 (MAIL_KEYS + SMTP_PORT). 하나라도 비면 None (연동 안 됨)."""
    values = {k: os.getenv(k, "").strip() for k in MAIL_KEYS}
    if not all(values.values()):
        return None
    return values | {"SMTP_PORT": os.getenv("SMTP_PORT", "").strip() or str(SMTP_PORT)}

# ══════════════════════════════════════════════════════════════════════
# 1. 저장
# ══════════════════════════════════════════════════════════════════════
BACKEND_DIR = Path(__file__).resolve().parents[2]               # backend (컨테이너에서는 /app)
DEFAULT_OUTPUT_DIR = BACKEND_DIR / "outputs" / "summary_action"  # 결과 → {위치}/{회의 ID}/{회의 ID}_summary.json

# ══════════════════════════════════════════════════════════════════════
# 2. LLM 호출 (모델 이름은 환경변수 OPENAI_MODEL)
# ══════════════════════════════════════════════════════════════════════
ALLOWED_EFFORTS = {"medium", "high"}
REASONING_EFFORT = "high"            # 추론 강도 / None이면 모델 기본값
LLM_TEMPERATURE = None               # None이면 모델 기본값
VERIFY_EFFORT = None                 # 검증 추론 강도 / None이면 REASONING_EFFORT
REVISE_EFFORT = None                 # 재수정 추론 강도 / None이면 REASONING_EFFORT
# 실행 1회당 실제 API 호출 상한은 max_llm_calls()로 자동 계산 (단계별 최대 횟수의 합 + LLM_CALL_MARGIN, 저장된 응답 사용은 세지 않음)
LLM_CALL_MARGIN = 2                  # 호출 상한의 여유 값 (0~3)
GENERATE_EMPTY_RETRIES = 1           # 생성 결과가 비었을 때(안건 · 항목 없음) 다시 호출하는 횟수 (0~2) — 그래도 비면 오류로 중단
LLM_MAX_RETRIES = 3                  # 네트워크 · 일시 오류 재시도 횟수 (호출 1건당, OpenAI SDK 내부 재시도 — 위 상한과 별개)
LLM_TIMEOUT = 600                    # 호출 1건당 대기 시간 (초) — 회의 전체를 한 번에 넣으므로 길게
USE_LLM_CACHE = True                 # 단계별 LLM 응답을 저장 · 재사용 (중간 실패 후 재실행 시 실패 지점부터 진행, 완료되면 삭제)

# ── 2-1. 검증 · 재수정 루프 (application/summarize_meeting.py) ──────────────────
#   생성 → 후처리 → 검증 ─ 통과 ────────────────────────→ 저장
#                     └ 실패 → 재수정 → 검증 … (최대 MAX_VERIFY_ROUNDS회 검증)
#                     └ MAX_VERIFY_ROUNDS번째 검증도 실패 → 남은 문제 항목에 '검토 필요' 표시 → 저장
#   생성 후에는 반드시 1회 이상 검증함 (검증을 건너뛰는 경로 없음)
MAX_VERIFY_ROUNDS = 3                # 검증 최대 횟수 (1~3) — 재수정은 최대 MAX_VERIFY_ROUNDS-1회 (무한 루프 방지)
# 개선 유형: 1회차에만 재수정, 2회차부터는 검증 기록에만 남김 (통과를 막지 않고 '검토 필요'로 표시하지 않음)
# 그 외 유형은 오류: 회차와 관계없이 재수정 대상이고, 마지막 검증까지 남으면 '검토 필요'
UI_TEST_TRANSCRIPT = "test"          # 화면 확인용 시험 모드에서 회의 내용으로 넣는 값 (docs/ui-test-mode.md)
UI_TEST_DELAY_SECONDS = 5            # 시험 모드에서 진행 중 화면을 볼 수 있도록 기다리는 시간 (초)
SUMMARY_TIMEOUT_SECONDS = 1800       # 요약 · 액션 추출 전체 제한 시간 (30분) — 넘으면 실패 처리, 다시 시도하면 이어서 진행
REVIEW_TIMEOUT_SECONDS = 600         # 확정 후 근거 연결 · 검증 전체 제한 시간 (10분)
MAX_SUMMARY_JOBS = 4                 # 동시에 실행할 요약 작업 수 (사람마다 1건 · application/meeting_jobs.py)
# 사용자 수정(HITL) 반영 (application/review_meeting.py)
HITL_MAX_VERIFY_ROUNDS = 2           # 근거 검증 최대 횟수 (1~3) — 근거 연결도 같은 횟수까지 (무한 루프 방지)
HITL_EFFORT = None                   # 근거 연결 · 검증 추론 강도 / None이면 REASONING_EFFORT
IMPROVEMENT_ISSUE_TYPES = {"깊이 부족", "논의 흐름 단절", "세부 정보 누락", "결정 고려사항 누락", "중복 요약",
                           "요약 범위 오류", "안건 구성 오류", "기타 안건 분류 오류"}

# ── 2-2. 사용량 · 비용 기록 (PostgreSQL llm_usage · 관리자 「비용 관리」 · adapters/llm/usage.py) ──────────
#   AI 응답이 올 때마다 1줄 저장 (회의록 분석 · 근거 연결 · 담당자 추천 · 회의 질문). 결과 JSON에는 넣지 않음
#   API 서버에서만 기록 (테스트 · CLI는 기록 안 함) · 기록이 실패해도 본 작업은 계속함
RECORD_LLM_USAGE = True
# 모델별 단가 (USD · 100만 토큰당: 입력, 캐시 입력, 출력) — 응답의 모델 이름이 이 키로 시작하면 그 단가
# 표에 없는 모델은 비용을 비워 두고 경고 (토큰 수는 남아 있어 나중에 다시 계산할 수 있음)
LLM_PRICES_USD = {
    "gpt-6-luna": (0.10, 0.01, 0.50),
}
# 달러 → 원 환율: 하루 한 번 받아 옴 (유럽중앙은행 기준 · 키 없음). 받지 못하면 관리자 설정(app_settings.usd_krw_rate) → 아래 값
EXCHANGE_RATE_URL = "https://api.frankfurter.dev/v1/latest?base=USD&symbols=KRW"
EXCHANGE_RATE_TTL_SECONDS = 86400    # 환율을 다시 받는 간격 (초)
EXCHANGE_RATE_TIMEOUT = 5            # 환율 요청 대기 시간 (초)
DEFAULT_USD_KRW = 1400               # 환율도 관리자 설정도 없을 때
DEFAULT_BUDGET_KRW = 50000           # 월 예산 기본값 (원) — 관리자가 저장하면 app_settings 값을 씀
DEFAULT_BUDGET_ALERT_PCT = 80        # 예산 알림 기준 기본값 (%)
USAGE_MONTHS = 12                    # 비용 관리 화면에 보내는 최근 개월 수

# ══════════════════════════════════════════════════════════════════════
# 3. LangSmith (키는 환경변수, LANGSMITH_TRACING=true일 때만 기록)
# ══════════════════════════════════════════════════════════════════════
USE_LANGSMITH = True                 # False면 환경변수와 관계없이 기록 안 함
LANGSMITH_PROJECT = "REMEET"         # 환경변수 LANGSMITH_PROJECT가 없을 때 쓰는 프로젝트 이름
LANGSMITH_TAGS = ["summary_action"]  # trace에 붙는 태그 (LangSmith에서 필터용)

# ══════════════════════════════════════════════════════════════════════
# 4. 입력
# ══════════════════════════════════════════════════════════════════════
# 입력 길이 상한 (LLM 호출 전에 막음 · 처리 시간과 비용을 묶어 둠)
#   전사문 35,000자 ≈ 회의 1시간 10분~1시간 30분 · 프롬프트로 바뀌면 약 1.23배, 요약 결과는 원문의 약 0.41배
#   가장 큰 단계(재수정: 지시 + 전사문 + 요약 + 검증 결과 + 출력 형식)가 MAX_STEP_INPUT_CHARS 안에 들도록 정한 값
MAX_TRANSCRIPT_CHARS = 35000         # 사용자가 넣은 회의 내용(전사문) 최대 글자 수 — 넘으면 422 (화면 apps/web/config.py와 같게 유지)
MAX_STEP_INPUT_CHARS = 80000         # LLM 호출 1번의 입력 최대 글자 수 (지시 프롬프트 + 사용자 프롬프트 + 출력 형식 JSON 스키마) — 넘으면 호출하지 않고 중단
UTTERANCE_START = 1                  # 첫 발화 번호 (GoldenSet position과 같게: 메타데이터 · 빈 줄 제외, 1부터)
# 전사문 앞부분의 '키: 값' 줄 중 아래 키만 메타데이터로 읽음 (그 외 '화자: 내용' 줄은 발화)
TITLE_KEYS = ("회의 제목",)                         # 회의 제목
DATETIME_KEYS = ("일시", "회의 일시")                # 일시
PARTICIPANT_KEYS = ("참여자", "참석자")              # 참석자
SPEAKER_KEYS = ("발화자",)                          # 발화자 (ex. 한유리_u1, 홍길동_u2) — 있으면 speaker 모드, 없으면 none 모드
PARTICIPANT_SEPARATOR = "_"          # 값 구분자 — 참여자 '이름_직무_userid' · 발화자 '이름_userid'
ALIAS_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"   # 다른 직무의 동명이인에게 LLM 입력에서만 붙이는 구분 글자 (저장 · 표시 안 함)
METADATA_KEYS = TITLE_KEYS + DATETIME_KEYS + PARTICIPANT_KEYS + SPEAKER_KEYS
MAX_SPEAKER_LEN = 30                 # speaker 모드 inline 형식에서 화자 표기('이름_userid')로 인정하는 최대 길이 (넘으면 구조 오류)
MAX_REPORTED_ERRORS = 20             # 구조 오류 메시지에 보여줄 최대 건수 (나머지는 건수만)
SPEAKER_LIKE_WARN_RATIO = 0.5        # none 모드: 화자 표기처럼 보이는 줄이 이 비율 이상이면 매핑 누락 의심 경고
SPEAKER_LIKE_LABEL_LEN = 10          # none 모드 경고 판정: '표기: …'의 표기가 이 길이 이하일 때만 화자 표기처럼 봄

# ══════════════════════════════════════════════════════════════════════
# 5. 후처리 (근거 발화 검사 · ID 부여)
# ══════════════════════════════════════════════════════════════════════
DEFAULT_SUBTOPIC = "기타"            # 주요 논의 소주제가 비었을 때
MISC_AGENDA_TITLE = "기타"           # 핵심 안건이 아닌 논의 · 액션을 모으는 안건 제목 (회의당 하나, 목록 맨 뒤 — 프롬프트 문구와 같게 유지)
EMPTY_VALUES = {"", "-", "null", "none", "없음"}   # assignees에서 제외하고 due에서 null로 처리할 값 (소문자 비교)
DUE_FORMATS = ("%Y-%m-%d", "%Y-%m-%d %H:%M")      # due로 인정하는 형식 (프롬프트 문구와 같게 유지) — 그 외 값은 null로 저장


def output_dir() -> Path:
    return Path(os.getenv("SUMMARY_OUTPUT_DIR") or DEFAULT_OUTPUT_DIR)


def load_env() -> None:
    # 로컬 실행용 .env 읽기 (이미 설정된 환경변수를 덮어쓰지 않음, 컨테이너는 env_file 사용)
    load_dotenv(find_dotenv(usecwd=True), override=False, encoding="utf-8-sig")


def ui_test_enabled() -> bool:
    # 화면 확인용 시험 모드 (.env의 UI_TEST_MODE=true · 기본 꺼짐, 서버에는 켜지 않음)
    load_env()
    return os.getenv("UI_TEST_MODE", "").strip().lower() == "true"


def openai_key_set() -> bool:
    return bool(os.getenv("OPENAI_API_KEY"))


def langsmith_requested() -> bool:
    return bool(os.getenv("LANGSMITH_API_KEY")) and os.getenv("LANGSMITH_TRACING", "").strip().lower() == "true"


def model_name() -> str:
    # 환경변수 OPENAI_MODEL (모델 이름만 읽음, 키는 읽지 않음)
    model = (os.getenv("OPENAI_MODEL") or "").strip()
    if not model:
        raise RuntimeError("OPENAI_MODEL이 설정되지 않음")
    return model


def llm_call_plan() -> dict[str, int]:
    # 단계별 최대 LLM 호출 횟수 (LLM 단계를 추가하면 여기에 한 줄 추가)
    return {"생성": 1, "생성 재호출": GENERATE_EMPTY_RETRIES, "검증": MAX_VERIFY_ROUNDS, "재수정": MAX_VERIFY_ROUNDS - 1}


def max_llm_calls() -> int:
    # 실행 1회당 실제 API 호출 상한 = 단계별 최대 횟수의 합 + 여유 값 (기본 1+1+3+2+2=9)
    return sum(llm_call_plan().values()) + LLM_CALL_MARGIN


def hitl_max_llm_calls() -> int:
    # HITL 실행 1회당 실제 API 호출 상한 = 회차마다 근거 연결 1 + 검증 1
    return HITL_MAX_VERIFY_ROUNDS * 2


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
    from followup.analysis.summary.schemas import IssueType     # 여기서만 필요 (schemas는 settings를 읽지 않음)
    unknown = IMPROVEMENT_ISSUE_TYPES - set(get_args(IssueType))
    if unknown:
        errors.append(f"IMPROVEMENT_ISSUE_TYPES에 없는 유형: {sorted(unknown)} — schemas.IssueType의 이름과 같아야 함")
    for name, value in (("SUMMARY_TIMEOUT_SECONDS", SUMMARY_TIMEOUT_SECONDS), ("REVIEW_TIMEOUT_SECONDS", REVIEW_TIMEOUT_SECONDS)):
        if not isinstance(value, (int, float)) or value <= 0:
            errors.append(f"{name}={value!r} — 0보다 커야 함")
    if not isinstance(HITL_MAX_VERIFY_ROUNDS, int) or not 1 <= HITL_MAX_VERIFY_ROUNDS <= 3:
        errors.append(f"HITL_MAX_VERIFY_ROUNDS={HITL_MAX_VERIFY_ROUNDS!r} — 1~3 (LLM 반복은 최대 3회)")
    for name, value in (("VERIFY_EFFORT", VERIFY_EFFORT), ("REVISE_EFFORT", REVISE_EFFORT), ("HITL_EFFORT", HITL_EFFORT)):
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
    if not isinstance(UTTERANCE_START, int) or UTTERANCE_START < 0:
        errors.append(f"UTTERANCE_START={UTTERANCE_START!r} — 0 이상 정수")
    for name, value in (("MAX_TRANSCRIPT_CHARS", MAX_TRANSCRIPT_CHARS), ("MAX_STEP_INPUT_CHARS", MAX_STEP_INPUT_CHARS)):
        if not isinstance(value, int) or value <= 0:
            errors.append(f"{name}={value!r} — 0보다 큰 정수")
    if isinstance(MAX_TRANSCRIPT_CHARS, int) and isinstance(MAX_STEP_INPUT_CHARS, int) and MAX_TRANSCRIPT_CHARS >= MAX_STEP_INPUT_CHARS:
        errors.append("MAX_TRANSCRIPT_CHARS는 MAX_STEP_INPUT_CHARS보다 작아야 함 (전사문 외에 지시 프롬프트 · 요약도 들어감)")
    if errors:
        raise ValueError("settings 설정 오류:\n  - " + "\n  - ".join(errors))


def snapshot() -> dict:
    # 실행 설정 기록용 (키 · 비용 정보 없음)
    return {"model": os.getenv("OPENAI_MODEL"), "reasoning_effort": REASONING_EFFORT,
            "verify_effort": VERIFY_EFFORT or REASONING_EFFORT, "revise_effort": REVISE_EFFORT or REASONING_EFFORT,
            "temperature": LLM_TEMPERATURE, "use_llm_cache": USE_LLM_CACHE,
            "max_verify_rounds": MAX_VERIFY_ROUNDS}
