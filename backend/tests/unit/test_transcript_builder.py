"""POST /api/meetings 입력 → 파이프라인 입력 텍스트"""
import unittest

from pydantic import ValidationError

from unittest.mock import patch

from followup import settings as cfg
from followup.analysis.summary.preprocessing import TranscriptFormatError, parse_transcript
from followup.analysis.summary.transcript_builder import MeetingSubmission, build_meeting_text, new_meeting_id, submission_key

BART = {"user_id": "u1", "name": "바트", "job": "프로젝트 매니저"}
JO = {"user_id": "u10", "name": "조현우B", "job": "산업 디자이너"}


def submission(fmt="colon", transcript="바트: 시안은 금요일까지 보낼게요.\n\n조현우B: 일정표는 제가 고칠게요.", **kw):
    return MeetingSubmission(**{**dict(project_id="TS3003", directory_id="weekly", title="신제품 일정 회의", date="2026-11-05", host_user_id="u1",
                                       attendees=[BART, JO], transcript_format=fmt, transcript=transcript), **kw})


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
        self.assertEqual(build_meeting_text(submission("bracket", "[바트]\n시안은 금요일까지 보낼게요.\n\n[조현우B]\n일정표는 제가 고칠게요.")), EXPECTED)

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
        text = build_meeting_text(submission(transcript="바트: 시간은 10:30 입니다.\n\n조현우B: 참고: 네."))
        self.assertEqual([u.text for u in parse_transcript(text)["transcript"]], ["시간은 10:30 입니다.", "참고: 네."])

    def test_file_bom_and_crlf_are_removed(self):
        text = build_meeting_text(submission(transcript="﻿바트: 첫 발화\r\n\r\n﻿조현우B: 둘째 발화\r\n"))
        self.assertIn("바트_u1: 첫 발화\n조현우B_u10: 둘째 발화\n", text)


class LengthTests(unittest.TestCase):
    def test_transcript_at_limit_is_accepted(self):
        sub = submission()
        with patch.object(cfg, "MAX_TRANSCRIPT_CHARS", len(sub.transcript)):
            self.assertEqual(build_meeting_text(sub), EXPECTED)

    def test_transcript_over_limit_is_rejected_before_llm(self):
        sub = submission()
        with patch.object(cfg, "MAX_TRANSCRIPT_CHARS", len(sub.transcript) - 1):
            with self.assertRaises(TranscriptFormatError) as ctx:
                build_meeting_text(sub)
        self.assertIn("너무 길어요", ctx.exception.errors[0])

    def test_default_limits(self):
        self.assertEqual((cfg.MAX_TRANSCRIPT_CHARS, cfg.MAX_STEP_INPUT_CHARS), (35000, 80000))
        cfg.validate()


