"""[10] 최종 병합 및 회의록 렌더링 (LLM 호출 없음)

구조 검증을 통과한 안건별 요약(agendas) → 최종 회의록
    ① build_final_summary: 최소 단위 JSON(FinalSummary)으로 병합
       - 회의 정보: metadata의 제목 · 일시 · 참석자 + 원본 전사문 전문(utterance_texts)
       - 안건 목차: 안건별 상세의 안건 제목 + 결정사항 · 미해결 이슈 개수
       - 회의 결과: 안건별 결정사항 · 미해결 이슈 · 다음 회의 후보 안건 취합 (안건 번호 표시)
       - 안건별 상세: 섹션별 항목, 주요 논의는 소주제별 묶음 ({안건 번호}-{n})
       삭제된(DELETED) 항목 · 표시할 항목이 없는 안건은 제외, REVIEW_REQUIRED 항목은 review_required=True로 표시
    ② render_*: 블록별 문자열 (회의 정보 · 안건 목차 · 회의 결과 · 안건별 상세 · 원본 전사문)
       render_minutes: 블록을 config.BLOCK_ORDER 순서로 합쳐 최종 회의록 문자열 구성 (순서 · 포함 여부 변경 가능)
    ③ save_final_summary: outputs/summary/{meeting_id}/{meeting_id}_final_summary.json 저장 (config.SAVE_FINAL_JSON=True일 때 노드에서 자동 저장)
                          outputs/summary/{meeting_id}/{meeting_id}_final_minutes.txt 저장 (config.SAVE_MINUTES_TEXT=True일 때)

노트북 확인용: build_final_summary(...) → render_*(final)로 블록별 확인 가능
LangGraph 노드: render_node(state) → 마지막 노드
"""

import os
from pathlib import Path

import config as cfg
from schemas import (SECTIONS, Agenda, AgendaDetail, AgendaIndexEntry, FinalItem, FinalSummary, MeetingInfo,
                     MeetingResultEntry, SourceDocument, SubtopicGroup, SummaryItem, SummaryState, Utterance)
from tracing import traceable

# 사용자 설정: config.py — META_KEYS · RESULT_SECTIONS · 저장(SAVE_*, OUTPUT_DIR) · 렌더링(BLOCK_ORDER, HIDE_EMPTY 등)

# ── 내부 상수 ────────────────────────────────────────
SECTION_LABEL = {
    "discussion_background": "논의 배경",
    "major_discussions": "주요 논의",
    "decisions": "결정사항",
    "unresolved_issues": "미해결 이슈",
    "next_meeting_topics": "다음 회의 후보 안건",
}
UNTAGGED_LABEL = {"unresolved_issues": "미해결"}     # 태그 없는 항목을 회의 결과에 표시할 때의 라벨


# ── ① 최소 단위 JSON ──────────────────────────────────
def meta_value(metadata: dict[str, str], field: str) -> str:
    return next((metadata[k] for k in cfg.META_KEYS[field] if metadata.get(k)), "")


def build_meeting_info(metadata: dict[str, str], utterance_texts: dict[int, str]) -> MeetingInfo:
    utterances = [Utterance(utterance_index=i, text=utterance_texts[i]) for i in sorted(utterance_texts)]
    return MeetingInfo(
        title=meta_value(metadata, "title"),
        date=meta_value(metadata, "date"),
        participants=meta_value(metadata, "participants"),
        source_document=SourceDocument(utterance_count=len(utterances), utterances=utterances,
                                       full_text="\n".join(u.text for u in utterances)),
    )


def to_final_item(item: SummaryItem) -> FinalItem:
    return FinalItem(item_id=item.item_id, tag=getattr(item, "tag", None), text=item.text,
                     evidence_utterance_indices=item.evidence_utterance_indices,
                     review_required=item.status == "REVIEW_REQUIRED")


def visible(agenda: Agenda, section: str) -> list[SummaryItem]:
    return [item for item in agenda.section(section) if item.status != "DELETED"]


def group_subtopics(agenda: Agenda, number: int) -> list[SubtopicGroup]:
    # 소주제가 처음 나온 순서대로 묶음
    groups: dict[str, list[FinalItem]] = {}
    for item in visible(agenda, "major_discussions"):
        groups.setdefault(item.subtopic.strip() or cfg.DEFAULT_SUBTOPIC, []).append(to_final_item(item))
    return [SubtopicGroup(number=f"{number}-{k}", subtopic=name, items=items)
            for k, (name, items) in enumerate(groups.items(), start=1)]


