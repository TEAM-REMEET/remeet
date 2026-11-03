"""요약 파이프라인 실행 설정 — 사용자가 조정하는 값을 한곳에서 관리

원칙
    - 비밀값(API 키)은 여기에 두지 않음 → .env에만 두고 OpenAI · LangSmith 클라이언트가 환경 변수에서 직접 읽음
      (이 모듈은 키 값을 읽거나 저장하지 않으므로, 설정을 출력 · 저장해도 키가 노출되지 않음)
    - 모델 이름은 .env의 OPENAI_MODEL, 사용할 수 있는 모델은 ALLOWED_MODELS로 제한
    - 실행 전 validate()로 값의 범위를 확인 (LLM 반복 상한 3회, 추론 강도, 그래프 실행 상한 등)
    - 각 노드는 import config as cfg 후 cfg.값으로 실행 시점에 읽음
      → 노트북에서 cfg.CONTENT_MAX_LOOPS = 2처럼 바꾸면 그 커널에서 바로 반영
    - 노드 이름 · 섹션 라벨 · 정규식 등 코드 구조에 해당하는 값은 각 노드 파일에 둠

구성
    0. 파이프라인 변형   1. 경로   2. 모델 · LLM 호출   3. 단계별 설정   4. 그래프 실행   5. 저장   6. 렌더링   7. 검증 · 기록
"""

import os
from pathlib import Path

# ══════════════════════════════════════════════════════════════════════
# 0. 파이프라인 변형 — 같은 노드를 공유하고, 변형별로 [2] 병합과 [3] 내용 검증 사이에 단계를 추가
#    결과 저장: base → {OUTPUT_DIR}/{meeting_id}/ / 그 외 → {OUTPUT_DIR}/{meeting_id}/{변형 이름}/
#    1차 요약 캐시는 변형끼리 공유 (같은 입력 · 프롬프트), 이후 단계는 입력이 달라 캐시가 따로 생김
# ══════════════════════════════════════════════════════════════════════
VARIANT_STEPS: dict[str, tuple[str, ...]] = {
    "base": (),                                                     # 기존 프로세스
    "agenda_split": ("agenda_restructure", "item_consolidation"),   # 1안: 병합 후 안건 재구성 + 항목 정돈
}
PIPELINE_VARIANT = "base"            # 기본 실행 변형 (명령줄 --variant · run_summary(variant=...)로 바꿀 수 있음)

# ══════════════════════════════════════════════════════════════════════
# 1. 경로
# ══════════════════════════════════════════════════════════════════════
SUMMARY_DIR = Path(__file__).resolve().parent                   # playground/chaewon/src/Summary
PROJECT_DIR = SUMMARY_DIR.parents[1]                            # playground/chaewon
ENV_PATH = next((p / ".env" for p in SUMMARY_DIR.parents if (p / ".env").is_file()), None)   # 상위 폴더에서 .env 탐색

BOUNDARY_DIR = PROJECT_DIR / "dataset" / "Text_Boundaries"      # 주제 경계 탐지 결과 JSON 폴더
BOUNDARY_SUFFIX = "_boundary_candidates.json"                   # 회의 ID → {BOUNDARY_DIR}/{회의 ID}{BOUNDARY_SUFFIX}
OUTPUT_DIR = PROJECT_DIR / "outputs" / "summary"                # 최종 결과 → output_dir(meeting_id, variant)
LLM_CACHE_DIR = PROJECT_DIR / "outputs" / "llm_cache"           # LLM 응답 캐시

# ══════════════════════════════════════════════════════════════════════
# 2. 모델 · LLM 호출 (모델 이름은 .env의 OPENAI_MODEL)
# ══════════════════════════════════════════════════════════════════════
ALLOWED_MODELS = {"gpt-6-luna"}      # 사용할 수 있는 모델 — .env의 OPENAI_MODEL이 여기 없으면 실행 중단
ALLOWED_EFFORTS = {"low", "medium", "high"}
LLM_REASONING_EFFORT = "low"         # 기본 추론 강도 / None이면 모델 기본값
LLM_TEMPERATURE = None               # None이면 모델 기본값 (temperature를 지원하지 않는 모델 대비)
LLM_MAX_RETRIES = 3                  # 네트워크 · 일시 오류 재시도 횟수 (호출 1건당)
LLM_TIMEOUT = 180                    # 호출 1건당 대기 시간 (초)
USE_LLM_CACHE = True                 # 같은 입력(모델 · 추론 강도 · 프롬프트 · 스키마)이면 저장된 응답 사용