class ParticipantTests(unittest.TestCase):
    def test_participants_are_exactly_attendees_in_order(self):
        parsed = parse_transcript(build_meeting_text(submission(attendees=[JO, BART])))
        self.assertEqual([p.user_id for p in parsed["participants"]], ["u10", "u1"])

    def test_speaker_uses_attendee_user_id_and_job(self):
        parsed = parse_transcript(build_meeting_text(submission(transcript="조현우B: 가")))
        utterance = parsed["transcript"][0]
        self.assertEqual((utterance.speaker_user_id, utterance.speaker_name, utterance.speaker_job), ("u10", "조현우B", "산업 디자이너"))

    def test_attendee_who_does_not_speak_stays_out_of_speakers(self):
        parsed = parse_transcript(build_meeting_text(submission(transcript="바트: 가")))
        self.assertEqual(parsed["speakers"], ["u1"])
        self.assertEqual([p.user_id for p in parsed["participants"]], ["u1", "u10"])

    def test_same_name_with_different_jobs_uses_name_and_job(self):
        one, two = {"user_id": "u2", "name": "홍길동", "job": "산업 디자이너"}, {"user_id": "u3", "name": "홍길동", "job": "마케팅 담당자"}
        sub = submission(host_user_id="u2", attendees=[one, two], transcript="홍길동(산업 디자이너): 가\n\n홍길동(마케팅담당자): 나")
        text = build_meeting_text(sub)
        self.assertIn("발화자: 홍길동_u2, 홍길동_u3\n", text)
        self.assertEqual([u.speaker_user_id for u in parse_transcript(text)["transcript"]], ["u2", "u3"])

    def test_all_kinds_of_same_name_in_one_meeting(self):
        people = [{"user_id": "u1", "name": "홍길동A", "job": "디자이너"}, {"user_id": "u2", "name": "홍길동B", "job": "디자이너"},
                  {"user_id": "u3", "name": "홍길동", "job": "마케터"}, {"user_id": "u4", "name": "이영희", "job": "PM"}]
        sub = submission(attendees=people, transcript="홍길동A: 가\n\n홍길동B: 나\n\n홍길동: 다\n\n이영희: 라")
        self.assertEqual([u.speaker_user_id for u in parse_transcript(build_meeting_text(sub))["transcript"]], ["u1", "u2", "u3", "u4"])

    def test_same_name_with_different_jobs_is_allowed_without_speakers(self):
        one, two = {"user_id": "u2", "name": "홍길동", "job": "디자이너"}, {"user_id": "u3", "name": "홍길동", "job": "마케터"}
        sub = submission("plain", "회의 내용입니다.", host_user_id="u2", attendees=[one, two])
        self.assertEqual([p.user_id for p in parse_transcript(build_meeting_text(sub))["participants"]], ["u2", "u3"])

    def test_extra_values_from_people_api_are_ignored(self):
        self.assertEqual(build_meeting_text(submission(attendees=[{**BART, "team": "프로젝트 관리팀"}, JO])), EXPECTED)

    def test_host_is_not_written_into_text(self):
        self.assertEqual(build_meeting_text(submission(host_user_id="u10")), EXPECTED)


class MeetingIdTests(unittest.TestCase):
    def test_format_is_m_and_ulid(self):
        self.assertRegex(new_meeting_id(), r"^M_[0-9A-HJKMNP-TV-Z]{26}$")

    def test_same_request_gets_same_key_and_changed_request_does_not(self):
        self.assertEqual(submission_key(submission()), submission_key(submission()))
        self.assertNotEqual(submission_key(submission()), submission_key(submission(title="다른 회의")))
        self.assertNotEqual(submission_key(submission()), submission_key(submission(directory_id="monthly")))


class DirectoryIdTests(unittest.TestCase):
    def test_accepts_three_meeting_kinds(self):
        for kind in ("monthly", "weekly", "normal"):
            self.assertEqual(submission(directory_id=kind).directory_id, kind)

    def test_rejects_other_values(self):
        with self.assertRaises(ValidationError):
            submission(directory_id="yearly")


class RejectedInputTests(unittest.TestCase):
    def rejected(self, sub):
        with self.assertRaises(TranscriptFormatError) as caught:
            build_meeting_text(sub)
        return "\n".join(caught.exception.errors)

    def test_speaker_outside_attendees_is_rejected(self):
        self.assertIn("'없는사람'이(가) 참석자 목록에 없음", self.rejected(submission(transcript="바트: 가\n\n없는사람: 나")))

    def test_speaker_with_ambiguous_name_is_rejected(self):
        one, two = {"user_id": "u2", "name": "홍길동", "job": "디자이너"}, {"user_id": "u3", "name": "홍길동", "job": "마케터"}
        sub = submission(transcript="홍길동: 가", host_user_id="u2", attendees=[one, two])
        self.assertIn("여러 명이라 구분할 수 없음 — '이름(직무)'로 적어 주세요", self.rejected(sub))

    def test_same_name_and_same_job_cannot_be_told_apart_by_job(self):
        one, two = {"user_id": "u2", "name": "홍길동", "job": "디자이너"}, {"user_id": "u3", "name": "홍길동", "job": "디자이너"}
        sub = submission(transcript="홍길동(디자이너): 가", host_user_id="u2", attendees=[one, two])
        self.assertIn("여러 명이라 구분할 수 없음", self.rejected(sub))

    def test_host_outside_attendees_is_rejected(self):
        self.assertIn("주관자 'u99'", self.rejected(submission(host_user_id="u99")))

    def test_removed_speaker_map_and_empty_attendees_are_rejected(self):
        for kw in ({"speaker_map": {"바트": "u1"}}, {"attendees": []}, {"attendees": [{**BART, "job": " "}]},
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
