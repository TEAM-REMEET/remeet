-- 회의를 지워서 그 프로젝트(folders)에 남은 회의가 하나도 없으면 프로젝트도 같이 지움 (soft delete)
--
-- Neo4j 삭제(adapters/neo4j/load.py DELETE_MEETING)가 회의 · Topic 이 안 남은 Project 노드를 지우는 것과 맞춤.
-- 회의 삭제는 주관자가 하는데 폴더 수정(RLS folder_update)은 폴더 주인만 돼서, 018 의 SECURITY DEFINER 함수 안에서 같이 처리.
-- 처음부터 비어 있던 폴더(회의를 아직 안 올림)는 회의 삭제가 없으니 그대로 남아요.

CREATE OR REPLACE FUNCTION remeet_internal.delete_meeting(mid text) RETURNS SETOF public.meetings
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
 WITH gone AS (
   UPDATE public.meetings SET deleted_at = now()
   WHERE id = mid AND deleted_at IS NULL AND remeet_internal.meeting_manager(mid)
   RETURNING *
 ), emptied AS (
   -- 같은 문장 안이라 meetings 는 지우기 전 모습으로 보임 → 지운 회의(mid)는 빼고 셈
   UPDATE public.folders f SET deleted_at = now()
   FROM gone
   WHERE f.id = gone.project_id AND f.deleted_at IS NULL
     AND NOT EXISTS (SELECT 1 FROM public.meetings m
                     WHERE m.project_id = f.id AND m.deleted_at IS NULL AND m.id <> mid)
   RETURNING f.id
 )
 SELECT * FROM gone
$$;
REVOKE ALL ON FUNCTION remeet_internal.delete_meeting(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION remeet_internal.delete_meeting(text) TO remeet_app;
