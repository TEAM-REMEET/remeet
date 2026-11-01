"""브라우저에서 실행되는 작은 스크립트 (클립보드, 스크롤, 쿠키).

스크립트는 화면 레이아웃에 자리를 차지하지 않도록 app.py 가 만든 숨은 컨테이너(js-host)에 넣습니다.
(레이아웃 안에 바로 넣으면 요소가 생겼다 사라지면서 화면이 살짝 움직입니다.)
"""

from __future__ import annotations

import json

import streamlit as st

_HOST_KEY = "_js_host"


def set_host(container) -> None:
    """app.py 에서 한 번: 스크립트를 넣을 숨은 컨테이너 등록."""
    st.session_state[_HOST_KEY] = container


def _run(js: str) -> None:
    html = f"<script>{js}</script>"
    host = st.session_state.get(_HOST_KEY)
    try:
        (host or st).html(html, unsafe_allow_javascript=True)
    except st.errors.StreamlitAPIException:  # 프래그먼트(모달 등) 안에서는 숨은 컨테이너에 못 씀
        st.html(html, unsafe_allow_javascript=True)


def copy_to_clipboard(text: str, nonce: str = "") -> None:
    payload = json.dumps(text, ensure_ascii=False)
    _run(f"/* {nonce} */ try {{ navigator.clipboard && navigator.clipboard.writeText({payload}); }} catch (e) {{}}")


def scroll_into_view(element_id: str, nonce: str = "") -> None:
    """요소가 들어 있는 스크롤 박스만 움직입니다 (페이지 전체는 움직이지 않음)."""
    target = json.dumps(element_id)
    _run(
        f"/* {nonce} */ setTimeout(function () {{"
        f" var el = document.getElementById({target}); if (!el) return;"
        " var box = el.parentElement;"
        " while (box && !(box.scrollHeight > box.clientHeight && /(auto|scroll)/.test(getComputedStyle(box).overflowY)))"
        "   box = box.parentElement;"
        " if (!box) return;"
        " var r = el.getBoundingClientRect(), b = box.getBoundingClientRect();"
        " if (r.top < b.top || r.bottom > b.bottom)"
        "   box.scrollTo({top: box.scrollTop + (r.top - b.top) - 12, behavior: 'smooth'});"
        " }, 120);"
    )


def set_cookie(name: str, value: str, days: int = 7) -> None:
    _run(f"document.cookie = {json.dumps(name)} + '=' + {json.dumps(value)} + '; path=/; max-age={days * 86400}; SameSite=Lax';")


def clear_cookie(name: str) -> None:
    _run(f"document.cookie = {json.dumps(name)} + '=; path=/; max-age=0; SameSite=Lax';")

    

def scroll_to_bottom(selector: str, nonce: str = "") -> None:
    """selector 요소(또는 그 안의 스크롤 박스)를 맨 아래로 부드럽게 스크롤."""
    target = json.dumps(selector)
    _run(
        f"/* {nonce} */ setTimeout(function () {{"
        f" var root = document.querySelector({target}); if (!root) return;"
        " var boxes = [root].concat(Array.prototype.slice.call(root.querySelectorAll('*')));"
        " for (var i = 0; i < boxes.length; i++) {"
        "   var b = boxes[i];"
        "   if (b.scrollHeight > b.clientHeight && /(auto|scroll)/.test(getComputedStyle(b).overflowY)) {"
        "     b.scrollTo({top: b.scrollHeight, behavior: 'smooth'}); return; }"
        " }"
        " }, 150);"
    )