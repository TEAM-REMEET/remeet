-- 006: 회의 상세 화면(web data/models.py 의 Meeting)에 맞춘 표
--
--   화면                         표
--   참석자 (people)              meeting_attendees            ← 사람 정보 자체는 구성원 표(팀원 담당)
--   요약 · 근거 발화 (summary)   meeting_summaries.overview · overview_src
--   논의 · 결정 · 미해결 · 결과  summary_points (section = discussion | decision | open_issue | result)
--   안건 (상태 · 톤 · 근거)      summary_agendas (+ status · tone · deep · src · detailed)
--     요점 · 배경 · 결정 …       summary_points (section = point | background | decision | open_issue | next)
--     주요 논의 소주제           summary_topics + summary_points (section = topic_point, kind = 정보 | 제안 | …)
--   액션 아이템 (todos)          meeting_todos + meeting_todo_owners (요약 버전과 따로: 사람이 추가 · 삭제 · 담당자 지정)
--   안건 분리 요청               branch_requests
--
-- 004 의 summary_discussions · summary_action_items · 안건의 글자 칸들은 여기로 옮기고 지움 (데이터는 보존).
-- 근거(src) = transcript_lines.line_id 목록.

-- ---------------------------------------------------------------- 참석자
CREATE TABLE IF NOT EXISTS meeting_attendees (
    meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    user_id    TEXT NOT NULL,                       -- 구성원 id (구성원 표가 생기면 FK 로 묶기)
    position   INT  NOT NULL DEFAULT 0,
    PRIMARY KEY (meeting_id, user_id)
);
CREATE INDEX IF NOT EXISTS meeting_attendees_user_idx ON meeting_attendees(user_id);

-- ---------------------------------------------------------------- 요약: 칸 추가
ALTER TABLE meeting_summaries ADD COLUMN IF NOT EXISTS overview_src TEXT[] NOT NULL DEFAULT '{}';

ALTER TABLE summary_agendas ADD COLUMN IF NOT EXISTS status   TEXT    NOT NULL DEFAULT '';       -- "결정" · "보류" …
ALTER TABLE summary_agendas ADD COLUMN IF NOT EXISTS tone     TEXT    NOT NULL DEFAULT 'info';
ALTER TABLE summary_agendas ADD COLUMN IF NOT EXISTS deep     BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE summary_agendas ADD COLUMN IF NOT EXISTS src      TEXT[]  NOT NULL DEFAULT '{}';
ALTER TABLE summary_agendas ADD COLUMN IF NOT EXISTS detailed BOOLEAN NOT NULL DEFAULT FALSE;      -- 안건별 상세가 있는지
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'summary_agendas_tone_check') THEN
    ALTER TABLE summary_agendas ADD CONSTRAINT summary_agendas_tone_check CHECK (tone IN ('ok', 'warn', 'hold', 'info'));
  END IF;
END $$;

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

-- ---------------------------------------------------------------- 004 구조 → 새 구조로 옮기기 (있을 때만)
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_name = 'summary_agendas' AND column_name = 'background') THEN
    INSERT INTO summary_points (summary_id, section, agenda_position, position, text)
      SELECT summary_id, 'background', position, 0, background FROM summary_agendas WHERE background <> '';
    INSERT INTO summary_points (summary_id, section, agenda_position, position, text)
      SELECT summary_id, 'decision', position, o - 1, t FROM summary_agendas, unnest(decisions) WITH ORDINALITY AS u(t, o);
    INSERT INTO summary_points (summary_id, section, agenda_position, position, text)
      SELECT summary_id, 'open_issue', position, o - 1, t FROM summary_agendas, unnest(unresolved_issues) WITH ORDINALITY AS u(t, o);
    INSERT INTO summary_points (summary_id, section, agenda_position, position, text)
      SELECT summary_id, 'next', position, o - 1, t FROM summary_agendas, unnest(next_topics) WITH ORDINALITY AS u(t, o);
    UPDATE summary_agendas a SET detailed = TRUE
      WHERE a.background <> '' OR cardinality(a.decisions) > 0 OR cardinality(a.unresolved_issues) > 0
         OR cardinality(a.next_topics) > 0;
    ALTER TABLE summary_agendas DROP COLUMN background, DROP COLUMN decisions,
                                DROP COLUMN unresolved_issues, DROP COLUMN next_topics;
  END IF;

  IF to_regclass('public.summary_discussions') IS NOT NULL THEN
    INSERT INTO summary_topics (summary_id, agenda_position, position, title)
      SELECT summary_id, agenda_position, position, subtopic FROM summary_discussions;
    INSERT INTO summary_points (summary_id, section, agenda_position, topic_position, position, kind, text, src)
      SELECT summary_id, 'topic_point', agenda_position, position, 0, '정보', content, evidence_line_ids
      FROM summary_discussions;
    UPDATE summary_agendas a SET detailed = TRUE
      WHERE EXISTS (SELECT 1 FROM summary_discussions d
                    WHERE d.summary_id = a.summary_id AND d.agenda_position = a.position);
    DROP TABLE summary_discussions CASCADE;
  END IF;

  IF to_regclass('public.summary_action_items') IS NOT NULL THEN
    -- 회의마다 최신 요약의 할 일만 살아 있는 목록으로
    INSERT INTO meeting_todos (meeting_id, position, text, due_label, due_date, origin, summary_id, owner_raw)
      SELECT s.meeting_id, i.position, i.description,
             COALESCE(to_char(i.due_date, 'FMMM/FMDD'), ''), i.due_date, 'ai', s.id, i.owner_raw
      FROM summary_action_items i
      JOIN meeting_summaries s ON s.id = i.summary_id
      WHERE s.version = (SELECT max(version) FROM meeting_summaries x WHERE x.meeting_id = s.meeting_id)
        AND NOT EXISTS (SELECT 1 FROM meeting_todos t WHERE t.meeting_id = s.meeting_id);
    DROP TABLE summary_action_items CASCADE;
  END IF;
