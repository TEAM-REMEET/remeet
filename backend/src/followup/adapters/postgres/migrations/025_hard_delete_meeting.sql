-- 회의 · 프로젝트를 Neo4j 처럼 실제로 지움 (soft delete 를 그만씀)
--
-- ① 회의 삭제 (018 의 delete_meeting 을 교체): deleted_at 표시 대신 행을 지움.
--    요약 · 원문 · 할 일 · 참석자 · 공유 · 알림 · 상태 이력 · 전송 실패 기록은 FK CASCADE 로 같이 지워져요.
--    이 회의에서 갈라져 나온 회의(branch_of_id)는 남기고 연결만 끊어요.
--    마지막 회의를 지워도 프로젝트는 지우지 않아요 (프로젝트는 회의가 0개일 때 「폴더 삭제」로만 지움).
-- ② 프로젝트 안 회의 수 (권한과 상관없이 전부): 「폴더 삭제」는 회의가 0개일 때만 되는데,
--    RLS 때문에 주관자에게 안 보이는 회의(다른 사람의 작성 중 회의 등)까지 세려면 SECURITY DEFINER 가 필요해요.
--    프로젝트 자체는 folders RLS(folder_delete = 주관자)로 지우고, 멤버 · 브리프 · 알림은 FK CASCADE.
-- (예전 024 는 쓰지 않기로 해서 지웠어요. 이미 024 를 적용한 DB 도 이 파일이 함수를 덮어써요.)

CREATE OR REPLACE FUNCTION remeet_internal.delete_meeting(mid text) RETURNS SETOF public.meetings
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
  IF NOT remeet_internal.meeting_manager(mid) THEN
    RETURN;
  END IF;
  UPDATE public.meetings SET branch_of_id = NULL WHERE branch_of_id = mid;
  RETURN QUERY DELETE FROM public.meetings WHERE id = mid RETURNING *;
END
$$;
REVOKE ALL ON FUNCTION remeet_internal.delete_meeting(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION remeet_internal.delete_meeting(text) TO remeet_app;

CREATE OR REPLACE FUNCTION remeet_internal.folder_meeting_count(fid text) RETURNS bigint
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
 SELECT count(*) FROM public.meetings WHERE project_id = fid AND deleted_at IS NULL
$$;
REVOKE ALL ON FUNCTION remeet_internal.folder_meeting_count(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION remeet_internal.folder_meeting_count(text) TO remeet_app;
