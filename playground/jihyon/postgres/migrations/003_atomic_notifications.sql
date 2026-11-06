-- Triggers run inside the SAME transaction as membership/sharing INSERT.
-- Trigger owner needs permission to insert notifications; no app INSERT permission needed.
CREATE FUNCTION remeet_internal.notify_folder_member() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
  IF NEW.user_id <> COALESCE(NEW.added_by, '') THEN
    INSERT INTO public.notifications(id,user_id,kind,text,folder_id)
      VALUES ('n-'||replace(gen_random_uuid()::text,'-',''), NEW.user_id,
        'member', '폴더에 구성원으로 추가됐습니다.', NEW.folder_id);
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER folder_member_auto_notification
AFTER INSERT ON folder_members
FOR EACH ROW EXECUTE FUNCTION remeet_internal.notify_folder_member();

CREATE FUNCTION remeet_internal.notify_meeting_share() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
  IF NEW.recipient_user_id IS NOT NULL AND NEW.recipient_user_id <> NEW.sender_id THEN
    INSERT INTO public.notifications(id,user_id,kind,text,meeting_id)
      VALUES ('n-'||replace(gen_random_uuid()::text,'-',''), NEW.recipient_user_id,
        'shared', '회의가 공유됐습니다.', NEW.meeting_id);
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER meeting_share_auto_notification
AFTER INSERT ON meeting_shares
FOR EACH ROW EXECUTE FUNCTION remeet_internal.notify_meeting_share();
REVOKE ALL ON FUNCTION remeet_internal.notify_folder_member() FROM PUBLIC;
REVOKE ALL ON FUNCTION remeet_internal.notify_meeting_share() FROM PUBLIC;
