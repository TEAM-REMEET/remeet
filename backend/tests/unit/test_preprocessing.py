"""전사문 파싱 · 참여자 이름 처리"""
import unittest

from followup.analysis.summary.preprocessing import (TranscriptFormatError, llm_metadata, llm_names, load_transcript, parse_participants,
                                             parse_transcript, resolve_assignee)

HEAD = "회의 제목: 가상 회의\n일시: 2004-10-11\n"


class ParticipantTests(unittest.TestCase):
    def test_alias_only_for_homonyms_with_different_jobs(self):
        people = parse_participants("한유리_매니저_u1, 홍길동_디자이너_u2, 홍길동_마케터_u3, 김철수A_개발자_u4, 김철수B_개발자_u5")
        self.assertEqual(llm_names(people), {"u1": "한유리", "u2": "홍길동A", "u3": "홍길동B", "u4": "김철수A", "u5": "김철수B"})
        self.assertEqual([p.display_name for p in people[1:3]], ["홍길동(디자이너)", "홍길동(마케터)"])

    def test_alias_skips_letters_used_by_real_names(self):
        people = parse_participants("홍길동A_개발자_u1, 홍길동B_개발자_u2, 홍길동_디자이너_u3, 홍길동_마케터_u4")
        names = llm_names(people)
        self.assertEqual((names["u3"], names["u4"]), ("홍길동C", "홍길동D"))

    def test_same_job_homonyms_with_a_plain_name_of_other_job(self):
        people = parse_participants("홍길동A_개발자_u1, 홍길동B_개발자_u2, 홍길동_디자이너_u3")
        self.assertEqual(llm_names(people), {"u1": "홍길동A", "u2": "홍길동B", "u3": "홍길동C"})
        self.assertEqual([p.display_name for p in people], ["홍길동A", "홍길동B", "홍길동(디자이너)"])

    def test_same_job_homonyms_alone_need_no_job_in_display(self):
        people = parse_participants("홍길동A_개발자_u1, 홍길동B_개발자_u2")
        self.assertEqual([p.display_name for p in people], ["홍길동A", "홍길동B"])

    def test_bad_participants_are_rejected(self):
        for raw in ("홍길동", "홍길동_개발자", "홍길동_개발자_u1, 김철수_개발자_u1", "홍길동_개발자_u1, 홍길동_개발자_u2"):
            with self.subTest(raw=raw), self.assertRaises(TranscriptFormatError):
                parse_participants(raw)

    def test_resolve_assignee(self):
        people = parse_participants("홍길동_디자이너_u2, 홍길동_마케터_u3, 한유리_매니저_u1")
        for value in ("홍길동A", "홍길동(디자이너)"):
            with self.subTest(value=value):
                person, problem = resolve_assignee(value, people)
                self.assertEqual((person.user_id, problem), ("u2", None))
        for value in ("홍길동", "없는사람"):
            with self.subTest(value=value):
                person, problem = resolve_assignee(value, people)
                self.assertIsNone(person)
                self.assertTrue(problem)


class HeaderTests(unittest.TestCase):
    def test_metadata_ends_at_first_blank_line(self):
        for first in ("일시: 내일 3시로 옮기죠", "일시：내일 3시로 옮기죠"):
            with self.subTest(first=first):
                parsed = parse_transcript(f"{HEAD}\n{first}\n다음 내용\n")
                self.assertEqual(parsed["metadata"]["일시"], "2004-10-11")
                self.assertEqual([u.text for u in parsed["transcript"]], [first, "다음 내용"])

    def test_blank_lines_before_metadata_are_allowed(self):
        self.assertEqual(parse_transcript(f"\n\n{HEAD}\n본문\n")["metadata"]["일시"], "2004-10-11")

    def test_body_without_blank_line_still_works(self):
        self.assertEqual([u.text for u in parse_transcript(f"{HEAD}본문\n")["transcript"]], ["본문"])


class TranscriptTests(unittest.TestCase):
    def speaker(self, body, speakers="홍길동_u1"):
        return parse_transcript(f"{HEAD}참여자: 홍길동_개발자_u1 (1명)\n발화자: {speakers}\n\n{body}")

    def test_speaker_mode_splits_user_and_text(self):
        parsed = self.speaker("홍길동_u1: 안녕하세요\n")
        self.assertEqual(parsed["speaker_mode"], "speaker")
        utterance = parsed["transcript"][0]
        self.assertEqual((utterance.utterance_index, utterance.speaker_user_id, utterance.speaker_job, utterance.text),
                         (1, "u1", "개발자", "안녕하세요"))

    def test_unknown_speaker_is_rejected(self):
        with self.assertRaises(TranscriptFormatError):
            self.speaker("없는사람_u9: 안녕하세요\n")

    def test_missing_speaker_line_is_rejected(self):
        with self.assertRaises(TranscriptFormatError):
            parse_transcript(f"{HEAD}참여자: 홍길동_개발자_u1 (1명)\n\n홍길동_u1: 안녕하세요\n")

    def test_none_mode_keeps_lines_without_speaker(self):
        parsed = parse_transcript(f"{HEAD}\n첫 줄: 콜론 포함\n둘째 줄\n")
        self.assertEqual(parsed["speaker_mode"], "none")
        self.assertEqual([u.text for u in parsed["transcript"]], ["첫 줄: 콜론 포함", "둘째 줄"])

    def test_title_is_required(self):
        with self.assertRaises(TranscriptFormatError):
            parse_transcript("참여자: 홍길동_개발자_u1\n\n발화\n")

    def test_llm_metadata_adds_weekday_and_hides_user_id(self):
        people = parse_participants("홍길동_개발자_u1")
        shown = llm_metadata({"회의 제목": "t", "일시": "2004-10-11", "참여자": "홍길동_개발자_u1", "발화자": "홍길동_u1"}, people)
        self.assertEqual(shown["일시"], "2004-10-11 (월요일)")
        self.assertNotIn("발화자", shown)
        self.assertNotIn("u1", shown["참여자"])

    def test_load_transcript_accepts_bom_and_crlf(self):
        text = f"﻿{HEAD}\n첫 줄\n둘째 줄\n".replace("\n", "\r\n")
        loaded = load_transcript("M", text)
        self.assertEqual((loaded["meeting_id"], loaded["input_path"], len(loaded["transcript"])), ("M", "M", 2))

    def test_load_transcript_rejects_no_utterance(self):
        with self.assertRaises(ValueError):
            load_transcript("M", HEAD)


if __name__ == "__main__":
    unittest.main()