def build_final_summary(agendas: list[Agenda], meeting_id: str, metadata: dict[str, str],
                        utterance_texts: dict[int, str]) -> FinalSummary:
    shown = [a for a in agendas if any(True for _ in a.iter_items())]      # 표시할 항목이 있는 안건만
    index, results, details = [], [], []
    for number, agenda in enumerate(shown, start=1):
        index.append(AgendaIndexEntry(number=number, agenda_id=agenda.agenda_id, agenda_title=agenda.agenda_title,
                                      decision_count=len(visible(agenda, "decisions")),
                                      unresolved_count=len(visible(agenda, "unresolved_issues"))))
        details.append(AgendaDetail(
            number=number, agenda_id=agenda.agenda_id, agenda_title=agenda.agenda_title,
            discussion_background=[to_final_item(i) for i in visible(agenda, "discussion_background")],
            major_discussions=group_subtopics(agenda, number),
            decisions=[to_final_item(i) for i in visible(agenda, "decisions")],
            unresolved_issues=[to_final_item(i) for i in visible(agenda, "unresolved_issues")],
            next_meeting_topics=[to_final_item(i) for i in visible(agenda, "next_meeting_topics")],
        ))
    for section in cfg.RESULT_SECTIONS:                     # 섹션 순서 → 안건 순서
        for number, agenda in enumerate(shown, start=1):
            results += [MeetingResultEntry(**to_final_item(i).model_dump(), section=section,
                                           agenda_id=agenda.agenda_id, agenda_number=number)
                        for i in visible(agenda, section)]
    return FinalSummary(meeting_id=meeting_id, meeting_info=build_meeting_info(metadata, utterance_texts),
                        agenda_index=index, meeting_results=results, agenda_details=details)


# ── ② 블록별 문자열 ───────────────────────────────────
def item_line(item: FinalItem, label: str | None = None, suffix: str = "") -> str:
    # · [태그] 문장 (안건 번호) (근거 발화) (검토 필요)
    label = label if label is not None else item.tag
    parts = [f"· [{label}] {item.text}" if label else f"· {item.text}"]
    if suffix:
        parts.append(suffix)
    if cfg.SHOW_EVIDENCE:
        parts.append(f"(발화 {', '.join(map(str, item.evidence_utterance_indices))})")
    if cfg.SHOW_REVIEW_MARK and item.review_required:
        parts.append(cfg.REVIEW_MARK)
    return " ".join(parts)


def render_meeting_info(final: FinalSummary) -> list[str]:
    info = final.meeting_info
    source = f"원본 전사문 (발화 {info.source_document.utterance_count}개)"
    if "transcript" in cfg.BLOCK_ORDER:
        source += " — 하단 '원본 전사문' 참고"
    fields = [("제목", info.title), ("일시", info.date), ("참석자", info.participants), ("원본 문서", source)]
    return ["■ 회의 정보"] + [f"{cfg.INDENT}{name}: {value or cfg.EMPTY_TEXT}" for name, value in fields
                             if value or not cfg.HIDE_EMPTY]


def render_agenda_index(final: FinalSummary) -> list[str]:
    lines = ["■ 안건 목차"]
    lines += [f"{cfg.INDENT}{e.number}. {e.agenda_title} [결정 {e.decision_count} · 미해결 {e.unresolved_count}]"
              for e in final.agenda_index] or [f"{cfg.INDENT}{cfg.EMPTY_TEXT}"]
    return lines


def render_meeting_results(final: FinalSummary) -> list[str]:
    if cfg.HIDE_EMPTY and not final.meeting_results:
        return []
    lines = ["■ 회의 결과"]
    lines += [f"{cfg.INDENT}" + item_line(r, r.tag or UNTAGGED_LABEL.get(r.section), f"[안건{r.agenda_number}]")
              for r in final.meeting_results] or [f"{cfg.INDENT}{cfg.EMPTY_TEXT}"]
    return lines


def render_section(title: str, items: list[FinalItem]) -> list[str]:
    return [f"{cfg.INDENT}{title}"] + ([f"{cfg.INDENT * 2}{item_line(i)}" for i in items] or [f"{cfg.INDENT * 2}{cfg.EMPTY_TEXT}"])


