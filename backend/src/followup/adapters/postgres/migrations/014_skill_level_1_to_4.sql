-- 014: 스킬 레벨 범위를 backend API(SkillLevel: 1~4)와 맞춤 (011 은 1~5).
-- users.json 에는 5 가 없음. 예전 테스트 등으로 들어간 5 는 4 로 낮춤 (Neo4j 와 다르면 sync_people 로 다시 맞춤).
DO $$
DECLARE n INT;
BEGIN
    UPDATE person_skills SET level = 4, updated_at = now() WHERE level > 4;
    GET DIAGNOSTICS n = ROW_COUNT;
    IF n > 0 THEN
        RAISE NOTICE '스킬 레벨 5 → 4 로 낮춘 행: %', n;
    END IF;
END $$;

ALTER TABLE person_skills DROP CONSTRAINT IF EXISTS person_skills_level_check;
ALTER TABLE person_skills ADD CONSTRAINT person_skills_level_check CHECK (level BETWEEN 1 AND 4);