-- =====================================================================
-- RE:MEET · PostgreSQL 스키마  (Neo4j 에 없는 것만)
--
-- ▶ Neo4j 담당 (playground/eunje/ontology · scripts/load.py) — 여기 만들지 않음
--     Person · Team · Role · Skill (+ HAS_SKILL level)           사람 · 조직 · 역량
--     Project · Topic                                           프로젝트 · 회의를 가로지르는 주제
--     Meeting {meeting_id, title, held_at} + ATTENDED           회의 제목 · 날짜 · 참석자
--     Agenda {background, decisions, unresolved_issues, next_topics}
--     Discussion {discussions, evidence, tags}                  소주제별 논의
--     ActionItem {description, status, due, owner_raw}          할 일 · 완료 상태
--     ASSIGNED_TO · RECOMMENDED_FOR                             담당자 지정 · 추천
--     위키 그래프
--
--   ※ 회의 제목 · 날짜는 목록 정렬 때문에 meetings.title · held_at 에도 둠. 안건 · 논의 · 할 일은
--     요약 회의록(summary_*) 으로도 저장함 → Neo4j 쪽과 어느 쪽을 원본으로 할지 팀에서 확정 필요
--
-- ▶ PostgreSQL 담당 (이 파일)
--     로그인 계정, 회의 종류 · 폴더 · 폴더 멤버 · 브리프
--     회의 "관리 정보"(폴더, 주관자, 진행 상태, 파일, 삭제 여부, 안건 분리)
--     회의 원문(발화) · 화자 매핑 · AI 분석 실행 기록(summary.json 원본)
--     회의 원본 파일(meeting_sources) · 요약 회의록(meeting_summaries · summary_*)
--     요약 · 키워드, 공유, 외부 연동, 알림, 앱 설정
--
-- ▶ 두 DB 를 잇는 열쇠 = 같은 id 를 쓰는 것
--     meetings.id   = Neo4j Meeting.meeting_id   (예: TS3003a)
--     folders.id    = Neo4j Project.project_id   (예: TS3003)
--     *_user_id     = Neo4j Person.user_id       (예: u1)
--     *_team_id     = Neo4j Team.team_id         (예: t-pm)
--   다른 DB 에 있는 값은 외래 키(REFERENCES)로 묶을 수 없어서, 이런 칸은 그냥 TEXT 로 둡니다.
--   (= DB 가 "그런 사람 없음"을 막아 주지 못하니, 코드에서 조심)
--
-- 읽는 법
--   PRIMARY KEY  : 그 행을 유일하게 구분하는 값
--   REFERENCES   : 외래 키(FK). 같은 PostgreSQL 안의 다른 표에 실제로 있는 값만 넣을 수 있게 막음
--   ON DELETE CASCADE : 부모 행이 지워지면 딸린 행도 같이 지움
--   TEXT[] / JSONB    : 문자열 배열 / JSON 그대로
--   position     : 순서 (표에는 순서가 없어서 직접 저장)
-- =====================================================================