# 단계별 추론 강도 (None이면 LLM_REASONING_EFFORT)
STEP_EFFORT: dict[str, str | None] = {
    "segment_summary": None,         # [1] 1차 요약
    "agenda_restructure": None,      # [2-1] 안건 재구성 (agenda_split)
    "item_consolidation": None,      # [2-2] 항목 정돈 (agenda_split)
    "content_validation": None,      # [3][5] 내용 검증 · 재검증
    "content_revise": None,          # [4] 재수정
    "readability_validation": None,  # [6][8] 가독성 검증 · 재검증
    "readability_revise": None,      # [7] 가독성 수정
}

LANGSMITH_PROJECT = "REMEET"         # LangSmith 프로젝트 이름 (.env에 LANGSMITH_PROJECT가 있으면 그 값 사용)

# ══════════════════════════════════════════════════════════════════════
# 3. 단계별 설정
# ══════════════════════════════════════════════════════════════════════
MAX_LLM_LOOPS = 3                    # LLM 검증 · 수정 반복 상한 — 아래 *_MAX_LOOPS는 이 값을 넘을 수 없음

# [1] 1차 요약
REQUIRE_CORE_EVIDENCE = True         # 근거에 Core 발화가 하나도 없는 항목(Overlap만 근거)은 제외

# [2] 안건 병합
REMOVE_EXACT_DUPLICATES = True       # 같은 섹션 · 태그 · 근거 발화 집합인 항목 중복 제거 (False면 그룹화만)

# [2-1] 안건 재구성 (agenda_split) — 큰 범주 안건을 '안건 목록으로 흐름 · 핵심 안건을 파악할 수 있는' 단위로 나눔
RESTRUCTURE_MAX_TOTAL_AGENDAS = 3    # 병합 결과 안건이 이 개수 이하일 때만 재구성
RESTRUCTURE_MIN_SUBTOPICS = 4        # 소주제가 이 개수 이상이거나
RESTRUCTURE_MIN_ITEMS = 12           # 표시 항목이 이 개수 이상인 안건만 재구성
RESTRUCTURE_TARGET_AGENDAS = (4, 8)  # 회의 전체 목표 안건 수 (재구성 대상 안건에 항목 수 비율로 나눠 LLM에 안내)
RESTRUCTURE_MAX_AGENDAS = 10         # 안건 하나를 나눌 수 있는 최대 개수 (넘으면 재구성하지 않음)
SORT_AGENDAS_BY_FLOW = True          # 재구성 후 전체 안건을 처음 논의된 순서(첫 근거 발화)로 정렬

# [2-2] 항목 정돈 (agenda_split) — 같은 섹션의 비슷한 항목 병합, 배경 성격 [정보] → 논의 배경, 소주제 정리
CONSOLIDATE_MIN_ITEMS = 3            # 표시 항목이 이 개수 이상인 안건만 정돈
CONSOLIDATE_ALLOW_REMOVE = True      # 사소한 항목 제거 허용 (결정사항 · 미해결 이슈 · 다음 회의 후보 안건은 제거 안 함)

# [3]~[5] 내용 검증 · 재수정
CONTENT_MAX_LOOPS = 3                # 내용 검증 최대 횟수 (첫 검증 포함) — 이 횟수에서도 이슈가 남으면 REVIEW_REQUIRED
INCLUDE_OVERLAP = True               # 원본 발화에 안건이 나온 Segment의 오버랩 발화까지 포함

# [6]~[8] 가독성 검증 · 수정
READABILITY_MAX_LOOPS = 3            # 가독성 검증 최대 횟수 (첫 검증 포함) — 이 횟수의 검증에서 나온 이슈는 수정하지 않음
EVIDENCE_CONTEXT = 0                 # 근거 발화 앞뒤로 함께 보여줄 발화 수 (0이면 근거 발화만)
CHECK_NUMBERS = True                 # 수정 전후 숫자가 다르면 반영 안 함
MAX_LENGTH_RATIO = 2.0               # 수정 문장 길이 상한 (현재 문장 대비 배수) / None이면 확인 안 함

# 구조 검증
STRUCTURE_MAX_RETRIES = 2            # 자동 보정 후 재검증 최대 횟수

