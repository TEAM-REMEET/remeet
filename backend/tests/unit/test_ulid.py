"""ULID 생성"""
import unittest

from followup.domain.ulid import new_ulid


class UlidTests(unittest.TestCase):
    def test_format_is_26_crockford_characters(self):
        self.assertRegex(new_ulid(), r"^[0-9A-HJKMNP-TV-Z]{26}$")

    def test_known_timestamp_prefix(self):
        self.assertEqual(new_ulid(now_ms=1469918176385)[:10], "01ARYZ6S41")    # ULID 명세의 예시 값

    def test_later_time_sorts_after_earlier_time(self):
        self.assertLess(new_ulid(now_ms=1000), new_ulid(now_ms=2000))

    def test_same_millisecond_values_differ(self):
        self.assertEqual(len({new_ulid(now_ms=1000) for _ in range(1000)}), 1000)


if __name__ == "__main__":
    unittest.main()
