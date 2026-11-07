-- 013: 기준 데이터 — 예전엔 프론트 목업(integrations.json · folders.json)에서 seed.py 가 넣던 것
--   외부 연동 도구 목록 · 처음 연결 상태 · 회의 종류 설명. 운영 DB 에도 필요해서 seed.py 가 아니라 마이그레이션으로.
--   이미 있는 값은 바꾸지 않음 (관리자가 바꾼 연결 상태 · 문구 보존). 다시 돌려도 안전.

INSERT INTO integration_tools (id, mark, name, description, target, is_phase2, sort_order) VALUES
    ('slack',  'S',  'Slack',           '채널에 회의 결과를 공유합니다',          '사내 워크스페이스',  FALSE, 0),
    ('mail',   '이', '이메일',          '참석자에게 회의 결과를 메일로 보냅니다', '사내 메일 서버',     FALSE, 1),
    ('cal',    '캘', '캘린더',          '다음 회의 일정을 등록합니다',            '팀 공용 캘린더',     FALSE, 2),
    ('jira',   'J',  'Jira',            '액션 아이템을 이슈로 만듭니다',          '',                   FALSE, 3),
    ('notion', 'N',  'Notion',          '회의록 페이지를 만듭니다',               '팀 위키',            FALSE, 4),
    ('teams',  'M',  'Microsoft Teams', '팀 채널에 회의 결과를 공유합니다',       '',                   TRUE,  5),
    ('conf',   'C',  'Confluence',      '회의록 페이지를 만듭니다',               '',                   TRUE,  6)
ON CONFLICT (id) DO NOTHING;

-- 처음엔 모두 연결 안 됨 (관리자가 MCP 관리에서 연결 → integrations.set_tool_status)
INSERT INTO integration_status (tool_id, status)
SELECT id, 'off' FROM integration_tools
ON CONFLICT (tool_id) DO NOTHING;

-- 회의 종류 설명 (schema.sql 의 기본 문구 → 화면 문구). 이미 바꾼 문구는 그대로
UPDATE meeting_types SET meta = '매월 마지막 금요일 15:00 · 회의 날마다 회의록 1개'
 WHERE id = 'monthly' AND meta = '매월 정기 회의';
UPDATE meeting_types SET meta = '매주 월요일 10:00 · 회의 날마다 회의록 1개'
 WHERE id = 'weekly'  AND meta = '매주 정기 회의';
UPDATE meeting_types SET meta = '정기 회의가 아닌 평소 회의 · 열릴 때마다 회의록 1개'
 WHERE id = 'normal'  AND meta = '정기 회의가 아닌 평소 회의';