-- RLS for limited remeet_app role. Run as schema owner; role NOLOGIN by default.
-- Provision a separate LOGIN role and grant remeet_app via your secret manager.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'remeet_app') THEN
    CREATE ROLE remeet_app NOLOGIN NOBYPASSRLS;
  END IF;
END $$;

-- These SECURITY DEFINER functions are internal policy helpers; keep ownership to trusted DB owner.
CREATE SCHEMA IF NOT EXISTS remeet_internal;
REVOKE ALL ON SCHEMA remeet_internal FROM PUBLIC;
GRANT USAGE ON SCHEMA remeet_internal TO remeet_app;

CREATE FUNCTION remeet_internal.actor_id() RETURNS text
LANGUAGE sql STABLE AS $$ SELECT nullif(current_setting('remeet.user_id', true), '') $$;
CREATE FUNCTION remeet_internal.is_regular() RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
 SELECT EXISTS (SELECT 1 FROM public.accounts a
  WHERE a.user_id = remeet_internal.actor_id() AND a.role = 'USER')
$$;
CREATE FUNCTION remeet_internal.folder_visible(fid text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
 SELECT remeet_internal.is_regular() AND EXISTS (
   SELECT 1 FROM public.folders f WHERE f.id=fid AND f.deleted_at IS NULL
     AND (f.host_user_id=remeet_internal.actor_id() OR EXISTS (
       SELECT 1 FROM public.folder_members fm WHERE fm.folder_id=f.id
       AND fm.user_id=remeet_internal.actor_id())))
$$;
CREATE FUNCTION remeet_internal.folder_owned(fid text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
 SELECT remeet_internal.is_regular() AND EXISTS (
 SELECT 1 FROM public.folders f WHERE f.id=fid AND f.deleted_at IS NULL
 AND f.host_user_id=remeet_internal.actor_id())
$$;
CREATE FUNCTION remeet_internal.folder_host(fid text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
 SELECT remeet_internal.is_regular() AND EXISTS (
 SELECT 1 FROM public.folders f WHERE f.id=fid AND f.host_user_id=remeet_internal.actor_id())
$$;
CREATE FUNCTION remeet_internal.meeting_host(mid text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
 SELECT remeet_internal.is_regular() AND EXISTS (
 SELECT 1 FROM public.meetings m WHERE m.id=mid AND m.host_user_id=remeet_internal.actor_id())
$$;
CREATE FUNCTION remeet_internal.meeting_visible(mid text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
 SELECT remeet_internal.is_regular() AND EXISTS (
 SELECT 1 FROM public.meetings m WHERE m.id=mid AND m.deleted_at IS NULL
 AND (m.host_user_id=remeet_internal.actor_id()
 OR (m.folder_id IS NOT NULL AND remeet_internal.folder_visible(m.folder_id))
 OR EXISTS (SELECT 1 FROM public.meeting_shares s WHERE s.meeting_id=m.id
 AND s.recipient_user_id=remeet_internal.actor_id() AND s.revoked_at IS NULL)))
$$;
CREATE FUNCTION remeet_internal.meeting_owned(mid text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
 SELECT remeet_internal.is_regular() AND EXISTS (
 SELECT 1 FROM public.meetings m WHERE m.id=mid AND m.deleted_at IS NULL
 AND m.host_user_id=remeet_internal.actor_id())
$$;
CREATE FUNCTION remeet_internal.raw_visible(mid text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
 SELECT remeet_internal.is_regular() AND EXISTS (
 SELECT 1 FROM public.meetings m WHERE m.id=mid AND m.deleted_at IS NULL
 AND (m.host_user_id=remeet_internal.actor_id() OR
   (NOT EXISTS (SELECT 1 FROM public.meeting_shares s WHERE s.meeting_id=m.id
      AND s.recipient_user_id=remeet_internal.actor_id() AND s.revoked_at IS NULL)
    AND m.folder_id IS NOT NULL AND remeet_internal.folder_visible(m.folder_id))
   OR EXISTS (SELECT 1 FROM public.meeting_shares s WHERE s.meeting_id=m.id
      AND s.recipient_user_id=remeet_internal.actor_id() AND s.revoked_at IS NULL
      AND s.scope='full' AND NOT s.lock_raw)))
$$;

REVOKE ALL ON ALL FUNCTIONS IN SCHEMA remeet_internal FROM PUBLIC;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA remeet_internal TO remeet_app;
-- Limit privileges, especially direct raw access. Owners still bypass RLS.
GRANT SELECT (user_id,role) ON accounts TO remeet_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON folders, folder_members, meetings,
  meeting_shares, transcript_lines, notifications TO remeet_app;
GRANT SELECT ON meeting_types TO remeet_app;
GRANT USAGE ON SEQUENCE meeting_shares_id_seq TO remeet_app;
ALTER TABLE folders ENABLE ROW LEVEL SECURITY;
ALTER TABLE folder_members ENABLE ROW LEVEL SECURITY;
ALTER TABLE meetings ENABLE ROW LEVEL SECURITY;
ALTER TABLE meeting_shares ENABLE ROW LEVEL SECURITY;
ALTER TABLE transcript_lines ENABLE ROW LEVEL SECURITY;
ALTER TABLE notifications ENABLE ROW LEVEL SECURITY;

CREATE POLICY folder_select ON folders FOR SELECT TO remeet_app
 USING (remeet_internal.folder_visible(id));
CREATE POLICY folder_insert ON folders FOR INSERT TO remeet_app
 WITH CHECK (remeet_internal.is_regular() AND host_user_id=remeet_internal.actor_id());
CREATE POLICY folder_update ON folders FOR UPDATE TO remeet_app
 USING (remeet_internal.folder_owned(id)) WITH CHECK (remeet_internal.folder_host(id));
CREATE POLICY folder_delete ON folders FOR DELETE TO remeet_app USING (remeet_internal.folder_owned(id));

CREATE POLICY member_select ON folder_members FOR SELECT TO remeet_app
 USING (remeet_internal.folder_visible(folder_id));
CREATE POLICY member_insert ON folder_members FOR INSERT TO remeet_app
 WITH CHECK (remeet_internal.folder_owned(folder_id));
CREATE POLICY member_update ON folder_members FOR UPDATE TO remeet_app
 USING (remeet_internal.folder_owned(folder_id)) WITH CHECK (remeet_internal.folder_owned(folder_id));
CREATE POLICY member_delete ON folder_members FOR DELETE TO remeet_app
 USING (remeet_internal.folder_owned(folder_id));

CREATE POLICY meeting_select ON meetings FOR SELECT TO remeet_app
 USING (remeet_internal.meeting_visible(id));
CREATE POLICY meeting_insert ON meetings FOR INSERT TO remeet_app
 WITH CHECK (remeet_internal.is_regular() AND host_user_id=remeet_internal.actor_id()
 AND (folder_id IS NULL OR remeet_internal.folder_owned(folder_id)));
CREATE POLICY meeting_update ON meetings FOR UPDATE TO remeet_app
 USING (remeet_internal.meeting_owned(id)) WITH CHECK (remeet_internal.meeting_host(id));
CREATE POLICY meeting_delete ON meetings FOR DELETE TO remeet_app
 USING (remeet_internal.meeting_owned(id));

CREATE POLICY share_select ON meeting_shares FOR SELECT TO remeet_app
 USING (recipient_user_id=remeet_internal.actor_id() OR remeet_internal.meeting_owned(meeting_id));
CREATE POLICY share_insert ON meeting_shares FOR INSERT TO remeet_app
 WITH CHECK (remeet_internal.meeting_owned(meeting_id) AND sender_id=remeet_internal.actor_id());
CREATE POLICY share_update ON meeting_shares FOR UPDATE TO remeet_app
 USING (remeet_internal.meeting_owned(meeting_id)) WITH CHECK (remeet_internal.meeting_owned(meeting_id));
CREATE POLICY share_delete ON meeting_shares FOR DELETE TO remeet_app
 USING (remeet_internal.meeting_owned(meeting_id));

CREATE POLICY raw_select ON transcript_lines FOR SELECT TO remeet_app
 USING (remeet_internal.raw_visible(meeting_id));
CREATE POLICY raw_insert ON transcript_lines FOR INSERT TO remeet_app
 WITH CHECK (remeet_internal.meeting_owned(meeting_id));
CREATE POLICY raw_update ON transcript_lines FOR UPDATE TO remeet_app
 USING (remeet_internal.meeting_owned(meeting_id)) WITH CHECK (remeet_internal.meeting_owned(meeting_id));
CREATE POLICY raw_delete ON transcript_lines FOR DELETE TO remeet_app
 USING (remeet_internal.meeting_owned(meeting_id));

CREATE POLICY notification_select ON notifications FOR SELECT TO remeet_app
 USING (user_id=remeet_internal.actor_id());
CREATE POLICY notification_update ON notifications FOR UPDATE TO remeet_app
 USING (user_id=remeet_internal.actor_id()) WITH CHECK (user_id=remeet_internal.actor_id());
CREATE POLICY notification_delete ON notifications FOR DELETE TO remeet_app
 USING (user_id=remeet_internal.actor_id());
-- Notification creation is performed by trusted DB triggers in migration 003.
-- Do not give application callers INSERT on notifications.
