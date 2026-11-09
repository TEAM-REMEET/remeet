"""폴더 위키 판정 (application/folder_wiki.build) — Neo4j 없이 read_rows 모양의 행으로."""
import unittest

from followup.application.folder_wiki import build


def row(topic, meeting, date, *, subtopics=(), decisions=(), unresolved=(), superseded=(), topic_id=None):
    return {"topic_id": topic_id or topic, "topic": topic, "last_at": date, "meeting_id": meeting,
            "meeting_title": f"{meeting} 회의", "date": date, "decisions": list(decisions),
            "unresolved": list(unresolved), "next_topics": [], "subtopics": list(subtopics),
            "superseded": list(superseded)}


def sub(*lines):
    """lines: (discussion_tag, decision_tag, text, evidence)"""
    return {"discussions": [f"[{t}] {x}" for t, _, x, _ in lines], "discussion_tags": [t for t, *_ in lines],
            "decision_tags": [d for _, d, *_ in lines], "evidence": [e for *_, e in lines]}


class FolderWikiTests(unittest.TestCase):
    def test_concern_without_decision_is_split(self):
        t = build([row("배터리", "m1", "2026-11-09", subtopics=[sub(("제안", "", "LED", "1"), ("반대·우려", "", "비용", "2,3"))],
                       decisions=["다른 결정"])])["topics"][0]
        self.assertEqual(t["status"], "split")
        self.assertEqual(t["rounds"][0]["concerns"], ["비용"])
        self.assertEqual(t["rounds"][0]["lines"][1]["src"], [2, 3])
        self.assertEqual(t["rounds"][0]["lines"][0]["text"], "LED")  # 태그 머리 떼기

    def test_concern_with_decision_in_same_subtopic_is_not_split(self):
        t = build([row("내구성", "m1", "2026-11-09", decisions=["범퍼"],
                       subtopics=[sub(("반대·우려", "", "무거움", ""), ("제안", "결정", "범퍼", ""))])])["topics"][0]
        self.assertEqual(t["status"], "done")

    def test_only_latest_meeting_decides_status(self):
        rows = [row("기능", "m1", "2026-11-02", unresolved=["적외선 미정"]),
                row("기능", "m2", "2026-11-05", decisions=["통합"], subtopics=[sub(("제안", "결정", "통합", ""))])]
        self.assertEqual(build(rows)["topics"][0]["status"], "done")

    def test_hold_or_no_decision_is_open(self):
        rows = [row("a", "m1", "2026-11-01", subtopics=[sub(("제안", "보류", "적외선", ""))], decisions=["x"]),
                row("b", "m1", "2026-11-01", subtopics=[sub(("정보", "", "참고", ""))])]
        self.assertEqual([t["status"] for t in build(rows)["topics"]], ["open", "open"])

    def test_order_unfinished_first_then_recent_misc_last(self):
        rows = [row("기타", "m3", "2026-11-09"),
                row("끝남", "m3", "2026-11-09", decisions=["d"]),
                row("옛 미정", "m1", "2026-11-01"),
                row("새 미정", "m2", "2026-11-05")]
        self.assertEqual([t["title"] for t in build(rows)["topics"]], ["새 미정", "옛 미정", "끝남", "기타"])

    def test_superseded_decision_and_ragged_arrays(self):
        broken = {"discussions": ["[제안] a", "[정보] b"], "discussion_tags": ["제안"], "decision_tags": [], "evidence": []}
        t = build([row("가격", "m1", "2026-11-01", decisions=["10유로"], subtopics=[broken],
                       superseded=[{"old": "10유로", "new": "12.5유로"}])])["topics"][0]
        self.assertEqual(t["rounds"][0]["decisions"], [{"text": "10유로", "superseded_by": "12.5유로"}])
        self.assertEqual(t["rounds"][0]["lines"][1], {"tag": "", "decision_tag": "", "text": "b", "src": []})

    def test_meetings_listed_once_in_order(self):
        rows = [row("a", "m1", "2026-11-01"), row("b", "m1", "2026-11-01"), row("a", "m2", "2026-11-05")]
        self.assertEqual([m["meeting_id"] for m in build(rows)["meetings"]], ["m1", "m2"])


if __name__ == "__main__":
    unittest.main()
