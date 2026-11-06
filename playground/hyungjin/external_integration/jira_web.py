"""Jira 이슈 입력·전송 + 담당자 메일 알림 화면. 실행: streamlit run jira_web.py"""

import asyncio
import json

import streamlit as st

from integrations import mail_message, send_jira_issue, send_mail
from jira import connect, list_issue_types, settings
from mail import settings as mail_settings

PRIORITIES = ["", "Highest", "High", "Medium", "Low", "Lowest"]


@st.cache_data(ttl=600, show_spinner="이슈 유형을 불러오는 중...")
def issue_types():
    async def fetch():
        async with connect(config) as session:
            response = await list_issue_types(session, config["JIRA_CLOUD_ID"], config["JIRA_PROJECT_KEY"])
            return [t["name"] for t in json.loads(response.content[0].text)["data"]["issueTypes"] if not t["subtask"]]
    return asyncio.run(fetch())


def reason(error):
    # MCP 오류는 TaskGroup의 ExceptionGroup에 싸여 올라온다.
    while isinstance(error, BaseExceptionGroup):
        error = error.exceptions[0]
    return error


def notify(mail):
    """Jira 이슈가 생긴 뒤 메일을 보낸다. 실패하면 이슈는 그대로 두고 메일만 다시 보낼 수 있게 남긴다."""
    try:
        with st.spinner("메일 보내는 중..."):
            send_mail(mail_config, mail_message(mail["result"], mail_config["MAIL_FROM"], mail["to"], mail["key"]))
        st.session_state.pop("pending_mail", None)
        st.success(f"메일 발송: {mail['to']}")
    except Exception as error:
        st.session_state["pending_mail"] = mail
        st.error(f"메일 발송 실패 (Jira 이슈 {mail['key']}는 생성됨): {reason(error)}")


async def send(args):
    async with connect(config) as session:
        response = await send_jira_issue(session, args)
        return json.loads(response.content[0].text)["data"]["key"]


st.set_page_config(page_title="Jira 이슈 전송", page_icon="📝")
st.title("Jira 이슈 전송")

try:
    config = settings()
    types = issue_types()
except Exception as error:  # 설정·토큰 오류를 화면에 보여준다
    st.error(f"Jira 연결 실패: {reason(error)}")
    st.stop()

try:
    mail_config = mail_settings()
except ValueError as error:  # 메일 설정이 없어도 Jira 전송은 쓸 수 있다
    mail_config = None
    st.info(f"메일 알림 꺼짐: {error}")

st.caption(f"프로젝트 `{config['JIRA_PROJECT_KEY']}` · 전송하면 Jira에 실제 이슈가 생성됩니다.")
default_type = config.get("JIRA_ISSUE_TYPE")

with st.form("issue", clear_on_submit=False):
    summary = st.text_input("제목 *")
    issue_type = st.selectbox("이슈 유형 *", types, index=types.index(default_type) if default_type in types else 0)
    assignee = st.text_input("담당자 Atlassian accountId", value=config.get("JIRA_ASSIGNEE_ACCOUNT_ID") or "",
                             help="비우면 미배정. 서비스 user_id가 아니라 Atlassian accountId입니다.")
    left, right = st.columns(2)
    due = left.date_input("기한", value=None, format="YYYY-MM-DD")
    priority = right.selectbox("우선순위", PRIORITIES, format_func=lambda p: p or "(기본값)")
    labels = st.text_input("레이블", help="쉼표로 구분. 레이블에는 공백을 쓸 수 없습니다.")
    description = st.text_area("설명 (마크다운)", height=160)
    send_notice = st.checkbox("이슈 생성 후 담당자에게 메일 보내기", value=bool(mail_config), disabled=not mail_config)
    mail_to = st.text_input("메일 받는 주소", value=(mail_config or {}).get("MAIL_TEST_TO") or "",
                            disabled=not mail_config, help="테스트 중에는 본인 주소로 받으세요.")
    submitted = st.form_submit_button("Jira로 전송", type="primary")

if submitted:
    label_list = [label.strip() for label in labels.split(",") if label.strip()]
    if not summary.strip():
        st.error("제목을 입력하세요.")
    elif send_notice and "@" not in mail_to:
        st.error("메일 받는 주소를 입력하세요.")
    elif any(" " in label for label in label_list):
        st.error("레이블에는 공백을 쓸 수 없습니다. 예: remeet-test")
    else:
        args = {"cloudId": config["JIRA_CLOUD_ID"], "projectKey": config["JIRA_PROJECT_KEY"],
                "issueType": issue_type, "summary": summary.strip()}
        optional = {"assignee": assignee.strip(), "description": description.strip(), "priority": priority,
                    "labels": label_list, "additional_fields": {"duedate": due.isoformat()} if due else None}
        args.update({key: value for key, value in optional.items() if value})
        try:
            with st.spinner("전송 중..."):
                key = asyncio.run(send(args))
            st.success(f"생성됨: **{key}**")
        except Exception as error:
            st.error(f"전송 실패: {reason(error)}")
        else:
            if send_notice:
                item = {"title": summary.strip(), "due": due.isoformat() if due else None}
                notify({"result": {"action_item": item}, "to": mail_to.strip(), "key": key})

if st.session_state.get("pending_mail") and mail_config:
    pending, slot = st.session_state["pending_mail"], st.empty()
    if slot.button(f"메일만 다시 보내기 ({pending['key']} → {pending['to']})"):
        notify(pending)
        if "pending_mail" not in st.session_state:  # 성공하면 버튼을 바로 지운다
            slot.empty()
