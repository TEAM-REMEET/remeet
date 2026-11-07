-- 010: 프론트(web) 기준으로 맞추기
--
--   ① 이름: meetings.folder_id → project_id · meetings.type_id → directory_id (web Meeting.project_id · directory_id)
--           folders.type_id 삭제 (web Folder = 프로젝트, 종류 없음 · 종류는 회의마다)
--   ② 회의를 볼 수 있는 사람 (web selectors.can_see): 주관 · 참석 · 공유받음 (+ 작성자 · 검토자)
--      프로젝트 멤버라는 것만으로는 안 보임 (미참석 멤버는 주관자가 공유해 줘야 보임)
--   ③ 생성 뒤 고치는 · 확정하는 사람 (web start_finalize: 주관자만): 검토자(reviewer_id), 없으면 주관자
--   ④ 액션 아이템: item_id (MeetingSummary 액션 item_id = 백엔드 담당자 추천/지정 id) · 마감일 수정
--   ⑤ 요약: 검토가 끝난 MeetingSummary JSON (web AppState.summaries)
--   ⑥ 회의에 물어보기 기록 (web AppState.ask_history)
--   ⑦ 알림 종류 budget (관리자 · 예산 알림)
--
-- ※ schema.sql 은 예전 이름(folder_id · type_id)으로 표를 만들고, 이 파일이 바꿔요. 새 DB 도 꼭 python migrate.py.

-- ---------------------------------------------------------------- ① 이름
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'meetings' AND column_name = 'folder_id') THEN
    ALTER TABLE meetings RENAME COLUMN folder_id TO project_id;
  END IF;
  IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'meetings' AND column_name = 'type_id') THEN
    ALTER TABLE meetings RENAME COLUMN type_id TO directory_id;
  END IF;
END $$;
ALTER TABLE folders DROP COLUMN IF EXISTS type_id;
ALTER INDEX IF EXISTS meetings_folder_idx        RENAME TO meetings_project_idx;
ALTER INDEX IF EXISTS meetings_folder_latest_idx RENAME TO meetings_project_latest_idx;
ALTER INDEX IF EXISTS meetings_type_latest_idx   RENAME TO meetings_directory_latest_idx;

-- ---------------------------------------------------------------- ② · ③ 권한 (RLS 함수 — 새 이름 · 새 규칙)
CREATE OR REPLACE FUNCTION remeet_internal.meeting_editor(mid text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
 SELECT remeet_internal.is_regular() AND EXISTS (
   SELECT 1 FROM public.meetings m WHERE m.id = mid
   AND CASE WHEN m.status = 'draft' THEN remeet_internal.actor_id() IN (m.created_by, m.host_user_id)
            ELSE COALESCE(m.reviewer_id, m.host_user_id) = remeet_internal.actor_id() END)
$$;
CREATE OR REPLACE FUNCTION remeet_internal.meeting_visible(mid text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
 SELECT remeet_internal.is_regular() AND EXISTS (
 SELECT 1 FROM public.meetings m WHERE m.id=mid AND m.deleted_at IS NULL
 AND CASE WHEN m.status = 'draft'
   THEN remeet_internal.actor_id() IN (m.created_by, m.host_user_id)
   ELSE (remeet_internal.actor_id() IN (m.host_user_id, m.created_by, m.reviewer_id)
     OR remeet_internal.attended(m.id)
     OR EXISTS (SELECT 1 FROM public.meeting_shares s WHERE s.meeting_id=m.id
       AND s.recipient_user_id=remeet_internal.actor_id() AND s.revoked_at IS NULL)) END)
$$;
CREATE OR REPLACE FUNCTION remeet_internal.raw_visible(mid text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
 SELECT remeet_internal.is_regular() AND EXISTS (
 SELECT 1 FROM public.meetings m WHERE m.id=mid AND m.deleted_at IS NULL
 AND CASE WHEN m.status = 'draft'
   THEN remeet_internal.actor_id() IN (m.created_by, m.host_user_id)
   ELSE (remeet_internal.actor_id() IN (m.host_user_id, m.created_by, m.reviewer_id)
     OR remeet_internal.attended(m.id)
     OR EXISTS (SELECT 1 FROM public.meeting_shares s WHERE s.meeting_id=m.id
           AND s.recipient_user_id=remeet_internal.actor_id() AND s.revoked_at IS NULL
           AND s.scope='full' AND NOT s.lock_raw)) END)
$$;

-- ---------------------------------------------------------------- ④ 액션 아이템
ALTER TABLE meeting_todos ADD COLUMN IF NOT EXISTS item_id TEXT;   -- MeetingSummary 액션 item_id (직접 추가한 일은 NULL)
CREATE UNIQUE INDEX IF NOT EXISTS meeting_todos_item_once ON meeting_todos(meeting_id, item_id) WHERE item_id IS NOT NULL;

-- ---------------------------------------------------------------- ⑤ 검토가 끝난 MeetingSummary JSON
ALTER TABLE meeting_summaries ADD COLUMN IF NOT EXISTS summary_json JSONB;

-- ---------------------------------------------------------------- ⑥ 회의에 물어보기 기록
CREATE TABLE IF NOT EXISTS ask_history (
    id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id   TEXT  NOT NULL,                        -- 물어본 사람
    question  TEXT  NOT NULL,
    answer    JSONB NOT NULL,                        -- 온보딩 에이전트 ask 결과 JSON 그대로 (answer_status · summary · sentences …)
    asked_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ask_history_user_idx ON ask_history(user_id, asked_at DESC);
GRANT SELECT, INSERT, DELETE ON ask_history TO remeet_app;
GRANT USAGE ON SEQUENCE ask_history_id_seq TO remeet_app;
ALTER TABLE ask_history ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS ask_own ON ask_history;
CREATE POLICY ask_own ON ask_history FOR ALL TO remeet_app
 USING (user_id = remeet_internal.actor_id()) WITH CHECK (user_id = remeet_internal.actor_id());

-- ---------------------------------------------------------------- ⑦ 알림 종류
ALTER TABLE notifications DROP CONSTRAINT IF EXISTS notifications_kind_check;
ALTER TABLE notifications ADD CONSTRAINT notifications_kind_check
    CHECK (kind IN ('review', 'shared', 'integration', 'member', 'delivered', 'fail', 'created', 'budget'));
