"""구성원 관리: 담당자 추천에 쓰는 프로필 · 스킬 · 업무 이력 (프로토타입과 같은 표 + 상세 모달)."""
from __future__ import annotations

import streamlit as st

from core import auth
from core.state import flash, members, settings
from services.rules import OVERLOAD
from ui.components import html, notice
from ui.layout import head_row, page_head, panel, tap
from utils.dates import now_time
from utils.text import esc

GRID = "grid-template-columns:1.25fr 1.6fr 3fr 1.4fr 2.2fr"


# ---------------------------------------------------------------- 작은 조각

def _avatar(name: str, size: int = 24) -> str:
    return f'<span class="av" style="width:{size}px;height:{size}px;font-size:{11 if size < 30 else 18}px">{esc(name[0])}</span>'


def _load_bar(load: int) -> str:
    hot = load >= OVERLOAD
    return (f'<span class="load"><span class="bar"><span class="{"hot" if hot else ""}" style="width:{min(100, load * 10)}%"></span></span>'
            f'<span class="{"c-err fw6" if hot else ""}">{load}건</span></span>')


def _chips(skills: list[str], limit: int | None = 3, size: str = "sm") -> str:
    shown = skills if limit is None else skills[:limit]
    more = f'<span class="small muted">+{len(skills) - len(shown)}</span>' if len(skills) > len(shown) else ""
    return '<div class="chips">' + "".join(f'<span class="chip {size}">{esc(s)}</span>' for s in shown) + more + "</div>"


def _open(member_id: str) -> None:
    st.session_state["mb_open"] = member_id


def _rebuild() -> None:
    settings()["graph_at"] = "10.22 " + now_time()
    flash("구성원 정보를 담당자 추천에 다시 반영했어요.")


# ---------------------------------------------------------------- 상세 모달

@st.dialog("구성원 정보")
def _detail_dialog(member_id: str) -> None:
    m = next(x for x in members() if x["id"] == member_id)
    admin = auth.is_admin()
    depts = sorted({x["dept"] for x in members()})

    html(f'<div class="owner-box">{_avatar(m["name"], 44)}<div><div class="fw7" style="font-size:16px">{esc(m["name"])}</div>'
         f'<div class="small muted">{esc(m["dept"])} · {esc(m["role"])}</div></div></div>')

    with st.container(key="mb_kv"):
        c1, c2 = st.columns([1, 3], vertical_alignment="center")
        c1.markdown('<span class="kv-dt">부서</span>', unsafe_allow_html=True)
        if admin:
            dept = c2.selectbox("부서", depts, index=depts.index(m["dept"]), key=f"mb_dept_{member_id}", label_visibility="collapsed")
            if dept != m["dept"]:
                m["dept"] = dept
                flash("부서를 바꿨어요.")
                st.rerun(scope="fragment")
        else:
            c2.markdown(esc(m["dept"]))
        overload = ' <span class="c-err small fw6">· 과부하 경고 기준</span>' if m["load"] >= OVERLOAD else ""
        html(f'<dl class="kv-list"><div><dt>진행 중 업무</dt><dd>{m["load"]}건{overload}</dd></div>'
             f'<div><dt>참여 프로젝트</dt><dd>{esc(", ".join(m["projects"]) or "—")}</dd></div></dl>')

    html('<div class="sub-h" style="margin-top:8px">보유 스킬</div>')
    if admin:
        with st.container(key="mb_skills", horizontal=True, gap="small"):
            for s in list(m["skills"]):
                with st.container(key=f"chipx_{member_id}_{s}", width="content"):
                    if st.button(f"{s}  ✕", key=f"rm_{member_id}_{s}", help=f"{s} 삭제"):
                        m["skills"].remove(s)
                        st.rerun(scope="fragment")
        with st.form(f"mb_addform_{member_id}", clear_on_submit=True, border=False):
            c1, c2 = st.columns([5, 1], vertical_alignment="bottom")
            new = c1.text_input("스킬 추가", placeholder="스킬 추가 (Enter)", label_visibility="collapsed")
            if c2.form_submit_button("추가", use_container_width=True) and new.strip() and new.strip() not in m["skills"]:
                m["skills"].append(new.strip())
                st.toast("스킬을 추가했어요.", icon="✅")
                st.rerun(scope="fragment")
    else:
        html(_chips(m["skills"], limit=None, size=""))

    html('<div class="sub-h" style="margin-top:8px">최근 수행 업무 <span class="muted small" style="font-weight:500">확정된 업무만</span></div>')
    if m["history"]:
        html('<ul class="bullets">' + "".join(f"<li>{esc(h)}</li>" for h in m["history"][:6]) + "</ul>")
    else:
        html('<p class="small muted" style="margin:0">아직 기록이 없어요.</p>')
    html('<p class="tiny muted" style="margin:12px 0 0;padding-top:8px;border-top:1px solid var(--line-2);line-height:1.6">'
         "이 정보는 담당자 추천 점수(스킬 적합도·부서·역할·업무 여유도)에 쓰여요. 프로필이 바뀌거나 회의가 확정되면 추천 데이터가 갱신돼요.</p>")
    with st.container(key="mb_foot", horizontal=True, horizontal_alignment="right"):
        if st.button("닫기", key="mb_close"):
            st.rerun()


