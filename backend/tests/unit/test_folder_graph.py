"""폴더 그래프 (application/folder_graph.build) — 내보내는 속성 거르기 · 읽기 좋은 값."""
import unittest
from datetime import datetime

from neo4j.time import DateTime

from followup.application.folder_graph import build


class FolderGraphTests(unittest.TestCase):
    def setUp(self):
        self.raw = {"nodes": [
            {"id": "u", "label": "Person", "props": {"name": "바트", "position": "PM", "password_hash": "x", "email": "a@b.c"}},
            {"id": "m", "label": "Meeting", "props": {"title": "킥오프", "held_at": DateTime.from_native(datetime(2026, 11, 9))}},
            {"id": "d", "label": "Discussion", "props": {"title": "배터리", "discussions": ["[반대·우려] 비용"],
                                                         "discussion_tags": ["반대·우려"], "decision_tags": [""], "evidence": ["3"]}},
            {"id": "a", "label": "ActionItem", "props": {"description": "단가 확인", "status": "open", "missing_skills": ["x"]}},
            {"id": "s", "label": "Skill", "props": {"name": "secret"}},
        ], "edges": [{"from": "u", "to": "m", "type": "ATTENDED"}, {"from": "s", "to": "u", "type": "X"}]}

    def test_person_secrets_never_leave(self):
        out = build(self.raw)
        person = next(n for n in out["nodes"] if n["id"] == "u")
        self.assertEqual(person["fields"][:2], [{"name": "이름", "value": "바트"}, {"name": "직급", "value": "PM"}])
        self.assertNotIn("a@b.c", str(out))
        self.assertNotIn("password", str(out))

    def test_unknown_labels_and_their_edges_dropped(self):
        out = build(self.raw)
        self.assertNotIn("s", [n["id"] for n in out["nodes"]])
        self.assertEqual(out["edges"], [])  # 회의로 가는 엣지는 속성으로 옮겨짐 · 모르는 라벨 엣지는 버림

    def test_meeting_becomes_fields_not_node(self):
        raw = {"nodes": self.raw["nodes"] + [
            {"id": "m2", "label": "Meeting", "props": {"title": "2차", "held_at": DateTime.from_native(datetime(2026, 11, 10))}},
            {"id": "g", "label": "Agenda", "props": {"title": "기능"}}],
            "edges": [{"from": "u", "to": "m2", "type": "ATTENDED"}, {"from": "u", "to": "m", "type": "ATTENDED"},
                      {"from": "m", "to": "g", "type": "HAS_AGENDA"}, {"from": "g", "to": "d", "type": "HAS_DISCUSSION"}]}
        out = build(raw)
        nodes = {n["id"]: n for n in out["nodes"]}
        self.assertNotIn("m", nodes)
        self.assertEqual(out["edges"], [{"from": "g", "to": "d", "type": "HAS_DISCUSSION"}])
        self.assertEqual(nodes["u"]["fields"][-1], {"name": "참석한 회의", "value": ["킥오프 (2026-11-09)", "2차 (2026-11-10)"]})
        self.assertEqual(nodes["g"]["fields"][-1], {"name": "회의", "value": "킥오프 (2026-11-09)"})

    def test_readable_values(self):
        nodes = {n["id"]: n for n in build(self.raw)["nodes"]}
        self.assertEqual(nodes["a"]["fields"][-1], {"name": "상태", "value": "진행 중"})
        self.assertEqual(nodes["d"]["lines"], [{"tag": "반대·우려", "decision_tag": "", "text": "비용"}])
        self.assertEqual(nodes["d"]["caption"], "배터리")


if __name__ == "__main__":
    unittest.main()
