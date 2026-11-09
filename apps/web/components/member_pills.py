"""'멤버 6명 [서 이서윤 주관] [박 박도윤] ...' 알약 줄."""

from __future__ import annotations

from state import selectors as sel
from ui.avatar import avatar
from ui.html import markup
from utils.text import esc


def ordered_host_first(host: str, people: list[str]) -> list[str]:
    return [host] + [p for p in people if p != host]


def member_pills(lead: str, host: str | None, people: list[str]) -> None:
    """host 가 None 이면 '주관' 표시 없이 그대로 (프로젝트 멤버 줄)."""
    pills = []
    for pid in ordered_host_first(host, people) if host else people:
        p = sel.person(pid)
        is_host = pid == host
        role = '<span class="role">주관</span>' if is_host else ""
        info = " · ".join(x for x in (p.team, sel.person_desc(pid)) if x)  # hover 카드: 팀 · 직무 · 직급
        email = sel.person_email(pid)
        # 순서 중요 (CSS 형제 선택자): 알약 → 카드 → 닫기 덮개. 스크립트 없이 focus 로 열고 닫음 (components.css)
        card = (f'<span class="mcard" tabindex="-1" role="tooltip"><span class="top"><b>{esc(p.name)}</b>'
                f'{f"<span class=em>{esc(email)}</span>" if email else ""}</span>'
                f'{f"<span>{esc(info)}</span>" if info else ""}</span><span class="mclose" tabindex="-1"></span>'
                if info or email else "")
        pills.append(
            f'<span class="mwrap"><span class="mpill{" host" if is_host else ""}" tabindex="0">'
            f'{avatar(p.initial, size=24, host=is_host)}<span class="nm">{esc(p.name)}</span>{role}</span>{card}</span>'
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
