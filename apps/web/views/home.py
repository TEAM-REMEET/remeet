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
from utils.text import esc, md_escape

SOON_DAYS = 3  # 마감까지 이 날 수 안이면 「마감 임박」


def _due(issue: dict) -> date | None:
    try:
        return date.fromisoformat(issue["due"]) if issue.get("due") else None
    except ValueError:
        return None


def _due_tag(d: date | None, now: date) -> str:
    if d is None:
        return ""
    if d < now:  # 지남 = 빨강 · 오늘 마감 · D-n = 노랑
        return tag(f"{(now - d).days}일 지남", "fail")
    if d <= now + timedelta(days=SOON_DAYS):
        return tag("오늘 마감" if d == now else f"D-{(d - now).days}", "warn")
    return ""


_HEAD = ('<div class="dash-row dash-head"><span class="dash-key">이슈</span><span class="dash-title">할 일</span>'
         '<span class="dash-due">마감일</span><span class="dash-state">상태</span></div>')


def _cells(issue: dict, now: date, done: bool) -> tuple[str, str]:
    """(이슈 키 칸, 마감일 · 상태 칸) HTML · 할 일 제목은 가운데 버튼 (누르면 상세 모달)."""
    d = _due(issue)
    key = esc(issue["key"])
    link = f'<a href="{esc(issue["url"])}" target="_blank" rel="noopener">{key}</a>' if issue.get("url") else key
    return (f'<span class="dash-key">{link}</span>',
            f'<span class="dash-tail"><span class="dash-due"><span class="dash-date">{f"{d.month}/{d.day}" if d else "없음"}</span>'
            f'{"" if done else _due_tag(d, now)}</span></span>')


HIDDEN_STATUSES = ("검토 중",)  # Jira 워크플로엔 있지만 RE:MEET 드롭다운에선 안 씀 (지금 이 상태인 이슈는 그대로 보임)


def _status_menu(issue: dict, statuses: list[dict]) -> None:
    """상태 (Jira 처럼): 지금 상태 색 딱지 ▾ → 누르면 다른 상태들이 각자 색 딱지로 (회색 해야 할 일 · 파랑 진행 중 · 초록 완료).
    색은 감싼 컨테이너 key 의 범주로 칠해요 (pages.css · dash-st- / dash-opt-)."""
    current = issue["status"] or "-"
    with st.container(key=f"dash-st-{issue.get('category_key') or 'new'}-{issue['key']}", width=130):
        menu = f"home_status_{issue['key']}_{current}"
        # 열림 상태를 추적해야(on_change="rerun") 고르는 순간 닫을 수 있음 (actions.change_issue_status)
        with st.popover(current, key=menu, on_change="rerun"):
            for n, x in enumerate(x for x in statuses if x["name"] not in (current, *HIDDEN_STATUSES)):
                with st.container(key=f"dash-opt-{x['category']}-{issue['key']}-{n}"):
                    st.button(x["name"], key=f"home_status_to_{issue['key']}_{n}", width="stretch",
                              on_click=actions.change_issue_status, args=(issue["key"], x["name"], menu))


def _rows(issues: list[dict], now: date, done: bool, statuses: list[dict]) -> None:
    """할 일 줄: 이슈 키 · 제목(누르면 상세 모달) · 마감일 · 상태 (누르면 바꿀 상태 목록 · 완료 · 되돌리기도 여기서)."""
    markup('<div class="dash-list">' + _HEAD + "</div>")
    with st.container(gap=None):  # 줄 사이 기본 간격(16px)이 구분선 밖에 붙어 칸 위아래가 달라 보임 → 간격 없이, 여백은 줄 안에서 위아래 같게
        for i in issues:
            key_cell, tail = _cells(i, now, done)
            with st.container(key=f"dash-r-{i['key']}", horizontal=True, vertical_alignment="center", gap=None):
                markup(key_cell)
                button(md_escape(i["title"]), key=f"home_detail_{i['key']}", variant="link", width="stretch",
                       on_click=actions.open_issue_detail, args=(i,))
                markup(tail)
                _status_menu(i, statuses)


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


FILTERS = (("late", "마감 지남"), ("soon", f"{SOON_DAYS}일 안 마감"), ("all", "남은 일"), ("done", "완료"))


def _issues(data: dict) -> None:
    issues, now = data["issues"], today()
    groups = {"late": [i for i in issues if (d := _due(i)) and d < now],
              "soon": [i for i in issues if (d := _due(i)) and now <= d <= now + timedelta(days=SOON_DAYS)],
              "all": issues, "done": data.get("done") or []}  # 완료 = 최근 30일 안에 완료된 일
    current = st.query_params.get("filter", "all")
    current = current if current in groups else "all"
    # 요약 칸 = 예전 카드 그대로 · 누르면 ?filter= 로 그 조건만 (다시 「남은 일」을 누르면 전체)
    markup('<div class="dash-stats">' + "".join(
        f'<a class="{"on" if k == current else ""}" href="?filter={k}" target="_self"><b>{len(groups[k])}</b>{label}</a>'
        for k, label in FILTERS) + "</div>")
    if data.get("all_url"):  # 요약 바로 아래 · 목록보다 위
        markup(f'<a class="dash-all" href="{esc(data["all_url"])}" target="_blank" rel="noopener">'
               f'{tool_mark("jira", "J", "dash-jira-logo")}Jira 에서 전체 보기 ↗</a>')
    shown = groups[current]
    if not shown:
        empty_state("남은 할 일이 없어요. 회의에서 새로 맡은 일이 생기면 여기에 보여요." if current == "all"
                    else f"「{dict(FILTERS)[current]}」인 할 일이 없어요.")
    else:
        _rows(shown, now, current == "done", data.get("statuses") or [])
    actions.apply_pending_status()  # 목록(닫힌 드롭다운)을 다 그린 뒤에 Jira 반영


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
        elif not data["linked"] and not data["issues"]:
            notice("내 이메일로 Jira 계정을 찾지 못했어요. Jira 에 초대된 이메일과 RE:MEET 이메일이 같은지 확인해 주세요.")
        else:  # Jira 계정을 못 찾아도 RE:MEET 에서 내가 담당자인 일(공동 포함)은 보여 줌
            _issues(data)


def render() -> None:
    s = get_state()
    with page("home"):
        page_header(f"{s.user_name}님, 안녕하세요",
                    "회의에서 맡은 일 중 아직 끝나지 않은 것을 모아 봤어요. 상태는 이슈를 눌러 Jira 에서 바꿔 주세요.")
        _review_queue()
        _jira()