def render_agenda_detail(detail: AgendaDetail) -> list[str]:
    lines = [f"{cfg.INDENT}{detail.number}. {detail.agenda_title}", ""]
    for section in SECTIONS:
        content = getattr(detail, section)
        if cfg.HIDE_EMPTY and not content:      # 항목이 없는 섹션은 제목까지 생략
            continue
        if section == "major_discussions":
            lines.append(f"{cfg.INDENT}{SECTION_LABEL[section]}")
            for group in content:
                lines.append(f"{cfg.INDENT * 2}{group.number}. {group.subtopic}")
                lines += [f"{cfg.INDENT * 2}{item_line(i)}" for i in group.items]
            if not content:
                lines.append(f"{cfg.INDENT * 2}{cfg.EMPTY_TEXT}")
        else:
            lines += render_section(SECTION_LABEL[section], content)
        lines.append("")
    return lines


def render_agenda_details(final: FinalSummary) -> list[str]:
    lines = ["■ 안건별 상세", ""]
    for detail in final.agenda_details:
        lines += render_agenda_detail(detail)
    while lines and lines[-1] == "":
        lines.pop()
    return lines


def render_transcript(final: FinalSummary) -> list[str]:
    return ["■ 원본 전사문"] + [f"{cfg.INDENT}[{u.utterance_index}] {u.text}" for u in final.meeting_info.source_document.utterances]


RENDERERS = {
    "meeting_info": render_meeting_info,
    "agenda_index": render_agenda_index,
    "meeting_results": render_meeting_results,
    "agenda_details": render_agenda_details,
    "transcript": render_transcript,
}


def render_minutes(final: FinalSummary, block_order: tuple[str, ...] | None = None) -> str:
    # 블록 사이는 빈 줄 하나 / block_order가 None이면 config.BLOCK_ORDER
    blocks = [RENDERERS[name](final) for name in (block_order or cfg.BLOCK_ORDER)]
    return "\n\n".join("\n".join(lines) for lines in blocks if lines) + "\n"      # 빈 블록(HIDE_EMPTY로 생략)은 제외


# ── ③ 저장 ────────────────────────────────────────────
def _write(path: Path, text: str) -> None:
    # 임시 파일에 쓴 뒤 교체 (중간에 실패해도 기존 파일이 깨지지 않음)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def save_final_summary(final: FinalSummary, minutes: str | None = None, out_dir: Path | None = None,
                       save_json: bool = True, variant: str | None = None) -> list[Path]:
    # out_dir를 주면 그 아래 회의 ID 폴더, 아니면 config.output_dir(회의 ID, 변형)에 저장 — JSON (save_json) / 회의록 txt
    out_dir = Path(out_dir) / final.meeting_id if out_dir else cfg.output_dir(final.meeting_id, variant)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    if save_json:
        paths.append(out_dir / f"{final.meeting_id}_final_summary.json")
        _write(paths[-1], final.model_dump_json(indent=2))
    if minutes is not None:
        paths.append(out_dir / f"{final.meeting_id}_final_minutes.txt")
        _write(paths[-1], minutes)
    return paths


# ── LangGraph 노드 ────────────────────────────────────
@traceable(name="render_node")
def render_node(state: SummaryState) -> dict:
    final = build_final_summary(state["agendas"], state["meeting_id"], state.get("metadata", {}), state["utterance_texts"])
    review = sum(i.review_required for d in final.agenda_details for i in
                 [*d.discussion_background, *[x for g in d.major_discussions for x in g.items],
                  *d.decisions, *d.unresolved_issues, *d.next_meeting_topics])
    print(f"[렌더링] 안건 {len(final.agenda_index)}개 / 회의 결과 {len(final.meeting_results)}개 / 검토 필요 항목 {review}개")
    minutes = render_minutes(final)
    if cfg.SAVE_FINAL_JSON or cfg.SAVE_MINUTES_TEXT:
        for path in save_final_summary(final, minutes if cfg.SAVE_MINUTES_TEXT else None, save_json=cfg.SAVE_FINAL_JSON,
                                       variant=state.get("variant")):
            print(f"[렌더링] 저장: {path}")
    return {"final_summary": final, "final_markdown": minutes}
