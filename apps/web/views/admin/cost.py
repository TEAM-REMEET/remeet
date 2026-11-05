"""관리자 · 비용 관리: 달마다 AI 사용 비용 · 예산 대비 · 팀별 / 기능별 비중 · 예산 설정.

처음 들어오면 이번 달을 보여 주고, 제목 오른쪽에서 연도 · 월을 골라 다른 달을 볼 수 있어요.
예산 · 알림 기준은 조직 설정(서버 공용)이라 저장하면 모든 관리자에게 같은 값이 보여요.
"""

from __future__ import annotations

import streamlit as st

from data import repository as repo
from state import actions
from state.store import AppState, get_state
from ui.buttons import button
from ui.html import markup
from ui.layout import card, card_title, page, page_header
from utils.text import esc

_TREND_N = 6  # 월별 막대: 고른 달까지 최근 6개월


def _won(n: float) -> str:
    return f"₩{round(n):,}"


def _months() -> list[dict]:
    return repo.admin()["months"]


def _label(m: dict) -> str:
    return f'{m["year"]}년 {m["month"]}월'


def _forecast(mo: dict) -> float:
    """진행 중인 달: 지금까지 쓴 속도로 월말 예상. 끝난 달: 실제 금액."""
    return mo["amount"] / mo["days"] * mo["month_days"] if mo.get("running") else mo["amount"]


def _alert(mo: dict) -> None:
    """예산 알림 (숫자 카드 위). 월말 예상 · 사용 금액이 알림 기준을 넘었을 때만."""
    b = repo.budget()
    fpct = _forecast(mo) / b["amount"] * 100
    if fpct < b["alert_at"]:
        return
    when = "이 속도면 월말에" if mo.get("running") else f"{mo['month']}월에"
    markup(f'<div class="adm-alert" role="status"><b>예산 알림</b>{when} 예산의 {fpct:.0f}%를 써요. '
           f"알림 기준({b['alert_at']}%)을 넘었어요. 예산을 늘리거나 사용량이 많은 팀을 확인해 보세요.</div>")


def _kpis(mo: dict, prev: dict | None) -> None:
    b = repo.budget()
    total = _forecast(mo)
    pct = mo["amount"] / b["amount"] * 100
    fpct = total / b["amount"] * 100
    over = fpct >= b["alert_at"]
    per_note = mo["amount"] / max(mo["notes"], 1)
    if prev:
        diff = (total - prev["amount"]) / prev["amount"] * 100
        vs = f'{"+" if diff >= 0 else ""}{diff:.0f}%'
        vs_sub = ("월말 예상 " if mo.get("running") else "") + f'{prev["month"]}월 {_won(prev["amount"])} 대비'
    else:
        vs, vs_sub = "-", "비교할 지난달 기록이 없어요"
    used_sub = (f'{mo["month"]}월 1일 ~ {mo["days"]}일 · {mo["days"]}일 사용' if mo.get("running")
                else f'{mo["month"]}월 확정 금액')
    budget_sub = (f"월말 예상 {_won(total)} · 예산의 {fpct:.0f}%" if mo.get("running")
                  else f"월 예산 {_won(b['amount'])}")
    markup(
        '<div class="adm-kpis">'
        f'<div class="adm-kpi"><span class="lb">사용 금액</span><b>{_won(mo["amount"])}</b><span class="sub">{esc(used_sub)}</span></div>'
        f'<div class="adm-kpi{" warn" if over else ""}"><span class="lb">예산 대비</span><b>{pct:.0f}%</b>'
        f'<span class="meter"><i class="fc" style="width:{min(fpct, 100):.1f}%"></i><i style="width:{min(pct, 100):.1f}%"></i>'
        f'<em style="left:{min(b["alert_at"], 100)}%"></em></span>'
        f'<span class="sub">{esc(budget_sub)}</span></div>'
        f'<div class="adm-kpi"><span class="lb">처리한 회의록</span><b>{mo["notes"]:,}건</b>'
        f'<span class="sub">1건당 평균 {_won(per_note)}</span></div>'
        f'<div class="adm-kpi"><span class="lb">지난달 대비</span><b>{esc(vs)}</b><span class="sub">{esc(vs_sub)}</span></div>'
        "</div>"
    )


def _trend(months: list[dict], sel_key: str) -> None:
    """월별 사용 금액 막대 (고른 달 주황 · 진행 중인 달은 월말 예상을 옅게) + 예산 선."""
    budget = repo.budget()["amount"]
    top = max(max(_forecast(m) for m in months), budget) * 1.12
    vals, tracks, labels = [], [], []
    for i, m in enumerate(months):
        h = m["amount"] / top * 100
        on = " on" if m["key"] == sel_key else ""
        fc = f'<i class="fc" style="height:{_forecast(m) / top * 100:.1f}%"></i>' if m.get("running") else ""
        extra = f'<em>예상 {_won(_forecast(m))}</em>' if m.get("running") else ""
        vals.append(f'<span class="val{on}">{_won(m["amount"])}{extra}</span>')
        tracks.append(f'<div class="track{on}">{fc}<i style="height:{h:.1f}%"></i></div>')
        year = f'<small>{m["year"]}</small>' if i == 0 or m["month"] == 1 else ""  # 해가 바뀌는 곳에 연도
        labels.append(f'<span class="mo{on}">{m["month"]}월{year}</span>')
    line = budget / top * 100
    markup('<div class="adm-trend">'
           f'<div class="row">{"".join(vals)}</div>'
           f'<div class="area"><span class="budget" style="bottom:{line:.1f}%"><b>예산 {_won(budget)}</b></span>'
           f'{"".join(tracks)}</div>'
           f'<div class="row">{"".join(labels)}</div></div>')


