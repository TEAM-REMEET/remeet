-- 012: 사람 id 칸 → accounts(user_id) 외래 키 · 팀 공유 → teams(team_id)
--
-- 프론트 목업이 없어지고 id(u1 …)는 백엔드 · users.json 이 정함 → 이제 DB 가 "없는 사람" 을 막아도 됨.
-- accounts 를 가리키는 이유: 구성원(people)과 관리자 모두 accounts 에 있고, sync_people() 이 계정을 먼저 만듦.
--   people 은 schema.sql 이 지우지 않는 표라 (migrations/011) 여기서 people 로는 걸지 않음.
--
-- 지울 때 (사람은 원래 지우지 않고 비활성화 · PEOPLE_SEED_GUIDE 6-4)
--   주관 · 참석 · 멤버 · 담당 · 공유 등 "그 사람의 회의 데이터"  → 막음 (NO ACTION: 계정 삭제가 실패)
--   알림 · 물어보기 기록 · 분리 요청 (그 사람만의 것)             → 같이 지움 (CASCADE)
--   "누가 했나" 기록 (확정한 사람 · 바꾼 사람 …)                  → 비움 (SET NULL)
--
-- 기존 데이터에 accounts 에 없는 id 가 있으면 아무것도 바꾸지 않고 그 목록을 보여 주며 멈춤 → 고친 뒤 다시 python migrate.py

DO $$
DECLARE
  spec text[];
  specs text[][] := ARRAY[
    -- 표, 칸, 지울 때
    ARRAY['meetings',               'host_user_id',      'NO ACTION'],
    ARRAY['meetings',               'created_by',        'NO ACTION'],
    ARRAY['meetings',               'reviewer_id',       'NO ACTION'],
    ARRAY['meetings',               'confirmed_by',      'SET NULL'],
    ARRAY['meeting_attendees',      'user_id',           'NO ACTION'],
    ARRAY['meeting_todo_owners',    'user_id',           'NO ACTION'],
    ARRAY['meeting_todo_owners',    'assigned_by',       'SET NULL'],
    ARRAY['meeting_todos',          'created_by',        'SET NULL'],
    ARRAY['meeting_summaries',      'created_by',        'SET NULL'],
    ARRAY['meeting_sources',        'uploaded_by',       'NO ACTION'],
    ARRAY['meeting_speakers',       'user_id',           'SET NULL'],
    ARRAY['meeting_shares',         'sender_id',         'NO ACTION'],
    ARRAY['meeting_shares',         'recipient_user_id', 'NO ACTION'],
    ARRAY['meeting_status_history', 'changed_by',        'SET NULL'],
    ARRAY['folders',                'host_user_id',      'NO ACTION'],
    ARRAY['folder_members',         'user_id',           'NO ACTION'],
    ARRAY['folder_members',         'added_by',          'SET NULL'],
    ARRAY['branch_requests',        'requested_by',      'CASCADE'],
    ARRAY['notifications',          'user_id',           'CASCADE'],
    ARRAY['ask_history',            'user_id',           'CASCADE'],
    ARRAY['failed_exports',         'resolved_by',       'SET NULL'],
    ARRAY['integration_status',     'updated_by',        'SET NULL']
  ];
  bad text := '';
  n bigint;
  ids text;
BEGIN
  -- 1) 검사: accounts 에 없는 id (빈 문자열 포함)
  FOREACH spec SLICE 1 IN ARRAY specs LOOP
    EXECUTE format('SELECT count(*), string_agg(DISTINCT quote_literal(%2$I), '', '')
                    FROM %1$I WHERE %2$I IS NOT NULL
                      AND NOT EXISTS (SELECT 1 FROM accounts a WHERE a.user_id = %1$I.%2$I)', spec[1], spec[2])
      INTO n, ids;
    IF n > 0 THEN
      bad := bad || format(E'\n  %s.%s: %s행 · id %s', spec[1], spec[2], n, left(ids, 200));
    END IF;
  END LOOP;
  SELECT count(*), string_agg(DISTINCT quote_literal(recipient_team_id), ', ') INTO n, ids
    FROM meeting_shares s WHERE recipient_team_id IS NOT NULL
     AND NOT EXISTS (SELECT 1 FROM teams t WHERE t.team_id = s.recipient_team_id);
  IF n > 0 THEN
    bad := bad || format(E'\n  meeting_shares.recipient_team_id: %s행 · 팀 %s (teams 에 없음)', n, left(ids, 200));
  END IF;
  IF bad <> '' THEN
    RAISE EXCEPTION E'accounts 에 없는 사람 id 가 있어서 외래 키를 걸 수 없어요 (아무것도 바꾸지 않음):%\n'
                    '→ 그 사람을 seed_people.py / sync_people() 로 넣거나, 예전 목업 데이터 행을 지운 뒤 다시 python migrate.py', bad;
  END IF;

  -- 2) 외래 키 (이미 있으면 건너뜀 → 다시 돌려도 안전)
  FOREACH spec SLICE 1 IN ARRAY specs LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = spec[1] || '_' || spec[2] || '_account_fk') THEN
      EXECUTE format('ALTER TABLE %1$I ADD CONSTRAINT %3$I FOREIGN KEY (%2$I) REFERENCES accounts(user_id) ON DELETE %4$s',
                     spec[1], spec[2], spec[1] || '_' || spec[2] || '_account_fk', spec[3]);
    END IF;
  END LOOP;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'meeting_shares_recipient_team_fk') THEN
    ALTER TABLE meeting_shares ADD CONSTRAINT meeting_shares_recipient_team_fk
      FOREIGN KEY (recipient_team_id) REFERENCES teams(team_id);
  END IF;
END $$;

-- 외래 키 칸 인덱스 (지울 때 · 사람별 조회가 느려지지 않게. 이미 있는 것은 건너뜀)
CREATE INDEX IF NOT EXISTS meetings_host_idx           ON meetings(host_user_id);
CREATE INDEX IF NOT EXISTS meetings_created_by_idx     ON meetings(created_by);
CREATE INDEX IF NOT EXISTS folders_host_idx            ON folders(host_user_id);
CREATE INDEX IF NOT EXISTS folder_members_user_idx     ON folder_members(user_id);
CREATE INDEX IF NOT EXISTS meeting_shares_sender_idx   ON meeting_shares(sender_id);
CREATE INDEX IF NOT EXISTS meeting_shares_recipient_idx ON meeting_shares(recipient_user_id) WHERE recipient_user_id IS NOT NULL;