# ---------------------------------------------------------------- 화면

def render() -> None:
    admin = auth.is_admin()
    left, right = head_row()
    with left:
        page_head("구성원 관리", "담당자 추천에 쓰이는 구성원 프로필, 스킬, 업무 이력을 관리합니다. 확정된 회의에서 승인된 업무만 이력에 반영돼요.", ["홈", "구성원 관리"])
    with right:
        html(f'<span class="small muted">추천 데이터 갱신 {esc(settings()["graph_at"])}</span>', width="content")
        if admin:
            st.button("추천 데이터 다시 만들기", icon=":material/refresh:", on_click=_rebuild)
    if not admin:
        notice("🔒 열람 전용이에요. 프로필 수정은 시스템 관리자만 할 수 있어요. (데모 계정 ‘admin’으로 로그인해 보세요)")

    depts = sorted({m["dept"] for m in members()})
    with panel("members", flush=True):
        with st.container(key="mb_bar", horizontal=True, vertical_alignment="center", gap="small"):
            q = st.text_input("구성원 검색", key="mb_q", placeholder="이름, 부서, 스킬로 검색", label_visibility="collapsed",
                              icon=":material/search:", width=420)
            dept = st.selectbox("부서", ["all"] + depts, key="mb_dept", label_visibility="collapsed", width=200,
                                format_func=lambda x: "전체 부서" if x == "all" else x)
            s = q.strip()
            rows = [m for m in members() if (dept == "all" or m["dept"] == dept)
                    and (not s or s in m["name"] or s in m["dept"] or any(s in k for k in m["skills"]))]
            html(f'<span class="small muted">{len(rows)}명</span>', width="content")

        with st.container(key="mb_table"):
            html(f'<div class="thead" style="{GRID}"><span>이름</span><span>부서 · 역할</span><span>보유 스킬</span><span>진행 중 업무</span><span>최근 수행 업무</span></div>')
            if not rows:
                html('<div class="small muted" style="padding:24px;text-align:center">조건에 맞는 구성원이 없어요.</div>')
            for m in rows:
                with tap(f"mbrow_{m['id']}", _open, (m["id"],), label=f"{m['name']} 정보 보기"):
                    html(f'<div class="mb-row" style="{GRID}">'
                         f'<span class="mb-name">{_avatar(m["name"])}{esc(m["name"])}</span>'
                         f'<span>{esc(m["dept"])}<div class="t-sub">{esc(m["role"])}</div></span>'
                         f'<span>{_chips(m["skills"])}</span>'
                         f'<span>{_load_bar(m["load"])}</span>'
                         f'<span class="small muted">{esc(m["history"][0] if m["history"] else "—")}</span></div>')

    opened = st.session_state.pop("mb_open", None)
    if opened:
        _detail_dialog(opened)