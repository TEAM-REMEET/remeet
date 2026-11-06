"""상태 변경 함수 모음.

버튼의 on_click 콜백으로 바로 넘길 수 있도록 모두 `state` 를 직접 받지 않고 get_state() 로 꺼내 씁니다.
화면 코드는 상태를 직접 고치지 말고 여기 있는 함수만 호출하세요.
"""

from __future__ import annotations

import time

import streamlit as st

from state.store import me_id
from data import api
from components import member_fields as mf
from data import repository as repo
from data.models import Todo
from state import router
from utils.files import ALLOWED_LABEL, is_allowed, read_text
from state import selectors as sel
from state.store import Delivery, ExportRun, NewFolder, SentShare, get_state, reset_state

# 화면을 옮길 때 항상 초기화하는 값
_SCREEN_RESET = dict(editing=False, highlight=[], highlight_key="", show_brief=False, raw_query="", copied="", nn_error="",
                     not_found="")


def _set(**kwargs) -> None:
    s = get_state()
    for k, v in kwargs.items():
        setattr(s, k, v)


def update(**kwargs) -> None:
    """단순 값 변경용 콜백. 예: on_click=update, kwargs={"home_role": "host"}"""
    _set(**kwargs)


def toggle(attr: str) -> None:
    s = get_state()
    setattr(s, attr, not getattr(s, attr))


# ---------------------------------------------------------------- 인증
def login(email: str, password: str) -> bool:
    """이메일 · 비밀번호 → 백엔드 확인 (Neo4j). 관리자면 관리자 화면, 아니면 새 회의록 화면으로."""
    try:
        res = repo.login(email.strip(), password)
    except api.ApiError as e:
        _set(logged_in=False, login_error=str(e))
        return False
    start_session(res["token"], res["user"])
    return True


def start_session(token: str, user: dict, restore: bool = False) -> None:
    """로그인 성공 → 그 사람으로 첫 화면 시작. restore: 새로고침 후 쿠키로 되살림 (보던 주소 그대로)."""
    admin = user["permission"] == "admin"
    _set(logged_in=True, login_error="", is_admin=admin, token=token, user_id=user["user_id"],
         user_name=user["name"], user_email=user["email"], nn_host=user["user_id"], nn_people=[user["user_id"]], cookie_written=restore)
    if not restore:
        screen = "adm_cost" if admin else "compose"
        _set(screen=screen, not_found="")
        router.request(screen)


def logout() -> None:
    reset_state(logged_out=True)
    router.request("compose")


# ---------------------------------------------------------------- 이동
def go(screen: str, **extra) -> None:
    """화면 이동: 상태를 정리하고 URL 이동을 예약합니다 (실제 이동은 app.py 의 router.apply_pending)."""
    _set(screen=screen, **{**_SCREEN_RESET, **extra})
    router.request(screen)


def open_archive(mtype: str = "") -> None:
    """회의록 화면: 회의록(회의 종류 카드) → 회의 종류의 회의 목록."""
    s = get_state()
    go("archive", archive_type=mtype, archive_role="all", archive_query="", archive_qnonce=s.archive_qnonce + 1)


def start_search(widget_key: str) -> None:
    """회의록 첫 화면 검색칸: 치는 즉시 검색 결과 페이지로."""
    s = get_state()
    q = (st.session_state.get(widget_key) or "").strip()
    if not q:
        return
    go("search", search_query=q, search_tab="", search_nonce=s.search_nonce + 1,
       archive_qnonce=s.archive_qnonce + 1)  # 돌아가면 회의록 화면 검색칸은 비어 있게


def set_search_query(widget_key: str) -> None:
    """검색 결과 페이지에서 검색어를 고칠 때 (페이지는 그대로)."""
    _set(search_query=(st.session_state.get(widget_key) or "").strip(), search_tab="")


def open_meeting_search(mid: str, q: str) -> None:
    """회의 내용 검색 결과 → 회의를 열고 원문에서 그 글자를 찾아 둠."""
    open_meeting(mid)
    _set(raw_query=q, left_tab="raw")


def tree_toggle(node: str) -> None:
    """사이드바 트리 화살표: 화면 이동 없이 바로 펼치기/접기."""
    s = get_state()
    if node in s.tree_open:
        s.tree_open.discard(node)
    else:
        s.tree_open.add(node)


def set_tree_mode(mode: str) -> None:
    """사이드바 회의록 트리 토글: time(정기 회의) | project(프로젝트 단위)."""
    get_state().tree_mode = mode


def tree_click(node: str) -> None:
    """사이드바 트리 폴더 이름: 그 화면으로 이동 + 펼치기 (접기는 화살표로).

    연도(y:) · 월(ym:) · 프로젝트 묶음(proj)은 해당 화면이 없어 펼치기만 합니다.
    """
    s = get_state()
    s.tree_open.add(node)
    kind, *rest = node.split(":")
    if kind == "root":
        open_archive()
    elif kind == "t":
        open_archive(rest[0])
    elif kind == "pf":
        open_folder(rest[0])
    elif kind == "m":
        open_meeting(rest[0])


def open_folder(fid: str) -> None:
    f = sel.folders(get_state())[fid]
    go("folder", folder_id=fid, folder_tab="all", folder_role="all", todo_group="")


def open_meeting(mid: str, *, editing: bool = False) -> None:
    # 확정 흐름(fin_flow)은 지우지 않음 → 흐름 중에 회의 상세로 와도 단계 표시가 이어짐 (외부 도구 전송을 마칠 때 끝)
    go("note", meeting_id=mid, insight_tab="sum", left_tab="raw", just_shared=False, todo_scope="all")
    _set(editing=editing)


def open_source(mid: str, src: tuple[str, ...]) -> None:
    """위키 타임라인의 출처 → 해당 회의의 원문 발언 강조."""
    go("note", meeting_id=mid, insight_tab="sum", left_tab="raw", just_shared=False)
    _set(highlight=list(src), highlight_key="wiki")


# ---------------------------------------------------------------- 모달
def open_modal(name: str, **extra) -> None:
    _set(modal=name, **extra)


def close_modal() -> None:
    get_state().pp_nonce += 1  # 사람 고르기 검색칸 비우기 (다음에 열 때 새로)
    _set(modal=None, assign_pending="", members_folder="", members_scope=[], delete_target=())


def _wizard_reset() -> dict:
    """새 회의록 2단계 작성을 처음(1단계 · 빈 칸)으로."""
    return dict(cp_step=1, nn_title="", nn_date=None, nn_text="", nn_files=[], nn_speaker_map={}, nn_error="",
                cp_example="plain", preview_meeting=None)


