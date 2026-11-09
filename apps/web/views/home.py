"""홈 (로그인 직후 첫 화면) · 회의록 생성 검토 대기 · 내 Jira 할 일 대시보드.

회의록 생성 검토 대기: 작성자가 나를 주관자로 정하고 분석을 마쳐 넘긴 회의록 (GET /api/review-queue).
「검토하기」를 누르면 작성 2단계(분석 및 검토)부터 이어서 해요 (actions.open_handoff).

내가 담당자인 미완료 Jira 이슈를 보여 주기만 해요. 상태는 이슈를 눌러 Jira 에서 바꿔요 (이력에 본인 이름이 남게).
범위(누구 이슈인지)는 백엔드가 로그인 정보로 정해요 (GET /api/dashboard/jira · 서버가 1분 캐시).
「동기화」는 캐시를 건너뛰고 바로 다시 불러와요. 마지막으로 Jira 에서 가져온 시각을 같이 보여 줘요.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import streamlit as st

from data import repository as repo
from data.api import ApiError
from state import actions
from state.store import get_state
from ui.buttons import button
from ui.html import markup
from ui.icons import tool_mark
from ui.layout import card, card_title, empty_state, notice, page, page_header
from ui.tags import tag
from utils.dates import today
from utils.text import esc

SOON_DAYS = 3  # 마감까지 이 날 수 안이면 「마감 임박」


def _due(issue: dict) -> date | None:
    try:
        return date.fromisoformat(issue["due"]) if issue.get("due") else None
    except ValueError:
        return None


def _due_tag(d: date | None, now: date) -> str:
    if d is None:
        return ""
    if d < now:
        return tag(f"{(now - d).days}일 지남", "warn")
    if d <= now + timedelta(days=SOON_DAYS):
        return tag("오늘 마감" if d == now else f"D-{(d - now).days}", "hold")
    return ""


def _row(issue: dict, now: date) -> str:
    d = _due(issue)
    key = esc(issue["key"])
    link = f'<a href="{esc(issue["url"])}" target="_blank" rel="noopener">{key}</a>' if issue.get("url") else key
    return (f'<div class="dash-row"><span class="dash-key">{link}</span>'
            f'<span class="dash-title">{esc(issue["title"])}</span>'
            f'<span class="dash-due">{f"{d.month}/{d.day}" if d else "마감일 없음"}</span>'
            f'{_due_tag(d, now)}{tag(issue["status"] or "-", "info")}</div>')


def _when(iso: str | None) -> str:
    """'2026-11-09T14:03:00+09:00' → '11/9 14:03'."""
    if not iso:
        return ""
    t = datetime.fromisoformat(str(iso))
    return f"{t.month}/{t.day} {t:%H:%M}"


def _review_queue() -> None:
    """회의록 생성 검토 대기 (Jira 할 일 위). 비어 있어도 카드는 보여 줘요."""
    try:
        rows, error = repo.review_queue(), ""
    except ApiError as e:
        rows, error = [], str(e)
    with card("home-review"):
        card_title("회의록 생성 검토 대기", aside=f"{len(rows)}건" if rows else "")
        if error:
            notice(esc(error), "neutral")
            return
        if not rows:
            empty_state("검토를 기다리는 회의록이 없어요. 다른 사람이 나를 주관자로 정해 회의록을 쓰면 분석이 끝나는 대로 여기에 보여요.")
            return
        for r in rows:
            handoff = r["kind"] == "handoff"
            with st.container(key=f"home-rv-{r['id']}", horizontal=True, vertical_alignment="center", gap="small"):
                sub = (f"작성 {r.get('author_name') or '-'} · 분석 완료 {_when(r.get('requested_at'))}" if handoff
                       else f"작성 {r.get('author_name') or '-'} · 검토 요청 {_when(r.get('requested_at'))}")
                markup(f'<div class="dash-row" style="flex:1"><span class="dash-title"><b>{esc(r["title"] or "제목 없음")}</b>'
                       f'<br><span style="color:var(--muted, #888);font-size:13px">{esc(sub)}</span></span>'
                       f'{tag("검토 대기", "warn")}</div>')
                button("검토하기", key=f"home_rv_{r['id']}", variant="primary", icon=":material/rate_review:",
                       on_click=actions.open_handoff if handoff else actions.open_meeting, args=(r["id"],))


def _synced(iso: str | None) -> str:
    """'2026-11-08T15:03:00+09:00' → '마지막 동기화 15:03'."""
    return f"마지막 동기화 {datetime.fromisoformat(iso):%H:%M}" if iso else ""


def _issues(data: dict) -> None:
    issues, now = data["issues"], today()
    late = sum(1 for i in issues if (d := _due(i)) and d < now)
    soon = sum(1 for i in issues if (d := _due(i)) and now <= d <= now + timedelta(days=SOON_DAYS))
    markup('<div class="dash-stats">'
           f'<span><b>{late}</b>마감 지남</span><span><b>{soon}</b>{SOON_DAYS}일 안 마감</span>'
           f'<span><b>{len(issues)}</b>남은 일</span></div>')
    if data.get("all_url"):  # 요약 바로 아래 · 목록보다 위
        markup(f'<a class="dash-all" href="{esc(data["all_url"])}" target="_blank" rel="noopener">'
               f'{tool_mark("jira", "J", "dash-jira-logo")}Jira 에서 전체 보기 ↗</a>')
    if not issues:
        empty_state("남은 할 일이 없어요. 회의에서 새로 맡은 일이 생기면 여기에 보여요.")
    else:
        markup('<div class="dash-list">' + "".join(_row(i, now) for i in issues) + "</div>")


def _jira() -> None:
    refresh = st.session_state.pop("_home_refresh", False)
    with st.spinner("Jira 에서 내 할 일을 가져오는 중이에요…"):
        try:
            data, error = repo.dashboard_jira(refresh), ""
        except ApiError as e:
            data, error = None, str(e)
    with card("home-jira"):
        with st.container(horizontal=True, vertical_alignment="center", gap="small"):  # 제목 바로 옆에 동기화
            card_title("내 Jira 할 일", aside=_synced(data and data.get("synced_at")))
            button("동기화", key="home_sync", variant="secondary", icon=":material/sync:", on_click=actions.refresh_dashboard)
        if error:
            notice(esc(error), "neutral")
        elif not data["linked"]:
            notice("내 이메일로 Jira 계정을 찾지 못했어요. Jira 에 초대된 이메일과 RE:MEET 이메일이 같은지 확인해 주세요.")
        else:
            _issues(data)


def render() -> None:
    s = get_state()
    with page("home"):
        page_header(f"{s.user_name}님, 안녕하세요",
                    "회의에서 맡은 일 중 아직 끝나지 않은 것을 모아 봤어요. 상태는 이슈를 눌러 Jira 에서 바꿔 주세요.")
        _review_queue()
        _jira()
