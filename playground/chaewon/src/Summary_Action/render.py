"""회의록 렌더링 · 저장 (MeetingSummary → 문자열)

블록: 회의 정보 / 안건 목차 / 회의 결과 / 안건별 상세 / 원본 전사문 (config.SHOW_TRANSCRIPT)
항목 한 줄: [논의 종류][결정사항] 요약문 [근거 발화 …] — 값은 JSON에 분리 저장, 여기서만 조합
"""

import json
from pathlib import Path

import config as cfg
from render_md import markdown_from_json, participants_text, speaker_text
from schemas import BaseItem, DiscussionItem, MeetingSummary


def _alive(items: list[BaseItem]) -> list[BaseItem]:
    return [it for it in items if it.status != "DELETED"]


def format_item(item: BaseItem) -> str:
    tags = ""
    if isinstance(item, DiscussionItem):
        tags = f"[{item.discussion_tag}]" + (f"[{item.decision_tag}]" if item.decision_tag else "")
    elif getattr(item, "tag", None):
        tags = f"[{item.tag}]"
    parts = [f"{item.item_id} | " if cfg.SHOW_ITEM_ID else "", f"{tags} " if tags else "", item.text]
    if isinstance(item, DiscussionItem) and item.is_action:
        parts.append(f" (담당: {item.assignee or '-'} · 기한: {item.due or '-'})")
    if cfg.SHOW_EVIDENCE:
        parts.append(f" [근거 발화 {', '.join(map(str, item.evidence_utterance_indices)) or '-'}]")
    if item.status == "REVIEW_REQUIRED":
        parts.append(cfg.REVIEW_MARK)
    return "· " + "".join(parts)


def render_meeting_info(s: MeetingSummary) -> list[str]:
    info, i = s.meeting_info, cfg.INDENT
    when = " ".join(x for x in (info.date, f"({info.duration})" if info.duration else None) if x)
    people = participants_text(info)
    src = s.source_document
    return ["■ 회의 정보",
            f"{i}제목: {info.title or '-'}",
            f"{i}일시: {when or '-'}",
            f"{i}참석자: {people or '-'}",
            f"{i}원본 문서: {Path(src.input_path).name} (발화 {src.utterance_count}개)"]


def render_agenda_index(s: MeetingSummary) -> list[str]:
    return ["■ 안건 목차"] + [
        f"{cfg.INDENT}{e.agenda_number}. {e.agenda_title} [결정 {e.decision_count} · 미해결 {e.unresolved_count}"
        + (f" · 액션 {e.action_count}]" if e.action_count else "]") for e in s.agenda_index]


def render_meeting_results(s: MeetingSummary) -> list[str]:
    r, i = s.meeting_results, cfg.INDENT
    lines = ["■ 회의 결과"]
    for label, ids in (("결정사항", r.decision_item_ids), ("액션 아이템", r.action_item_ids),
                       ("미해결 이슈", r.unresolved_item_ids), ("다음 회의 후보 안건", r.next_topic_item_ids)):
        if not ids and cfg.HIDE_EMPTY:
            continue
        lines.append(f"{i}{label}")
        for item_id in ids or []:
            item, agenda = s.find_item(item_id), s.agenda_of(item_id)
            lines.append(f"{i * 2}{format_item(item)} [안건{agenda.agenda_number}]")
        if not ids:
            lines.append(f"{i * 2}{cfg.EMPTY_TEXT}")
    return lines


def render_agenda_details(s: MeetingSummary) -> list[str]:
    i = cfg.INDENT
    lines = ["■ 안건별 상세"]
    for a in s.agendas:
        lines += ["", f"{i}{a.agenda_number}. {a.agenda_title}"]
        background = _alive(a.discussion_background)
        if background or not cfg.HIDE_EMPTY:
            lines.append(f"{i * 2}논의 배경")
            lines += [f"{i * 3}{format_item(it)}" for it in background] or [f"{i * 3}{cfg.EMPTY_TEXT}"]

        by_id = {it.item_id: it for it in _alive(a.major_discussions)}
        groups = [(st, [by_id[x] for x in st.item_ids if x in by_id]) for st in a.subtopics]
        groups = [(st, items) for st, items in groups if items]
        if groups or not cfg.HIDE_EMPTY:
            lines.append(f"{i * 2}주요 논의")
            for n, (st, items) in enumerate(groups, 1):
                lines.append(f"{i * 3}{a.agenda_number}-{n}. {st.title}")
                lines += [f"{i * 4}{format_item(it)}" for it in items]
            if not groups:
                lines.append(f"{i * 3}{cfg.EMPTY_TEXT}")

        for name, label in (("unresolved_issues", "미해결 이슈"), ("next_meeting_topics", "다음 회의 후보 안건")):
            items = _alive(a.section(name))
            if items or not cfg.HIDE_EMPTY:
                lines.append(f"{i * 2}{label}")
                lines += [f"{i * 3}{format_item(it)}" for it in items] or [f"{i * 3}{cfg.EMPTY_TEXT}"]
    return lines


def render_transcript(s: MeetingSummary) -> list[str]:
    return ["■ 원본 전사문"] + [f"{cfg.INDENT}[{u.utterance_index}] " + (f"{speaker_text(u)}: " if u.speaker else "") + u.text
                              for u in s.transcript]


def render_minutes(s: MeetingSummary) -> str:
    blocks = [render_meeting_info(s), render_agenda_index(s), render_meeting_results(s), render_agenda_details(s)]
    if cfg.SHOW_TRANSCRIPT:
        blocks.append(render_transcript(s))
    return "\n\n".join("\n".join(b) for b in blocks) + "\n"


# ── 저장 ──────────────────────────────────────────────
def save_outputs(summary: MeetingSummary, llm_out=None, out_dir: Path | None = None,
                 save_json: bool | None = None, save_md: bool | None = None,
                 save_minutes: bool | None = None, save_llm: bool | None = None) -> list[Path]:
    # None이면 config 값 사용 / md는 저장한 JSON을 다시 읽어 생성 (JSON에 없는 값은 md에 나올 수 없음)
    out_dir = Path(out_dir) if out_dir else cfg.summary_dir(summary.meeting_id)
    save_json = cfg.SAVE_SUMMARY_JSON if save_json is None else save_json
    save_md = cfg.SAVE_MINUTES_MD if save_md is None else save_md
    save_minutes = cfg.SAVE_MINUTES_TEXT if save_minutes is None else save_minutes
    save_llm = cfg.SAVE_LLM_OUTPUT if save_llm is None else save_llm
    out_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    json_path = out_dir / f"{summary.meeting_id}_summary.json"
    if save_json or save_md:
        json_path.write_text(summary.model_dump_json(indent=2), encoding="utf-8")
        saved.append(json_path)
    if save_md:
        saved.append(markdown_from_json(json_path))
    if save_minutes:
        path = out_dir / f"{summary.meeting_id}_minutes.txt"
        path.write_text(render_minutes(summary), encoding="utf-8")
        saved.append(path)
    if save_llm and llm_out is not None:
        path = out_dir / f"{summary.meeting_id}_llm_output.json"
        path.write_text(json.dumps(llm_out.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
        saved.append(path)
    return saved
