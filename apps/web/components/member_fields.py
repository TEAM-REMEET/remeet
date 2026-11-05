"""구성원 입력칸 (직급 · 스킬 및 숙련도). 구성원 추가 줄과 구성원 수정 팝업이 같이 써요.

입력칸 키는 prefix 로 구분해요: {prefix}_position · {prefix}_sk_{줄} · {prefix}_lv_{줄}
스킬은 「스킬 드롭다운 + 숙련도 1~4」 한 줄씩. 줄 목록은 {prefix}_rows,
저장할 때 숙련도를 안 고른 줄은 {prefix}_invalid 에 들고 있다가 빨간 테두리로 보여 줘요.
"""

from __future__ import annotations

import streamlit as st

from data import repository as repo
from ui.buttons import button
from ui.html import markup
from utils.text import esc

POSITION = {"senior": "시니어", "mid": "미드", "junior": "주니어"}
LEVELS = (1, 2, 3, 4)
LEVEL_DESC = {
    1: "지시받아 보조 수행 가능",
    2: "단독으로 수행 가능",
    3: "결과물의 품질을 책임지고 판단 가능",
    4: "타인을 지도하거나 기준을 수립 가능",
}
_LEVEL_HELP = "  \n".join(f"Lv{k} — {v}" for k, v in LEVEL_DESC.items())


def position_select(prefix: str, current: str | None = None) -> None:
    """직급 (담당자 추천이 직급별로 후보를 뽑아요)."""
    options = list(POSITION)
    st.selectbox("직급", options, index=options.index(current) if current in options else None,
                 key=f"{prefix}_position", format_func=POSITION.get, placeholder="직급 선택")


def _add_row(prefix: str) -> None:
    rows = st.session_state[f"{prefix}_rows"]
    rows.append(max(rows, default=0) + 1)


def _remove_row(prefix: str, rid: int) -> None:
    st.session_state[f"{prefix}_rows"].remove(rid)


def skill_fields(prefix: str, current: dict[str, int] | None = None) -> None:
    """스킬 및 숙련도: 줄마다 스킬 드롭다운 + 숙련도 1~4 + 그 숙련도 설명. current: {스킬 id: 숙련도} (지금 값)."""
    skills = {k["skill_id"]: k for k in repo.catalog("skills")}
    if f"{prefix}_rows" not in st.session_state:  # 처음 그릴 때만 지금 값으로 채움
        start = [(k, lv) for k, lv in (current or {}).items() if k in skills]
        st.session_state[f"{prefix}_rows"] = list(range(1, len(start) + 1))
        for rid, (k, lv) in enumerate(start, 1):
            st.session_state[f"{prefix}_sk_{rid}"] = k
            st.session_state[f"{prefix}_lv_{rid}"] = lv if lv in LEVELS else 2
    markup('<span class="adm-flabel">스킬 및 숙련도</span>')
    for rid in st.session_state[f"{prefix}_rows"]:
        c = st.columns([3, 1.9, 2.6, 0.45], gap="small", vertical_alignment="center")
        with c[0]:
            st.selectbox("스킬", list(skills), index=None, key=f"{prefix}_sk_{rid}", placeholder="스킬 검색",
                         format_func=lambda k: f"{skills[k]['name']} · {skills[k]['domain']}",
                         label_visibility="collapsed")
        with c[1]:
            lv = st.session_state.get(f"{prefix}_lv_{rid}")
            missing = lv is None and rid in st.session_state.get(f"{prefix}_invalid", ())  # 고르면 바로 테두리 사라짐
            with st.container(key=f"lv{'-missing' if missing else ''}-{prefix}-{rid}"):
                st.segmented_control("숙련도", LEVELS, key=f"{prefix}_lv_{rid}", help=_LEVEL_HELP,
                                     label_visibility="collapsed")
        with c[2]:
            if missing:
                markup('<span class="cp-error">숙련도를 골라 주세요</span>')
            else:
                markup(f'<span class="hint">{esc(LEVEL_DESC.get(lv, "숙련도를 골라 주세요"))}</span>')
        with c[3]:
            button("", key=f"{prefix}_rm_{rid}", variant="ghost", icon=":material/close:", help="이 스킬 빼기",
                   on_click=_remove_row, args=(prefix, rid))
    button("스킬 추가", key=f"{prefix}_add", variant="secondary", icon=":material/add:", on_click=_add_row, args=(prefix,))


def read_skills(prefix: str) -> tuple[list[dict], str]:
    """입력칸 값 → (API 형식 [{skill_id, level}], 오류 문구). 스킬을 안 고른 줄은 무시해요."""
    out, seen, missing, dup = [], set(), set(), False
    for rid in st.session_state.get(f"{prefix}_rows", []):
        k, lv = st.session_state.get(f"{prefix}_sk_{rid}"), st.session_state.get(f"{prefix}_lv_{rid}")
        if not k:
            continue
        dup |= k in seen
        seen.add(k)
        if lv not in LEVELS:
            missing.add(rid)
        out.append({"skill_id": k, "level": lv})
    st.session_state[f"{prefix}_invalid"] = missing
    if missing:
        return [], "숙련도를 고르지 않은 스킬이 있어요."
    if dup:
        return [], "같은 스킬을 두 번 골랐어요. 한 줄은 빼 주세요."
    return out, ""
