-- 004: 회의 원본 · 요약 회의록 + 목록 정렬용 칸 (제목 · 회의 날짜)
-- 지우는 것 없음. 이미 쓰던 DB 에도 그대로 적용 가능 (python migrate.py)

ALTER TABLE meetings ADD COLUMN IF NOT EXISTS title   TEXT NOT NULL DEFAULT '';
ALTER TABLE meetings ADD COLUMN IF NOT EXISTS held_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS meetings_latest_idx
    ON meetings ((COALESCE(held_at, created_at)) DESC) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS meetings_folder_latest_idx
    ON meetings (folder_id, (COALESCE(held_at, created_at)) DESC) WHERE deleted_at IS NULL;

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
    created_by  TEXT,                                -- 사람이 고쳤으면 그 사람 (Neo4j Person.user_id)
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (meeting_id, version)
);

-- 요약 안의 안건 (순서 = position)
CREATE TABLE IF NOT EXISTS summary_agendas (
    summary_id        BIGINT NOT NULL REFERENCES meeting_summaries(id) ON DELETE CASCADE,
    position          INT    NOT NULL,
    title             TEXT   NOT NULL,
    background        TEXT   NOT NULL DEFAULT '',
    decisions         TEXT[] NOT NULL DEFAULT '{}',   -- 결정 사항
    unresolved_issues TEXT[] NOT NULL DEFAULT '{}',   -- 미해결
    next_topics       TEXT[] NOT NULL DEFAULT '{}',   -- 다음 회의 주제
    PRIMARY KEY (summary_id, position)
);

-- 안건 안의 소주제별 논의. evidence_line_ids = 근거 발화 번호 (transcript_lines.line_id)
CREATE TABLE IF NOT EXISTS summary_discussions (
    summary_id        BIGINT NOT NULL,
    agenda_position   INT    NOT NULL,
    position          INT    NOT NULL,
    subtopic          TEXT   NOT NULL DEFAULT '',
    content           TEXT   NOT NULL,
    evidence_line_ids TEXT[] NOT NULL DEFAULT '{}',
    tags              TEXT[] NOT NULL DEFAULT '{}',
    PRIMARY KEY (summary_id, agenda_position, position),
    FOREIGN KEY (summary_id, agenda_position)
        REFERENCES summary_agendas(summary_id, position) ON DELETE CASCADE
);

-- 요약에서 뽑은 할 일. 담당자 "사람 연결"은 Neo4j(ASSIGNED_TO) 몫이라 여기는 요약에 적힌 표기만
CREATE TABLE IF NOT EXISTS summary_action_items (
    summary_id      BIGINT NOT NULL REFERENCES meeting_summaries(id) ON DELETE CASCADE,
    position        INT    NOT NULL,
    agenda_position INT,                             -- 어느 안건에서 나왔나 (모르면 NULL)
    description     TEXT   NOT NULL,
    owner_raw       TEXT   NOT NULL DEFAULT '',      -- 요약에 적힌 담당자 표기 ("A", "디자이너" …)
    due_date        DATE,
    PRIMARY KEY (summary_id, position),
    FOREIGN KEY (summary_id, agenda_position)
        REFERENCES summary_agendas(summary_id, position) ON DELETE CASCADE
);

-- ---------------------------------------------------------------- RLS (002 와 같은 규칙)
-- 원본 파일 = 원문과 같은 권한(raw_visible: 요약만 공유받은 사람은 못 봄)
-- 요약 = 회의를 볼 수 있으면 봄(meeting_visible). 쓰기는 회의 주관자만
GRANT SELECT, INSERT, UPDATE, DELETE ON meeting_sources, meeting_summaries,
      summary_agendas, summary_discussions, summary_action_items TO remeet_app;
GRANT USAGE ON SEQUENCE meeting_sources_id_seq, meeting_summaries_id_seq TO remeet_app;

ALTER TABLE meeting_sources      ENABLE ROW LEVEL SECURITY;
ALTER TABLE meeting_summaries    ENABLE ROW LEVEL SECURITY;
ALTER TABLE summary_agendas      ENABLE ROW LEVEL SECURITY;
ALTER TABLE summary_discussions  ENABLE ROW LEVEL SECURITY;
ALTER TABLE summary_action_items ENABLE ROW LEVEL SECURITY;

CREATE FUNCTION remeet_internal.summary_meeting(sid bigint) RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
 SELECT meeting_id FROM public.meeting_summaries WHERE id = sid
$$;
REVOKE ALL ON FUNCTION remeet_internal.summary_meeting(bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION remeet_internal.summary_meeting(bigint) TO remeet_app;

CREATE POLICY source_select ON meeting_sources FOR SELECT TO remeet_app
 USING (remeet_internal.raw_visible(meeting_id));
CREATE POLICY source_write ON meeting_sources FOR ALL TO remeet_app
 USING (remeet_internal.meeting_owned(meeting_id)) WITH CHECK (remeet_internal.meeting_owned(meeting_id));

CREATE POLICY summary_select ON meeting_summaries FOR SELECT TO remeet_app
 USING (remeet_internal.meeting_visible(meeting_id));
CREATE POLICY summary_write ON meeting_summaries FOR ALL TO remeet_app
 USING (remeet_internal.meeting_owned(meeting_id)) WITH CHECK (remeet_internal.meeting_owned(meeting_id));

CREATE POLICY agenda_select ON summary_agendas FOR SELECT TO remeet_app
 USING (remeet_internal.meeting_visible(remeet_internal.summary_meeting(summary_id)));
CREATE POLICY agenda_write ON summary_agendas FOR ALL TO remeet_app
 USING (remeet_internal.meeting_owned(remeet_internal.summary_meeting(summary_id)))
 WITH CHECK (remeet_internal.meeting_owned(remeet_internal.summary_meeting(summary_id)));

CREATE POLICY discussion_select ON summary_discussions FOR SELECT TO remeet_app
 USING (remeet_internal.meeting_visible(remeet_internal.summary_meeting(summary_id)));
CREATE POLICY discussion_write ON summary_discussions FOR ALL TO remeet_app
 USING (remeet_internal.meeting_owned(remeet_internal.summary_meeting(summary_id)))
 WITH CHECK (remeet_internal.meeting_owned(remeet_internal.summary_meeting(summary_id)));

CREATE POLICY action_select ON summary_action_items FOR SELECT TO remeet_app
 USING (remeet_internal.meeting_visible(remeet_internal.summary_meeting(summary_id)));
CREATE POLICY action_write ON summary_action_items FOR ALL TO remeet_app
 USING (remeet_internal.meeting_owned(remeet_internal.summary_meeting(summary_id)))
 WITH CHECK (remeet_internal.meeting_owned(remeet_internal.summary_meeting(summary_id)));
