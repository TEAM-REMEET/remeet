"""모달(st.dialog) 모음.

AppState.modal 값에 맞는 모달 하나만 엽니다. 사용자가 X/바깥 클릭으로 닫으면 on_dismiss 로 상태를 정리합니다.
모달 안에서 '닫히는' 동작(저장·확정 등)은 상태를 바꾼 뒤 st.rerun() 으로 전체 화면을 다시 그립니다.
"""

from __future__ import annotations

from components.dialogs.assign import assign_dialog
from components.dialogs.compose_pickers import folder_picker_dialog, host_picker_dialog, people_picker_dialog
from components.dialogs.folder import delete_folder_dialog, delete_meeting_dialog, members_dialog
from components.dialogs.meeting_people import people_dialog
from components.dialogs.share import share_dialog
from state.store import get_state

_DIALOGS = {
    "members": members_dialog,
    "delete_folder": delete_folder_dialog,
    "delete_meeting": delete_meeting_dialog,
    "people": people_dialog,
    "assign": assign_dialog,
    "share": share_dialog,
    "cp_folder": folder_picker_dialog,
    "cp_host": host_picker_dialog,
    "cp_people": people_picker_dialog,
}


def render_active_dialog() -> None:
    name = get_state().modal
    if name in _DIALOGS:
        _DIALOGS[name]()
