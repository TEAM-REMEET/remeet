"""'멤버 6명 [서 이서윤 주관] [박 박도윤] ...' 알약 줄."""

from __future__ import annotations

from state import selectors as sel
from ui.avatar import avatar
from ui.html import markup
from utils.text import esc


def ordered_host_first(host: str, people: list[str]) -> list[str]:
    return [host] + [p for p in people if p != host]


def member_pills(lead: str, host: str, people: list[str]) -> None:
    pills = []
    for pid in ordered_host_first(host, people):
        p = sel.person(pid)
        is_host = pid == host
        role = '<span class="role">주관</span>' if is_host else ""
        pills.append(
            f'<span class="mpill{" host" if is_host else ""}">{avatar(p.initial, size=24, host=is_host)}{esc(p.name)}{role}</span>'
        )
    markup(f'<div class="pills"><span class="lead">{esc(lead)}</span>{"".join(pills)}</div>')


def people_list(rows: list[dict]) -> str:
    """모달 안 사람 목록 HTML. rows: [{letter, name, team, role, tone, host}]"""
    out = []
    for r in rows:
        out.append(
            f'<div class="plist-row">{avatar(r["letter"], size=34, host=r["host"])}'
            f'<span class="who"><b>{esc(r["name"])}</b><span>{esc(r["team"])}</span></span>'
            f'<span class="tag tag-{r["tone"]}">{esc(r["role"])}</span></div>'
        )
    return "".join(out)
