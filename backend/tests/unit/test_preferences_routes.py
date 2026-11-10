"""/api/me/preferences · 로그인한 사람 것만 (어댑터는 가짜 · DB 없음)"""
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from followup.adapters.postgres import preferences
from followup.adapters.postgres.common import ValidationError
from followup.entrypoints.api.main import app
from followup.entrypoints.api.routes.auth import current_user


class PreferenceRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.addCleanup(app.dependency_overrides.clear)

    def test_requires_login(self):
        self.assertEqual(self.client.get("/api/me/preferences").status_code, 401)
        self.assertEqual(self.client.put("/api/me/preferences/people_fav", json={"value": []}).status_code, 401)

    def test_reads_and_saves_only_my_own(self):
        app.dependency_overrides[current_user] = lambda: {"user_id": "u2", "permission": "member"}
        with patch.object(preferences, "get_preferences", return_value={"people_fav": ["u3"]}) as g:
            self.assertEqual(self.client.get("/api/me/preferences").json(), {"people_fav": ["u3"]})
        g.assert_called_once_with(actor_id="u2")
        with patch.object(preferences, "set_preference", return_value=["u3"]) as s:
            r = self.client.put("/api/me/preferences/people_fav", json={"value": ["u3"], "user_id": "u9"})
        self.assertEqual((r.status_code, r.json()), (200, ["u3"]))
        s.assert_called_once_with(actor_id="u2", key="people_fav", value=["u3"])  # 본문의 user_id 는 무시

    def test_bad_input_is_400_or_422(self):
        app.dependency_overrides[current_user] = lambda: {"user_id": "u2", "permission": "member"}
        with patch.object(preferences, "set_preference", side_effect=ValidationError("저장할 수 없는 설정이에요")):
            self.assertEqual(self.client.put("/api/me/preferences/theme", json={"value": ["x"]}).status_code, 400)
        self.assertEqual(self.client.put("/api/me/preferences/people_fav", json={"value": "u3"}).status_code, 422)


if __name__ == "__main__":
    unittest.main()
