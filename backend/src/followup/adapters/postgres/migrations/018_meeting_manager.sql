-- 018: 회의 삭제 · 공유는 주관자 · 작성자만 (common.is_manager 와 같음) · 검토자는 검토만
--      고치기 · 확정 · 결과 전달은 그대로 meeting_editor (검토자가 있으면 검토자, 없으면 주관자)
--      → 신입 주관자는 제출 뒤 회의록을 고칠 수 없어도 삭제 · 공유는 할 수 있어요. 검토자는 삭제 · 공유 못 함.

CREATE OR REPLACE FUNCTION remeet_internal.meeting_manager(mid text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
 SELECT remeet_internal.is_regular() AND EXISTS (
   SELECT 1 FROM public.meetings m WHERE m.id = mid AND m.deleted_at IS NULL
   AND remeet_internal.actor_id() IN (m.host_user_id, m.created_by))
$$;
REVOKE ALL ON FUNCTION remeet_internal.meeting_manager(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION remeet_internal.meeting_manager(text) TO remeet_app;

-- 삭제(deleted_at)만 따로: 수정 규칙(meeting_update)을 넓히지 않으려고 이 함수로만 지워요
CREATE OR REPLACE FUNCTION remeet_internal.delete_meeting(mid text) RETURNS SETOF public.meetings
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
 UPDATE public.meetings SET deleted_at = now()
 WHERE id = mid AND deleted_at IS NULL AND remeet_internal.meeting_manager(mid)
 RETURNING *
$$;
REVOKE ALL ON FUNCTION remeet_internal.delete_meeting(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION remeet_internal.delete_meeting(text) TO remeet_app;

DROP POLICY IF EXISTS share_select ON meeting_shares;
DROP POLICY IF EXISTS share_insert ON meeting_shares;
DROP POLICY IF EXISTS share_update ON meeting_shares;
DROP POLICY IF EXISTS share_delete ON meeting_shares;
CREATE POLICY share_select ON meeting_shares FOR SELECT TO remeet_app
 USING (recipient_user_id = remeet_internal.actor_id() OR remeet_internal.meeting_manager(meeting_id));
CREATE POLICY share_insert ON meeting_shares FOR INSERT TO remeet_app
 WITH CHECK (remeet_internal.meeting_manager(meeting_id) AND sender_id = remeet_internal.actor_id());
CREATE POLICY share_update ON meeting_shares FOR UPDATE TO remeet_app
 USING (remeet_internal.meeting_manager(meeting_id)) WITH CHECK (remeet_internal.meeting_manager(meeting_id));
CREATE POLICY share_delete ON meeting_shares FOR DELETE TO remeet_app
 USING (remeet_internal.meeting_manager(meeting_id));
