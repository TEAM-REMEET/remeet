-- 020: 앱 계정(remeet_app)이 accounts.email 칸을 읽을 수 있게.
--      외부 도구 전송에 필요: Jira 담당자 지정(이메일 → Jira 계정 찾기) · 요약본 메일 발송(받는 사람 주소).
--      칸 단위 권한이라 password_hash 는 여전히 못 읽음 (002 의 user_id · role 에 email 만 더함).

GRANT SELECT (email) ON accounts TO remeet_app;
