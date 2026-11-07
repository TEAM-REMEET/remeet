"""백엔드 요약 결과(MeetingSummary JSON) → 사용자가 내려받는 회의록 TXT.

블록: 회의 정보 / 안건 목차 / 회의 결과 / 안건별 상세
원본 전사문과 근거 발화 번호는 넣지 않고, 삭제 · 검증 오류가 남은 항목(state/summary_view.is_shown)과 값이 없는 칸은 보이지 않아요.
"""

from __future__ import annotations

import re

from state.summary_view import is_shown

_INDENT = "  "
_SECTIONS = ("discussion_background", "major_discussions", "unresolved_issues", "next_meeting_topics")
_UNSAFE_FILENAME = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def _shown(items: list[dict]) -> list[dict]:
    return [it for it in items if is_shown(it)]


def _is_action(item: dict) -> bool:
    return item.get("discussion_tag") == "액션"


def _is_decision(item: dict) -> bool:
    return item.get("decision_tag") == "결정" and not _is_action(item)


def _item_line(item: dict) -> str:
    """· [제안][결정] 요약문 / 액션은 담당 · 기한을 덧붙임"""
    if "discussion_tag" in item:
        tags = f'[{item["discussion_tag"]}]' + (f'[{item["decision_tag"]}]' if item.get("decision_tag") else "")
    else:
        tags = f'[{item["tag"]}]' if item.get("tag") else ""
    line = "· " + (f"{tags} " if tags else "") + item["text"]
    if _is_action(item):
        due = f'기한: {item["due"] or ""}'.rstrip()  # 일정이 없으면 '기한:'만 표시
        line += f' (담당: {", ".join(item["assignees"]) or "-"} · {due})'
    return line


def _meeting_info(summary: dict) -> list[str]:
    info = summary["meeting_info"]
    people = info["participants"]
    names = {p["user_id"]: p.get("display_name") or p["name"] for p in people}
    speakers = [names.get(uid, uid) for uid in info.get("speaker_user_ids") or []]
    lines = ["■ 회의 정보",
             f'{_INDENT}제목: {info.get("title") or "-"}',
             f'{_INDENT}일시: {info.get("date") or "-"}',
             f'{_INDENT}참석자: {", ".join(names.values()) or "-"} ({len(people)}명)']
    if speakers:
        lines.append(f'{_INDENT}발화자: {" · ".join(speakers)}')
    return lines


def _agenda_index(agendas: list[dict]) -> list[str]:
    lines = ["■ 안건 목차"]
    for number, a in enumerate(agendas, 1):
        discussions = _shown(a["major_discussions"])
        decisions = sum(1 for d in discussions if _is_decision(d))
        actions = sum(1 for d in discussions if _is_action(d))
        counts = f'결정 {decisions} · 미해결 {len(_shown(a["unresolved_issues"]))}' + (f" · 액션 {actions}" if actions else "")
        lines.append(f'{_INDENT}{number}. {a["agenda_title"]} [{counts}]')
    return lines


def _meeting_results(agendas: list[dict]) -> list[str]:
    groups: dict[str, list[str]] = {"결정사항": [], "액션 아이템": [], "미해결 이슈": [], "다음 회의 후보 안건": []}
    for number, a in enumerate(agendas, 1):
        tag = f" [안건{number}]"
        for d in _shown(a["major_discussions"]):
            if _is_decision(d):
                groups["결정사항"].append(_item_line(d) + tag)
            elif _is_action(d):
                groups["액션 아이템"].append(_item_line(d) + tag)
        groups["미해결 이슈"] += [_item_line(it) + tag for it in _shown(a["unresolved_issues"])]
        groups["다음 회의 후보 안건"] += [_item_line(it) + tag for it in _shown(a["next_meeting_topics"])]
    lines = ["■ 회의 결과"]
    for label, rows in groups.items():
        if rows:
            lines.append(f"{_INDENT}{label}")
            lines += [f"{_INDENT * 2}{row}" for row in rows]
    return lines


def _agenda_details(agendas: list[dict]) -> list[str]:
    lines = ["■ 안건별 상세"]
    for number, a in enumerate(agendas, 1):
        lines += ["", f'{_INDENT}{number}. {a["agenda_title"]}']
        background = _shown(a["discussion_background"])
        if background:
            lines.append(f"{_INDENT * 2}논의 배경")
            lines += [f"{_INDENT * 3}{_item_line(it)}" for it in background]
        by_id = {d["item_id"]: d for d in _shown(a["major_discussions"])}
        groups = [(sub["title"], [by_id[i] for i in sub["item_ids"] if i in by_id]) for sub in a["subtopics"]]
        groups = [(title, items) for title, items in groups if items]
        if groups:
            lines.append(f"{_INDENT * 2}주요 논의")
            for n, (title, items) in enumerate(groups, 1):
                lines.append(f"{_INDENT * 3}{number}-{n}. {title}")
                lines += [f"{_INDENT * 4}{_item_line(it)}" for it in items]
        for label, name in (("미해결 이슈", "unresolved_issues"), ("다음 회의 후보 안건", "next_meeting_topics")):
            items = _shown(a[name])
            if items:
                lines.append(f"{_INDENT * 2}{label}")
                lines += [f"{_INDENT * 3}{_item_line(it)}" for it in items]
    return lines


def render_minutes(summary: dict) -> str:
    agendas = [a for a in summary["agendas"] if any(_shown(a[name]) for name in _SECTIONS)]
    blocks = [_meeting_info(summary), _agenda_index(agendas), _meeting_results(agendas), _agenda_details(agendas)]
    return "\n\n".join("\n".join(block) for block in blocks) + "\n"


def minutes_filename(summary: dict) -> str:
    """{회의 제목}_{회의 날짜}.txt · 파일 이름에 쓸 수 없는 문자는 뺌"""
    info = summary["meeting_info"]
    parts = [" ".join(_UNSAFE_FILENAME.sub(" ", x).split()) for x in (info.get("title"), info.get("date")) if x]
    return f'{"_".join(p for p in parts if p) or summary["meeting_id"]}.txt'
