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

def ime_live(label: str) -> None:
    """한글처럼 조합해서 쓰는 글자도 「치는 대로」 검색되게.

    Streamlit 의 live 입력칸은 한글 조합 중(마지막 글자가 아직 확정 전)에는 값을 보내지 않아서 엔터를 쳐야 검색돼요.
    조합 중 입력이 0.4초 멈추면 입력칸을 잠깐 벗어났다(blur) 바로 돌아와(focus) 조합을 확정시켜 값이 바로 반영되게 합니다.
    label: st.text_input 의 label (화면에서는 숨겨도 aria-label 로 남아요)
    """
    _run(
        "window.__imeLabels = window.__imeLabels || [];"
        f" if (window.__imeLabels.indexOf({json.dumps(label)}) < 0) window.__imeLabels.push({json.dumps(label)});"
        " if (!window.__imeLive) { window.__imeLive = true; var timers = new WeakMap();"
        "  document.addEventListener('input', function (e) {"
        "   var el = e.target; if (!el || el.tagName !== 'INPUT' || !e.isComposing) return;"
        "   if (window.__imeLabels.indexOf(el.getAttribute('aria-label') || '') < 0) return;"
        "   clearTimeout(timers.get(el));"
        "   timers.set(el, setTimeout(function () {"
        "    if (document.activeElement !== el) return;"
        "    var pos = el.value.length; el.blur();"
        "    setTimeout(function () { el.focus(); try { el.setSelectionRange(pos, pos); } catch (_) {} }, 0);"
        "   }, 400));"
        "  }, true); }"
    )


def focus_input(label: str, nonce: str = "") -> None:
    """label 이 같은 입력칸에 커서를 두고 글자 끝으로 (검색 결과 페이지로 넘어와도 이어서 칠 수 있게)."""
    target = json.dumps(label)
    _run(
        f"/* {nonce} */ setTimeout(function () {{"
        f" var el = document.querySelector('input[aria-label=' + JSON.stringify({target}) + ']'); if (!el) return;"
        " el.focus(); var n = el.value.length; try { el.setSelectionRange(n, n); } catch (_) {}"
        " }, 120);"
    )