def open_new_note() -> None:
    """'새 회의록' → 작성 화면. 보고 있던 프로젝트 · 회의 종류를 미리 골라 둡니다."""
    s = get_state()
    mtype = s.archive_type if s.screen == "archive" and s.archive_type else s.nn_type
    topic = s.folder_id if s.screen == "folder" and s.folder_id in sel.folders(s) else ""
    go("compose", nn_type=mtype, nn_topic=topic, nn_name="", nn_host=me_id(), nn_people=[me_id()],
       compose_nonce=s.compose_nonce + 1, **_wizard_reset())


def compose_text(s) -> str:
    """직접 쓴 내용 + 첨부한 TXT · MD · DOCX · HWPX 내용."""
    parts = [s.nn_text.strip()] + [body.strip() for _, body in s.nn_files if body]  # 읽지 못한 파일은 빼고
    return "\n\n".join(p for p in parts if p)  # 직접 쓴 내용 · 파일 사이도 한 줄 띄움 (빈 줄 규칙)


def compose_example(kind: str) -> None:
    """「지원하는 입력 형식」 버튼: 입력 형식을 고름. 입력칸에는 그 형식의 예시가 회색 안내글(placeholder)로만 보임.

    입력한 내용(nn_text)은 건드리지 않음 — 예시는 값이 아니라서 수정·제출되지 않음.
    """
    _set(cp_example=kind, nn_error="")


def compose_reset() -> None:
    """초기화: 1단계의 입력을 모두 비움 (기본 정보 포함)."""
    s = get_state()
    _set(compose_nonce=s.compose_nonce + 1, nn_host=me_id(), nn_people=[me_id()], nn_name="", **_wizard_reset())


def compose_sync(nonce: int) -> None:
    """1단계 입력칸 값을 상태에 옮겨 둠 (다른 단계로 가도 남도록)."""
    s = get_state()
    ss = st.session_state
    s.nn_title = (ss.get(f"cp_title_{nonce}") or "").strip()
    s.nn_date = ss.get(f"cp_date_{nonce}")
    s.nn_text = ss.get(f"cp_text_{nonce}_{s.cp_text_rev}", s.nn_text) or ""
    files = ss.get(f"cp_files_{nonce}") or []
    if files:  # TXT · MD · DOCX · HWPX 는 글을 읽고, 그 밖의 형식 · 읽을 수 없는 파일은 None (발화자 확인 때 안내)
        s.nn_files = [(f.name, read_text(f.name, f.getvalue())) for f in files]


def compose_check() -> None:
    """회의 내용 칸을 바꿀 때마다(칸을 벗어나면) 형식 검사 결과를 새로."""
    get_state().nn_error = ""


def _format_error(error: str) -> None:
    """「다음 단계」를 눌렀는데 입력이 맞지 않음: 입력칸 아래 빨간 안내 + 경고 창."""
    _set(nn_error=error, modal="cp_format_error")


def compose_next(nonce: int) -> None:
    """1단계 「다음 단계」 → 2단계(분석 및 검토): 기본 정보 · 입력 형식 · 발화자(참석자 안에 있는지)를 확인."""
    from state import compose_ai

    compose_sync(nonce)
    s = get_state()
    missing = [n for n, ok in (("회의 제목", s.nn_title), ("회의 날짜", s.nn_date), ("주관자", s.nn_host)) if not ok]
    if s.nn_topic not in sel.all_my_folder_ids(s) and not s.nn_name.strip():
        missing.append("프로젝트")
    parsed = compose_ai.parse(compose_text(s))
    if missing:
        _format_error(" · ".join(missing) + "을(를) 입력해 주세요.")
        return
    # 첨부 파일: TXT · MD · DOCX · HWPX 만 (그 밖의 형식이면 다음 단계로 못 감)
    wrong = [name for name, _ in s.nn_files if not is_allowed(name)]
    if wrong:
        _format_error(f"지원하지 않는 파일 형식이에요: {', '.join(wrong)} · {ALLOWED_LABEL} 파일만 첨부할 수 있어요.")
        return
    broken = [name for name, body in s.nn_files if body is None]
    if broken:
        _format_error(f"파일을 읽을 수 없어요: {', '.join(broken)} · 파일이 손상되지 않았는지 확인해 주세요.")
        return
    if parsed.error or parsed.format != s.cp_example:  # 고른 형식에서 벗어나면 다음 단계로 못 감
        _format_error(f"회의 내용이 「{compose_ai.FORMAT_LABEL[s.cp_example]}」 형식에 맞지 않아요. "
                      + (parsed.error or "입력칸 위 안내대로 적어 주세요."))
        return
    # 발화자는 모두 참석자여야 함 (참석자 안에 없는 발화자가 있으면 형식 오류 · 미지정 없음)
    mapping, unknown = compose_ai.match_speakers(parsed.labels, [s.nn_host, *s.nn_people])
    if unknown:
        _format_error(f"참석자에 없는 발화자가 있어요: {', '.join(unknown)} · "
                      "발화자는 참석자 이름으로 적거나, 참석자에 그 사람을 추가해 주세요.")
        return
    s.nn_speaker_map = mapping
    compose_goto(2)


PREVIEW_ID = "cp-preview"


def _build_preview(s, mid: str):
    from state import compose_ai

    parsed = compose_ai.parse(compose_text(s))
    topic = s.nn_topic if s.nn_topic in sel.all_my_folder_ids(s) else None
    return compose_ai.build_meeting(
        mid, title=s.nn_title, day=s.nn_date, project_id=topic, directory_id=s.nn_type, host=s.nn_host,
        people=list(s.nn_people),
        parsed=parsed, mapping=dict(s.nn_speaker_map), files=[n for n, _ in s.nn_files],
    )


def compose_goto(step: int) -> None:
    """단계 이동. 2단계(분석 및 검토)로 가면 회의 상세와 같은 화면으로 볼 미리보기 회의록을 만듦."""
    s = get_state()
    if step == 2:
        s.preview_meeting = _build_preview(s, PREVIEW_ID)
        s.added_todos.pop(PREVIEW_ID, None)
        s.deleted_todos.pop(PREVIEW_ID, None)
        _set(meeting_id=PREVIEW_ID, insight_tab="sum", left_tab="raw", highlight=[], highlight_key="", raw_query="")
    _set(cp_step=step, nn_error="")


def compose_clear_files() -> None:
    _set(nn_files=[])


def open_compose_confirm() -> None:
    """2단계 「확정하기」(주관자) · 「검토 요청하기」(주관자가 아닌 사람) → 한 번 더 묻는 창."""
    open_modal("cp_confirm" if get_state().nn_host == me_id() else "cp_review")


def compose_confirm() -> None:
    """확인 창에서 확정 · 검토 요청: 다음 화면에서 로딩 화면을 띄운 채 회의록을 만듦 (views/compose.py → compose_finish)."""
    _set(modal=None, cp_finishing=True)


