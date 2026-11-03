"""요약 파이프라인 LangGraph main — 주제 경계 탐지 결과 JSON → 최종 회의록

[0] load_input            입력 JSON → 초기 State
[1] segment_summary       Segment별 1차 요약 (LLM)
[2] agenda_merge          안건 기준 1차 병합 (코드)
    ↓ 변형별 추가 단계 (config.VARIANT_STEPS) — agenda_split: [2-1] agenda_restructure 안건 재구성 · [2-2] item_consolidation 항목 정돈 (LLM)
[3][5] content_validation 내용 검증 · 재검증 (LLM)  ─┐ 이슈 있음 → [4] / 없음 · 최대 횟수 → [6]
[4] content_revise        특정 문장 재수정 (LLM)    ─┘ → [3][5]
[6][8] readability_validation 가독성 검증 · 재검증 (LLM) ─┐ 이슈 있음 → [7] / 없음 · 최대 횟수 → 구조 검증
[7] readability_revise    가독성 수정 (LLM)              ─┘ → [6][8]
[9] structure_validation  구조 검증 · 자동 보정 · 격리 (코드)
[10] render                최종 병합 · 회의록 렌더링 · 저장 (코드) → outputs/summary/{meeting_id}/

설정: config.py (실행 전 config.validate() · config.check_model()로 확인, API 키는 .env에만)

최종 결과 저장 (outputs/summary/{meeting_id}/)
    {meeting_id}_final_summary.json  최소 단위 JSON (config.SAVE_FINAL_JSON)
    {meeting_id}_final_minutes.txt   최종 회의록 구조의 텍스트 — 사람이 읽는 용도 (config.SAVE_MINUTES_TEXT)

단계별 출력 저장 (config.SAVE_STEPS=True일 때)
    outputs/summary/{meeting_id}/steps/{실행 시각}/00_config.json — 이 실행의 설정 (API 키 없음)
    outputs/summary/{meeting_id}/steps/{실행 시각}/{순번}_{노드 이름}.json — 노드가 State에 반영한 값 (루프는 실행마다 따로 저장)

실행
    python summary_graph_main.py                                   # config.DEFAULT_INPUTS 실행
    python summary_graph_main.py TS3003c                           # 회의 ID → config.BOUNDARY_DIR/TS3003c_boundary_candidates.json
    python summary_graph_main.py TS3003a TS3003b                   # 여러 회의 순서대로 실행
    python summary_graph_main.py path/to/x_boundary_candidates.json  # 파일 경로도 가능
    python summary_graph_main.py --max-segments 3                  # 앞 Segment 3개만 (비용 확인용)
    python summary_graph_main.py TS3003c --variant agenda_split    # 변형 실행 → outputs/summary/TS3003c/agenda_split/
    python summary_graph_main.py TS3003c --variant base agenda_split  # 두 변형 차례로 실행 (비교: compare_variants.py)
    python summary_graph_main.py --no-steps                        # 단계별 출력 저장 안 함
    python summary_graph_main.py --no-minutes-text                 # 회의록 txt 저장 안 함 (--no-save: 최종 결과 모두 저장 안 함)
노트북
    from summary_graph_main import run_summary
    state = run_summary("TS3003c")                                  # 회의 ID 또는 파일 경로
    state = run_summary("TS3003c", save_steps=False)                # 단계별 출력 저장 안 함
    state = run_summary("TS3003c", variant="agenda_split")          # 변형 실행
    import config; config.CONTENT_MAX_LOOPS = 2                     # 설정 변경은 config 값을 바꾼 뒤 실행
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

SUMMARY_DIR = Path(__file__).resolve().parent
if str(SUMMARY_DIR) not in sys.path:      # 다른 폴더에서 실행해도 schemas · node · prompt를 찾도록
    sys.path.insert(0, str(SUMMARY_DIR))

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

import config as cfg
import node.agenda_restructure as agenda_restructure
import node.content_validation as content_validation
import node.item_consolidation as item_consolidation
import node.readability_validation as readability_validation
import node.render as render
import node.structure_validation as structure_validation
from node.agenda_merge import agenda_merge_node
from node.content_revise import content_revise_node
from node.load_input import load_input
from node.readability_revise import readability_revise_node
from node.segment_summary import segment_summary_node
from schemas import SummaryState
from tracing import tracing_status

# 사용자 설정: config.py — DEFAULT_INPUTS · MAX_SEGMENTS · RECURSION_LIMIT · 저장(SAVE_*, STEP*) · 경로


# ── [0] 입력 노드 ─────────────────────────────────────
def load_input_node(state: SummaryState) -> dict:
    loaded = load_input(state["input_path"])
    if cfg.MAX_SEGMENTS is not None:
        loaded["segments"] = loaded["segments"][:cfg.MAX_SEGMENTS]
    print(f"[입력] {loaded['meeting_id']}: Segment {len(loaded['segments'])}개 / 발화 {len(loaded['utterance_texts'])}개")
    return loaded


# ── 그래프 ────────────────────────────────────────────
# 변형별 추가 단계 이름 → 노드 함수 ([2] 병합과 [3] 내용 검증 사이에 config.VARIANT_STEPS 순서대로 연결)
VARIANT_NODES = {
    "agenda_restructure": agenda_restructure.agenda_restructure_node,
    "item_consolidation": item_consolidation.item_consolidation_node,
}


def build_graph(variant: str | None = None):
    variant = variant or cfg.PIPELINE_VARIANT
    extra = cfg.VARIANT_STEPS[variant]
    graph = StateGraph(SummaryState)
    graph.add_node("load_input", load_input_node)
    graph.add_node("segment_summary", segment_summary_node)
    graph.add_node("agenda_merge", agenda_merge_node)
    graph.add_node("content_validation", content_validation.content_validation_node)
    graph.add_node("content_revise", content_revise_node)
    graph.add_node("readability_validation", readability_validation.readability_validation_node)
    graph.add_node("readability_revise", readability_revise_node)
    graph.add_node("structure_validation", structure_validation.structure_validation_node)
    graph.add_node("render", render.render_node)
    for name in extra:
        graph.add_node(name, VARIANT_NODES[name])

    graph.add_edge(START, "load_input")
    graph.add_edge("load_input", "segment_summary")
    graph.add_edge("segment_summary", "agenda_merge")
    chain = ["agenda_merge", *extra, "content_validation"]        # 변형 단계가 없으면 병합 → 내용 검증
    for a, b in zip(chain, chain[1:]):
        graph.add_edge(a, b)
    # 내용 검증 루프: 이슈 있음 → 재수정 → 재검증 / 없음 · 최대 횟수 → 가독성 검증
    graph.add_conditional_edges("content_validation", content_validation.route_after_content_validation,
                                [content_validation.REVISE_NODE, content_validation.NEXT_NODE])
    graph.add_edge("content_revise", "content_validation")
    # 가독성 검증 루프: 이슈 있음 → 수정 → 재검증 / 없음 · 최대 횟수 → 구조 검증
    graph.add_conditional_edges("readability_validation", readability_validation.route_after_readability_validation,
                                [readability_validation.REVISE_NODE, readability_validation.NEXT_NODE])
    graph.add_edge("readability_revise", "readability_validation")
    graph.add_edge("structure_validation", structure_validation.NEXT_NODE)
    graph.add_edge("render", END)
    return graph.compile()


# ── 단계별 출력 저장 ──────────────────────────────────
def to_jsonable(value):
    # Pydantic 모델 · dict · list를 JSON으로 저장할 수 있는 값으로 변환
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(v) for v in value]
    return value


class StepRecorder:
    """노드 실행 결과(State 업데이트)를 실행 순서대로 저장 — 저장 폴더는 첫 노드(load_input)에서 meeting_id를 받은 뒤 정함"""

    def __init__(self, run_id: str, variant: str | None = None):
        self.run_id = run_id
        self.variant = variant
        self.step = 0
        self.dir: Path | None = None

    def save(self, node_name: str, update: dict) -> Path:
        if self.dir is None:
            self.dir = cfg.output_dir(update["meeting_id"], self.variant) / cfg.STEPS_DIR_NAME / self.run_id
            self.dir.mkdir(parents=True, exist_ok=True)
            self._write(self.dir / "00_config.json", cfg.snapshot())      # 이 실행의 설정 (API 키 없음)
        self.step += 1
        path = self.dir / f"{self.step:02d}_{node_name}.json"
        self._write(path, {k: to_jsonable(v) for k, v in update.items() if k not in cfg.STEP_EXCLUDE_KEYS})
        return path

    @staticmethod
    def _write(path: Path, data: dict) -> None:
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)


# ── 실행 ──────────────────────────────────────────────
def resolve_input(name: str | Path) -> Path:
    # 파일 경로면 그대로, 아니면 회의 ID로 보고 config.BOUNDARY_DIR에서 찾음 (TS3003c / TS3003c_boundary_candidates / ….json)
    path = Path(name)
    if path.is_file():
        return path
    suffix = cfg.BOUNDARY_SUFFIX
    meeting_id = path.name.removesuffix(".json").removesuffix(suffix.removesuffix(".json"))
    candidate = cfg.BOUNDARY_DIR / f"{meeting_id}{suffix}"
    if candidate.is_file():
        return candidate
    available = sorted(p.name.removesuffix(suffix) for p in cfg.BOUNDARY_DIR.glob(f"*{suffix}"))
    raise FileNotFoundError(f"입력 파일 없음: {name} (찾은 위치: {candidate}) / 사용 가능한 회의 ID: {available}")


def run_summary(input_path: str | Path, graph=None, save_steps: bool | None = None,
                save_final: bool | None = None, save_minutes: bool | None = None,
                variant: str | None = None) -> SummaryState:
    # save_*가 None이면 config 값(SAVE_STEPS · SAVE_FINAL_JSON · SAVE_MINUTES_TEXT) 사용 — 이번 실행에만 적용
    # variant가 None이면 config.PIPELINE_VARIANT / graph를 넘길 때는 같은 variant로 만든 그래프여야 함
    cfg.validate()          # 설정값 범위 확인 (반복 상한 · 추론 강도 · 그래프 실행 상한 · 변형 이름 등)
    cfg.check_model()       # .env의 OPENAI_MODEL이 config.ALLOWED_MODELS에 있는지 확인
    variant = variant or cfg.PIPELINE_VARIANT
    if variant not in cfg.VARIANT_STEPS:
        raise ValueError(f"없는 변형: {variant} ({list(cfg.VARIANT_STEPS)})")
    input_path = resolve_input(input_path)
    graph = graph or build_graph(variant)
    overrides = {"SAVE_STEPS": save_steps, "SAVE_FINAL_JSON": save_final, "SAVE_MINUTES_TEXT": save_minutes}
    previous = {k: getattr(cfg, k) for k in overrides}
    for k, v in overrides.items():
        if v is not None:
            setattr(cfg, k, v)
    try:
        recorder = StepRecorder(datetime.now().strftime("%Y%m%d_%H%M%S"), variant) if cfg.SAVE_STEPS else None
        run_config = {"recursion_limit": cfg.RECURSION_LIMIT, "run_name": f"summary_{Path(input_path).stem}_{variant}"}
        start = time.perf_counter()

        state = None
        # updates: 노드별 State 업데이트 (단계별 저장용) / values: 노드 실행 후 전체 State (마지막 값이 최종 State)
        for mode, chunk in graph.stream({"input_path": str(input_path), "variant": variant}, config=run_config,
                                        stream_mode=["updates", "values"]):
            if mode == "values":
                state = chunk
            elif recorder is not None:
                for node_name, update in chunk.items():
                    if update:
                        recorder.save(node_name, update)
        if recorder is not None and recorder.dir is not None:
            print(f"[단계별 저장] 설정 + {recorder.step}개 → {recorder.dir}")
    finally:
        for k, v in previous.items():       # 이번 실행에서 바꾼 저장 설정 원래대로
            setattr(cfg, k, v)

    report = state["structure_report"]
    print(f"\n[완료] {state['meeting_id']} · {variant} ({time.perf_counter() - start:.1f}초) / "
          f"안건 {len(state['final_summary'].agenda_index)}개 / "
          f"내용 검증 {state.get('content_loop', 0)}회 · 가독성 검증 {state.get('readability_loop', 0)}회 / "
          f"구조 검증 {report.validation_status} (격리 {len(state.get('quarantine', []))}건)")
    return state


def main() -> None:
    parser = argparse.ArgumentParser(description="주제 경계 탐지 결과 JSON → 최종 회의록")
    parser.add_argument("inputs", nargs="*", help="회의 ID(ex. TS3003c) 또는 *_boundary_candidates.json 경로 (없으면 config.DEFAULT_INPUTS)")
    parser.add_argument("--max-segments", type=int, default=cfg.MAX_SEGMENTS, help="앞 Segment N개만 요약")
    parser.add_argument("--no-save", action="store_true", help="최종 결과(JSON · 회의록 txt)를 저장하지 않음")
    parser.add_argument("--no-minutes-text", action="store_true", help="회의록 txt를 저장하지 않음 (JSON만 저장)")
    parser.add_argument("--no-steps", action="store_true", help="단계별 출력을 저장하지 않음")
    parser.add_argument("--variant", nargs="+", default=[cfg.PIPELINE_VARIANT], choices=list(cfg.VARIANT_STEPS),
                        help=f"실행할 파이프라인 변형 (여러 개면 차례로, 기본: {cfg.PIPELINE_VARIANT})")
    args = parser.parse_args()

    cfg.MAX_SEGMENTS = args.max_segments
    print(tracing_status())

    failed = []
    for variant in args.variant:
        graph = build_graph(variant)
        for path in args.inputs or cfg.DEFAULT_INPUTS:
            print(f"\n{'=' * 60}\n{path} · {variant}\n{'=' * 60}")
            try:
                run_summary(path, graph, save_steps=cfg.SAVE_STEPS and not args.no_steps,
                            save_final=cfg.SAVE_FINAL_JSON and not args.no_save,
                            save_minutes=cfg.SAVE_MINUTES_TEXT and not args.no_save and not args.no_minutes_text,
                            variant=variant)
            except Exception as e:      # 한 회의가 실패해도 다음 회의는 계속 실행
                failed.append(f"{path} · {variant}")
                print(f"[실패] {path} · {variant}: {type(e).__name__}: {e}")
    if failed:
        print(f"\n실패한 실행 {len(failed)}개: {failed}")
        sys.exit(1)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
