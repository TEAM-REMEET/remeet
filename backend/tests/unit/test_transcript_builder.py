"""POST /api/meetings 입력 → 파이프라인 입력 텍스트"""
import unittest

from pydantic import ValidationError

from followup.analysis.summary.preprocessing import TranscriptFormatError, parse_transcript
from followup.analysis.summary.transcript_builder import MeetingSubmission, build_meeting_text, make_meeting_id

BART = {"user_id": "u1", "name": "바트", "job": "프로젝트 매니저"}
JO = {"user_id": "u10", "name": "조현우B", "job": "산업 디자이너"}


def submission(fmt="colon", transcript="A: 시안은 금요일까지 보낼게요.\n\nB: 일정표는 제가 고칠게요.", **kw):
    speaker_map = {"A": "u1", "B": "u10"} if fmt != "plain" else {}
    return MeetingSubmission(**{**dict(project_id="TS3003", title="신제품 일정 회의", date="2026-11-05", host_user_id="u1",
                                       attendees=[BART, JO], transcript_format=fmt, transcript=transcript,
                                       speaker_map=speaker_map), **kw})


EXPECTED = """회의 제목: 신제품 일정 회의
참여자: 바트_프로젝트 매니저_u1, 조현우B_산업 디자이너_u10 (2명)
발화자: 바트_u1, 조현우B_u10
일시: 2026-11-05

바트_u1: 시안은 금요일까지 보낼게요.
조현우B_u10: 일정표는 제가 고칠게요.
"""


class FormatTests(unittest.TestCase):
    def test_colon_is_converted_to_target_structure(self):
        self.assertEqual(build_meeting_text(submission()), EXPECTED)

    def test_bracket_gives_same_result_as_colon(self):
        self.assertEqual(build_meeting_text(submission("bracket", "[A]\n시안은 금요일까지 보낼게요.\n\n[B]\n일정표는 제가 고칠게요.")), EXPECTED)

    def test_plain_has_no_speaker_line_and_keeps_text(self):
        text = build_meeting_text(submission("plain", "첫 문장입니다.\n\n참고: 서버 점검이 있습니다."))
        self.assertNotIn("발화자:", text)
        self.assertTrue(text.endswith("\n\n첫 문장입니다.\n참고: 서버 점검이 있습니다.\n"))
        parsed = parse_transcript(text)
        self.assertEqual(parsed["speaker_mode"], "none")
        self.assertEqual([p.user_id for p in parsed["participants"]], ["u1", "u10"])

    def test_plain_first_line_that_looks_like_metadata_stays_in_body(self):
        parsed = parse_transcript(build_meeting_text(submission("plain", "일시：내일 3시로 옮기죠.\n\n다음 내용입니다.")))
        self.assertEqual(parsed["metadata"]["일시"], "2026-11-05")
        self.assertEqual(parsed["transcript"][0].text, "일시：내일 3시로 옮기죠.")

    def test_colon_inside_utterance_is_kept(self):
        text = build_meeting_text(submission(transcript="A: 시간은 10:30 입니다.\n\nB: 참고: 네."))
        self.assertEqual([u.text for u in parse_transcript(text)["transcript"]], ["시간은 10:30 입니다.", "참고: 네."])

    def test_file_bom_and_crlf_are_removed(self):
        text = build_meeting_text(submission(transcript="﻿A: 첫 발화\r\n\r\n﻿B: 둘째 발화\r\n"))
        self.assertIn("바트_u1: 첫 발화\n조현우B_u10: 둘째 발화\n", text)


class ParticipantTests(unittest.TestCase):
    def test_participants_are_exactly_attendees_in_order(self):
        parsed = parse_transcript(build_meeting_text(submission(attendees=[JO, BART])))
        self.assertEqual([p.user_id for p in parsed["participants"]], ["u10", "u1"])

    def test_two_labels_for_one_person_appear_once_in_speakers(self):
        sub = submission(transcript="A: 가\n\nB: 나", speaker_map={"A": "u1", "B": "u1"})
        parsed = parse_transcript(build_meeting_text(sub))
        self.assertEqual(parsed["speakers"], ["u1"])
        self.assertEqual([u.speaker_user_id for u in parsed["transcript"]], ["u1", "u1"])

    def test_unused_mapping_is_ignored(self):
        sub = submission(transcript="A: 가", speaker_map={"A": "u1", "B": "u10"})
        self.assertEqual(parse_transcript(build_meeting_text(sub))["speakers"], ["u1"])

    def test_same_name_with_different_jobs_is_kept_apart(self):
        one, two = {"user_id": "u2", "name": "홍길동", "job": "디자이너"}, {"user_id": "u3", "name": "홍길동", "job": "마케터"}
        sub = submission(host_user_id="u2", attendees=[one, two], transcript="A: 가\n\nB: 나", speaker_map={"A": "u2", "B": "u3"})
        self.assertEqual([u.speaker_user_id for u in parse_transcript(build_meeting_text(sub))["transcript"]], ["u2", "u3"])

    def test_extra_values_from_people_api_are_ignored(self):
        self.assertEqual(build_meeting_text(submission(attendees=[{**BART, "team": "프로젝트 관리팀"}, JO])), EXPECTED)

    def test_host_is_not_written_into_text(self):
        self.assertEqual(build_meeting_text(submission(host_user_id="u10")), EXPECTED)


