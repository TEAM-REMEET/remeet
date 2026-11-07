-- 007: 외부 전송 실패 기록 · 연동 상태(누가 바꿨나) · 마지막 전송 시각 · 회의 상태 변경 이력
-- (폴더 브리프 수정은 표 변경 없이 crud 함수만 추가)

-- ---------------------------------------------------------------- 외부 전송 실패 기록 (web ExportFailure)
-- 예전: (tool_id, meeting_id) 한 쌍만 → 같은 도구 · 회의가 여러 번 실패할 수 있으니 실패마다 1행
ALTER TABLE failed_exports ADD COLUMN IF NOT EXISTS id          TEXT;
ALTER TABLE failed_exports ADD COLUMN IF NOT EXISTS kind        TEXT NOT NULL DEFAULT 'simple';
ALTER TABLE failed_exports ADD COLUMN IF NOT EXISTS reason      TEXT NOT NULL DEFAULT '';
ALTER TABLE failed_exports ADD COLUMN IF NOT EXISTS code        TEXT NOT NULL DEFAULT '';   -- "timeout (30초)" · "401 …"
ALTER TABLE failed_exports ADD COLUMN IF NOT EXISTS failed_at   TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE failed_exports ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ;               -- 다시 보내서 해결된 시각
ALTER TABLE failed_exports ADD COLUMN IF NOT EXISTS resolved_by TEXT;
UPDATE failed_exports SET id = 'f-' || replace(gen_random_uuid()::text, '-', '') WHERE id IS NULL;
ALTER TABLE failed_exports ALTER COLUMN id SET DEFAULT 'f-' || replace(gen_random_uuid()::text, '-', '');
ALTER TABLE failed_exports ALTER COLUMN id SET NOT NULL;
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'failed_exports_pkey'
             AND pg_get_constraintdef(oid) <> 'PRIMARY KEY (id)') THEN
    ALTER TABLE failed_exports DROP CONSTRAINT failed_exports_pkey;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'failed_exports_pkey') THEN
    ALTER TABLE failed_exports ADD CONSTRAINT failed_exports_pkey PRIMARY KEY (id);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'failed_exports_kind_check') THEN
    -- simple: 일시적 오류 → 주관자가 다시 보내기 · tech: API · 인증 문제 → 관리자가 처리
    ALTER TABLE failed_exports ADD CONSTRAINT failed_exports_kind_check CHECK (kind IN ('simple', 'tech'));
  END IF;
END $$;
CREATE INDEX IF NOT EXISTS failed_exports_meeting_idx ON failed_exports(meeting_id, failed_at DESC);
CREATE INDEX IF NOT EXISTS failed_exports_open_idx ON failed_exports(failed_at DESC) WHERE resolved_at IS NULL;

-- ---------------------------------------------------------------- 연동: 마지막 전송 시각 · 누가 바꿨나
ALTER TABLE integration_tools  ADD COLUMN IF NOT EXISTS last_sync_at TIMESTAMPTZ;
ALTER TABLE integration_status ADD COLUMN IF NOT EXISTS updated_by   TEXT;

-- (이 파일의 예전 판에 있던 안건 분리 인덱스 정리 · 없으면 아무 일 없음)
DROP INDEX IF EXISTS meetings_branch_once;

-- ---------------------------------------------------------------- 알림 종류: 화면이 쓰는 것 추가
-- delivered(전달 완료) · fail(전송 실패, 관리자)
ALTER TABLE notifications DROP CONSTRAINT IF EXISTS notifications_kind_check;
ALTER TABLE notifications ADD CONSTRAINT notifications_kind_check
    CHECK (kind IN ('review', 'shared', 'integration', 'member', 'delivered', 'fail'));

-- ---------------------------------------------------------------- 회의 상태 변경 이력
CREATE TABLE IF NOT EXISTS meeting_status_history (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    meeting_id  TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    from_status TEXT,                                -- 처음 만들 때는 NULL
    to_status   TEXT NOT NULL,
    changed_by  TEXT,                                -- transaction(actor_id) 로 넘긴 사람 (없으면 NULL = 시스템)
    changed_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS meeting_status_history_idx ON meeting_status_history(meeting_id, changed_at);

-- 상태가 바뀔 때마다 DB 가 자동으로 기록 (코드에서 깜빡해도 빠지지 않게)
CREATE OR REPLACE FUNCTION remeet_internal.log_status_change() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
  IF TG_OP = 'INSERT' OR NEW.status IS DISTINCT FROM OLD.status THEN
    INSERT INTO public.meeting_status_history (meeting_id, from_status, to_status, changed_by)
    VALUES (NEW.id, CASE WHEN TG_OP = 'UPDATE' THEN OLD.status END, NEW.status,
            nullif(current_setting('remeet.user_id', true), ''));
  END IF;
  RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION remeet_internal.log_status_change() FROM PUBLIC;
DROP TRIGGER IF EXISTS meetings_status_history ON meetings;
CREATE TRIGGER meetings_status_history AFTER INSERT OR UPDATE OF status ON meetings
    FOR EACH ROW EXECUTE FUNCTION remeet_internal.log_status_change();

-- 지금 상태를 이력의 시작점으로 (이미 있는 회의)
INSERT INTO meeting_status_history (meeting_id, from_status, to_status, changed_at)
SELECT m.id, NULL, m.status, m.created_at FROM meetings m
WHERE NOT EXISTS (SELECT 1 FROM meeting_status_history h WHERE h.meeting_id = m.id);

-- 상태 전환 규칙 보완 (001): 전송 실패 → 다시 보내기(sending) · 다시 보내서 해결(done) 허용 (화면 흐름)
CREATE OR REPLACE FUNCTION remeet_validate_status_transition() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.status = OLD.status THEN RETURN NEW; END IF;
  IF (OLD.status = 'review' AND NEW.status IN ('confirmed','fail'))
    OR (OLD.status = 'confirmed' AND NEW.status IN ('sending','fail','review'))
    OR (OLD.status = 'sending' AND NEW.status IN ('done','fail'))
    OR (OLD.status = 'fail' AND NEW.status IN ('review','sending','done')) THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION '허용되지 않는 회의 상태 전환: % -> %', OLD.status, NEW.status
    USING ERRCODE = '23514';
END $$;

-- ---------------------------------------------------------------- RLS
GRANT SELECT ON meeting_status_history TO remeet_app;
ALTER TABLE meeting_status_history ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS status_history_select ON meeting_status_history;
CREATE POLICY status_history_select ON meeting_status_history FOR SELECT TO remeet_app
 USING (remeet_internal.meeting_visible(meeting_id));
