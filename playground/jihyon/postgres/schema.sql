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
-- ▶ PostgreSQL 담당 (이 파일)
--     로그인 계정, 회의 종류 · 폴더 · 폴더 멤버 · 브리프
--     회의 "관리 정보"(폴더, 주관자, 진행 상태, 파일, 삭제 여부, 안건 분리)
--     회의 원문(발화) · 화자 매핑 · AI 분석 실행 기록(summary.json 원본)
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
    notifications, failed_exports, integration_status, integration_tools,
    meeting_shares, meeting_keywords, transcript_lines, meeting_speakers, meeting_analyses,
    meetings, folder_briefs, folder_members, folders, meeting_types,
    accounts, app_settings,
    -- 예전 버전(Neo4j 와 겹치던 표)도 남아 있으면 지움
    todo_assignees, todos, agenda_points, agendas, meeting_findings, meeting_discussions,
    meeting_attendees, paragraphs, assignee_candidates, user_skills, assign_reasons, users, teams
CASCADE;

-- ---------------------------------------------------------------- 로그인 계정
-- 사람의 이름 · 팀 · 역할 · 스킬은 Neo4j Person 에 있음. 여기는 로그인에 필요한 것만.
-- CREATE TABLE accounts (
--     user_id       TEXT PRIMARY KEY,                 -- = Neo4j Person.user_id
--     email         TEXT NOT NULL UNIQUE,
--     password_hash TEXT,                             -- 평문 비밀번호 저장 금지! (bcrypt 등으로 해시)
--     created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
--     last_login_at TIMESTAMPTZ
-- );
CREATE TABLE accounts (
    user_id       TEXT PRIMARY KEY,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT,

    role          TEXT NOT NULL DEFAULT 'USER'
                  CHECK (role IN ('USER', 'ADMIN')),

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

-- 폴더 = Neo4j 의 Project. 폴더는 웹에서 만들어지므로 이름의 원본은 여기 (Neo4j Project.name 은 복사본)
CREATE TABLE folders (
    id           TEXT PRIMARY KEY,                  -- = Neo4j Project.project_id
    name         TEXT NOT NULL,
    type_id      TEXT NOT NULL REFERENCES meeting_types(id),
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
    folder_id           TEXT REFERENCES folders(id),-- 공유받은 회의는 NULL
    host_user_id        TEXT NOT NULL,              -- 주관자 (Neo4j 에는 참석(ATTENDED)만 있고 주관자는 없음)
    time_label          TEXT NOT NULL DEFAULT '',   -- "10:00 – 10:48"
    is_deep             BOOLEAN NOT NULL DEFAULT FALSE,
    file_name           TEXT NOT NULL DEFAULT '',   -- 올린 회의록 파일 이름
    status              TEXT NOT NULL DEFAULT 'review'
                        CHECK (status IN ('review', 'confirmed', 'sending', 'done', 'fail')),
    summary_text        TEXT NOT NULL DEFAULT '',   -- 회의 전체 한 줄 요약 (Neo4j Meeting 에 없음)
    summary_src         TEXT[] NOT NULL DEFAULT '{}',
    branch_of_id        TEXT REFERENCES meetings(id),
    branch_agenda_index INT,
    position            INT NOT NULL DEFAULT 0,     -- 전체 목록 순서
    folder_position     INT NOT NULL DEFAULT 0,     -- 폴더 안 순서
    created_by          TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at          TIMESTAMPTZ
);
CREATE INDEX meetings_folder_idx ON meetings(folder_id);

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
    sort_order  INT NOT NULL DEFAULT 0
);

CREATE TABLE integration_status (
    tool_id    TEXT PRIMARY KEY REFERENCES integration_tools(id) ON DELETE CASCADE,
    status     TEXT NOT NULL CHECK (status IN ('on', 'off', 'expired')),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE failed_exports (
    tool_id    TEXT NOT NULL REFERENCES integration_tools(id) ON DELETE CASCADE,
    meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    PRIMARY KEY (tool_id, meeting_id)
);

-- ---------------------------------------------------------------- 알림
CREATE TABLE notifications (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,                       -- 받는 사람 (Neo4j Person.user_id)
    kind       TEXT NOT NULL CHECK (kind IN ('review', 'shared', 'integration', 'member')),
    text       TEXT NOT NULL,
    meeting_id TEXT REFERENCES meetings(id) ON DELETE CASCADE,
    folder_id  TEXT REFERENCES folders(id)  ON DELETE CASCADE,
    is_read    BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX notifications_user_idx ON notifications(user_id, created_at DESC);
