"""화면에서 고친 액션 아이템 → POST /api/meetings/{id}/review 수정 목록 (backend schemas.HitlEdits)."""

from __future__ import annotations

from data.models import MISC_AGENDA, Todo

PREVIEW_ID = "cp-preview"


def _update(item: Todo, edit: dict) -> dict | None:
    """바뀐 값만 담음 (쓰지 않은 값은 백엔드에서 그대로 유지 · due: None은 일정 비움)."""
    changes: dict = {}
    if edit["text"] != item.text:
        changes["text"] = edit["text"]
    if list(edit["owners"]) != list(item.owners):
        changes["assignee_user_ids"] = list(edit["owners"])
    if edit["due_iso"] != item.due_iso:
        changes["due"] = edit["due_iso"] or None
    return {"op": "update", "item_id": item.item_id, **changes} if changes else None


def build_edits(s) -> list[dict]:
    """미리보기의 수정 · 삭제 · 추가 → 수정 목록. 추가한 뒤 삭제한 할 일은 넣지 않아요."""
    base = s.preview_meeting.todos  # 백엔드 결과 그대로의 할 일 (수정값이 겹치기 전)
    agenda_ids = {a.title: a.agenda_id for a in s.preview_meeting.agendas}
    edited = s.edited_todos.get(PREVIEW_ID, {})
    gone = set(s.deleted_todos.get(PREVIEW_ID, []))
    edits: list[dict] = []
    for i, item in enumerate(base):
        if i in gone:
            edits.append({"op": "delete", "item_id": item.item_id})
        elif i in edited and (update := _update(item, edited[i])):
            edits.append(update)
    for j, todo in enumerate(s.added_todos.get(PREVIEW_ID, []), len(base)):
        if j in gone:
            continue
        current = edited.get(j, {})
        due = current.get("due_iso", todo.due_iso)
        create = {"op": "create", "agenda_id": MISC_AGENDA if todo.agenda == MISC_AGENDA else agenda_ids[todo.agenda],
                  "text": current.get("text", todo.text), "assignee_user_ids": list(current.get("owners", todo.owners))}
        edits.append({**create, "due": due} if due else create)
    return edits
