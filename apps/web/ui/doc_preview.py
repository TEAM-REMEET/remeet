"""회의 결과 미리보기.

- 전체 미리보기 · 이메일 · Notion · Confluence: 같은 Markdown 문서
- Slack · Teams: 표 없는 메시지 형식
- 캘린더: 일정 + 설명란 텍스트
- Jira: 액션 아이템마다 이슈 카드
"""
from __future__ import annotations

import streamlit as st

from data.seed import INTEGRATIONS
from services import delivery
from services.rules import member_name
from ui.components import html
from utils.dates import fmt_d
from utils.text import esc

CAPTION = {
    "email": "전체 미리보기와 같은 형식으로 메일 본문에 들어가요.",
    "notion": "전체 미리보기와 같은 형식의 페이지로 만들어져요.",
    "confluence": "전체 미리보기와 같은 형식의 페이지로 만들어져요.",
    "slack": "순서와 내용은 전체 미리보기와 같아요. Slack 메시지는 표를 지원하지 않아 액션 아이템을 목록으로 보여줘요.",
    "teams": "순서와 내용은 전체 미리보기와 같아요. Teams 메시지는 표 대신 목록으로 보여줘요.",
    "calendar": "다음 회의 일정이 만들어지고, 설명란에 전체 미리보기와 같은 순서의 텍스트가 들어가요.",
    "jira": "액션 아이템마다 이슈가 하나씩 만들어져요. 각 이슈 설명에 회의 정보와 원문 근거가 들어가요.",
}


def doc_html(meeting: dict, members: list[dict], items: dict | None = None, include_drafts: bool = False) -> str:
    """전체 미리보기 문서 (프로토타입 .pv-doc와 같은 구성). 내용은 delivery.build_markdown과 같아요."""
    d, it = meeting["detail"], items or delivery.ALL_ITEMS
    nm = lambda mid: esc(member_name(members, mid, "미정"))  # noqa: E731
    acts = [a for a in d["actions"] if a["review"] == "approved" or (include_drafts and a["review"] == "draft")]
    empty = '<p class="muted small">없음</p>'
    out = [f'<article class="pv-doc"><header><h2>{esc(meeting["title"])}</h2>'
           f'<p>{fmt_d(meeting["date"], True)} {meeting["start"]}–{meeting["end"]} · 주관 {nm(meeting["organizer"])}</p>'
           f'<p>참석 {", ".join(nm(x) for x in meeting["attendees"])}</p></header>']
    if it.get("summary"):
        out.append(f'<h3>회의 개요</h3><p>{esc(d["core"])}</p><p class="muted small">목적 · {esc(d["purpose"])}</p>')
        out.append("<h3>안건별 논의</h3><ol>" + "".join(f'<li><strong>{esc(t["title"])}</strong><br>{esc(t["note"])}</li>' for t in d["topics"]) + "</ol>")
    if it.get("decisions"):
        out.append("<h3>결정사항</h3>" + ("<ol>" + "".join(f"<li>{esc(x['text'])}</li>" for x in d["decisions"]) + "</ol>" if d["decisions"] else empty))
    if it.get("actions"):
        rows = "".join(
            f'<tr><td>{esc(a["title"])}{" <span class=edited>검토 전</span>" if a["review"] == "draft" else ""}</td>'
            f'<td class="nw">{nm(a["assignee"])}</td><td class="nw">{fmt_d(a["due"]) or "미정"}</td><td class="nw">{esc(a["priority"])}</td></tr>'
            for a in acts)
        out.append('<h3>액션 아이템</h3><table class="pv-tbl"><thead><tr><th>업무</th><th>담당자</th><th>기한</th><th>우선순위</th></tr></thead>'
                   f'<tbody>{rows or "<tr><td colspan=4 class=muted>승인된 업무가 없어요</td></tr>"}</tbody></table>')
    if it.get("issues"):
        out.append("<h3>미해결 이슈</h3>" + ("<ul>" + "".join(f'<li><strong>{esc(x["title"])}</strong> — {esc(x["text"])}</li>' for x in d["issues"]) + "</ul>" if d["issues"] else empty))
    if it.get("agenda"):
        out.append("<h3>다음 회의 안건</h3>" + ("<ol>" + "".join(f"<li>{esc(x['text'])}</li>" for x in d["agenda"]) + "</ol>" if d["agenda"] else empty))
    return "".join(out) + "</article>"


def meeting_doc(meeting: dict, members: list[dict], items: dict | None = None, include_drafts: bool = False, key: str = "doc_preview") -> None:
    with st.container(key=key):
        html(doc_html(meeting, members, items, include_drafts))


def target_preview(target: str, meeting: dict, members: list[dict], items: dict, options: dict) -> None:
    st.caption("ℹ️ " + CAPTION[target])
    nm = lambda mid: member_name(members, mid, "미정")  # noqa: E731
    if target == "email":
        recipients = ", ".join(nm(x) for x in meeting["attendees"]) + (", " + ", ".join(options.get("extra", [])) if options.get("extra") else "")
        html(f'<dl><div class="kv"><dt>받는 사람</dt><dd>{esc(recipients)}</dd></div><div class="kv"><dt>제목</dt><dd>[회의 결과] {esc(meeting["title"])} ({fmt_d(meeting["date"])})</dd></div></dl>')
        meeting_doc(meeting, members, items, key=f"doc_{target}")
    elif target in ("notion", "confluence"):
        st.caption(("Notion · 팀 위키 / 회의록 / " if target == "notion" else "Confluence · 회의록 스페이스 / ") + meeting["title"])
        meeting_doc(meeting, members, items, key=f"doc_{target}")
    elif target in ("slack", "teams"):
        channel = options.get("channel", "#온보딩-tf") if target == "slack" else "팀 채널"
        html(f'<div class="chat"><strong>RE:MEET 앱</strong> <span class="muted small">{esc(channel)}</span>\n\n{esc(delivery.build_chat_text(meeting, members, items))}</div>')
    elif target == "calendar":
        html(f'<div class="card"><dl><div class="kv"><dt>일정</dt><dd><strong>{esc(meeting["title"])} 후속 회의</strong></dd></div>'
             f'<div class="kv"><dt>일시</dt><dd>{fmt_d(delivery.next_meeting_date(meeting), True)} {meeting["start"]} – {meeting["end"]}</dd></div>'
             f'<div class="kv"><dt>참석자</dt><dd>{esc(", ".join(nm(x) for x in meeting["attendees"]))}</dd></div></dl>'
             f'<div class="plain">{esc(delivery.build_plain_text(meeting, members, items))}</div></div>')
    elif target == "jira":
        issues = delivery.jira_issues(meeting, members, options.get("project", "ONB"))
        if not issues:
            st.info("보낼 액션 아이템이 없어요.")
        for it in issues:
            desc = esc(it["description"]).replace("\n", "<br>")
            html(f'<div class="issue-card"><span class="tag">{it["key"]}</span> <strong>{esc(it["summary"])}</strong>'
                 f'<dl><div class="kv"><dt>담당자</dt><dd>{esc(it["assignee"])}</dd></div><div class="kv"><dt>기한</dt><dd>{esc(it["due"])}</dd></div>'
                 f'<div class="kv"><dt>우선순위</dt><dd>{esc(it["priority"])}</dd></div><div class="kv"><dt>설명</dt><dd class="small">{desc}</dd></div></dl></div>')


def target_name(target: str) -> str:
    return INTEGRATIONS[target]["name"]