def compose_finish() -> None:
    """확정하기 로딩이 끝나면: 완성된 회의록을 만들어 폴더에 넣고, 회의록 확정 1단계(담당자 추천)로 이동."""
    from state import compose_ai

    s = get_state()
    parsed = compose_ai.parse(compose_text(s))
    s.cp_finishing = False
    if parsed.error:
        _set(cp_step=1, nn_error="회의 내용 형식을 확인해 주세요.")
        return
    # 프로젝트: 고른 프로젝트, 없으면 적은 이름으로 새로 만들기 (회의 종류는 프로젝트가 아니라 이 회의에)
    if s.nn_topic in sel.all_my_folder_ids(s):
        fid = s.nn_topic
    else:
        fid = f"n{len(s.new_folders) + 1}"
        s.new_folders.append(NewFolder(fid, s.nn_name.strip() or "새 프로젝트"))
    mid = f"new{len(s.new_meetings) + 1}"
    s.new_meetings[mid] = compose_ai.build_meeting(
        mid, title=s.nn_title, day=s.nn_date, project_id=fid, directory_id=s.nn_type, host=s.nn_host,
        people=list(s.nn_people),
        parsed=parsed, mapping=dict(s.nn_speaker_map), files=[n for n, _ in s.nn_files],
    )
    s.created_notes.append((fid, mid))
    for store in (s.assigned, s.todo_due):  # 미리보기에서 정한 담당자 · 마감일을 새 회의로 옮김
        for key in [k for k in store if k.startswith(f"{PREVIEW_ID}:")]:
            store[f"{mid}:{key.split(':', 1)[1]}"] = store.pop(key)
    if PREVIEW_ID in s.added_todos:  # 미리보기에서 직접 추가한 액션 아이템도 새 회의로
        s.added_todos[mid] = s.added_todos.pop(PREVIEW_ID)
    if PREVIEW_ID in s.deleted_todos:  # 미리보기에서 삭제한 액션 아이템도
        s.deleted_todos[mid] = s.deleted_todos.pop(PREVIEW_ID)
    s.preview_meeting = None
    title = s.nn_title
    if s.nn_host == me_id():  # 확정 → 담당자 추천 → 최종 회의록 → 결과 전달 → 외부 도구 전송
        start_finalize(mid)
        toast = f"「{title}」 회의록을 확정했어요. 담당자를 확인해 주세요."
    else:  # 다른 사람을 주관자로 정함 → 주관자에게 검토 요청 알림 (주관자가 검토 · 편집한 뒤 확정)
        from state.delivery import now_text

        host = sel.name_of(s.nn_host)
        s.review_requested.add(mid)
        repo.push_notification("user", "review", f"{sel.me().name}님이 「{title}」 회의록 검토를 요청했어요. "
                               f"주관자 {host}님이 검토한 뒤 확정해 주세요.", now_text(), mid)
        open_meeting(mid)
        toast = f"주관자 {host}님에게 「{title}」 회의록 검토를 요청했어요."
    _set(toast=toast,
         compose_nonce=s.compose_nonce + 1, nn_host=me_id(), nn_people=[me_id()], nn_name="", **_wizard_reset())


# ---------------------------------------------------------------- 작성 화면 · 선택 모달
def open_picker(kind: str) -> None:
    """kind: cp_host(주관자) | cp_people(참석자) — 지금 값을 임시 선택으로 복사해 모달 열기."""
    s = get_state()
    if kind == "cp_people" and s.nn_host not in s.nn_people:  # 주관자는 참석자에 늘 체크된 채로
        s.nn_people = [s.nn_host, *s.nn_people]
    draft = [s.nn_host] if kind == "cp_host" else list(s.nn_people)
    first = draft[0] if draft else me_id()
    open_modal(kind, pick_draft=draft, pick_team=sel.person(first).team, pick_folder=s.nn_topic,
               pick_name=s.nn_name, pick_type=s.nn_type,
               pick_query="", pick_nonce=s.pick_nonce + 1)


def set_compose_type(mtype: str) -> None:
    """작성 화면: 이 회의의 종류 (고른 프로젝트는 그대로 · 한 프로젝트에 여러 종류의 회의가 있음)."""
    _set(nn_type=mtype, nn_error="")


# ---------------------------------------------------------------- 사람 고르기 (조직도 · components/org_people.py)
def people_pick(ctx: str, pid: str, on_pick) -> None:
    """이름을 누름: 부르는 쪽의 고르기/빼기 + 「최근」에 넣기."""
    s = get_state()
    on_pick(pid)
    s.pp_recent = [pid] + [p for p in s.pp_recent if p != pid][:7]


def people_toggle(ctx: str, node: str) -> None:
    s = get_state()
    s.pp_open.setdefault(ctx, set()).symmetric_difference_update({node})


def people_tab(ctx: str, tab: str) -> None:
    get_state().pp_tab[ctx] = tab


def people_fav(pid: str) -> None:
    s = get_state()
    s.pp_fav ^= {pid}


def pick_person(pid: str, single: bool) -> None:
    """새 회의록 · 주관자/참석자, 액션 아이템 담당자: 한 명이면 바꾸기, 여러 명이면 고르기/빼기."""
    s = get_state()
    if s.modal == "cp_people" and pid == s.nn_host:  # 참석자 고르기: 주관자는 늘 체크된 채로 (뺄 수 없음)
        return
    if single:
        s.pick_draft = [] if s.pick_draft == [pid] else [pid]
    elif pid in s.pick_draft:
        s.pick_draft.remove(pid)
    else:
        s.pick_draft.append(pid)


def pick_people(team_ids: list[str], widget_key: str, single: bool) -> None:
    """팀원 칩을 누를 때: 이 팀에서 고른 사람으로 임시 선택을 갱신 (다른 팀에서 고른 사람은 그대로)."""
    s = get_state()
    value = st.session_state.get(widget_key)
    if single:
        s.pick_draft = [value] if value else []
    else:
        s.pick_draft = [p for p in s.pick_draft if p not in team_ids] + list(value or [])
        if s.modal == "cp_people" and s.nn_host not in s.pick_draft:  # 주관자는 뺄 수 없음
            s.pick_draft.insert(0, s.nn_host)


def confirm_pick(kind: str) -> None:
    s = get_state()
    if kind == "cp_host":  # 주관자는 참석자에 항상 들어가요 (바꾸면 예전 주관자는 참석자에서 빠짐)
        old, s.nn_host = s.nn_host, (s.pick_draft[0] if s.pick_draft else s.nn_host)
        s.nn_people = [s.nn_host] + [p for p in s.nn_people if p not in (old, s.nn_host)]
    elif kind == "cp_people":
        s.nn_people = [s.nn_host] + [p for p in s.pick_draft if p != s.nn_host]
    s.nn_error = ""
    close_modal()


