"""모달(st.dialog) 모음.

AppState.modal 값에 맞는 모달 하나만 엽니다. 사용자가 X/바깥 클릭으로 닫으면 on_dismiss 로 상태를 정리합니다.
모달 안에서 '닫히는' 동작(저장·확정 등)은 상태를 바꾼 뒤 st.rerun() 으로 전체 화면을 다시 그립니다.
"""

from __future__ import annotations

from components.dialogs.admin import contact_admin_dialog, member_edit_dialog, org_picker_dialog
from components.dialogs.assign import assign_dialog
from components.dialogs.confirm import confirm_meeting_dialog
from components.dialogs.compose_pickers import (
    host_picker_dialog,
    people_picker_dialog,
    speaker_picker_dialog,
)
from components.dialogs.folder import delete_folder_dialog, delete_meeting_dialog, members_dialog
from components.dialogs.meeting_people import people_dialog
from components.dialogs.share import share_dialog
from components.dialogs.todo import add_todo_dialog, todo_owner_dialog
from state.store import get_state

_DIALOGS = {
    "members": members_dialog,
    "delete_folder": delete_folder_dialog,
    "delete_meeting": delete_meeting_dialog,
    "people": people_dialog,
    "assign": assign_dialog,
    "confirm_meeting": confirm_meeting_dialog,
    "share": share_dialog,
    "add_todo": add_todo_dialog,
    "todo_owner": todo_owner_dialog,
    "cp_host": host_picker_dialog,
    "cp_people": people_picker_dialog,
    "cp_speaker": speaker_picker_dialog,
    "contact_admin": contact_admin_dialog,
    "org_picker": org_picker_dialog,
    "member_edit": member_edit_dialog,
}


def render_active_dialog() -> None:
    name = get_state().modal
    if name in _DIALOGS:
        _DIALOGS[name]()