DROP TABLE IF EXISTS
    ask_history, notifications, failed_exports, integration_status, integration_tools, meeting_status_history,
    branch_requests, meeting_todo_owners, meeting_todos, meeting_attendees,
    summary_points, summary_topics, summary_action_items, summary_discussions, summary_agendas,
    meeting_summaries, meeting_sources,
    meeting_shares, meeting_keywords, transcript_lines, meeting_speakers, meeting_analyses,
    meetings, folder_briefs, folder_members, folders, meeting_types,
    accounts, app_settings, user_preferences,  -- user_preferences: migrations/026 (지우면 026 이 다시 만듦)
    -- 예전 버전(Neo4j 와 겹치던 표)도 남아 있으면 지움
    todo_assignees, todos, agenda_points, agendas, meeting_findings, meeting_discussions, paragraphs,
    -- ⚠️ 구성원 표(users · teams · user_skills …)는 팀원 담당이라 여기서 지우지 않음. 이름을 여기 넣지 마세요.
    -- 마이그레이션 기록도 지움: 표를 새로 만들었으니 migrations/*.sql 을 다시 적용해야 함 (python migrate.py)
    schema_migrations
CASCADE;
-- migrations/ 가 만든 함수 · 스키마도 지워야 migrate.py 를 다시 돌릴 때 "이미 있음" 에러가 안 남
DROP SCHEMA IF EXISTS remeet_internal CASCADE;
DROP FUNCTION IF EXISTS remeet_validate_status_transition() CASCADE;

-- ---------------------------------------------------------------- 로그인 계정
-- 사람의 이름 · 팀 · 역할 · 스킬은 Neo4j Person 에 있음. 여기는 로그인에 필요한 것만.
CREATE TABLE accounts (
    user_id       TEXT PRIMARY KEY,                 -- = Neo4j Person.user_id
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT,                             -- 해시만 저장
    role          TEXT NOT NULL DEFAULT 'USER' CHECK (role IN ('USER', 'ADMIN')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at TIMESTAMPTZ
);

-- 자잘한 설정값 (key → JSON)
CREATE TABLE app_settings (
    key   TEXT PRIMARY KEY,
    value JSONB NOT NULL
);

-- ---------------------------------------------------------------- 폴더
CREATE TABLE meeting_types (
    id           TEXT PRIMARY KEY,                  -- weekly | monthly | normal
    name         TEXT NOT NULL,
    meta         TEXT NOT NULL,
    hidden_count INT  NOT NULL DEFAULT 0,
    sort_order   INT  NOT NULL DEFAULT 0
);
-- 기본 회의 종류 (회의를 만들려면 꼭 있어야 함. seed.py 가 목업 문구로 덮어씀)
INSERT INTO meeting_types (id, name, meta, sort_order) VALUES
    ('monthly', '월간 회의', '매월 정기 회의', 0),
    ('weekly',  '주간 회의', '매주 정기 회의', 1),
    ('normal',  '일반 회의', '정기 회의가 아닌 평소 회의', 2);

-- 회의록 화면 구조
--   전체 ─ 월간 / 주간 / 일반 회의 ─ 회의 목록 (최신순)   = meetings.type_id
--   프로젝트별 ─ 프로젝트 폴더 ─ 회의 목록 (최신순)        = meetings.folder_id → folders
-- ⚠️ 이 파일은 "기본 모양"이에요. 최종 모양은 python migrate.py 까지 돌린 뒤 (seed.py 는 자동으로 돌림):
--    010 이 meetings.folder_id → project_id · meetings.type_id → directory_id 로 이름을 바꾸고 folders.type_id 를 지우고,
--    meeting_todos.item_id · meeting_summaries.summary_json · ask_history 를 더해요 (web data/models.py 이름에 맞춤).
-- 폴더 = Neo4j 의 Project. 폴더는 웹에서 만들어지므로 이름의 원본은 여기 (Neo4j Project.name 은 복사본)
CREATE TABLE folders (
    id           TEXT PRIMARY KEY,                  -- = Neo4j Project.project_id
    name         TEXT NOT NULL,
    type_id      TEXT REFERENCES meeting_types(id),  -- 선택 (예전 화면 호환용). 회의 종류는 meetings.type_id
    host_user_id TEXT NOT NULL,                     -- 폴더 주관자 (Neo4j Person.user_id)
    sort_order   INT  NOT NULL DEFAULT 0,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at   TIMESTAMPTZ                        -- NULL 이면 살아 있음 (소프트 삭제)
);

-- 폴더 ↔ 사람 (다대다 → 연결 표). "누가 이 폴더를 볼 수 있나" = 권한이라 RDB 에 둠
CREATE TABLE folder_members (
    folder_id TEXT NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
    user_id   TEXT NOT NULL,
    position  INT  NOT NULL DEFAULT 0,
    added_by  TEXT,
    added_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (folder_id, user_id)
);

CREATE TABLE folder_briefs (
    folder_id TEXT NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
    position  INT  NOT NULL,
    label     TEXT NOT NULL,
    text      TEXT NOT NULL,
    PRIMARY KEY (folder_id, position)
);

-- ---------------------------------------------------------------- 회의 (관리 정보)
-- 제목 · 날짜 · 참석자 · 안건 · 논의 · 결정 · 할 일은 Neo4j. 여기는 "앱이 회의를 관리하는 정보"만.
CREATE TABLE meetings (
    id                  TEXT PRIMARY KEY,           -- = Neo4j Meeting.meeting_id
    folder_id           TEXT REFERENCES folders(id),-- 프로젝트 폴더. 프로젝트 없는 회의 · 공유받은 회의는 NULL
    type_id             TEXT NOT NULL DEFAULT 'normal' REFERENCES meeting_types(id),  -- 월간 · 주간 · 일반 (전체 탭 분류)
    title               TEXT NOT NULL DEFAULT '',   -- 회의 제목 (목록 화면용)
    held_at             TIMESTAMPTZ,                -- 회의 날짜 · 시각. 목록 "최신순" 정렬 기준 (모르면 NULL → created_at 사용)
    host_user_id        TEXT NOT NULL,              -- 주관자 (Neo4j 에는 참석(ATTENDED)만 있고 주관자는 없음)
    time_label          TEXT NOT NULL DEFAULT '',   -- "10:00 – 10:48"
    is_deep             BOOLEAN NOT NULL DEFAULT FALSE,
    file_name           TEXT NOT NULL DEFAULT '',   -- 올린 회의록 파일 이름
    status              TEXT NOT NULL DEFAULT 'review'  -- draft = 미리보기 저장 (migrations/008)
                        CONSTRAINT meetings_status_check
                        CHECK (status IN ('draft', 'review', 'confirmed', 'sending', 'done', 'fail')),
    reviewer_id         TEXT,                       -- 검토 · 수정 · 확정할 윗사람 (없으면 주관자). 신입이 쓴 회의록
    submitted_at        TIMESTAMPTZ,                -- 「회의록 생성 완료」(draft → review) 시각
    confirmed_at        TIMESTAMPTZ,
    confirmed_by        TEXT,
    summary_text        TEXT NOT NULL DEFAULT '',   -- 회의 전체 한 줄 요약 (Neo4j Meeting 에 없음)
    summary_src         TEXT[] NOT NULL DEFAULT '{}',
    branch_of_id        TEXT REFERENCES meetings(id),
    branch_agenda_index INT,
    position            INT NOT NULL DEFAULT 0,     -- 전체 목록 순서
    folder_position     INT NOT NULL DEFAULT 0,     -- 폴더 안 순서
    created_by          TEXT,                       -- 작성자 (신입일 수 있음)
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at          TIMESTAMPTZ,
    CONSTRAINT meetings_reviewer_not_author_ck CHECK (reviewer_id IS NULL OR reviewer_id <> created_by)
);
CREATE INDEX meetings_folder_idx ON meetings(folder_id);
CREATE INDEX meetings_reviewer_idx ON meetings(reviewer_id) WHERE reviewer_id IS NOT NULL;

-- 회의 상태 변경 이력 (migrations/007 의 트리거가 자동으로 기록)
CREATE TABLE meeting_status_history (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    meeting_id  TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    from_status TEXT,                                -- 처음 만들 때는 NULL
    to_status   TEXT NOT NULL,
    changed_by  TEXT,                                -- transaction(actor_id) 로 넘긴 사람 (없으면 NULL = 시스템)
    changed_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX meeting_status_history_idx ON meeting_status_history(meeting_id, changed_at);
-- 목록 정렬용: 전체 = 최신순, 프로젝트별 = 폴더 안에서 최신순
CREATE INDEX meetings_latest_idx        ON meetings ((COALESCE(held_at, created_at)) DESC) WHERE deleted_at IS NULL;
CREATE INDEX meetings_type_latest_idx   ON meetings (type_id, (COALESCE(held_at, created_at)) DESC) WHERE deleted_at IS NULL;
CREATE INDEX meetings_folder_latest_idx ON meetings (folder_id, (COALESCE(held_at, created_at)) DESC) WHERE deleted_at IS NULL;

-- AI 분석 실행 1번 = 1행. summary.json 에서 Neo4j 로 안 가는 부분(설정 · 원본 정보 · 검토 이력)과
-- summary.json 전체(raw)를 보관 → Neo4j 를 지우고 다시 적재(load.py --reset)할 때의 원본이 됨
CREATE TABLE meeting_analyses (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    meeting_id        TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    created_at        TIMESTAMPTZ NOT NULL,         -- summary.json 의 created_at
    model             TEXT,                         -- config.model
    reasoning_effort  TEXT,
    config            JSONB NOT NULL DEFAULT '{}',
    source_document   JSONB NOT NULL DEFAULT '{}',  -- 입력 파일 경로 · 발화 수
    meeting_info      JSONB NOT NULL DEFAULT '{}',  -- 제목 · 날짜 · 길이 · 화자 원문
    postprocess_log   JSONB NOT NULL DEFAULT '[]',
    raw               JSONB NOT NULL,               -- summary.json 전체 (transcript 는 transcript_lines 로 따로)
    loaded_to_neo4j_at TIMESTAMPTZ                  -- Neo4j 에 적재한 시각 (아직이면 NULL)
);
CREATE INDEX meeting_analyses_meeting_idx ON meeting_analyses(meeting_id, created_at DESC);

-- 화자 표기(A, 참석자1 …) → 실제 사람. 발화자 확인 단계(작성 2단계)의 결과
CREATE TABLE meeting_speakers (
    meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    label      TEXT NOT NULL,                       -- "A"
    role_text  TEXT NOT NULL DEFAULT '',            -- "프로젝트 매니저"
    user_id    TEXT,                                -- 연결한 사람 (모르면 NULL)
    PRIMARY KEY (meeting_id, label)
);

-- 회의 원문 발화. Neo4j Discussion.evidence 가 가리키는 "86,87,88" 이 여기 line_id
CREATE TABLE transcript_lines (
    meeting_id  TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    line_id     TEXT NOT NULL,                      -- 발화 번호 (utterance_index). 목업은 h1 · a1 …
    position    INT  NOT NULL,
    speaker     TEXT NOT NULL DEFAULT '',           -- 빈 문자열이면 안건 제목 줄
    speaker_role TEXT NOT NULL DEFAULT '',
    text        TEXT NOT NULL,
    source_line INT,                                -- 원본 파일의 줄 번호
    PRIMARY KEY (meeting_id, line_id)
);

CREATE TABLE meeting_keywords (
    meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    position   INT  NOT NULL,
    keyword    TEXT NOT NULL,
    PRIMARY KEY (meeting_id, position)
);

-- ---------------------------------------------------------------- 회의 원본 · 요약 회의록
-- 회의 원본: 사용자가 올린 회의록 파일 그대로. 다시 올리면 version 이 하나 올라가고 옛 버전도 남음
--   (줄 단위로 나눈 발화는 transcript_lines, 이 표는 "파일 원본 그 자체")
CREATE TABLE IF NOT EXISTS meeting_sources (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    meeting_id   TEXT   NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    version      INT    NOT NULL CHECK (version >= 1),
    file_name    TEXT   NOT NULL DEFAULT '',
    mime_type    TEXT   NOT NULL DEFAULT 'text/plain',
    content_text TEXT,                              -- 텍스트 원본 (txt · 회의록 문서에서 뽑은 글)
    storage_uri  TEXT,                              -- 음성 · 큰 파일은 파일 저장소 경로 (S3 등). DB 에 바이너리는 안 넣음
    size_bytes   BIGINT CHECK (size_bytes IS NULL OR size_bytes >= 0),
    sha256       TEXT,                              -- 같은 파일을 또 올렸는지 확인용
    uploaded_by  TEXT   NOT NULL,                   -- Neo4j Person.user_id
    uploaded_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (meeting_id, version),
    CHECK (content_text IS NOT NULL OR storage_uri IS NOT NULL)   -- 둘 중 하나는 있어야 원본
);

-- 요약 회의록 1벌 = 1행. AI 가 다시 요약하거나 사람이 고치면 version 이 올라감 (가장 큰 version = 현재 요약)
CREATE TABLE IF NOT EXISTS meeting_summaries (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    meeting_id  TEXT   NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    version     INT    NOT NULL CHECK (version >= 1),
    origin      TEXT   NOT NULL DEFAULT 'ai' CHECK (origin IN ('ai', 'edited')),   -- AI 결과 / 사람이 고친 것
    analysis_id BIGINT REFERENCES meeting_analyses(id) ON DELETE SET NULL,       -- 어떤 AI 실행에서 나왔나
    source_id   BIGINT REFERENCES meeting_sources(id)  ON DELETE SET NULL,       -- 어떤 원본을 요약했나
    overview    TEXT   NOT NULL DEFAULT '',          -- 회의 전체 요약 (본문)
    overview_src TEXT[] NOT NULL DEFAULT '{}',       -- 요약의 근거 발화
    created_by  TEXT,                                -- 사람이 고쳤으면 그 사람 (Neo4j Person.user_id)
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (meeting_id, version)
);

-- 요약 안의 안건 (순서 = position). 안건의 요점 · 배경 · 결정 … 은 summary_points
CREATE TABLE IF NOT EXISTS summary_agendas (
    summary_id BIGINT  NOT NULL REFERENCES meeting_summaries(id) ON DELETE CASCADE,
    position   INT     NOT NULL,
    title      TEXT    NOT NULL,
    status     TEXT    NOT NULL DEFAULT '',          -- "결정" · "보류" …
    tone       TEXT    NOT NULL DEFAULT 'info' CONSTRAINT summary_agendas_tone_check
                       CHECK (tone IN ('ok', 'warn', 'hold', 'info')),
    deep       BOOLEAN NOT NULL DEFAULT FALSE,
    src        TEXT[]  NOT NULL DEFAULT '{}',        -- 근거 발화 transcript_lines.line_id
    detailed   BOOLEAN NOT NULL DEFAULT FALSE,       -- 안건별 상세(배경 · 주요 논의 · 결정 …)가 있는지
    PRIMARY KEY (summary_id, position)
);

-- 주요 논의 소주제 (안건 안)
CREATE TABLE IF NOT EXISTS summary_topics (
    summary_id      BIGINT NOT NULL,
    agenda_position INT    NOT NULL,
    position        INT    NOT NULL,
    title           TEXT   NOT NULL,
    PRIMARY KEY (summary_id, agenda_position, position),
    FOREIGN KEY (summary_id, agenda_position) REFERENCES summary_agendas(summary_id, position) ON DELETE CASCADE
);

-- 요약 안의 "근거가 붙은 문장" 전부. section 이 어디에 쓰이는 문장인지 정함
CREATE TABLE IF NOT EXISTS summary_points (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    summary_id      BIGINT NOT NULL REFERENCES meeting_summaries(id) ON DELETE CASCADE,
    section         TEXT   NOT NULL CHECK (section IN (
                        'discussion', 'decision', 'open_issue', 'result',            -- 회의 단위 (agenda_position NULL)
                        'point', 'background', 'next', 'topic_point')),             -- 안건 단위 (+ decision · open_issue)
    agenda_position INT,                             -- NULL = 회의 단위
    topic_position  INT,                             -- topic_point 만
    position        INT    NOT NULL,                 -- 같은 묶음 안 순서
    label           TEXT   NOT NULL DEFAULT '',      -- discussion: "비교 기준"
    kind            TEXT   NOT NULL DEFAULT '',      -- result: 결정 | 미해결 | 공유 · topic_point: 정보 | 제안 | 반대·우려 | 질문
    text            TEXT   NOT NULL,
    src             TEXT[] NOT NULL DEFAULT '{}',    -- 근거 발화 transcript_lines.line_id
    agendas         INT[]  NOT NULL DEFAULT '{}',    -- result: 관련 안건 번호 (1부터)
    FOREIGN KEY (summary_id, agenda_position) REFERENCES summary_agendas(summary_id, position) ON DELETE CASCADE,
    FOREIGN KEY (summary_id, agenda_position, topic_position)
        REFERENCES summary_topics(summary_id, agenda_position, position) ON DELETE CASCADE,
    CHECK ((section IN ('discussion', 'result')) <= (agenda_position IS NULL)),
    CHECK ((section IN ('point', 'background', 'next', 'topic_point')) <= (agenda_position IS NOT NULL)),
    CHECK ((section = 'topic_point') = (topic_position IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS summary_points_summary_idx ON summary_points(summary_id, section, agenda_position, topic_position, position);

-- ---------------------------------------------------------------- 참석자 (화면 Meeting.people)
CREATE TABLE IF NOT EXISTS meeting_attendees (
    meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    user_id    TEXT NOT NULL,                       -- 구성원 id (구성원 표가 생기면 FK 로 묶기)
    position   INT  NOT NULL DEFAULT 0,
    PRIMARY KEY (meeting_id, user_id)
);
CREATE INDEX IF NOT EXISTS meeting_attendees_user_idx ON meeting_attendees(user_id);


-- ---------------------------------------------------------------- 액션 아이템 (살아 있는 목록)
-- 요약은 버전으로 쌓이지만 할 일은 사람이 추가 · 삭제 · 담당자 지정을 하므로 회의에 직접 붙임
CREATE TABLE IF NOT EXISTS meeting_todos (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    meeting_id TEXT   NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    position   INT    NOT NULL,
    text       TEXT   NOT NULL,
    due_label  TEXT   NOT NULL DEFAULT '',           -- 화면 그대로 "오늘" · "10/30"
    due_date   DATE,                                 -- 날짜로 알 수 있으면 (정렬 · 알림용)
    src        TEXT[] NOT NULL DEFAULT '{}',
    urgent     BOOLEAN NOT NULL DEFAULT FALSE,
    origin     TEXT   NOT NULL DEFAULT 'ai' CHECK (origin IN ('ai', 'manual')),   -- AI 요약 / 주관자가 직접 추가
    summary_id BIGINT REFERENCES meeting_summaries(id) ON DELETE SET NULL,      -- 어느 요약에서 나왔나 (ai)
    owner_raw  TEXT   NOT NULL DEFAULT '',           -- AI 가 적은 담당자 표기 (사람 연결 전)
    created_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at TIMESTAMPTZ,                          -- 삭제 (되돌리기 가능)
    UNIQUE (meeting_id, position)
);

CREATE TABLE IF NOT EXISTS meeting_todo_owners (
    todo_id     BIGINT NOT NULL REFERENCES meeting_todos(id) ON DELETE CASCADE,
    user_id     TEXT   NOT NULL,                     -- 구성원 id
    position    INT    NOT NULL DEFAULT 0,
    assigned_by TEXT,
    assigned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (todo_id, user_id)
);
CREATE INDEX IF NOT EXISTS meeting_todo_owners_user_idx ON meeting_todo_owners(user_id);

-- 안건 분리 요청 (참석자 → 주관자)
CREATE TABLE IF NOT EXISTS branch_requests (
    meeting_id   TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    requested_by TEXT NOT NULL,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (meeting_id, requested_by)
);

-- ---------------------------------------------------------------- 공유
CREATE TABLE meeting_shares (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    meeting_id        TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    sender_id         TEXT NOT NULL,                -- Neo4j Person.user_id
    recipient_user_id TEXT,                         -- 사람에게 보냈으면
    recipient_team_id TEXT,                         -- 팀에게 보냈으면 (Neo4j Team.team_id)
    scope             TEXT NOT NULL CHECK (scope IN ('summary', 'full')),
    lock_raw          BOOLEAN NOT NULL DEFAULT FALSE,
    shared_on         DATE NOT NULL DEFAULT CURRENT_DATE,
    revoked_at        TIMESTAMPTZ,
    CHECK ((recipient_user_id IS NULL) <> (recipient_team_id IS NULL))  -- 둘 중 정확히 하나만
);

-- ---------------------------------------------------------------- 외부 연동
CREATE TABLE integration_tools (
    id          TEXT PRIMARY KEY,
    mark        TEXT NOT NULL,
    name        TEXT NOT NULL,
    description TEXT NOT NULL,
    target      TEXT NOT NULL DEFAULT '',
    is_phase2   BOOLEAN NOT NULL DEFAULT FALSE,
    sort_order  INT NOT NULL DEFAULT 0,
    last_sync_at TIMESTAMPTZ                        -- 마지막으로 보낸 시각 (관리자 · MCP 관리)
);

CREATE TABLE integration_status (
    tool_id    TEXT PRIMARY KEY REFERENCES integration_tools(id) ON DELETE CASCADE,
    status     TEXT NOT NULL CHECK (status IN ('on', 'off', 'expired')),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by TEXT
);

-- 외부 전송 실패 기록 (web ExportFailure). 실패마다 1행
CREATE TABLE failed_exports (
    id          TEXT PRIMARY KEY DEFAULT 'f-' || replace(gen_random_uuid()::text, '-', ''),
    tool_id     TEXT NOT NULL REFERENCES integration_tools(id) ON DELETE CASCADE,
    meeting_id  TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL DEFAULT 'simple' CONSTRAINT failed_exports_kind_check
                CHECK (kind IN ('simple', 'tech')),  -- simple: 주관자가 다시 보내기 · tech: 관리자가 처리
    reason      TEXT NOT NULL DEFAULT '',
    code        TEXT NOT NULL DEFAULT '',             -- "timeout (30초)" · "401 Unauthorized · 토큰 만료"
    failed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at TIMESTAMPTZ,                          -- 다시 보내서 해결된 시각 (NULL = 아직 실패)
    resolved_by TEXT
);
CREATE INDEX failed_exports_meeting_idx ON failed_exports(meeting_id, failed_at DESC);
CREATE INDEX failed_exports_open_idx ON failed_exports(failed_at DESC) WHERE resolved_at IS NULL;

-- ---------------------------------------------------------------- 알림
CREATE TABLE notifications (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,                       -- 받는 사람 (Neo4j Person.user_id)
    kind       TEXT NOT NULL CONSTRAINT notifications_kind_check
               CHECK (kind IN ('review', 'shared', 'integration', 'member', 'delivered', 'fail', 'created')),
    text       TEXT NOT NULL,
    meeting_id TEXT REFERENCES meetings(id) ON DELETE CASCADE,
    folder_id  TEXT REFERENCES folders(id)  ON DELETE CASCADE,
    is_read    BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX notifications_user_idx ON notifications(user_id, created_at DESC);
