-- app_settings 에 수정 시각 · 수정한 사람 칸 보강
--
-- schema.sql 은 app_settings 를 (key, value) 두 칸으로 만들고, 021_llm_usage.sql 은 CREATE TABLE IF NOT EXISTS 라서
-- schema.sql 로 시작한 DB(seed_postgres reset)에는 updated_at · updated_by 가 생기지 않았어요.
-- 그 상태에서 예산 저장(usage.save_budget → PUT /api/admin/budget)이 "column updated_at does not exist" 로 실패해요.
-- 이미 칸이 있는 DB(021 이 표를 직접 만든 경우)에서는 아무것도 바꾸지 않아요.

ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS updated_by text;