# ---------------------------------------------------------------- 폴더
def add_member(fid: str, pid: str = "dw") -> None:
    s = get_state()
    cur = s.members_added.setdefault(fid, [])
    if pid not in cur and pid not in sel.members_of(s, fid):
        cur.append(pid)
    s.members_removed.discard(f"{fid}:{pid}")

def add_members(fid: str, pids: list[str]) -> None:
    """멤버 관리 모달: 팀에서 고른 사람들을 한 번에 추가."""
    for pid in pids:
        add_member(fid, pid)
    get_state().member_nonce += 1


def remove_member(fid: str, pid: str) -> None:
    get_state().members_removed.add(f"{fid}:{pid}")


def open_members(fid: str, scope: list[str] | None = None) -> None:
    """멤버 모달. scope 를 주면(기간 폴더) 그 안의 프로젝트 폴더를 골라 가며 관리."""
    open_modal("members", members_folder=fid, members_scope=list(scope or []))


def open_delete(*target: str) -> None:
    """삭제 확인 모달. target: ("folder", fid) | ("meeting", mid)"""
    open_modal("delete_meeting" if target[0] == "meeting" else "delete_folder", delete_target=tuple(target))


def delete_meeting(mid: str) -> None:
    """주관자: 회의 삭제 → 그 회의가 있던 폴더로."""
    s = get_state()
    m = repo.meeting(mid)
    s.deleted_meetings.add(mid)
    if m.project_id in sel.folders(s):
        open_folder(m.project_id)
    else:
        go("compose")
    _set(modal=None, delete_target=(), toast=f"「{m.title}」 회의를 삭제했어요.")


def delete_folder(fid: str) -> None:
    s = get_state()
    name = sel.folders(s)[fid].name
    s.deleted_folders.add(fid)
    open_archive()
    _set(modal=None, delete_target=(), toast=f"「{name}」 폴더를 삭제했어요.")


# ---------------------------------------------------------------- 회의 상세
def pick_evidence(key: str, src: tuple[str, ...]) -> None:
    """요약·결정 문장 클릭 → 원문 근거 강조 (같은 걸 다시 누르면 해제)."""
    s = get_state()
    m = repo.meeting(s.meeting_id)
    if (m.shared and m.shared.lock_raw) or not src:
        return
    if s.highlight_key == key:
        _set(highlight=[], highlight_key="")
    else:
        _set(highlight=list(src), highlight_key=key, left_tab="raw", raw_query="")


def toggle_agenda_detail(mid: str, index: int) -> None:
    """요약 탭 「안건별 상세」: 안건 하나 펼치기/접기."""
    s = get_state()
    k = f"{mid}:{index}"
    _set(agenda_open=[x for x in s.agenda_open if x != k] if k in s.agenda_open else [*s.agenda_open, k])


def set_all_agenda_details(mid: str, count: int, open_: bool) -> None:
    s = get_state()
    rest = [x for x in s.agenda_open if not x.startswith(f"{mid}:")]
    _set(agenda_open=rest + ([f"{mid}:{i}" for i in range(count)] if open_ else []))


def jump_to_agenda(mid: str, index: int) -> None:
    """안건 목차 → 해당 안건 상세를 펼치고 그 자리로 스크롤."""
    s = get_state()
    k = f"{mid}:{index}"
    _set(agenda_open=s.agenda_open if k in s.agenda_open else [*s.agenda_open, k],
         agenda_jump=f"agd-{mid}-{index}", agenda_jump_n=s.agenda_jump_n + 1)


def open_add_todo() -> None:
    """「+ 액션 아이템 추가」: 주관자만."""
    s = get_state()
    if sel.my_role(repo.meeting(s.meeting_id)) != "host":
        return
    open_modal("add_todo", todo_nonce=s.todo_nonce + 1, todo_draft_text="", todo_draft_owners=[], todo_draft_due=None)


def keep_todo_due(due_key: str) -> None:
    """추가 모달의 마감일: 고르는 즉시 저장 (담당자 고르기 창에 다녀와도 남도록)."""
    get_state().todo_draft_due = st.session_state.get(due_key)


def open_todo_owner_picker(text: str) -> None:
    """추가 모달 「담당자」 칸 → 사람 찾기 창 (맨 앞 탭: 이 회의 참석자 · 그다음 최근 · 즐겨찾기 · 전체 조직)."""
    s = get_state()
    s.todo_draft_text = text or ""
    s.pp_tab.pop("todo_owner", None)  # 열 때마다 「이 회의 참석자」 탭부터
    open_modal("todo_owner", pick_draft=list(s.todo_draft_owners), pick_nonce=s.pick_nonce + 1)


def back_to_add_todo(confirm: bool = False) -> None:
    """사람 찾기 창 → 추가 모달로 돌아감 (confirm 이면 고른 사람들을 담당자로 · 여러 명 가능)."""
    s = get_state()
    if confirm:
        s.todo_draft_owners = list(s.pick_draft)
    s.pp_nonce += 1
    _set(modal="add_todo")


def remove_todo_owner(pid: str) -> None:
    """추가 모달: 고른 담당자 한 명 빼기."""
    s = get_state()
    s.todo_draft_owners = [p for p in s.todo_draft_owners if p != pid]


def submit_todo(mid: str, text: str, owners: list[str], due: str) -> bool:
    """액션 아이템 직접 추가 (주관자만): 원문에서 근거 발언을 찾아 함께 넣음 (로딩 화면 없이 바로)."""
    from state import todo_ai

    s = get_state()
    m = repo.meeting(mid)
    if sel.my_role(m) != "host" or not text.strip():
        return False
    src = todo_ai.rescan(m, text.strip())
    todo = Todo(text.strip(), tuple(dict.fromkeys(owners)), due, tuple(src))
    s.added_todos = {**s.added_todos, mid: [*s.added_todos.get(mid, []), todo]}
    note = f"근거 발언 {len(src)}개를 찾았어요." if src else "원문에서 관련 발언을 찾지 못했어요."
    _set(modal=None, todo_scope="all", toast=f"액션 아이템을 추가했어요. {note}")
    return True


def clear_highlight() -> None:
    _set(highlight=[], highlight_key="")


def set_insight_tab(tab: str) -> None:
    _set(insight_tab=tab, highlight=[], highlight_key="")


def search_keyword(keyword: str) -> None:
    s = get_state()
    if s.raw_query == keyword:
        _set(raw_query="")
    else:
        _set(raw_query=keyword, left_tab="raw", highlight=[], highlight_key="")


def delete_todo(mid: str, index: int) -> None:
    """액션 아이템 삭제 (주관자만) · 아래 「삭제한 액션 아이템」에서 되돌릴 수 있음. 체크(완료)와는 별개."""
    s = get_state()
    if sel.my_role(repo.meeting(mid)) != "host":
        return
    gone = s.deleted_todos.get(mid, [])
    if index not in gone:
        s.deleted_todos = {**s.deleted_todos, mid: [*gone, index]}
    text = repo.meeting(mid).todos[index].text
    _set(highlight=[], highlight_key="", toast=f"「{text}」을(를) 삭제했어요. 목록 아래에서 되돌릴 수 있어요.")


