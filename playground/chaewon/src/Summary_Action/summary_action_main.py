"""구조화된 요약문 생성 및 액션 아이템 추출 — 실행

[1]   LLM 1회: 회의 전체 전사문 → 안건별 구조화 요약 (액션은 주요 논의의 [액션][결정] 항목, ID 없음)
[1-1] 후처리: 근거 발화 검사 → agenda_id · subtopic_id · item_id 부여 → 안건 목차 · 회의 결과 (postprocess.py)
저장 → outputs/summary_action/{회의 ID}/
    {회의 ID}_summary.json      최소 단위로 분리한 결과 (회의 정보 · 전사문 · 안건 · 항목 · 목차 · 회의 결과)
    {회의 ID}_minutes.md        저장된 summary JSON으로 만든 회의록 (render_md.py, JSON만 고친 뒤 md만 다시 생성 가능)
    {회의 ID}_minutes.txt       사람이 읽는 회의록 (텍스트)
    {회의 ID}_llm_output.json   LLM 원본 출력 (ID 부여 전, 확인용)

실행 (src/Summary_Action 폴더에서)
    python summary_action_main.py                       # config.DEFAULT_INPUTS
    python summary_action_main.py TS3003c TS3003d       # 회의 ID 여러 개 (또는 전사문 txt 경로)
입력: config.INPUT_DIR/{회의 ID}.txt (dataset/Meeting_Input — 메타데이터 + '발화자: 발화문')
    python summary_action_main.py TS3003c --no-save     # 저장 안 함
    python summary_action_main.py TS3003c --no-cache    # 캐시 무시하고 다시 호출
노트북
    from summary_action_main import run
    summary = run("TS3003c")
    summary.find_item("TS3003c_a1_d1")

설정: config.py (경로 · 추론 강도 · 캐시 · 저장 · 렌더링 · LangSmith), 모델 · 키: .env
LangSmith: .env에 LANGSMITH_API_KEY가 있으면 회의 하나당 trace 'summary_action_{회의 ID}' 기록 (tracing.py)
"""

import argparse
import sys
import time
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import config as cfg
from llm import call_structured, reset_call_count
from postprocess import build_summary, load_input
from prompt.Extract_summary_action_item import SYSTEM_PROMPT, build_user_prompt
from render import save_outputs
from tracing import run_extra, status as tracing_status, traceable
from schemas import LLMMeetingSummary, MeetingSummary


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


@traceable(name="generate_summary")
def generate(loaded: dict) -> LLMMeetingSummary:
    utterances = [u.model_dump() for u in loaded["transcript"]]
    user_prompt = build_user_prompt(utterances, loaded["metadata"])
    return call_structured(SYSTEM_PROMPT, user_prompt, LLMMeetingSummary,
                           tag=f"{loaded['meeting_id']}_{cfg.CACHE_TAG}")


def _run_outputs(summary: MeetingSummary) -> dict:
    # LangSmith에 남길 run 결과 (전사문 · 항목 전체는 하위 run에 있으므로 요약 정보만)
    return {"meeting_id": summary.meeting_id, "agenda_index": [e.model_dump() for e in summary.agenda_index],
            "meeting_results": summary.meeting_results.model_dump(),
            "postprocess_log": [e.model_dump() for e in summary.postprocess_log]}


def run(input_name: str | Path, save: bool = True, use_cache: bool | None = None) -> MeetingSummary:
    cfg.validate()
    model = cfg.model_name()       # .env의 OPENAI_MODEL
    input_path = resolve_input(input_name)
    print(f"[설정] 모델 {model} · 추론 강도 {cfg.REASONING_EFFORT} · {tracing_status()}")
    run_name = f"summary_action_{input_path.stem}"
    metadata = {"model": model, "reasoning_effort": cfg.REASONING_EFFORT, "input": input_path.name}
    return _run(input_path, save, use_cache, **run_extra(run_name, metadata))


@traceable(name="summary_action_run", process_outputs=_run_outputs)
def _run(input_path: Path, save: bool, use_cache: bool | None) -> MeetingSummary:
    prev_cache = cfg.USE_LLM_CACHE
    if use_cache is not None:
        cfg.USE_LLM_CACHE = use_cache
    start = time.perf_counter()
    reset_call_count()      # 회의마다 API 호출 수를 새로 셈 (상한: config.MAX_LLM_CALLS_PER_MEETING)
    try:
        loaded = load_input(input_path)
        print(f"[입력] {loaded['meeting_id']}: 발화 {len(loaded['transcript'])}개 ({input_path.name})")
        for w in loaded["warnings"]:
            print(f"   - {w}")
        llm_out = generate(loaded)
        summary = build_summary(llm_out, loaded)
    finally:
        cfg.USE_LLM_CACHE = prev_cache

    items = [it for a in summary.agendas for it in a.iter_items()]
    r = summary.meeting_results
    print(f"[요약] 안건 {len(summary.agendas)}개 · 항목 {len(items)}개 "
          f"(결정 {len(r.decision_item_ids)} · 액션 {len(r.action_item_ids)} · 미해결 {len(r.unresolved_item_ids)} · "
          f"다음 회의 {len(r.next_topic_item_ids)}) · 검토 필요 {sum(it.status == 'REVIEW_REQUIRED' for it in items)}")
    for e in summary.agenda_index:
        print(f"   {e.agenda_number}. {e.agenda_title} [결정 {e.decision_count} · 미해결 {e.unresolved_count} · 액션 {e.action_count}]")
    for entry in summary.postprocess_log:
        print(f"   - {entry.item_id or '-'}: {entry.message}")
    if save:
        for path in save_outputs(summary, llm_out):
            print(f"[저장] {path}")
    print(f"[완료] {summary.meeting_id} ({time.perf_counter() - start:.1f}초)")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="구조화된 요약문 생성 및 액션 아이템 추출")
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
