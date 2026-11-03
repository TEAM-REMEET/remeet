"""회의록 Markdown 렌더링 (저장된 {회의 ID}_summary.json → {회의 ID}_minutes.md)

블록
    ■ 회의 정보       제목 · 일시 · 참석자 · 원본 문서
    ■ 안건 목차       N. 안건 제목 [결정 n · 미해결 n]          (안건별 상세로 링크)
    ■ 회의 결과       결정사항 · 액션 아이템 · 미해결 이슈 · 다음 회의 후보 안건 (각 항목 끝에 [안건N])
    ■ 안건별 상세     안건 제목 / 논의 배경 / 주요 논의 (N-M. 소주제) / 결정사항 / 미해결 이슈 / 다음 회의 후보 안건
    ■ 원본 전사문     근거 발화 번호를 누르면 해당 발화로 이동 (config.SHOW_TRANSCRIPT)

- 결정사항 = decision_tag '결정'이고 액션이 아닌 항목 (안건 목차 · 회의 결과의 결정 수와 같은 기준)
  주요 논의의 항목을 다시 보여주는 것이며 JSON에는 item_id 하나로만 저장됨
- JSON만 있으면 LLM 없이 다시 만들 수 있음 (JSON을 직접 고친 뒤 md만 다시 생성)
    python render_md.py TS3003c                  # outputs/summary_action/TS3003c/TS3003c_summary.json
    python render_md.py path/to/xxx_summary.json
"""

import re
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import config as cfg
from schemas import BaseItem, DiscussionItem, MeetingSummary

_MD_SPECIAL = re.compile(r"([\\`*_\[\]<>|])")


def _esc(text: str | None) -> str:
    # 요약문 안의 Markdown 특수 문자가 서식으로 해석되지 않도록
    return _MD_SPECIAL.sub(r"\\\1", text or "")


def _alive(items: list[BaseItem]) -> list[BaseItem]:
    return [it for it in items if it.status != "DELETED"]


def participants_text(info) -> str:
    # '프로젝트 매니저(A), 산업 디자이너(C) … (4명)' — 발화자 표기가 있으면 역할 옆에 붙임
    label_of = {role: label for label, role in info.speakers.items()}
    people = [f"{p}({label_of[p]})" if p in label_of else p for p in info.participants]
    people += [f"{role}({label})" for label, role in info.speakers.items() if role not in info.participants]
    return ", ".join(people) + (f" ({info.participant_count}명)" if info.participant_count else "")


def speaker_text(u) -> str:
    # 'A(프로젝트 매니저)' / 역할을 모르면 'A'
    if not u.speaker:
        return ""
    return f"{u.speaker}({u.speaker_role})" if u.speaker_role else u.speaker


def _agenda_anchor(number: int) -> str:
    return f"agenda-{number}"


def _utt_anchor(index: int) -> str:
    return f"u{index}"


def _evidence(item: BaseItem) -> str:
    if not cfg.SHOW_EVIDENCE:
        return ""
    idx = item.evidence_utterance_indices
    if not idx:
        return " \\[근거 발화 -\\]"
    refs = [f"[{i}](#{_utt_anchor(i)})" if cfg.SHOW_TRANSCRIPT else str(i) for i in idx]
    return f" \\[근거 발화 {', '.join(refs)}\\]"


def _tags(item: BaseItem, decision_only: bool = False) -> str:
    if isinstance(item, DiscussionItem):
        if decision_only:           # 결정사항 블록: [결정]만
            return f"\\[{item.decision_tag}\\]"
        return f"\\[{item.discussion_tag}\\]" + (f"\\[{item.decision_tag}\\]" if item.decision_tag else "")
    tag = getattr(item, "tag", None)
    return f"\\[{tag}\\]" if tag else ""


def format_item(item: BaseItem, decision_only: bool = False, agenda_number: int | None = None,
                evidence: bool = True) -> str:
    parts = [f"`{item.item_id}` " if cfg.SHOW_ITEM_ID else ""]
    tags = _tags(item, decision_only)
    parts.append(f"{tags} " if tags else "")
    parts.append(_esc(item.text))
    if isinstance(item, DiscussionItem) and item.is_action:
        parts.append(f" (담당: {_esc(item.assignee) or '-'} · 기한: {_esc(item.due) or '-'})")
    if evidence:
        parts.append(_evidence(item))
    if agenda_number is not None:
        parts.append(f" [\\[안건{agenda_number}\\]](#{_agenda_anchor(agenda_number)})")
    if item.status == "REVIEW_REQUIRED":
        parts.append(_esc(cfg.REVIEW_MARK))
    return "- " + "".join(parts)


def _section(label: str, lines: list[str], level: str = "####") -> list[str]:
    # 빈 섹션은 config.HIDE_EMPTY에 따라 숨기거나 (없음) 표시
    if not lines:
        return [] if cfg.HIDE_EMPTY else ["", f"{level} {label}", "", cfg.EMPTY_TEXT]
    return ["", f"{level} {label}", ""] + lines