# ══════════════════════════════════════════════════════════════════════
# 4. 그래프 실행
# ══════════════════════════════════════════════════════════════════════
DEFAULT_INPUTS = ["TS3003c"]         # 인자 없이 실행할 때의 입력 (회의 ID 또는 파일 경로)
MAX_SEGMENTS = None                  # 앞 Segment N개만 요약 (테스트 · 비용 확인용) / None이면 전체
RECURSION_LIMIT = 100                # LangGraph 노드 실행 횟수 상한 (정상 최대: required_recursion_limit())

# ══════════════════════════════════════════════════════════════════════
# 5. 저장
# ══════════════════════════════════════════════════════════════════════
SAVE_FINAL_JSON = True               # {meeting_id}_final_summary.json
SAVE_MINUTES_TEXT = True             # {meeting_id}_final_minutes.txt (최종 회의록 구조, 블록 구성은 BLOCK_ORDER)
SAVE_STEPS = True                    # 노드마다 State에 반영한 값을 steps/{실행 시각}/에 저장 (실행 설정도 함께 기록)
STEPS_DIR_NAME = "steps"
STEP_EXCLUDE_KEYS = {"utterance_texts"}     # 단계별 저장에서 뺄 State 키 (segments와 중복 · 용량 큼)

# ══════════════════════════════════════════════════════════════════════
# 6. 렌더링 (최종 회의록 문자열)
# ══════════════════════════════════════════════════════════════════════
RENDER_BLOCKS = ("meeting_info", "agenda_index", "meeting_results", "agenda_details", "transcript")
BLOCK_ORDER = ("meeting_info", "agenda_index", "meeting_results", "agenda_details", "transcript")   # 순서 · 포함 여부
META_KEYS: dict[str, list[str]] = {  # 회의 정보 필드 → metadata 키 (앞에서부터 처음 있는 키 사용)
    "title": ["회의 대주제", "제목", "title"],
    "date": ["일시", "date"],
    "participants": ["참여자", "참석자", "participants"],
}
RESULT_SECTIONS = ("decisions", "unresolved_issues", "next_meeting_topics")     # 회의 결과에 취합할 섹션 (순서대로)
DEFAULT_SUBTOPIC = "기타"            # 소주제가 비어 있는 주요 논의 항목의 묶음 이름
SHOW_EVIDENCE = False                # 항목 끝에 근거 발화 번호 표시 (문서 기준: 화면에는 표시 안 함)
SHOW_REVIEW_MARK = True              # REVIEW_REQUIRED 항목 끝에 REVIEW_MARK 표시
REVIEW_MARK = "(검토 필요)"
HIDE_EMPTY = True                    # 값이 없는 섹션 · 회의 결과 블록 · 회의 정보 필드는 출력하지 않음
EMPTY_TEXT = "(없음)"                # HIDE_EMPTY=False일 때 값이 없는 곳에 표시
INDENT = "  "


# ══════════════════════════════════════════════════════════════════════
# 7. 검증 · 기록
# ══════════════════════════════════════════════════════════════════════
_SECRET_WORDS = ("KEY", "SECRET", "TOKEN", "PASSWORD")


def effort_for(step: str) -> str | None:
    return STEP_EFFORT.get(step) or LLM_REASONING_EFFORT


def required_recursion_limit() -> int:
    # 정상 흐름의 최대 노드 실행 수: 입력 · 1차 요약 · 병합 + 내용 루프(검증 N · 수정 N-1) + 가독성 루프 + 구조 검증 · 렌더링
    longest = max(len(steps) for steps in VARIANT_STEPS.values())
    return 3 + longest + (2 * CONTENT_MAX_LOOPS - 1) + (2 * READABILITY_MAX_LOOPS - 1) + 2


def output_dir(meeting_id: str, variant: str | None = None) -> Path:
    # base는 기존 위치 그대로, 다른 변형은 회의 폴더 아래 변형 이름 폴더
    variant = variant or PIPELINE_VARIANT
    return OUTPUT_DIR / meeting_id if variant == "base" else OUTPUT_DIR / meeting_id / variant


def check_model(model: str | None = None) -> str:
    # .env의 OPENAI_MODEL이 허용 모델인지 확인 (모델 이름만 읽음, 키는 읽지 않음)
    model = model if model is not None else os.getenv("OPENAI_MODEL")
    if not model:
        raise RuntimeError(f"OPENAI_MODEL이 설정되지 않음 (.env 위치: {ENV_PATH})")
    if model not in ALLOWED_MODELS:
        raise RuntimeError(f"허용되지 않은 모델: {model} (config.ALLOWED_MODELS: {sorted(ALLOWED_MODELS)})")
    return model


