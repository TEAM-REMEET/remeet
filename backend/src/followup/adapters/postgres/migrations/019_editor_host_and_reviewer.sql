-- 019: 생성(review) 뒤 고치기 · 확정 · 결과 전달 = 주관자 · 검토자 둘 다 (common.is_editor 와 같음)
--      전에는 검토자가 있으면 검토자만 → 신입 주관자도 검토자와 함께 편집 · 확정할 수 있게.
--      주관자가 아닌 작성자는 그대로 생성 뒤 못 고침 (주관자가 검토). 삭제 · 공유는 018 (주관자 · 작성자).

CREATE OR REPLACE FUNCTION remeet_internal.meeting_editor(mid text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
 SELECT remeet_internal.is_regular() AND EXISTS (
   SELECT 1 FROM public.meetings m WHERE m.id = mid
   AND CASE WHEN m.status = 'draft' THEN remeet_internal.actor_id() IN (m.created_by, m.host_user_id)
            ELSE remeet_internal.actor_id() IN (m.host_user_id, m.reviewer_id) END)
$$;
