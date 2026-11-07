-- 008: 회의록 작성 흐름 — 미리보기(임시 저장) → 생성(알림) → 윗사람 검토 · 수정 → 확정
--
--   draft      미리보기 저장 (작성 단계 · 작성자와 주관자만 봄 · 작성자가 고칠 수 있음)
--     │  submit (「회의록 생성 완료」) → 참석자에게 "회의록이 생성됐어요" 알림, 검토자에게 검토 요청 알림
--   review     검토 대기. 이때부터 고칠 수 있는 사람 = 검토자(reviewer_id), 검토자가 없으면 주관자
--     │  confirm
--   confirmed → sending → done / fail   (기존 그대로)
--
-- 신입(주니어)은 쓰기만: 작성자가 reviewer_id(윗사람)를 지정해 제출하면 작성자는 그 뒤로 못 고침.
-- 누가 주니어인지는 구성원 표(팀원 담당)가 생기면 그걸로 reviewer_id 필수 여부를 정하면 됨 — 지금은 넘겨준 값을 따름.

-- ---------------------------------------------------------------- 칸
ALTER TABLE meetings DROP CONSTRAINT IF EXISTS meetings_status_check;
ALTER TABLE meetings ADD CONSTRAINT meetings_status_check
    CHECK (status IN ('draft', 'review', 'confirmed', 'sending', 'done', 'fail'));
ALTER TABLE meetings ADD COLUMN IF NOT EXISTS reviewer_id  TEXT;          -- 검토 · 수정 · 확정할 윗사람 (없으면 주관자)
ALTER TABLE meetings ADD COLUMN IF NOT EXISTS submitted_at TIMESTAMPTZ;   -- 「회의록 생성 완료」 시각
ALTER TABLE meetings ADD COLUMN IF NOT EXISTS confirmed_at TIMESTAMPTZ;
ALTER TABLE meetings ADD COLUMN IF NOT EXISTS confirmed_by TEXT;
UPDATE meetings SET created_by = host_user_id WHERE created_by IS NULL;   -- 작성자 = created_by
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'meetings_reviewer_not_author_ck') THEN
    ALTER TABLE meetings ADD CONSTRAINT meetings_reviewer_not_author_ck
        CHECK (reviewer_id IS NULL OR reviewer_id <> created_by);
  END IF;
END $$;
CREATE INDEX IF NOT EXISTS meetings_reviewer_idx ON meetings(reviewer_id) WHERE reviewer_id IS NOT NULL;

-- 알림 종류: created(회의록이 생성됨)
ALTER TABLE notifications DROP CONSTRAINT IF EXISTS notifications_kind_check;
ALTER TABLE notifications ADD CONSTRAINT notifications_kind_check
    CHECK (kind IN ('review', 'shared', 'integration', 'member', 'delivered', 'fail', 'created'));

-- ---------------------------------------------------------------- 상태 전환 규칙: draft → review 추가
CREATE OR REPLACE FUNCTION remeet_validate_status_transition() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.status = OLD.status THEN RETURN NEW; END IF;
  IF (OLD.status = 'draft' AND NEW.status = 'review')
    OR (OLD.status = 'review' AND NEW.status IN ('confirmed','fail'))
    OR (OLD.status = 'confirmed' AND NEW.status IN ('sending','fail','review'))
    OR (OLD.status = 'sending' AND NEW.status IN ('done','fail'))
    OR (OLD.status = 'fail' AND NEW.status IN ('review','sending','done')) THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION '허용되지 않는 회의 상태 전환: % -> %', OLD.status, NEW.status
    USING ERRCODE = '23514';
END $$;

-- ---------------------------------------------------------------- RLS: 미리보기는 작성자 · 주관자만, 고치는 사람 = 검토자(없으면 주관자)
CREATE OR REPLACE FUNCTION remeet_internal.meeting_editor(mid text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
 SELECT remeet_internal.is_regular() AND EXISTS (
   SELECT 1 FROM public.meetings m WHERE m.id = mid
   AND CASE WHEN m.status = 'draft'
            THEN remeet_internal.actor_id() IN (m.created_by, m.host_user_id)
            ELSE COALESCE(m.reviewer_id, m.host_user_id) = remeet_internal.actor_id() END)
$$;
-- 기존 정책들이 부르는 함수 이름은 그대로 두고 내용만 바꿈
CREATE OR REPLACE FUNCTION remeet_internal.meeting_owned(mid text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
 SELECT remeet_internal.meeting_editor(mid)
    AND EXISTS (SELECT 1 FROM public.meetings m WHERE m.id = mid AND m.deleted_at IS NULL)
$$;
CREATE OR REPLACE FUNCTION remeet_internal.meeting_host(mid text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
 SELECT remeet_internal.meeting_editor(mid)
$$;
CREATE OR REPLACE FUNCTION remeet_internal.meeting_visible(mid text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
 SELECT remeet_internal.is_regular() AND EXISTS (
 SELECT 1 FROM public.meetings m WHERE m.id=mid AND m.deleted_at IS NULL
 AND CASE WHEN m.status = 'draft'
   THEN remeet_internal.actor_id() IN (m.created_by, m.host_user_id)
   ELSE (remeet_internal.actor_id() IN (m.host_user_id, m.created_by, m.reviewer_id)
     OR remeet_internal.attended(m.id)
     OR (m.folder_id IS NOT NULL AND remeet_internal.folder_visible(m.folder_id))
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
     OR (NOT EXISTS (SELECT 1 FROM public.meeting_shares s WHERE s.meeting_id=m.id
           AND s.recipient_user_id=remeet_internal.actor_id() AND s.revoked_at IS NULL)
         AND m.folder_id IS NOT NULL AND remeet_internal.folder_visible(m.folder_id))
     OR EXISTS (SELECT 1 FROM public.meeting_shares s WHERE s.meeting_id=m.id
           AND s.recipient_user_id=remeet_internal.actor_id() AND s.revoked_at IS NULL
           AND s.scope='full' AND NOT s.lock_raw)) END)
$$;
REVOKE ALL ON FUNCTION remeet_internal.meeting_editor(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION remeet_internal.meeting_editor(text) TO remeet_app;

-- 새 회의 쓰기: 작성자 = 나, 프로젝트에 넣으려면 그 프로젝트 멤버면 됨 (주관자가 다른 사람이어도 됨)
DROP POLICY IF EXISTS meeting_insert ON meetings;
CREATE POLICY meeting_insert ON meetings FOR INSERT TO remeet_app
 WITH CHECK (remeet_internal.is_regular() AND created_by = remeet_internal.actor_id() AND status IN ('draft', 'review')
             AND (folder_id IS NULL OR remeet_internal.folder_visible(folder_id)));