END $$;

-- 주관자는 늘 참석자 (화면 규칙). 이미 있는 회의도 채움
INSERT INTO meeting_attendees (meeting_id, user_id, position)
  SELECT id, host_user_id, 0 FROM meetings WHERE host_user_id <> ''
ON CONFLICT DO NOTHING;

-- ---------------------------------------------------------------- 권한: 참석자도 회의 · 원문을 볼 수 있음 (화면 can_see 규칙)
CREATE OR REPLACE FUNCTION remeet_internal.attended(mid text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
 SELECT EXISTS (SELECT 1 FROM public.meeting_attendees a
                WHERE a.meeting_id = mid AND a.user_id = remeet_internal.actor_id())
$$;
CREATE OR REPLACE FUNCTION remeet_internal.meeting_visible(mid text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
 SELECT remeet_internal.is_regular() AND EXISTS (
 SELECT 1 FROM public.meetings m WHERE m.id=mid AND m.deleted_at IS NULL
 AND (m.host_user_id=remeet_internal.actor_id()
 OR remeet_internal.attended(m.id)
 OR (m.folder_id IS NOT NULL AND remeet_internal.folder_visible(m.folder_id))
 OR EXISTS (SELECT 1 FROM public.meeting_shares s WHERE s.meeting_id=m.id
 AND s.recipient_user_id=remeet_internal.actor_id() AND s.revoked_at IS NULL)))
$$;
CREATE OR REPLACE FUNCTION remeet_internal.raw_visible(mid text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
 SELECT remeet_internal.is_regular() AND EXISTS (
 SELECT 1 FROM public.meetings m WHERE m.id=mid AND m.deleted_at IS NULL
 AND (m.host_user_id=remeet_internal.actor_id() OR remeet_internal.attended(m.id) OR
   (NOT EXISTS (SELECT 1 FROM public.meeting_shares s WHERE s.meeting_id=m.id
      AND s.recipient_user_id=remeet_internal.actor_id() AND s.revoked_at IS NULL)
    AND m.folder_id IS NOT NULL AND remeet_internal.folder_visible(m.folder_id))
   OR EXISTS (SELECT 1 FROM public.meeting_shares s WHERE s.meeting_id=m.id
      AND s.recipient_user_id=remeet_internal.actor_id() AND s.revoked_at IS NULL
      AND s.scope='full' AND NOT s.lock_raw)))
$$;
REVOKE ALL ON FUNCTION remeet_internal.attended(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION remeet_internal.attended(text) TO remeet_app;

CREATE OR REPLACE FUNCTION remeet_internal.todo_meeting(tid bigint) RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
 SELECT meeting_id FROM public.meeting_todos WHERE id = tid
$$;
REVOKE ALL ON FUNCTION remeet_internal.todo_meeting(bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION remeet_internal.todo_meeting(bigint) TO remeet_app;

GRANT SELECT, INSERT, UPDATE, DELETE ON meeting_attendees, summary_topics, summary_points,
      meeting_todos, meeting_todo_owners, branch_requests TO remeet_app;
GRANT USAGE ON SEQUENCE summary_points_id_seq, meeting_todos_id_seq TO remeet_app;

ALTER TABLE meeting_attendees   ENABLE ROW LEVEL SECURITY;
ALTER TABLE summary_topics      ENABLE ROW LEVEL SECURITY;
ALTER TABLE summary_points      ENABLE ROW LEVEL SECURITY;
ALTER TABLE meeting_todos       ENABLE ROW LEVEL SECURITY;
ALTER TABLE meeting_todo_owners ENABLE ROW LEVEL SECURITY;
ALTER TABLE branch_requests     ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS attendee_select ON meeting_attendees;
DROP POLICY IF EXISTS attendee_write ON meeting_attendees;
CREATE POLICY attendee_select ON meeting_attendees FOR SELECT TO remeet_app
 USING (remeet_internal.meeting_visible(meeting_id));
CREATE POLICY attendee_write ON meeting_attendees FOR ALL TO remeet_app
 USING (remeet_internal.meeting_owned(meeting_id)) WITH CHECK (remeet_internal.meeting_owned(meeting_id));

DROP POLICY IF EXISTS topic_select ON summary_topics;
DROP POLICY IF EXISTS topic_write ON summary_topics;
CREATE POLICY topic_select ON summary_topics FOR SELECT TO remeet_app
 USING (remeet_internal.meeting_visible(remeet_internal.summary_meeting(summary_id)));
CREATE POLICY topic_write ON summary_topics FOR ALL TO remeet_app
 USING (remeet_internal.meeting_owned(remeet_internal.summary_meeting(summary_id)))
 WITH CHECK (remeet_internal.meeting_owned(remeet_internal.summary_meeting(summary_id)));

DROP POLICY IF EXISTS point_select ON summary_points;
DROP POLICY IF EXISTS point_write ON summary_points;
CREATE POLICY point_select ON summary_points FOR SELECT TO remeet_app
 USING (remeet_internal.meeting_visible(remeet_internal.summary_meeting(summary_id)));
CREATE POLICY point_write ON summary_points FOR ALL TO remeet_app
 USING (remeet_internal.meeting_owned(remeet_internal.summary_meeting(summary_id)))
 WITH CHECK (remeet_internal.meeting_owned(remeet_internal.summary_meeting(summary_id)));

DROP POLICY IF EXISTS todo_select ON meeting_todos;
DROP POLICY IF EXISTS todo_write ON meeting_todos;
CREATE POLICY todo_select ON meeting_todos FOR SELECT TO remeet_app
 USING (remeet_internal.meeting_visible(meeting_id));
CREATE POLICY todo_write ON meeting_todos FOR ALL TO remeet_app
 USING (remeet_internal.meeting_owned(meeting_id)) WITH CHECK (remeet_internal.meeting_owned(meeting_id));

DROP POLICY IF EXISTS todo_owner_select ON meeting_todo_owners;
DROP POLICY IF EXISTS todo_owner_write ON meeting_todo_owners;
CREATE POLICY todo_owner_select ON meeting_todo_owners FOR SELECT TO remeet_app
 USING (remeet_internal.meeting_visible(remeet_internal.todo_meeting(todo_id)));
CREATE POLICY todo_owner_write ON meeting_todo_owners FOR ALL TO remeet_app
 USING (remeet_internal.meeting_owned(remeet_internal.todo_meeting(todo_id)))
 WITH CHECK (remeet_internal.meeting_owned(remeet_internal.todo_meeting(todo_id)));

-- 분리 요청: 회의를 볼 수 있는 사람이 자기 이름으로 요청, 주관자 · 본인이 봄
DROP POLICY IF EXISTS branch_select ON branch_requests;
DROP POLICY IF EXISTS branch_insert ON branch_requests;
DROP POLICY IF EXISTS branch_delete ON branch_requests;
CREATE POLICY branch_select ON branch_requests FOR SELECT TO remeet_app
 USING (requested_by = remeet_internal.actor_id() OR remeet_internal.meeting_owned(meeting_id));
CREATE POLICY branch_insert ON branch_requests FOR INSERT TO remeet_app
 WITH CHECK (requested_by = remeet_internal.actor_id() AND remeet_internal.meeting_visible(meeting_id));
CREATE POLICY branch_delete ON branch_requests FOR DELETE TO remeet_app
 USING (requested_by = remeet_internal.actor_id() OR remeet_internal.meeting_owned(meeting_id));
