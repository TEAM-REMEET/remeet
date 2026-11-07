-- 009: 회의 검색용 인덱스 · 생성 뒤 고치는 사람 규칙 정리
--
-- ▶ 검색 (crud/search.py · 화면 탭: 회의명 · 폴더명 · 구성원 · 회의 내용)
--   한국어는 단어 사이 띄어쓰기 · 조사 때문에 PostgreSQL 기본 전문 검색(to_tsvector)이 잘 안 맞아서
--   "글자 일부 포함"(ILIKE '%검색어%')으로 찾고, pg_trgm(3글자 조각) 인덱스로 빠르게 함.
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX IF NOT EXISTS meetings_title_trgm        ON meetings          USING gin (title gin_trgm_ops);
CREATE INDEX IF NOT EXISTS folders_name_trgm          ON folders           USING gin (name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS transcript_lines_text_trgm ON transcript_lines  USING gin (text gin_trgm_ops);
CREATE INDEX IF NOT EXISTS meeting_summaries_ov_trgm  ON meeting_summaries USING gin (overview gin_trgm_ops);
CREATE INDEX IF NOT EXISTS summary_points_text_trgm   ON summary_points    USING gin (text gin_trgm_ops);
CREATE INDEX IF NOT EXISTS meeting_todos_text_trgm    ON meeting_todos     USING gin (text gin_trgm_ops);

-- ▶ 작성 흐름 (008 보완)
--   기본:  미리보기(draft) → 생성(review) → 확정          검토자 없음 · 작성자 또는 주관자가 고치고 확정
--   신입:  미리보기(draft) → 생성(review) → 윗사람 검토 → 확정   reviewer_id = 윗사람 · 그 사람만 고치고 확정
CREATE OR REPLACE FUNCTION remeet_internal.meeting_editor(mid text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
 SELECT remeet_internal.is_regular() AND EXISTS (
   SELECT 1 FROM public.meetings m WHERE m.id = mid
   AND CASE WHEN m.status = 'draft'          THEN remeet_internal.actor_id() IN (m.created_by, m.host_user_id)
            WHEN m.reviewer_id IS NOT NULL   THEN m.reviewer_id = remeet_internal.actor_id()
            ELSE remeet_internal.actor_id() IN (m.created_by, m.host_user_id) END)
$$;