def restore_todo(mid: str, index: int) -> None:
    s = get_state()
    if sel.my_role(repo.meeting(mid)) != "host":
        return
    s.deleted_todos = {**s.deleted_todos, mid: [i for i in s.deleted_todos.get(mid, []) if i != index]}


def restore_all_todos(mid: str) -> None:
    """삭제한 액션 아이템 모두 되돌리기 (리셋)."""
    s = get_state()
    if sel.my_role(repo.meeting(mid)) != "host":
        return
    n = len(s.deleted_todos.get(mid, []))
    s.deleted_todos = {k: v for k, v in s.deleted_todos.items() if k != mid}
    _set(toast=f"삭제한 액션 아이템 {n}개를 모두 되돌렸어요.")


def mark_copied(key: str) -> None:
    _set(copied=key)


def start_edit() -> None:
    s = get_state()
    s.agenda_backup = list(s.added_agendas.get(s.meeting_id, []))  # 취소하면 이 상태로 되돌림
    _set(editing=True, just_shared=False)


def stop_edit() -> None:
    """편집 취소: 이번 편집에서 추가·수정한 안건도 편집 전으로 되돌림 (확정하면 남음)."""
    s = get_state()
    if s.editing:
        s.added_agendas[s.meeting_id] = list(s.agenda_backup)
    _set(editing=False)


def add_agenda(mid: str) -> None:
    """편집 중 「+ 안건 추가」: 빈 안건 한 줄을 넣고 바로 제목을 적을 수 있게."""
    s = get_state()
    s.added_agendas.setdefault(mid, []).append("")


def set_agenda_title(mid: str, index: int, widget_key: str) -> None:
    get_state().added_agendas[mid][index] = (st.session_state.get(widget_key) or "").strip()


def remove_agenda(mid: str, index: int) -> None:
    s = get_state()
    s.added_agendas[mid].pop(index)


def request_branch(mid: str) -> None:
    """주관자가 아닐 때 「주관자에게 요청」: 안건 분리 요청을 보내고 알림."""
    s = get_state()
    s.branch_requests.add(mid)
    host = sel.person(repo.meeting(mid).host).name
    s.toast = f"{host} 님에게 안건 분리를 요청했어요. 주관자가 분리하면 알려드릴게요."


def branch_agenda() -> None:
    _set(branched=True)

def open_share() -> None:
    s = get_state()
    open_modal("share", share_nonce=s.share_nonce + 1, share_scope="summary", share_team="", share_recipients=[])


def toggle_share_recipient(pid: str) -> None:
    """회의 공유 모달 · 조직도에서 사람을 누름: 고르기/빼기."""
    s = get_state()
    if pid in s.share_recipients:
        s.share_recipients.remove(pid)
    else:
        s.share_recipients.append(pid)


def set_share_team_selection(team_id: str, widget_key: str) -> None:
    """공유 모달: 지금 보고 있는 팀에서 선택된 사람으로 받는 사람을 갱신 (다른 팀 선택은 그대로 유지)."""
    s = get_state()
    team_ids = {mb.id for mb in repo.teams()[team_id].members}
    picked = list(st.session_state.get(widget_key) or [])
    s.share_recipients = [pid for pid in s.share_recipients if pid not in team_ids] + picked


def share_summary(recipients: dict[str, list[str]]) -> str:
    """{'재무팀': ['강수현', '오지훈']} → '재무팀(강수현 외 1명)'"""
    parts = []
    for team, names in recipients.items():
        parts.append(f"{team}({names[0]}" + (f" 외 {len(names) - 1}명" if len(names) > 1 else "") + ")")
    return ", ".join(parts)


def confirm_share(recipients: dict[str, list[str]]) -> None:
    """recipients: 팀 이름 → 고른 구성원 이름 목록"""
    s = get_state()
    to = share_summary(recipients)
    # 이 회의에 빠진 사람에게 보냈다면 → 그 사람에게 이 회의가 「미참석 · 공유받음」으로 보이게 기록
    m = repo.meeting(s.meeting_id)
    absent = [pid for pid in s.share_recipients if pid not in m.people and pid != m.host]
    if absent:
        cur = s.absent_shares.setdefault(s.meeting_id, [])
        cur.extend(pid for pid in absent if pid not in cur)
    s.sent_shares.insert(0, SentShare(s.meeting_id, to, s.share_scope, "11/1"))
    _set(modal=None, just_shared=True, last_share_to=to)


def revoke_share(index: int) -> None:
    s = get_state()
    item = s.sent_shares.pop(index)
    s.toast = f"「{repo.meeting(item.meeting_id).title}」 공유를 취소했어요."


# ---------------------------------------------------------------- 담당자 지정
def _current_owners(key: str) -> list[str]:
    s = get_state()
    t = sel.todo_at(s, key)
    return list(sel.todo_owner_ids(s, key, t)) if t else list(s.assigned.get(key, []))


def open_assign(key: str, text: str) -> None:
    open_modal(
        "assign",
        assign_key=key,
        assign_text=text,
        assign_searching=False,
        assign_pending="",
        assign_query="",
        assign_team="",
        assign_draft=_current_owners(key),  # 지금 담당자들(회의록 담당자 포함)을 미리 골라 둠
    )


def toggle_assignee(pid: str) -> None:
    """담당자 모달 · 조직도: 누르면 고르고, 다시 누르면 빼기 (여러 명 · user_id로 저장해서 동명이인도 구분)."""
    s = get_state()
    if pid in s.assign_draft:
        s.assign_draft.remove(pid)
    else:
        s.assign_draft.append(pid)


def assign_to(pids: list[str] | None = None) -> None:
    """고른 사람들(user_id)에게 맡기기 (여러 명). 빈 목록이면 담당자를 비움."""
    s = get_state()
    pids = list(s.assign_draft if pids is None else pids)
    s.assign_prev[s.assign_key] = list(s.assigned.get(s.assign_key, []))  # 바꾸기 전 담당자 (「누구 → 누구」 안내)
    t = sel.todo_at(s, s.assign_key)
    original = list(t.owners) if t else []
    if original and pids in ([], original):  # 회의록 담당자가 있던 일: 그대로 두거나 비우면 회의록 담당자로
        s.assigned.pop(s.assign_key, None)
        msg = f"{s.assign_text} → 회의록의 담당자({', '.join(map(sel.name_of, original))})로 두었어요."
    elif pids:
        s.assigned[s.assign_key] = pids
        who = ", ".join(map(sel.assignee_name, pids))
        msg = f"{s.assign_text} → {who} 님에게 맡겼어요. 확정하기 전까지 바꿀 수 있고, 확정하면 알림이 가요."
    else:
        s.assigned.pop(s.assign_key, None)
        msg = f"{s.assign_text} → 담당자를 비웠어요."
    _set(modal=None, assign_pending="", assign_draft=[], last_assign=msg)


