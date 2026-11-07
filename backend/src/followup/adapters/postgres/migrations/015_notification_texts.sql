-- 015: 자동 알림 문구를 화면(web 목업 notifications.json)과 같게 + 조직 이름 설정 자리.
--   member  「정민재 · 서비스기획팀 님이 「고객 안내 개선」 폴더에 나를 멤버로 추가했어요.」
--   shared  「강수현 · 재무팀 님이 「정산 정책 리뷰」을 공유했어요.」
-- 사람 표기는 구성원 표(people · teams) "이름 · 팀", 없으면 user_id (adapters/postgres/common.person_label 과 같은 규칙).

CREATE OR REPLACE FUNCTION remeet_internal.person_label(uid TEXT) RETURNS TEXT
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
  SELECT COALESCE(
           (SELECT CASE WHEN t.name IS NULL THEN p.name ELSE p.name || ' · ' || t.name END
              FROM public.people p LEFT JOIN public.teams t ON t.team_id = p.team_id
             WHERE p.user_id = uid),
           uid)
$$;
REVOKE ALL ON FUNCTION remeet_internal.person_label(TEXT) FROM PUBLIC;

CREATE OR REPLACE FUNCTION remeet_internal.notify_folder_member() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
  IF NEW.user_id <> COALESCE(NEW.added_by, '') THEN
    INSERT INTO public.notifications(id,user_id,kind,text,folder_id)
      VALUES ('n-'||replace(gen_random_uuid()::text,'-',''), NEW.user_id, 'member',
        CASE WHEN NEW.added_by IS NULL THEN '「' || (SELECT name FROM public.folders WHERE id = NEW.folder_id)
                                            || '」 폴더에 멤버로 추가됐어요.'
             ELSE remeet_internal.person_label(NEW.added_by) || ' 님이 「'
                  || (SELECT name FROM public.folders WHERE id = NEW.folder_id) || '」 폴더에 나를 멤버로 추가했어요.'
        END,
        NEW.folder_id);
  END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION remeet_internal.notify_meeting_share() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
  IF NEW.recipient_user_id IS NOT NULL AND NEW.recipient_user_id <> NEW.sender_id THEN
    INSERT INTO public.notifications(id,user_id,kind,text,meeting_id)
      VALUES ('n-'||replace(gen_random_uuid()::text,'-',''), NEW.recipient_user_id, 'shared',
        remeet_internal.person_label(NEW.sender_id) || ' 님이 「'
          || COALESCE(NULLIF((SELECT title FROM public.meetings WHERE id = NEW.meeting_id), ''), '회의')
          || '」을 공유했어요.',
        NEW.meeting_id);
  END IF;
  RETURN NEW;
END $$;

-- 조직 이름 (외부 연동 화면 상단). 관리자가 바꾸기 전까지 빈 값
INSERT INTO app_settings (key, value) VALUES ('organization_name', '""'::jsonb) ON CONFLICT (key) DO NOTHING;