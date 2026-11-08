-- 017: 직접 추가한 액션 아이템의 「안건」 (화면에서 고른 안건 제목 · 회의를 다시 열면 그 안건 아래에 보여 줘요).
-- AI 가 만든 할 일은 빈 값. 안건 제목을 그대로 적어 두는 이유: 요약 버전이 바뀌면 안건 번호가 달라질 수 있어서.
ALTER TABLE meeting_todos ADD COLUMN IF NOT EXISTS agenda_title TEXT NOT NULL DEFAULT '';
