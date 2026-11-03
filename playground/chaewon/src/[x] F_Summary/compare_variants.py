"""파이프라인 변형별 최종 회의록 비교 (LLM 호출 없음)

저장된 {변형 결과 폴더}/{meeting_id}_final_summary.json을 읽어
    ① 구조 지표: 안건 수 · 안건당 항목 수 · 소주제 수 · 섹션별 항목 수 · [정보] 수 · 회의록 길이 등
    ② 안건 목차: 변형별 목차 나란히
    ③ (--save) 지표 · 목차 · 변형별 회의록(원본 전사문 제외)을 한 txt로 저장 → outputs/summary/{meeting_id}/comparison_{변형들}.txt

실행
    python compare_variants.py TS3003c                         # config.VARIANT_STEPS의 모든 변형 중 결과가 있는 것
    python compare_variants.py TS3003c base agenda_split --save
노트북
    from compare_variants import compare
    rows = compare("TS3003c")          # pd.DataFrame(rows).set_index("지표")
"""

import argparse
import sys
import unicodedata
from pathlib import Path

SUMMARY_DIR = Path(__file__).resolve().parent
if str(SUMMARY_DIR) not in sys.path:
    sys.path.insert(0, str(SUMMARY_DIR))

import config as cfg
from node.render import render_agenda_index, render_minutes
from schemas import FinalSummary

MINUTES_BLOCKS = ("meeting_info", "agenda_index", "meeting_results", "agenda_details")    # 비교용 회의록 (전사문 제외)


def final_path(meeting_id: str, variant: str) -> Path:
    return cfg.output_dir(meeting_id, variant) / f"{meeting_id}_final_summary.json"


def load_final(meeting_id: str, variant: str) -> FinalSummary | None:
    path = final_path(meeting_id, variant)
    return FinalSummary.model_validate_json(path.read_text(encoding="utf-8")) if path.is_file() else None


def stats(final: FinalSummary) -> dict:
    details = final.agenda_details
    discussions = [i for d in details for g in d.major_discussions for i in g.items]
    per_agenda = [len(d.discussion_background) + sum(len(g.items) for g in d.major_discussions) + len(d.decisions)
                  + len(d.unresolved_issues) + len(d.next_meeting_topics) for d in details]
    every = [*discussions, *[i for d in details for i in (*d.discussion_background, *d.decisions,
                                                         *d.unresolved_issues, *d.next_meeting_topics)]]
    tag = lambda t: sum(1 for i in discussions if i.tag == t)
    return {
        "안건 수": len(details),
        "전체 항목 수": sum(per_agenda),
        "안건당 항목 수 (평균)": round(sum(per_agenda) / len(details), 1) if details else 0,
        "안건당 항목 수 (최대)": max(per_agenda, default=0),
        "소주제 수 (전체)": sum(len(d.major_discussions) for d in details),
        "소주제당 항목 수 (평균)": round(len(discussions) / max(1, sum(len(d.major_discussions) for d in details)), 1),
        "논의 배경": sum(len(d.discussion_background) for d in details),
        "주요 논의": len(discussions),
        "  [정보]": tag("정보"),
        "  [제안]": tag("제안"),
        "  [반대·우려]": tag("반대·우려"),
        "  [질문]": tag("질문"),
        "결정사항": sum(len(d.decisions) for d in details),
        "미해결 이슈": sum(len(d.unresolved_issues) for d in details),
        "다음 회의 후보 안건": sum(len(d.next_meeting_topics) for d in details),
        "검토 필요 항목": sum(i.review_required for i in every),
        "항목 평균 길이 (자)": round(sum(len(i.text) for i in every) / max(1, len(every)), 1),
        "회의록 길이 (자, 전사문 제외)": len(render_minutes(final, MINUTES_BLOCKS)),
    }


def display_width(text: str) -> int:
    # 한글 등 전각 문자는 화면에서 2칸
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def pad(text, width: int, right: bool = False) -> str:
    text = str(text)
    space = " " * max(0, width - display_width(text))
    return space + text if right else text + space


def format_table(rows: list[dict], variants: list[str]) -> str:
    width = max(display_width(r["지표"]) for r in rows) + 2
    col = max(14, *(display_width(v) + 2 for v in variants))
    lines = [pad("지표", width) + "".join(pad(v, col, True) for v in variants)]
    lines += [pad(r["지표"], width) + "".join(pad(r[v], col, True) for v in variants) for r in rows]
    return "\n".join(lines)


def compare(meeting_id: str, variants: list[str] | None = None, save: bool = False) -> list[dict]:
    variants = variants or list(cfg.VARIANT_STEPS)
    finals = {v: load_final(meeting_id, v) for v in variants}
    missing = [v for v, f in finals.items() if f is None]
    for v in missing:
        print(f"[비교] {v}: 결과 없음 ({final_path(meeting_id, v)})")
    finals = {v: f for v, f in finals.items() if f is not None}
    if not finals:
        return []
    variants = list(finals)

    table = {v: stats(f) for v, f in finals.items()}
    rows = [{"지표": k, **{v: table[v][k] for v in variants}} for k in next(iter(table.values()))]
    blocks = [f"# {meeting_id} 변형 비교 ({' vs '.join(variants)})", "", "## 구조 지표", format_table(rows, variants)]
    for v, f in finals.items():
        blocks += ["", f"## 안건 목차 — {v}", "\n".join(render_agenda_index(f)[1:])]
    print("\n".join(blocks))

    if save:
        text = "\n".join(blocks)
        for v, f in finals.items():
            text += f"\n\n{'=' * 70}\n## 최종 회의록 — {v}\n{'=' * 70}\n" + render_minutes(f, MINUTES_BLOCKS)
        path = cfg.OUTPUT_DIR / meeting_id / f"comparison_{'_vs_'.join(variants)}.txt"
        path.write_text(text, encoding="utf-8")
        print(f"\n[비교] 저장: {path}")
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="파이프라인 변형별 최종 회의록 비교")
    parser.add_argument("meeting_id")
    parser.add_argument("variants", nargs="*", help=f"비교할 변형 (기본: {list(cfg.VARIANT_STEPS)})")
    parser.add_argument("--save", action="store_true", help="비교 결과 txt 저장")
    args = parser.parse_args()
    compare(args.meeting_id, args.variants or None, args.save)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
