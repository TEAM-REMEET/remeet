"""관리자 · 비용 관리: 달마다 AI 사용 비용 · 예산 대비 · 기능별 비중 · 예산 설정.

처음 들어오면 이번 달을 보여 주고, 제목 오른쪽에서 연도 · 월을 골라 다른 달을 볼 수 있어요.
예산 · 알림 기준은 조직 설정(서버 공용)이라 저장하면 모든 관리자에게 같은 값이 보여요.
비용은 AI 호출마다 서버가 기록한 실제 값(PostgreSQL llm_usage · 달러)이고, 오늘 환율로 원을 계산해
「₩12,345 ($8.82)」처럼 보여 줘요 (정확한 금액은 괄호 안 달러).
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


def _usd(krw: float) -> str:
    """원 → 정확한 달러 (작은 금액은 소수 셋째 자리까지)."""
    usd = krw / repo.cost_rate()["rate"]
    return f"${usd:,.2f}" if usd >= 1 or usd == 0 else f"${usd:.3f}"


def _money(krw: float) -> str:
    """₩12,345 ($8.82)"""
    return f"{_won(krw)} ({_usd(krw)})"


def _months() -> list[dict]:
    return repo.cost_months()


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
    if prev and prev["amount"] > 0:
        diff = (total - prev["amount"]) / prev["amount"] * 100
        vs = f'{"+" if diff >= 0 else ""}{diff:.0f}%'
        vs_sub = ("월말 예상 " if mo.get("running") else "") + f'{prev["month"]}월 {_money(prev["amount"])} 대비'
    else:
        vs, vs_sub = "-", "비교할 지난달 기록이 없어요"
    used_sub = (f'{mo["month"]}월 1일 ~ {mo["days"]}일 · {mo["days"]}일 사용' if mo.get("running")
                else f'{mo["month"]}월 확정 금액')
    budget_sub = (f"월말 예상 {_money(total)} · 예산의 {fpct:.0f}%" if mo.get("running")
                  else f"월 예산 {_won(b['amount'])}")
    markup(
        '<div class="adm-kpis">'
        f'<div class="adm-kpi"><span class="lb">사용 금액</span><b>{_won(mo["amount"])}</b><span class="sub">{esc(_usd(mo["amount"]))} · {esc(used_sub)}</span></div>'
        f'<div class="adm-kpi{" warn" if over else ""}"><span class="lb">예산 대비</span><b>{pct:.0f}%</b>'
        f'<span class="meter"><i class="fc" style="width:{min(fpct, 100):.1f}%"></i><i style="width:{min(pct, 100):.1f}%"></i>'
        f'<em style="left:{min(b["alert_at"], 100)}%"></em></span>'
        f'<span class="sub">{esc(budget_sub)}</span></div>'
        f'<div class="adm-kpi"><span class="lb">처리한 회의록</span><b>{mo["notes"]:,}건</b>'
        f'<span class="sub">1건당 평균 {esc(_money(per_note))}</span></div>'
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
        vals.append(f'<span class="val{on}" title="{esc(_usd(m["amount"]))}">{_won(m["amount"])}{extra}</span>')
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
    top = max(v for _, v, _ in rows) or 1
    return "".join(
        f'<div class="adm-share"><span class="nm">{esc(nm)}</span><span class="bar"><i style="width:{v / top * 100:.1f}%"></i></span>'
        f'<span class="pct">{v * 100:.0f}%</span><span class="amt">{esc(amt)}</span></div>'
        for nm, v, amt in rows
    )


def _breakdown(mo: dict) -> None:
    rate = repo.cost_rate()["rate"]
    total = sum(mo["features"].values())
    with st.container(key="adm-row-share"), card("adm-features", gap="medium"):
        card_title("기능별 사용량", aside=_label(mo))
        if total <= 0:
            markup('<p class="adm-note">이 달에는 AI 사용 기록이 없어요.</p>')
        else:
            rows = [(name, mo["features"].get(f, 0.0) / total, _money(mo["features"].get(f, 0.0) * rate))
                    for f, name in repo.COST_FEATURES.items()]
            markup(f'<div class="adm-shares wide">{_share_rows(rows)}</div>')
        markup('<p class="adm-note">AI 호출마다 기록한 토큰 수 × 모델 단가로 계산한 금액이에요 (부가세 별도).</p>')


def _budget() -> None:
    b = repo.budget()
    with card("adm-budget", gap="medium"):
        card_title("예산 설정")
        st.number_input("월 예산 (원)", min_value=10000, step=10000, value=b["amount"], key="adm_budget_in",
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


def _rate_note(mo: dict) -> None:
    """환율 기준 · 단가가 없어 빠진 비용 안내."""
    r = repo.cost_rate()
    text = (f"기본 환율 {r['rate']:,.0f}원 적용 (오늘 환율을 받지 못했어요)" if r["fallback"]
            else f"환율 {r['rate']:,.2f}원 · {r['date']} 기준")
    if mo.get("unpriced"):
        text += f" · 단가가 등록되지 않은 모델 호출 {mo['unpriced']:,}건은 금액에서 빠졌어요"
    markup(f'<p class="adm-note">{esc(text)} · 정확한 금액은 괄호 안 달러예요.</p>')


def render() -> None:
    s = get_state()
    months = _months()
    idx = next((i for i, m in enumerate(months) if m["key"] == s.adm_month), len(months) - 1)
    mo, prev = months[idx], (months[idx - 1] if idx else None)
    with page("adm-cost"):
        with st.container(horizontal=True, vertical_alignment="bottom"):
            page_header("비용 관리", "회의록 분석 · 근거 연결 · 담당자 추천 · 회의 질문에 쓴 AI 비용을 달마다 확인해요.")
            _picker(s, mo)
        _rate_note(mo)
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
