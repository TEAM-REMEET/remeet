"""사람 고르기 (조직도) — 사용자 화면의 모든 사람 선택 모달이 함께 씀.

구성원 관리의 「조직 선택」과 같은 모양이고, 팀을 펼치면 그 팀 사람이 나와요.
  🔍 이름 · 팀 · 조직 검색
  [최근] [즐겨찾기 ☆] [전체 조직]
  › 📁 제품본부
     ⌄ 📁 프로덕트부
        ⌄ 👥 서비스기획팀  2명
             ☑ 이서윤 · 기획자          ☆
             ☐ 정민재 · 매니저          ☆
팀 · 조직이 많아져도 검색하거나 펼쳐서 찾을 수 있어요.

쓰는 곳: 새 회의록(주관자 · 참석자 · 발화자 연결) · 폴더 멤버 · 담당자 지정 · 회의 공유
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable

import streamlit as st

from state.store import me_id
from data import repository as repo
from state import actions
from state import selectors as sel
from state.store import get_state
from ui.buttons import button, button_group
from ui.html import markup
from utils.text import esc

_TABS = (("recent", "최근"), ("fav", "즐겨찾기 ☆"), ("all", "전체 조직"))


def _k(name: str) -> str:
    """한글 이름 → 위젯 key 용 짧은 영문."""
    return hashlib.md5(name.encode()).hexdigest()[:8]


class _Picker:
    def __init__(self, ctx: str, chosen: list[str], on_pick: Callable[[str], None], *, single: bool,
                 locked: dict[str, str], sub: Callable[[str], str] | None,
                 featured: list[str] | None = None, featured_label: str = "") -> None:
        self.s = get_state()
        self.ctx = ctx
        self.chosen = set(chosen)
        self.on_pick = on_pick
        self.single = single
        self.locked = locked  # 고를 수 없는 사람 → 이유 (예: 이미 멤버)
        self.sub = sub or sel.person_desc
        self.members = dict(sel.team_groups())  # 팀 이름 → [사람 id]
        self.featured = [pid for pid in (featured or []) if pid in repo.people()]  # 맨 앞 탭 (예: 이 회의 참석자)
        self.tabs = ([("featured", featured_label or "추천")] if featured is not None else []) + list(_TABS)

    # ------------------------------------------------------------ 한 사람
    def person(self, pid: str, level: int, *, path: bool = False) -> None:
        s = self.s
        p = repo.people().get(pid)
        if not p:
            return
        on = pid in self.chosen
        lock = self.locked.get(pid)
        if self.single:
            icon = ":material/radio_button_checked:" if on else ":material/radio_button_unchecked:"
        else:
            icon = ":material/check_box:" if on or lock else ":material/check_box_outline_blank:"
        state = "lock" if lock else "on" if on else "off"
        with st.container(key=f"pprow-{level}-{state}-{self.ctx}-{pid}", horizontal=True, vertical_alignment="center",
                          gap=None):
            sub = self.sub(pid)
            label = p.name + (" (나)" if pid == me_id() else "") + (f"  ·  {sub}" if sub else "")
            button(label, key=f"pp_{self.ctx}_{level}_{pid}", variant="ghost", width="stretch", icon=icon,
                   disabled=bool(lock), on_click=actions.people_pick, args=(self.ctx, pid, self.on_pick))
            if lock:
                markup(f'<span class="pp-lock">{esc(lock)}</span>')
            else:
                fav = pid in s.pp_fav
                button("", key=f"ppfav_{self.ctx}_{level}_{pid}", variant="ghost",
                       icon=":material/star:" if fav else ":material/star_outline:", help="즐겨찾기",
                       on_click=actions.people_fav, args=(pid,))
        if path:
            where = " › ".join([*sel.team_ancestors(p.team), p.team])
            markup(f'<span class="pp-path">{esc(where)}</span>')

    # ------------------------------------------------------------ 팀 (펼치면 사람)
    def team(self, name: str, level: int) -> None:
        opened = self.s.pp_open.setdefault(self.ctx, set())
        pids = self.members.get(name, [])
        n_on = sum(1 for pid in pids if pid in self.chosen)
        is_open = name in opened
        with st.container(key=f"pprow-{level}-team-{self.ctx}-{_k(name)}", horizontal=True, vertical_alignment="center",
                          gap=None):
            button("", key=f"pptw_{self.ctx}_{_k(name)}", variant="ghost",
                   icon=":material/expand_more:" if is_open else ":material/chevron_right:",
                   on_click=actions.people_toggle, args=(self.ctx, name))
            label = f"{name}  {len(pids)}명" + (f" · {n_on}명 선택" if n_on and not self.single else "")
            button(label, key=f"ppteam_{self.ctx}_{_k(name)}", variant="ghost", width="stretch", icon=":material/groups:",
                   on_click=actions.people_toggle, args=(self.ctx, name))
        if is_open:
            for pid in pids:
                self.person(pid, level + 1)
            if not pids:
                markup('<span class="pp-path">아직 사람이 없어요</span>')

    # ------------------------------------------------------------ 본부 · 부
    def tree(self, nodes: list[dict], level: int = 0) -> None:
        opened = self.s.pp_open.setdefault(self.ctx, set())
        for n in nodes:
            kids = n.get("children")
            if not kids:
                self.team(n["name"], level)
                continue
            is_open = n["name"] in opened
            with st.container(key=f"pprow-{level}-dir-{self.ctx}-{_k(n['name'])}", horizontal=True,
                              vertical_alignment="center", gap=None):
                button("", key=f"pptw_{self.ctx}_{_k(n['name'])}", variant="ghost",
                       icon=":material/expand_more:" if is_open else ":material/chevron_right:",
                       on_click=actions.people_toggle, args=(self.ctx, n["name"]))
                button(n["name"], key=f"ppdir_{self.ctx}_{_k(n['name'])}", variant="ghost", width="stretch",
                       icon=":material/folder_open:" if is_open else ":material/folder:",
                       on_click=actions.people_toggle, args=(self.ctx, n["name"]))
            if is_open:
                self.tree(kids, level + 1)

    def render(self, height: int) -> None:
        s = self.s
        if self.ctx not in s.pp_open:  # 처음: 고른 사람(없으면 내 팀)이 있는 곳을 펼쳐 둠
            teams = {repo.people()[p].team for p in self.chosen if p in repo.people()} or {sel.me().team}
            s.pp_open[self.ctx] = {x for t in teams for x in (*sel.team_ancestors(t), t)}
        qkey = f"pp_q_{self.ctx}_{s.pp_nonce}"
        q = (st.text_input("이름 · 팀 · 조직 검색", key=qkey, placeholder="이름 · 팀 · 조직 검색", icon=":material/search:",
                           live=True, label_visibility="collapsed") or "").strip().lower()
        tab = s.pp_tab.get(self.ctx, self.tabs[0][0] if self.tabs[0][0] == "featured" else "all")
        if not q:
            with st.container(key=f"pp-tabs-{self.ctx}"):
                button_group(self.tabs, tab, lambda v: actions.people_tab(self.ctx, v), key=f"pp_tab_{self.ctx}",
                             style="tab")
        with st.container(key=f"pp-body-{self.ctx}", height=height, gap=None):
            if q:  # 검색: 이름 · 직무 · 팀 · 본부/부 이름으로
                hits = [pid for pid, p in repo.people().items()
                        if q in p.name.lower() or q in p.team.lower() or q in sel.person_role(pid).lower()
                        or q in sel.team_path(p.team).lower()]
                for pid in hits[:40]:
                    self.person(pid, 0, path=True)
                if not hits:
                    markup('<p class="hint" style="padding:24px 0;text-align:center">찾는 사람이 없어요.</p>')
            elif tab == "featured":
                for pid in self.featured:
                    self.person(pid, 0, path=True)
                if not self.featured:
                    markup('<p class="hint" style="padding:24px 0;text-align:center">보여 줄 사람이 없어요.</p>')
            elif tab == "all":
                self.tree(repo.org_tree())
                for t in [t for t in self.members if t not in _tree_teams()]:  # 조직도에 없는 팀
                    self.team(t, 0)
            else:
                pids = s.pp_recent if tab == "recent" else sorted(s.pp_fav, key=lambda p: repo.people()[p].name
                                                                   if p in repo.people() else p)
                for pid in pids:
                    self.person(pid, 0, path=True)
                if not pids:
                    empty = "최근 고른 사람이 없어요." if tab == "recent" else "☆ 를 눌러 자주 고르는 사람을 즐겨찾기에 넣어 보세요."
                    markup(f'<p class="hint" style="padding:24px 0;text-align:center">{empty}</p>')


def _tree_teams() -> set[str]:
    out: set[str] = set()

    def walk(nodes: list[dict]) -> None:
        for n in nodes:
            if n.get("children"):
                walk(n["children"])
            else:
                out.add(n["name"])

    walk(repo.org_tree())
    return out


def org_people_picker(ctx: str, *, chosen: list[str], on_pick: Callable[[str], None], single: bool = False,
                      locked: dict[str, str] | None = None, sub: Callable[[str], str] | None = None,
                      height: int = 300, featured: list[str] | None = None, featured_label: str = "") -> None:
    """사람 고르기 (조직도 · 검색 · 최근 · 즐겨찾기).

    ctx: 모달마다 다른 이름 (위젯 key · 펼친 상태를 따로 기억)
    chosen: 지금 고른 사람 id · on_pick(pid): 이름을 누르면 (고르기/빼기는 부르는 쪽에서)
    single: 한 명만 (라디오 모양) · locked: 고를 수 없는 사람 → 이유 · sub(pid): 이름 옆 설명
    featured: 맨 앞 탭에 먼저 보여 줄 사람 (예: 이 회의 참석자) · featured_label: 그 탭 이름 · 주면 처음엔 이 탭이 열림
    """
    _Picker(ctx, chosen, on_pick, single=single, locked=locked or {}, sub=sub, featured=featured,
            featured_label=featured_label).render(height)