def _share_rows(rows: list[tuple[str, float, str]]) -> str:
    top = max(v for _, v, _ in rows)
    return "".join(
        f'<div class="adm-share"><span class="nm">{esc(nm)}</span><span class="bar"><i style="width:{v / top * 100:.1f}%"></i></span>'
        f'<span class="pct">{v * 100:.0f}%</span><span class="amt">{esc(amt)}</span></div>'
        for nm, v, amt in rows
    )


def _breakdown(mo: dict) -> None:
    cfg = repo.admin()
    with st.container(key="adm-row-share"):
        left, right = st.columns(2, gap="medium")
        with left, card("adm-teams", gap="medium"):
            card_title("팀별 사용량", aside=f'{_label(mo)} · 회의록 {mo["notes"]}건')
            rows = [(t, v, f'{_won(mo["amount"] * v)} · {round(mo["notes"] * v)}건') for t, v in cfg["teams"].items()]
            markup(f'<div class="adm-shares">{_share_rows(rows)}</div>')
        with right, card("adm-features", gap="medium"):
            card_title("기능별 사용량", aside=_label(mo))
            rows = [(f, v, _won(mo["amount"] * v)) for f, v in cfg["features"].items()]
            markup(f'<div class="adm-shares wide">{_share_rows(rows)}</div>')
            markup(f'<p class="adm-note">{esc(cfg["unit_note"])}</p>')


def _budget() -> None:
    b = repo.budget()
    with card("adm-budget", gap="medium"):
        card_title("예산 설정")
        st.number_input("월 예산 (원)", min_value=50000, step=50000, value=b["amount"], key="adm_budget_in",
                        format="%d")
        st.select_slider("예산 알림 기준", options=[50, 60, 70, 80, 90, 100], value=b["alert_at"],
                         key="adm_alert_in", format_func=lambda v: f"{v}%")
        markup('<p class="adm-note">이번 달 월말 예상 사용 금액이 기준을 넘으면 이 화면 위와 알림으로 알려 드려요.</p>')
        st.space("stretch")
        button("저장", key="adm_budget_save", variant="primary", width="stretch", on_click=actions.adm_save_budget,
               args=("adm_budget_in", "adm_alert_in"))


def _picker(s: AppState, mo: dict) -> None:
    """연도 · 월 고르기 (처음엔 이번 달)."""
    months = _months()
    years = sorted({m["year"] for m in months}, reverse=True)
    in_year = [m["month"] for m in months if m["year"] == mo["year"]]
    with st.container(key="adm-month-pick", horizontal=True, gap="small", width="content"):
        st.selectbox("연도", years, index=years.index(mo["year"]), key=f"adm_year_{s.adm_month}",
                     format_func=lambda y: f"{y}년", label_visibility="collapsed",
                     on_change=actions.adm_pick_month, args=(f"adm_year_{s.adm_month}", f"adm_mon_{s.adm_month}"))
        st.selectbox("월", in_year, index=in_year.index(mo["month"]), key=f"adm_mon_{s.adm_month}",
                     format_func=lambda m: f"{m}월" + (" (진행 중)" if any(
                         x["year"] == mo["year"] and x["month"] == m and x.get("running") for x in months) else ""),
                     label_visibility="collapsed",
                     on_change=actions.adm_pick_month, args=(f"adm_year_{s.adm_month}", f"adm_mon_{s.adm_month}"))


def render() -> None:
    s = get_state()
    months = _months()
    idx = next(i for i, m in enumerate(months) if m["key"] == s.adm_month)
    mo, prev = months[idx], (months[idx - 1] if idx else None)
    with page("adm-cost"):
        with st.container(horizontal=True, vertical_alignment="bottom"):
            page_header("비용 관리", "회의록 분석 · 담당자 추천 · 위키 정리 · 외부 전송에 쓴 AI 비용을 달마다 확인해요.")
            _picker(s, mo)
        _alert(mo)
        _kpis(mo, prev)
        trend = months[max(0, idx - _TREND_N + 1): idx + 1]
        with st.container(key="adm-row-trend"):
            left, right = st.columns([2.1, 1], gap="medium")
            with left, card("adm-trend", gap="medium"):
                card_title("월별 사용 금액", aside=f"{_label(trend[0])} ~ {_label(trend[-1])}")
                _trend(trend, mo["key"])
            with right:
                _budget()
        _breakdown(mo)