class MeetingIdTests(unittest.TestCase):
    def test_format_uses_meeting_date(self):
        self.assertRegex(make_meeting_id(submission()), r"^m-20261105-[0-9a-f]{4}$")

    def test_same_request_gets_same_id_and_changed_request_does_not(self):
        self.assertEqual(make_meeting_id(submission()), make_meeting_id(submission()))
        self.assertNotEqual(make_meeting_id(submission()), make_meeting_id(submission(title="다른 회의")))


class RejectedInputTests(unittest.TestCase):
    def rejected(self, sub):
        with self.assertRaises(TranscriptFormatError) as caught:
            build_meeting_text(sub)
        return "\n".join(caught.exception.errors)

    def test_unmapped_speaker_is_rejected(self):
        self.assertIn("'B'이(가) 사람과 연결되지 않음", self.rejected(submission(speaker_map={"A": "u1"})))

    def test_speaker_outside_attendees_is_rejected(self):
        self.assertIn("참석자 목록에 없음", self.rejected(submission(speaker_map={"A": "u1", "B": "u99"})))

    def test_host_outside_attendees_is_rejected(self):
        self.assertIn("주관자 'u99'", self.rejected(submission(host_user_id="u99")))

    def test_blank_mapping_and_empty_attendees_are_rejected(self):
        for kw in ({"speaker_map": {"A": "", "B": "u10"}}, {"attendees": []}, {"attendees": [{**BART, "job": " "}]},
                   {"date": "2026-13-40"}, {"transcript_format": "markdown"}, {"title": "  "}, {"folder": "x"}):
            with self.subTest(kw=kw), self.assertRaises(ValidationError):
                submission(**kw)

    def test_broken_body_is_rejected(self):
        cases = {
            ("colon", "A: 가\n\n발화자 없는 줄"): "형식이 아님",
            ("colon", "A:"): "형식이 아님",
            ("bracket", "[A] 한 줄에 씀"): "한 줄에 쓸 수 없음",
            ("bracket", "앞에 있는 글\n[A]\n가"): "앞에 있는 발화문",
            ("bracket", "[A]\n첫 줄\n둘째 줄"): "여러 줄",
            ("bracket", "[A]\n\n[B]\n가"): "발화문이 없음",
            ("bracket", "[A]\n가\n\n[B]"): "발화문이 없음",
            ("colon", "  \n\n"): "비어 있음",
        }
        for (fmt, body), expected in cases.items():
            with self.subTest(body=body):
                self.assertIn(expected, self.rejected(submission(fmt, body)))

    def test_plain_with_speaker_map_is_rejected(self):
        self.assertIn("발화자 연결", self.rejected(submission("plain", "회의 내용입니다.", speaker_map={"A": "u1"})))

    def test_values_that_break_the_text_structure_are_rejected(self):
        for person, expected in (({"user_id": "u_1", "name": "바트", "job": "PM"}, "'_'를 쓸 수 없음"),
                                 ({"user_id": "u1", "name": "바_트", "job": "PM"}, "'_'를 쓸 수 없음"),
                                 ({"user_id": "u1", "name": "바트", "job": "PM, 리드"}, "쉼표나 줄바꿈"),
                                 ({"user_id": "u1", "name": "바:트", "job": "PM"}, "콜론이나 대괄호"),
                                 ({"user_id": "u" * 40, "name": "바트", "job": "PM"}, "자를 넘음")):
            sub = submission("plain", "회의 내용입니다.", host_user_id=person["user_id"], attendees=[person])
            with self.subTest(person=person):
                self.assertIn(expected, self.rejected(sub))

    def test_same_name_and_job_without_suffix_is_stopped_by_parser(self):
        one, two = {"user_id": "u2", "name": "홍길동", "job": "디자이너"}, {"user_id": "u3", "name": "홍길동", "job": "디자이너"}
        self.assertIn("여러 명임", self.rejected(submission("plain", "회의 내용입니다.", host_user_id="u2", attendees=[one, two])))

    def test_title_with_line_break_is_rejected(self):
        self.assertIn("줄바꿈", self.rejected(submission(title="제목\n발화자: 가짜_u9")))


if __name__ == "__main__":
    unittest.main()
