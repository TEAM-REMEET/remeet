"""구조화된 요약문 생성 · 검증 · 재수정 — 실행 (LangGraph: graph.py)

[1]   생성 (LLM 1회): 회의 전체 전사문 → 안건별 구조화 요약 (액션은 주요 논의의 [액션][결정] 항목, ID 없음)
[1-1] 후처리: 근거 발화 검사 → agenda_id · subtopic_id · item_id 부여 → 안건 목차 · 회의 결과 (postprocess.py)
[2]   검증 (LLM): 문제 목록 (prompt/Verify_summary.py) — 문제 없으면 통과
[3]   재수정 (LLM): 문제 항목만 수정 (prompt/Revise_summary.py → revision.py) → 다시 [2]
      검증은 최대 config.MAX_VERIFY_ROUNDS(3)회, 마지막 검증도 실패하면 남은 문제 항목에 '검토 필요' 표시
저장 → outputs/summary_action/{회의 ID}/
    {회의 ID}_summary.json      최소 단위로 분리한 결과 (회의 정보 · 전사문 · 안건 · 항목 · 목차 · 회의 결과 · 검증 기록)
    {회의 ID}_minutes.md        저장된 summary JSON으로 만든 회의록 (render_md.py, JSON만 고친 뒤 md만 다시 생성 가능)
    {회의 ID}_minutes.txt       사람이 읽는 회의록 (텍스트)
    {회의 ID}_llm_output.json   생성 LLM 원본 출력 (ID 부여 전, 내부 확인용 — 코드가 붙인 A · B가 그대로 남음)

실행 (src/Summary_Action 폴더에서)
    python summary_action_main.py                       # config.DEFAULT_INPUTS
    python summary_action_main.py TS3003c TS3003d       # 회의 ID 여러 개 (또는 전사문 txt 경로)
    python summary_action_main.py TS3003c --no-save     # 저장 안 함
    python summary_action_main.py TS3003c --no-cache    # 캐시 무시하고 다시 호출
입력: config.INPUT_DIR/{회의 ID}.txt (dataset/Meeting_Input — 메타데이터 + 본문, 규칙은 transcript_parser.py)
노트북
    from summary_action_main import run
    summary = run("TS3003c")
    summary.find_item("TS3003c_a1_d1")
    summary.verification.rounds          # 회차별 검증 문제 · 재수정 내역

설정: config.py (경로 · 추론 강도 · 검증 횟수 · 캐시 · 저장 · 렌더링 · LangSmith), 모델 · 키: .env
LangSmith: .env에 LANGSMITH_API_KEY가 있으면 회의 하나당 trace 'summary_action_{회의 ID}' 기록 (그래프 노드 · LLM 호출 포함)
"""

import argparse
import sys
import time
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import config as cfg
from graph import GRAPH, RECURSION_LIMIT
from llm import api_call_count, reset_call_count
from schemas import MeetingSummary
from tracing import status as tracing_status


def resolve_input(name: str | Path) -> Path:
    # 회의 ID(ex. TS3003c) 또는 전사문 txt 경로
    path = Path(name)
    if path.suffix == cfg.INPUT_SUFFIX:
        if not path.is_file():
            raise FileNotFoundError(path)
        return path
    path = cfg.INPUT_DIR / f"{name}{cfg.INPUT_SUFFIX}"
    if not path.is_file():
        raise FileNotFoundError(f"입력 파일 없음: {path}")
    return path


def run(input_name: str | Path, save: bool = True, use_cache: bool | None = None) -> MeetingSummary:
    # use_cache: None이면 config.USE_LLM_CACHE
    cfg.validate()
    model = cfg.model_name()       # .env의 OPENAI_MODEL
    input_path = resolve_input(input_name)
    prev_cache = cfg.USE_LLM_CACHE
    if use_cache is not None:
        cfg.USE_LLM_CACHE = use_cache
    try:
        print(f"[설정] 모델 {model} · 추론 강도 {cfg.REASONING_EFFORT} · 검증 최대 {cfg.MAX_VERIFY_ROUNDS}회 · "
              f"API 호출 상한 {cfg.llm_call_plan_text()}회 · {tracing_status()}")
        start = time.perf_counter()
        reset_call_count()      # 회의마다 API 호출 수를 새로 셈 (상한: config.max_llm_calls())
        state = GRAPH.invoke(
            {"input_path": str(input_path), "save": save},
            config={"run_name": f"summary_action_{input_path.stem}", "recursion_limit": RECURSION_LIMIT,
                    "tags": list(cfg.LANGSMITH_TAGS),
                    "metadata": {"model": model, "reasoning_effort": cfg.REASONING_EFFORT, "input": input_path.name,
                                 "max_verify_rounds": cfg.MAX_VERIFY_ROUNDS}})
    finally:
        cfg.USE_LLM_CACHE = prev_cache

    summary: MeetingSummary = state["summary"]
    for e in summary.agenda_index:
        print(f"   {e.agenda_number}. {e.agenda_title} [결정 {e.decision_count} · 미해결 {e.unresolved_count} · 액션 {e.action_count}]")
    for entry in summary.postprocess_log:
        print(f"   - {entry.item_id or '-'}: {entry.message}")
    v = summary.verification
    print(f"[완료] {summary.meeting_id} · 검증 {v.status}" + (f" ({v.message})" if v.message else "")
          + f" · API 호출 {api_call_count()}회 ({time.perf_counter() - start:.1f}초)")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="구조화된 요약문 생성 · 검증 · 재수정")
    parser.add_argument("inputs", nargs="*", help=f"회의 ID 또는 전사문 txt 경로 (기본: {cfg.DEFAULT_INPUTS})")
    parser.add_argument("--no-save", action="store_true", help="결과를 저장하지 않음")
    parser.add_argument("--no-cache", action="store_true", help="LLM 캐시를 쓰지 않고 다시 호출")
    args = parser.parse_args()

    failed = []
    for name in args.inputs or cfg.DEFAULT_INPUTS:
        print(f"\n{'=' * 60}\n{name}\n{'=' * 60}")
        try:
            run(name, save=not args.no_save, use_cache=False if args.no_cache else None)
        except Exception as e:      # 한 회의가 실패해도 다음 회의는 계속 실행
            failed.append(str(name))
            print(f"[실패] {name}: {type(e).__name__}: {e}")
    if failed:
        print(f"\n실패한 입력 {len(failed)}개: {failed}")
        sys.exit(1)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
