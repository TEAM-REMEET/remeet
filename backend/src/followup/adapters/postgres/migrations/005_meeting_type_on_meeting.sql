-- 005: 폴더 구조 변경 — 회의 종류(주간 · 월간 · 일반)를 폴더가 아니라 "회의"에 붙임
--
--   회의록
--   ├ 전체 ─ 월간 회의 / 주간 회의 / 일반 회의 ─ 회의 목록 (최신순)     ← meetings.type_id
--   └ 프로젝트별 ─ 프로젝트 폴더 ─ 회의 목록 (최신순)                    ← meetings.folder_id
--
-- 회의 하나는 종류가 꼭 하나 있고(type_id), 프로젝트는 있을 수도 없을 수도 있음(folder_id).
-- 프로젝트 회의도 전체 > 주간 회의 등에 같이 보임. 프로젝트 폴더에는 종류가 필요 없어서 folders.type_id 는 선택.

-- 1) 기본 회의 종류 3개 (없을 때만. 이게 없으면 회의를 못 만듦)
INSERT INTO meeting_types (id, name, meta, sort_order) VALUES
    ('monthly', '월간 회의', '매월 정기 회의', 0),
    ('weekly',  '주간 회의', '매주 정기 회의', 1),
    ('normal',  '일반 회의', '정기 회의가 아닌 평소 회의', 2)
ON CONFLICT (id) DO NOTHING;

-- 2) 회의에 종류 칸. 기존 회의는 속한 폴더의 종류로, 폴더가 없으면 일반 회의로 채움
ALTER TABLE meetings ADD COLUMN IF NOT EXISTS type_id TEXT REFERENCES meeting_types(id);
UPDATE meetings m SET type_id = f.type_id
FROM folders f WHERE f.id = m.folder_id AND m.type_id IS NULL AND f.type_id IS NOT NULL;
UPDATE meetings SET type_id = 'normal' WHERE type_id IS NULL;
ALTER TABLE meetings ALTER COLUMN type_id SET DEFAULT 'normal';
ALTER TABLE meetings ALTER COLUMN type_id SET NOT NULL;

-- 3) 예전 목업의 "주차별 · 월별 전용 폴더"(period-…)는 이제 필요 없음:
--    안의 회의는 프로젝트 없는 회의로 바꾸고(종류는 2번에서 이미 옮김), 폴더는 소프트 삭제
UPDATE meetings SET folder_id = NULL WHERE folder_id LIKE 'period-%';
UPDATE folders  SET deleted_at = now() WHERE id LIKE 'period-%' AND deleted_at IS NULL;

-- 4) 프로젝트 폴더는 종류 없어도 됨
ALTER TABLE folders ALTER COLUMN type_id DROP NOT NULL;

-- 5) 전체 > 종류별 목록 최신순 정렬용
CREATE INDEX IF NOT EXISTS meetings_type_latest_idx
    ON meetings (type_id, (COALESCE(held_at, created_at)) DESC) WHERE deleted_at IS NULL;
