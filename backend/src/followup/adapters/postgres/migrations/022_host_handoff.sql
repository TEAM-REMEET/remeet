-- 022: 작성자 → 주관자 넘기기 · 주관자만 주관자 역할 (common.is_editor · is_manager 와 같음)
--   작성 화면에서 주관자를 다른 사람으로 고르면, 분석이 끝난 미리보기(draft)를 주관자에게 넘김 (compose.handoff_meeting).
--   넘긴 뒤에는 작성자가 못 고치고 주관자만 2단계부터 이어서 고치고 확정 · 결과 전달 · 삭제 · 공유해요.
--   주관자는 시니어 · 미드만 · 검토자(reviewer_id) 단계는 없어요 (예전 방식으로 만든 회의록의 검토자는 계속 고치고 확정).

ALTER TABLE meetings ADD COLUMN IF NOT EXISTS handed_off_at TIMESTAMPTZ;   -- 주관자에게 넘긴 시각 (넘기지 않았으면 NULL)
ALTER TABLE meetings ADD COLUMN IF NOT EXISTS handoff JSONB;               -- 작성 화면 입력값 {request, draft} (2단계를 다시 열 때)
CREATE INDEX IF NOT EXISTS meetings_handoff_queue_idx ON meetings (host_user_id, handed_off_at DESC)
    WHERE status = 'draft' AND handed_off_at IS NOT NULL AND deleted_at IS NULL;

-- 고치기 · 확정 · 결과 전달
CREATE OR REPLACE FUNCTION remeet_internal.meeting_editor(mid text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
 SELECT remeet_internal.is_regular() AND EXISTS (
   SELECT 1 FROM public.meetings m WHERE m.id = mid
   AND CASE WHEN m.status = 'draft' AND m.handed_off_at IS NOT NULL THEN remeet_internal.actor_id() = m.host_user_id
            WHEN m.status = 'draft' THEN remeet_internal.actor_id() IN (m.created_by, m.host_user_id)
            ELSE remeet_internal.actor_id() IN (m.host_user_id, m.reviewer_id) END)
$$;

-- 삭제 · 공유: 주관자만
CREATE OR REPLACE FUNCTION remeet_internal.meeting_manager(mid text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
 SELECT remeet_internal.is_regular() AND EXISTS (
   SELECT 1 FROM public.meetings m WHERE m.id = mid AND m.deleted_at IS NULL
   AND remeet_internal.actor_id() = m.host_user_id)
$$;
