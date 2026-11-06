"""사용자 수정(HITL) 반영 — 실행 (LangGraph: hitl_graph.py, 수정 목록 형식: hitl.py)

입력    {OUTPUT_DIR}/{회의 ID}/{회의 ID}_summary.json       요약 · 액션 추출 결과 (변경하지 않음)
        {OUTPUT_DIR}/{회의 ID}/{회의 ID}_hitl_edits.json    사용자 수정 목록 (--edits로 다른 경로 지정)
저장    {회의 ID}_summary_final.json        다음 작업의 입력 (근거 연결 · 검증 기록은 hitl)
        {회의 ID}_minutes_final.md · .txt   사용자용 회의록 ('검토 필요' 표시 없음)

여러 번 수정해도 원본 summary.json에서 다시 시작하므로, 수정 목록에는 지금까지의 수정을 모두 적음

실행 (src/Summary_Action 폴더에서)
    python hitl_main.py TS3003c
    python hitl_main.py TS3003c --edits path/to/edits.json
    python hitl_main.py TS3003c --no-save       # 저장 안 함
    python hitl_main.py TS3003c --no-cache      # 캐시 무시하고 다시 호출
"""

import argparse
import sys
import time
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import config as cfg
from hitl_graph import GRAPH, RECURSION_LIMIT
from llm import api_call_count, reset_call_count
from schemas import MeetingSummary


def run_hitl(meeting_id: str, edits_path: str | Path | None = None, summary_path: str | Path | None = None,
             save: bool = True, use_cache: bool | None = None, out_dir: str | Path | None = None) -> MeetingSummary:
    cfg.validate()
    folder = cfg.summary_dir(meeting_id)
    summary_path = Path(summary_path) if summary_path else folder / f"{meeting_id}_summary.json"
    edits_path = Path(edits_path) if edits_path else folder / f"{meeting_id}_hitl_edits.json"
    for path in (summary_path, edits_path):
        if not path.is_file():
            raise FileNotFoundError(f"입력 파일 없음: {path}")
    prev_cache = cfg.USE_LLM_CACHE
    if use_cache is not None:
        cfg.USE_LLM_CACHE = use_cache
    try:
        print(f"[설정] 모델 {cfg.model_name()} · 근거 검증 최대 {cfg.HITL_MAX_VERIFY_ROUNDS}회 · "
              f"API 호출 상한 {cfg.hitl_max_llm_calls()}회")
        start = time.perf_counter()
        reset_call_count(cfg.hitl_max_llm_calls())      # 요약 · 액션 추출의 run()은 기본 상한으로 되돌림
        state = GRAPH.invoke({"summary_path": str(summary_path), "edits_path": str(edits_path), "save": save,
                              "out_dir": str(out_dir) if out_dir else None},
                             config={"run_name": f"summary_action_hitl_{meeting_id}", "recursion_limit": RECURSION_LIMIT,
                                     "tags": list(cfg.LANGSMITH_TAGS) + ["hitl"]})
    finally:
        cfg.USE_LLM_CACHE = prev_cache
    print(f"[완료] {meeting_id} · API 호출 {api_call_count()}회 ({time.perf_counter() - start:.1f}초)")
    return state["summary"]


def main() -> None:
    parser = argparse.ArgumentParser(description="사용자 수정 반영 · 근거 발화 재연결 · 검증")
    parser.add_argument("meeting_id", help="회의 ID (ex. TS3003c)")
    parser.add_argument("--edits", help="사용자 수정 목록 경로 (기본: 결과 폴더의 {회의 ID}_hitl_edits.json)")
    parser.add_argument("--no-save", action="store_true", help="결과를 저장하지 않음")
    parser.add_argument("--no-cache", action="store_true", help="LLM 캐시를 쓰지 않고 다시 호출")
    args = parser.parse_args()
    try:
        run_hitl(args.meeting_id, args.edits, save=not args.no_save, use_cache=False if args.no_cache else None)
    except Exception as e:
        print(f"[실패] {args.meeting_id}: {type(e).__name__}: {e}")
        sys.exit(1)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