def open_owner_change(key: str, text: str) -> None:
    """담당자 추천 화면 「담당자 변경」 → 사람 찾기 창 (지금 담당자들을 미리 골라 둠)."""
    s = get_state()
    t = sel.todo_at(s, key)
    current = list(sel.todo_owner_ids(s, key, t)) if t else []
    s.pp_tab.pop("owner_change", None)
    open_modal("owner_change", assign_key=key, assign_text=text, pick_draft=current, pick_nonce=s.pick_nonce + 1)


def confirm_owner_change() -> None:
    """사람 찾기 창에서 고른 사람들로 담당자 변경 (회의록 담당자와 같으면 원래대로)."""
    s = get_state()
    assign_to(list(s.pick_draft))
    s.pp_nonce += 1


def set_todo_due(key: str, widget_key: str) -> None:
    """담당자 추천: 액션 아이템 마감일 고치기 (비우면 회의록의 마감일로)."""
    s = get_state()
    d = st.session_state.get(widget_key)
    if d:
        s.todo_due[key] = f"{d.month}/{d.day}"
    else:
        s.todo_due.pop(key, None)


# ---------------------------------------------------------------- 결과 전달
def start_delivery(mid: str, attempt: int = 1) -> None:
    """결과 전달 시작 (참석자에게 보내기까지 · 외부 도구 전송은 끝난 뒤 따로)."""
    s = get_state()
    s.delivery = Delivery(meeting_id=mid, attempt=attempt, started_at=time.time(), finished=False)
    s.status_override[mid] = "sending"
    go("deliver")


def start_finalize(mid: str) -> None:
    """회의 상세 「확정하기」 → 회의록 확정 화면 1단계(담당자 추천) (주관자 · 확정 전만)."""
    if sel.my_role(repo.meeting(mid)) != "host" or sel.status_of(get_state(), mid) != "review":
        return
    go("finalize", meeting_id=mid, fin_step=1, fin_flow=mid)


def finalize_goto(step: int) -> None:
    """회의록 확정 화면: 1 담당자 추천 ↔ 2 최종 회의록."""
    _set(fin_step=step)


def confirm_assignees(mid: str) -> None:
    """「다음: 최종 회의록」: 주관자가 고른 담당자를 Neo4j에 확정하고 2단계로. 실패하면 1단계에 남아 다시 누르게 함.

    모든 일의 지금 담당자를 보냄 (바꾼 사람 · 회의록 담당자 · 비운 일은 빈 목록 → 앞서 저장한 지정도 덮어씀). 목업 회의는 저장 없이 넘어감.
    """
    s = get_state()
    if mid in s.summaries:
        try:
            for i, t in sel.todo_items(s, mid):
                if t.item_id:
                    repo.set_assignees(t.item_id, list(sel.todo_owner_ids(s, sel.todo_key(mid, i), t)))
        except api.ApiError as e:
            s.toast = f"담당자를 저장하지 못했어요. 다시 눌러 주세요. ({e})"
            return
    _set(fin_step=2)


def deliver_final(mid: str) -> None:
    """최종 회의록 「결과 전달」 → 결과 전달 화면 (참석자에게 보내기 · 그다음 외부 도구 전송)."""
    if sel.my_role(repo.meeting(mid)) != "host":
        return
    close_modal()
    start_delivery(mid, 1)


# ---------------------------------------------------------------- 외부 도구(MCP) 전송 (결과 전달이 끝난 뒤 따로)
def open_export(mid: str) -> None:
    """결과 전달 화면 → 「외부 도구 전송」 화면 (도구 고르기부터)."""
    from state import export

    if sel.my_role(repo.meeting(mid)) != "host":
        return
    s = get_state()
    s.export = ExportRun(meeting_id=mid, picked=export.default_pick(mid))
    go("export")


def toggle_export_tool(tool_id: str) -> None:
    s = get_state()
    x = s.export
    x.picked = tuple(t for t in x.picked if t != tool_id) if tool_id in x.picked else (*x.picked, tool_id)


def start_export() -> None:
    """고른 도구로 보내기 시작."""
    from state import export

    s = get_state()
    x = s.export
    if sel.my_role(repo.meeting(x.meeting_id)) != "host" or not x.picked:
        return
    s.export = ExportRun(meeting_id=x.meeting_id, picked=x.picked, attempt=1, started_at=time.time(),
                         fids=export.open_ids(s, x.meeting_id),
                         expired=tuple(k for k, v in repo.tool_statuses().items() if v == "expired"))
    s.status_override[x.meeting_id] = "sending"


def retry_export() -> None:
    """단순 이슈로 실패한 도구만 다시 보내기."""
    s = get_state()
    x = s.export
    s.export = ExportRun(meeting_id=x.meeting_id, picked=x.picked, attempt=2, started_at=time.time(),
                         fids=x.fids, expired=x.expired, contact_shown=True)
    s.status_override[x.meeting_id] = "sending"


def finish_export() -> None:
    """「전달 완료」 → 회의 상세로 · 마지막 단계라 확정 흐름(단계 표시)도 여기서 끝."""
    s = get_state()
    mid = s.export.meeting_id
    open_meeting(mid)
    _set(toast="외부 도구 전송을 마쳤어요.", fin_flow="" if s.fin_flow == mid else s.fin_flow)


def run_row_action(mid: str, kind: str) -> None:
    """표의 '관리' 버튼."""
    if kind == "review":
        open_meeting(mid, editing=True)
    elif kind == "deliver":
        start_delivery(mid, 1)
    elif kind == "retry":  # 외부 도구 전송이 실패했으면 외부 도구 전송 화면, 아니면 실패한 참석자에게 다시
        if sel.open_export_failure(get_state(), mid):
            open_export(mid)
        else:
            start_delivery(mid, 2)
    elif kind == "progress":
        get_state().delivery.meeting_id = mid
        go("deliver")
    else:
        open_meeting(mid)


# ---------------------------------------------------------------- 외부 연동
def set_integration(tool_id: str) -> None:
    """관리자 · MCP 관리: 조직의 외부 도구 연결 (모든 사용자에게 적용)."""
    s = get_state()
    tool = repo.tool(tool_id)
    cur = repo.tool_status(tool_id)
    if cur == "on":
        repo.set_tool_status(tool_id, "off")
        s.toast = f"{tool.name} 연결을 해제했어요."
    elif cur == "expired":
        repo.set_tool_status(tool_id, "on")  # 인증 갱신 → 아래 「전송 실패 기록」에서 다시 보낼 수 있음
        s.toast = f"{tool.name}을(를) 다시 연결했어요. 보내지 못한 회의 결과를 다시 보낼 수 있어요."
    elif tool.phase2:
        s.toast = f"{tool.name} 연동은 2단계(Phase 2)에서 제공될 예정이에요."
    else:
        repo.set_tool_status(tool_id, "on")
        s.toast = f"{tool.name}을(를) 연결했어요."