# ── 블록 ──────────────────────────────────────────────
def render_meeting_info(s: MeetingSummary) -> list[str]:
    info, src = s.meeting_info, s.source_document
    when = " ".join(x for x in (info.date, f"({info.duration})" if info.duration else None) if x)
    people = participants_text(info)
    return ["## ■ 회의 정보", "",
            f"- **제목**: {_esc(info.title) or '-'}",
            f"- **일시**: {_esc(when) or '-'}",
            f"- **참석자**: {_esc(people) or '-'}",
            f"- **원본 문서**: `{Path(src.input_path).name}` (발화 {src.utterance_count}개)"]


def render_agenda_index(s: MeetingSummary) -> list[str]:
    return ["## ■ 안건 목차", ""] + [
        f"{e.agenda_number}. [{_esc(e.agenda_title)}](#{_agenda_anchor(e.agenda_number)}) "
        f"\\[결정 {e.decision_count} · 미해결 {e.unresolved_count}\\]" for e in s.agenda_index]


def render_meeting_results(s: MeetingSummary) -> list[str]:
    r = s.meeting_results
    lines = ["## ■ 회의 결과"]
    for label, ids, decision_only in (("결정사항", r.decision_item_ids, True),
                                      ("액션 아이템", r.action_item_ids, False),
                                      ("미해결 이슈", r.unresolved_item_ids, False),
                                      ("다음 회의 후보 안건", r.next_topic_item_ids, False)):
        rows = []
        for item_id in ids:
            item, agenda = s.find_item(item_id), s.agenda_of(item_id)
            if item is None or item.status == "DELETED":
                continue
            rows.append(format_item(item, decision_only=decision_only, agenda_number=agenda.agenda_number,
                                    evidence=False))
        lines += _section(label, rows, level="###")
    return lines


def render_agenda(a) -> list[str]:
    lines = ["", f'<a id="{_agenda_anchor(a.agenda_number)}"></a>', "",
             f"### {a.agenda_number}. {_esc(a.agenda_title)}"]
    lines += _section("논의 배경", [format_item(it) for it in _alive(a.discussion_background)])

    by_id = {it.item_id: it for it in _alive(a.major_discussions)}
    groups = [(st, [by_id[x] for x in st.item_ids if x in by_id]) for st in a.subtopics]
    discussion = []
    for n, (st, items) in enumerate([g for g in groups if g[1]], 1):
        if discussion:
            discussion.append("")
        discussion += [f"**{a.agenda_number}-{n}. {_esc(st.title)}**", ""] + [format_item(it) for it in items]
    lines += _section("주요 논의", discussion)

    decisions = [it for it in by_id.values() if it.is_counted_decision]
    lines += _section("결정사항", [format_item(it, decision_only=True) for it in decisions])
    lines += _section("미해결 이슈", [format_item(it) for it in _alive(a.unresolved_issues)])
    lines += _section("다음 회의 후보 안건", [format_item(it) for it in _alive(a.next_meeting_topics)])
    return lines


def render_agenda_details(s: MeetingSummary) -> list[str]:
    lines = ["## ■ 안건별 상세"]
    for a in s.agendas:
        lines += render_agenda(a)
    return lines


def render_transcript(s: MeetingSummary) -> list[str]:
    # 줄마다 앵커 → 근거 발화 링크로 이동 / 줄 끝 공백 두 칸 = 줄바꿈
    return ["## ■ 원본 전사문", ""] + [
        f'<a id="{_utt_anchor(u.utterance_index)}"></a>**\\[{u.utterance_index}\\]** '
        + (f"{_esc(speaker_text(u))}: " if u.speaker else "") + _esc(u.text) + "  " for u in s.transcript]


def render_markdown(s: MeetingSummary) -> str:
    blocks = [render_meeting_info(s), render_agenda_index(s), render_meeting_results(s), render_agenda_details(s)]
    if cfg.SHOW_TRANSCRIPT:
        blocks.append(render_transcript(s))
    body = "\n\n---\n\n".join("\n".join(b) for b in blocks)
    return f"# {_esc(s.meeting_info.title or s.meeting_id)} 회의록\n\n{body}\n"


# ── JSON → md ─────────────────────────────────────────
def load_summary(json_path: str | Path) -> MeetingSummary:
    return MeetingSummary.model_validate_json(Path(json_path).read_text(encoding="utf-8"))


def markdown_from_json(json_path: str | Path, out_path: str | Path | None = None) -> Path:
    # 저장된 summary JSON을 읽어 md 생성 (기본: JSON과 같은 폴더의 {회의 ID}_minutes.md)
    json_path = Path(json_path)
    summary = load_summary(json_path)
    out_path = Path(out_path) if out_path else json_path.with_name(f"{summary.meeting_id}_minutes.md")
    out_path.write_text(render_markdown(summary), encoding="utf-8")
    return out_path


def _resolve_json(name: str) -> Path:
    path = Path(name)
    if path.suffix != ".json":
        path = cfg.summary_dir(name) / f"{name}_summary.json"
    if not path.is_file():
        raise FileNotFoundError(f"summary JSON 없음: {path}")
    return path


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for name in sys.argv[1:] or cfg.DEFAULT_INPUTS:
        print(f"[저장] {markdown_from_json(_resolve_json(name))}")
