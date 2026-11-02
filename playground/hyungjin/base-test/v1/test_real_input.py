import unittest

from real_input import parse_transcript


class RealInputTest(unittest.TestCase):
    def test_preserves_timestamp_speaker_and_text(self):
        text = '00:24 참석자 1\n첫 발화입니다.\n\n01:02:03 참석자 2\n다음 발화입니다.\n'
        result = parse_transcript(text)
        self.assertEqual([x['utterance_index'] for x in result], [0, 1])
        self.assertEqual(result[0]['timestamp'], '00:24')
        self.assertEqual(result[1]['speaker'], '참석자 2')
        self.assertEqual(result[1]['text'], '다음 발화입니다.')

    def test_rejects_missing_or_unmatched_content(self):
        with self.assertRaises(ValueError):
            parse_transcript('머리말\n00:24 참석자 1\n발화')
        with self.assertRaises(ValueError):
            parse_transcript('00:24 참석자 1\n\n01:00 참석자 2\n발화')


if __name__ == '__main__':
    unittest.main()
