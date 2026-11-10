-- 026: 사람마다 저장하는 화면 설정 (사람 고르기 · 조직 선택의 즐겨찾기 · 최근)
--
-- 예전에는 화면 상태(Streamlit 세션)에만 있어서 새로고침 · 다시 로그인 · 프론트 재시작 · 홈 필터 카드(?filter= 새로 불러오기)
-- 때마다 사라졌어요. 이제 로그인할 때 읽고, 바꿀 때마다 저장해요 (adapters/postgres/preferences.py).
--   key   = people_fav | people_recent | team_fav | team_recent   (허용 키 · 개수 제한은 preferences.KEYS)
--   value = 문자열 목록 JSON (사람 user_id 또는 팀 이름) · 최근 목록은 순서 = 최근 순
-- 본인 것만 읽고 씀 (RLS · remeet_app 으로 접속할 때). 회원을 지우면 같이 지워짐.

CREATE TABLE IF NOT EXISTS user_preferences (
  user_id    TEXT NOT NULL REFERENCES accounts(user_id) ON DELETE CASCADE,
  key        TEXT NOT NULL,
  value      JSONB NOT NULL DEFAULT '[]'::jsonb,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, key)
);

GRANT SELECT, INSERT, UPDATE, DELETE ON user_preferences TO remeet_app;
ALTER TABLE user_preferences ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS user_preferences_own ON user_preferences;
CREATE POLICY user_preferences_own ON user_preferences FOR ALL TO remeet_app
 USING (user_id = remeet_internal.actor_id()) WITH CHECK (user_id = remeet_internal.actor_id());