def resend_export(fid: str) -> None:
    """단순 이슈: 주관자가 회의록 안에서 「다시 보내기」."""
    s = get_state()
    f = next(x for x in repo.export_failures() if x.id == fid)
    s.export_resent.add(fid)
    if not any(sel.failure_open(s, x.id) for x in repo.export_failures(f.meeting)):
        s.status_override[f.meeting] = "done"  # 남은 실패가 없으면 「전달 완료」
    s.toast = f"「{repo.meeting(f.meeting).title}」 결과를 {repo.tool(f.tool).name}(으)로 다시 보냈어요."


def admin_resend(fid: str) -> None:
    """관리자 · MCP 관리: 기술 이슈를 다시 보냄 → 실패 해결 · 사용자 회의 「전달 완료」 · 사용자에게 알림."""
    from state import delivery

    s = get_state()
    f = next(x for x in repo.export_failures() if x.id == fid)
    tool = repo.tool(f.tool)
    title = repo.meeting(f.meeting).title
    repo.resolve_failure(f.id)
    repo.push_notification("user", "delivered", f"관리자가 「{title}」 결과를 {tool.name}(으)로 다시 보냈어요. 전달 완료!",
                           delivery.now_text(), f.meeting)
    s.toast = f"「{title}」 결과를 {tool.name}(으)로 보냈어요. 사용자에게 전달 완료 알림을 보냈어요."


def open_contact_admin(fid: str) -> None:
    """기술 이슈로 보내지 못함 → 「관리자에게 문의」 모달."""
    _set(modal="contact_admin", contact_failure=fid, copied="")


# ---------------------------------------------------------------- 관리자
def adm_set_month(key: str) -> None:
    _set(adm_month=key)


def adm_pick_month(year_key: str, month_key: str) -> None:
    """비용 관리: 연도 · 월 고르기. 그 연도에 없는 달이면 그 연도의 마지막 달로."""
    keys = [m["key"] for m in repo.admin()["months"]]
    y = st.session_state.get(year_key)
    mth = st.session_state.get(month_key)
    key = f"{y}-{mth:02d}"
    if key not in keys:
        key = [k for k in keys if k.startswith(f"{y}-")][-1]
    _set(adm_month=key)


def adm_save_budget(budget_key: str, alert_key: str) -> None:
    """월 예산 · 알림 기준 저장 (조직 설정 · 서버 공용)."""
    s = get_state()
    b = repo.budget()
    b["amount"] = int(st.session_state.get(budget_key) or b["amount"])
    b["alert_at"] = int(st.session_state.get(alert_key) or b["alert_at"])
    s.toast = f"월 예산을 {b['amount']:,}원, 알림 기준을 {b['alert_at']}%로 저장했어요."


def check_budget_alert() -> None:
    """관리자: 이번 달 월말 예상 금액이 알림 기준을 넘으면 알림 (같은 예산 · 기준으로는 한 번만)."""
    from state import delivery

    cur = next(m for m in repo.admin()["months"] if m.get("running"))
    b = repo.budget()
    forecast = cur["amount"] / cur["days"] * cur["month_days"]
    pct = forecast / b["amount"] * 100
    if pct >= b["alert_at"] and repo.budget_alert_once(f"{cur['key']}:{b['amount']}:{b['alert_at']}"):
        repo.push_notification("admin", "budget",
                               f"{cur['month']}월 예산 알림: 이 속도면 월말에 예산의 {pct:.0f}%를 써요. "
                               f"(알림 기준 {b['alert_at']}%)", delivery.now_text())


def adm_filter(query_key: str) -> None:
    get_state().adm_query = (st.session_state.get(query_key) or "").strip()


def adm_sort(col: str) -> None:
    """표 머리글 클릭: 다른 컬럼이면 그 컬럼 내림차순(⌄), 같은 컬럼이면 오름(⌃) ↔ 내림(⌄)."""
    s = get_state()
    _set(adm_sort=col, adm_sort_desc=True if s.adm_sort != col else not s.adm_sort_desc)


def adm_open_filter_bar() -> None:
    """필터 아이콘: 필터 줄을 엶 (닫기는 「초기화」로)."""
    _set(adm_filter_open=True)


def adm_add_filter(col: str) -> None:
    """「+ 필터」에서 항목 고름 → 필터 칩이 생기고 그 칩의 값 고르기 메뉴가 바로 열림."""
    get_state().adm_filters.setdefault(col, [])
    st.session_state["adm_fadd_pop"] = False  # 「+ 필터」 메뉴는 닫고
    st.session_state[f"adm_chip_pop_{col}"] = True  # 방금 만든 칩 메뉴를 염


def adm_remove_filter(col: str) -> None:
    get_state().adm_filters.pop(col, None)


def adm_set_filter(col: str, value: str) -> None:
    """필터 칩 안 체크박스 하나를 켬 · 끔."""
    picked = get_state().adm_filters.setdefault(col, [])
    if value in picked:
        picked.remove(value)
    else:
        picked.append(value)
    st.session_state[f"adm_chip_pop_{col}"] = True  # 칩 글자가 바뀌어도 메뉴가 닫히지 않게 (여러 개 연달아 고름)


def adm_reset_table() -> None:
    """필터 줄 · 「초기화」: 필터와 정렬을 처음 상태로 되돌리고 필터 줄을 닫음 (검색어는 그대로)."""
    _set(adm_sort="", adm_sort_desc=False, adm_filters={}, adm_filter_open=False)


def open_member_edit(pid: str) -> None:
    """목록 · 「수정」: 그 구성원의 수정 팝업 열기 (입력칸은 지금 값으로 새로)."""
    s = get_state()
    _set(modal="member_edit", adm_edit_pid=pid, adm_edit_error="", adm_edit_nonce=s.adm_edit_nonce + 1)