def validate() -> None:
    # 실행 전 설정값 범위 확인 — 문제가 있으면 모두 모아 한 번에 알림
    errors = []
    for name in ("CONTENT_MAX_LOOPS", "READABILITY_MAX_LOOPS"):
        value = globals()[name]
        if not isinstance(value, int) or not 1 <= value <= MAX_LLM_LOOPS:
            errors.append(f"{name}={value!r} — 1~{MAX_LLM_LOOPS} 사이 정수여야 함")
    for step, effort in {"LLM_REASONING_EFFORT": LLM_REASONING_EFFORT, **STEP_EFFORT}.items():
        if effort is not None and effort not in ALLOWED_EFFORTS:
            errors.append(f"추론 강도 {step}={effort!r} — {sorted(ALLOWED_EFFORTS)} 또는 None")
    if not isinstance(RECURSION_LIMIT, int) or RECURSION_LIMIT < required_recursion_limit():
        errors.append(f"RECURSION_LIMIT={RECURSION_LIMIT!r} — 정상 흐름의 최대 노드 실행 수({required_recursion_limit()}) 이상이어야 함")
    if MAX_SEGMENTS is not None and (not isinstance(MAX_SEGMENTS, int) or MAX_SEGMENTS < 1):
        errors.append(f"MAX_SEGMENTS={MAX_SEGMENTS!r} — 1 이상 정수 또는 None")
    if not isinstance(LLM_MAX_RETRIES, int) or not 0 <= LLM_MAX_RETRIES <= 5:
        errors.append(f"LLM_MAX_RETRIES={LLM_MAX_RETRIES!r} — 0~5")
    if not isinstance(STRUCTURE_MAX_RETRIES, int) or not 0 <= STRUCTURE_MAX_RETRIES <= 5:
        errors.append(f"STRUCTURE_MAX_RETRIES={STRUCTURE_MAX_RETRIES!r} — 0~5")
    if not isinstance(EVIDENCE_CONTEXT, int) or EVIDENCE_CONTEXT < 0:
        errors.append(f"EVIDENCE_CONTEXT={EVIDENCE_CONTEXT!r} — 0 이상 정수")
    if MAX_LENGTH_RATIO is not None and MAX_LENGTH_RATIO <= 1:
        errors.append(f"MAX_LENGTH_RATIO={MAX_LENGTH_RATIO!r} — 1보다 크거나 None")
    unknown = [b for b in BLOCK_ORDER if b not in RENDER_BLOCKS]
    if unknown:
        errors.append(f"BLOCK_ORDER에 없는 블록 {unknown} — {list(RENDER_BLOCKS)} 중에서 선택")
    if PIPELINE_VARIANT not in VARIANT_STEPS:
        errors.append(f"PIPELINE_VARIANT={PIPELINE_VARIANT!r} — {list(VARIANT_STEPS)} 중에서 선택")
    known_steps = {"agenda_restructure", "item_consolidation"}
    for name, steps in VARIANT_STEPS.items():
        if set(steps) - known_steps:
            errors.append(f"VARIANT_STEPS[{name!r}]에 없는 단계 {sorted(set(steps) - known_steps)} — {sorted(known_steps)}")
    if not 1 <= RESTRUCTURE_TARGET_AGENDAS[0] <= RESTRUCTURE_TARGET_AGENDAS[1] <= RESTRUCTURE_MAX_AGENDAS:
        errors.append(f"RESTRUCTURE_TARGET_AGENDAS={RESTRUCTURE_TARGET_AGENDAS} — 1 ≤ 최소 ≤ 최대 ≤ RESTRUCTURE_MAX_AGENDAS")
    if errors:
        raise ValueError("설정 오류 (config.py)\n  - " + "\n  - ".join(errors))


def snapshot() -> dict:
    # 실행 기록용 현재 설정 (대문자 설정값만, 비밀값으로 보이는 이름 제외, 경로는 문자열)
    def plain(v):
        if isinstance(v, Path):
            return str(v)
        if isinstance(v, (set, tuple)):
            return [plain(x) for x in sorted(v, key=str)] if isinstance(v, set) else [plain(x) for x in v]
        if isinstance(v, dict):
            return {str(k): plain(x) for k, x in v.items()}
        return v
    values = {k: plain(v) for k, v in globals().items()
              if k.isupper() and not k.startswith("_") and not any(w in k for w in _SECRET_WORDS)}
    values["OPENAI_MODEL"] = os.getenv("OPENAI_MODEL")     # 모델 이름 (비밀값 아님)
    return values