def save_member_edit(prefix: str) -> bool:
    """수정 팝업 · 「저장」: 바뀐 항목만 백엔드(Neo4j)에 저장. 실패하면 팝업에 이유를 보여 주고 False."""
    s = get_state()
    row = next(r for r in repo.people_rows() if r["user_id"] == s.adm_edit_pid)
    v = lambda k: st.session_state.get(f"{prefix}_{k}")  # noqa: E731
    team, job = v("team"), (v("job") or "").strip()
    if not team or repo.team_id_of(team) is None:
        s.adm_edit_error = "팀을 골라 주세요."
        return False
    if not job:
        s.adm_edit_error = "직무를 골라 주세요."
        return False
    if not v("position"):
        s.adm_edit_error = "직급을 골라 주세요."
        return False
    skills, err = mf.read_skills(prefix)
    if err:
        s.adm_edit_error = err
        return False
    new = {"team_id": repo.team_id_of(team), "job": job, "position": v("position"), "permission": v("permission"),
           "is_active": bool(v("active")), "skills": skills}
    old = {"team_id": row["team_id"], "job": row["job"], "position": row["position"], "permission": row["permission"],
           "is_active": row["is_active"] is not False,
           "skills": [{"skill_id": k["skill_id"], "level": k["level"]} for k in row["skills"]]}
    key = lambda xs: sorted((x["skill_id"], x["level"]) for x in xs)  # noqa: E731
    changes = {k: new[k] for k in new if (key(new[k]) != key(old[k]) if k == "skills" else new[k] != old[k])}
    if changes:
        try:
            repo.update_person(row["user_id"], changes, s.token)
        except api.ApiError as e:
            s.adm_edit_error = f"저장하지 못했어요: {e}"
            return False
        if "job" in changes:
            _remember_job(job)
    _set(modal=None, adm_edit_error="",
         toast=f"{row['name']} 님의 정보를 저장했어요." if changes else "바뀐 내용이 없어요.")
    return True


def _remember_job(job: str) -> None:
    """「최근 사용」 직무 (최근 것이 앞 · 최대 3개)."""
    s = get_state()
    s.adm_recent_jobs = [job] + [j for j in s.adm_recent_jobs if j != job][:2]


def add_set_job(nonce: int, job: str) -> None:
    """구성원 추가 · 「최근 사용」 직무 칩을 누르면 직무 칸에 넣음."""
    st.session_state[f"add_job_{nonce}"] = job


# ---------------------------------------------------------------- 조직 선택 모달 (구성원 추가 · 팀)
def open_org_picker(target: str) -> None:
    """조직 선택 패널 열기 (구성원 추가 줄의 팀 칸)."""
    s = get_state()
    cur = st.session_state.get(f"add_team_{s.adm_nonce}") or ""
    _set(modal="org_picker", adm_org_target=target, adm_org_pick=cur, adm_org_query="", adm_org_tab="all",
         adm_org_open=set(sel.team_ancestors(cur)) or {"제품본부", "프로덕트부"}, adm_org_nonce=s.adm_org_nonce + 1)


def org_toggle(node: str) -> None:
    s = get_state()
    s.adm_org_open ^= {node}


def org_pick(team: str) -> None:
    _set(adm_org_pick=team)


def org_tab(tab: str) -> None:
    _set(adm_org_tab=tab)


def org_query(widget_key: str) -> None:
    get_state().adm_org_query = (st.session_state.get(widget_key) or "").strip()


def org_fav(team: str) -> None:
    s = get_state()
    s.adm_org_fav ^= {team}


def org_done() -> None:
    """「선택 완료」: 구성원 추가 줄의 팀 칸(다음에 그릴 때)에 넣고 패널 닫기."""
    s = get_state()
    if s.adm_org_pick:
        s.adm_team_pending = s.adm_org_pick
    if s.adm_org_pick:
        s.adm_org_recent = [s.adm_org_pick] + [x for x in s.adm_org_recent if x != s.adm_org_pick][:4]
    s.modal = None


def toggle_add_member() -> None:
    """구성원 관리: 「구성원 추가」 입력 줄 열기/닫기."""
    s = get_state()
    _set(adm_adding=not s.adm_adding, adm_invite_error="", adm_nonce=s.adm_nonce + 1)


def adm_add_member(nonce: int) -> None:
    """구성원 바로 추가 (초대 메일 없이) → 백엔드(Neo4j)에 바로 저장 · 바로 「활성」."""
    s = get_state()
    g = lambda k: (st.session_state.get(f"add_{k}_{nonce}") or "").strip()  # noqa: E731
    name, email, team, job = g("name"), g("email"), g("team"), g("job")
    position = st.session_state.get(f"add_{nonce}_position")
    role = "admin" if st.session_state.get(f"add_role_{nonce}") == "admin" else "member"
    skills, skill_err = mf.read_skills(f"add_{nonce}")
    if not name:
        s.adm_invite_error = "이름을 적어 주세요."
    elif "@" not in email or "." not in email.split("@")[-1]:
        s.adm_invite_error = "이메일 주소를 확인해 주세요."
    elif not team or repo.team_id_of(team) is None:
        s.adm_invite_error = "팀을 골라 주세요."
    elif not job:
        s.adm_invite_error = "직무를 골라 주세요."
    elif not position:
        s.adm_invite_error = "직급을 골라 주세요. 담당자 추천이 직급별로 후보를 뽑아요."
    elif skill_err:
        s.adm_invite_error = skill_err
    else:
        body = {"name": name, "email": email, "team_id": repo.team_id_of(team), "job": job, "position": position,
                "permission": role, "skills": skills}
        try:
            row = repo.create_person(body, s.token)
        except api.ApiError as e:
            s.adm_invite_error = str(e)
            return
        _remember_job(job)
        _set(adm_adding=False, adm_invite_error="", adm_nonce=s.adm_nonce + 1,
             toast=f"{row['name']} 님을 {team} 구성원으로 추가했어요 ({row['user_id']}).")


# ---------------------------------------------------------------- 알림
def open_bell() -> None:
    s = get_state()
    s.bell_seen |= {n.id for n in sel.notifications(s)}


def open_notification(nid: str) -> None:
    s = get_state()
    s.read_notifications.add(nid)
    n = next(x for x in sel.notifications(s) if x.id == nid)
    if n.kind == "fail":  # 관리자: 사용자 전송 실패 → MCP 관리
        go("adm_mcp")
    elif n.kind == "budget":  # 관리자: 예산 알림 → 비용 관리
        go("adm_cost")
    elif n.meeting:
        open_meeting(n.meeting)
    elif n.folder and n.folder in sel.folders(s):  # 멤버로 추가됨 → 그 폴더로
        open_folder(n.folder)

def add_member_now(fid: str, pid: str) -> None:
    """폴더 멤버 모달 · 조직도에서 이름을 누르면 바로 멤버로 추가 → 목록 맨 아래로."""
    add_members(fid, [pid])
    get_state().member_just_added = True


def add_picked_members(fid: str, widget_key: str) -> None:
    """팀원 칩을 누르는 즉시(on_change) 멤버로 추가 → 목록에 바로 반영."""
    add_members(fid, list(st.session_state.get(widget_key) or []))
    get_state().member_just_added = True  # 모달이 다시 그려질 때 목록 맨 아래로 이동