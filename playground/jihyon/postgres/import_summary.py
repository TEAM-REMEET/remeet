"""AI 분석 결과(*_summary.json) 중 PostgreSQL 담당 부분을 넣는 스크립트.

같은 파일을 Neo4j 쪽 load.py(playground/eunje/ontology/scripts/load.py)도 읽습니다. 나눠 갖는 방식:

    summary.json
     ├─ meeting_info.title / date, agendas, subtopics, major_discussions, action items ─→ Neo4j (load.py)
     └─ transcript(발화), 화자 매핑, config · source_document · 검토 이력, 파일 전체   ─→ PostgreSQL (이 스크립트)

실행 (이 폴더에서):
    python import_summary.py ../../eunje/ontology/data/summary_action      # 폴더 안 */*_summary.json 전부
    python import_summary.py path/to/TS3003a_summary.json --folder TS3003 --host u1

같은 회의를 다시 넣으면 원문 · 화자는 새로 덮어쓰고, 분석 기록(meeting_analyses) · 요약(meeting_summaries)은
한 버전 더 쌓입니다. source_document.input_path 의 원본 파일을 찾으면 meeting_sources 에도 넣어요
(내용이 같은 원본이 이미 있으면 다시 넣지 않음).

⚠️ summary_from_json() 은 README 의 예전 Neo4j 속성 이름(background · decisions · unresolved_issues · next_topics ·
   discussions · evidence · description · due · owner_raw)을 기준으로 여러 키 이름을 받아 주게 짰고,
   결과는 web 목업 meetings.json 과 같은 모양이에요.
   실제 summary.json 키와 다르면 그 함수만 고치면 됩니다.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import hashlib
from datetime import date, datetime

import paths  # noqa: F401  (.env 읽기 — 꼭 맨 먼저)
from db import transaction
from crud.detail import insert_summary


def _first(d: dict, *keys, default=None):
    for k in keys:
        if d.get(k) not in (None, "", []):
            return d[k]
    return default


def _text(v) -> str:
    """문자열이면 그대로, 목록이면 줄바꿈으로 이어 붙임."""
    if isinstance(v, list):
        return "\n".join(_text(x) for x in v if x not in (None, ""))
    if isinstance(v, dict):
        return _text(_first(v, "content", "text", "summary", default=""))
    return "" if v is None else str(v)


def _ids(v) -> list[str]:
    """근거 발화 번호: "86,87,88" 이나 [86, 87] 모두 → ["86", "87", "88"]"""
    if v in (None, ""):
        return []
    items = v.split(",") if isinstance(v, str) else v
    return [str(x).strip() for x in items if str(x).strip()]


def _strs(v) -> list[str]:
    if v in (None, ""):
        return []
    return [_text(x) for x in (v if isinstance(v, list) else [v]) if _text(x)]


def _iso_date(v) -> str | None:
    try:
        return date.fromisoformat(str(v)[:10]).isoformat() if v else None
    except ValueError:
        return None


def _held_at(info: dict) -> datetime | None:
    v = _first(info, "date", "held_at", "datetime")
    try:
        return datetime.fromisoformat(str(v)) if v else None
    except ValueError:
        return None


def _evs(v) -> list[dict]:
    """문자열 · 문자열 목록 · [{text, src}] → [{text, src}] (화면 Evidence 모양)"""
    out = []
    for x in (v if isinstance(v, list) else [v] if v not in (None, "") else []):
        if isinstance(x, dict):
            text = _text(x)
            src = _ids(_first(x, "src", "evidence", default=[]))
        else:
            text, src = _text(x), []
        if text:
            out.append({"text": text, "src": src})
    return out


def _md(iso: str | None) -> str:
    """'2026-11-10' → '11/10' (화면 마감 표기)"""
    return f"{int(iso[5:7])}/{int(iso[8:10])}" if iso else ""


def summary_from_json(raw: dict) -> tuple[dict, list[dict]]:
    """summary.json → (요약 내용, 액션 아이템)  ※ crud.detail.insert_summary 형식 (= web 목업 meetings.json 모양)"""
    info = raw.get("meeting_info", {})
    overview = _first(raw, "summary", "overall_summary", "overview", default=_first(info, "summary", default=""))
    summary = ({"text": _text(overview), "src": _ids(overview.get("src"))} if isinstance(overview, dict)
               else {"text": _text(overview), "src": []})
    agendas, todos = [], []

    def add_todos(items):
        for t in items or []:
            text = _text(_first(t, "description", "task", "content", "text", default=""))
            if text:
                due = _iso_date(_first(t, "due", "due_date", "deadline"))
                todos.append({"text": text, "owners": [], "due": _md(due), "due_date": due,
                              "src": _ids(_first(t, "src", "evidence", default=[])), "urgent": False,
                              "owner_raw": _text(_first(t, "owner_raw", "owner", "assignee", default=""))})

    for a in raw.get("agendas") or []:
        title = _text(_first(a, "title", "agenda", "name", "topic", default=""))
        if not title:
            continue
        topics = []
        for d in _first(a, "subtopics", "discussions", "major_discussions", "topics", default=[]) or []:
            if isinstance(d, str):
                d = {"content": d}
            content = _text(_first(d, "discussions", "content", "summary", "discussion", default=""))
            if content:
                topics.append({"title": _text(_first(d, "subtopic", "title", "topic", default="")) or title,
                               "points": [{"kind": "정보", "text": content, "src": _ids(d.get("evidence"))}]})
        agendas.append({"title": title, "status": "", "tone": "info", "deep": False,
                        "src": _ids(_first(a, "src", "evidence", default=[])), "points": [],
                        "background": _evs(a.get("background")), "topics": topics,
                        "decisions": _evs(a.get("decisions")), "open_issues": _evs(a.get("unresolved_issues")),
                        "next": _evs(a.get("next_topics"))})
        add_todos(_first(a, "action_items", "actions"))
    add_todos(_first(raw, "action_items", "actions"))
    content = {"summary": summary, "agendas": agendas,
               "decisions": _evs(raw.get("decisions")), "open_issues": _evs(raw.get("unresolved_issues"))}
    return content, todos


def _read_source(path: Path, raw: dict) -> tuple[str, str] | None:
    """source_document.input_path 의 원본 파일 (json 파일 옆 · 절대 경로 · 이 폴더 기준 순서로 찾음)."""
    input_path = raw.get("source_document", {}).get("input_path", "")
    if not input_path:
        return None
    name = re.split(r"[\\\\/]", input_path)[-1]
    for cand in (path.parent / name, Path(input_path), Path(input_path.replace("\\", "/"))):
        if cand.is_file():
            return name, cand.read_text(encoding="utf-8", errors="replace")
    return None


def import_one(path: Path, *, folder: str | None, host: str | None, speakers: dict[str, str]) -> None:
    if not host:  # meetings.host_user_id 는 accounts 를 가리킴 (migrations/012) → 빈 값 X
        raise SystemExit(f"{path.name}: 주관자(--host 또는 화자 A 매핑)가 필요해요.")
    raw = json.loads(path.read_text(encoding="utf-8"))
    mid = raw["meeting_id"]
    info = raw.get("meeting_info", {})
    transcript = raw.get("transcript", [])
    rest = {k: v for k, v in raw.items() if k != "transcript"}  # 원문은 표로 따로 저장하니 raw 에서는 뺌

    with transaction() as cur:  # 회의 1건 = 트랜잭션 1개 (중간에 실패하면 이 회의는 통째로 취소)
        if folder:
            # 폴더(= Neo4j Project)는 웹에서 만드는 것이라 여기서 만들지 않음. 없으면 폴더 없이 넣음
            cur.execute("SELECT 1 FROM folders WHERE id = %s", (folder,))
            if cur.fetchone() is None:
                print(f"  (프로젝트 {folder} 가 없어서 프로젝트 없이 넣어요. 웹에서 만든 뒤 meetings.project_id 를 채우세요)")
                folder = None
        # 회의 관리 행: 없으면 만들고, 있으면 그대로 둠 (상태 · 폴더 등 화면에서 바꾼 값 보존)
        # 제목 · 날짜는 비어 있을 때만 채움 (화면에서 고친 값 보존)
        cur.execute(
            """INSERT INTO meetings (id, project_id, host_user_id, title, held_at, file_name, time_label, created_by)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (id) DO UPDATE SET
                   title   = CASE WHEN meetings.title = '' THEN EXCLUDED.title ELSE meetings.title END,
                   held_at = COALESCE(meetings.held_at, EXCLUDED.held_at)""",
            (mid, folder, host, _text(info.get("title")), _held_at(info),
             re.split(r"[\\\\/]", raw.get("source_document", {}).get("input_path", ""))[-1],  # 윈도우 경로(\\)도 파일 이름만
             info.get("duration", ""), host),
        )
        cur.execute(
            """INSERT INTO meeting_analyses (meeting_id, created_at, model, reasoning_effort, config,
                                             source_document, meeting_info, postprocess_log, raw)
               VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb)
               RETURNING id""",
            (mid, raw.get("created_at"), raw.get("config", {}).get("model"), raw.get("config", {}).get("reasoning_effort"),
             json.dumps(raw.get("config", {}), ensure_ascii=False),
             json.dumps(raw.get("source_document", {}), ensure_ascii=False),
             json.dumps(info, ensure_ascii=False),
             json.dumps(raw.get("postprocess_log", []), ensure_ascii=False),
             json.dumps(rest, ensure_ascii=False)),
        )
        analysis_id = cur.fetchone()["id"]
        # 회의 원본 파일: 찾았고, 같은 내용이 아직 없으면 새 버전으로
        source_id, src_note = None, "원본 파일 없음"
        if found := _read_source(path, raw):
            name, content = found
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
            cur.execute("SELECT id FROM meeting_sources WHERE meeting_id = %s AND sha256 = %s", (mid, digest))
            if row := cur.fetchone():
                source_id, src_note = row["id"], "원본 그대로"
            else:
                cur.execute(
                    """INSERT INTO meeting_sources (meeting_id, version, file_name, content_text, size_bytes,
                                                    sha256, uploaded_by)
                       VALUES (%s, (SELECT COALESCE(MAX(version), 0) + 1 FROM meeting_sources WHERE meeting_id = %s),
                               %s, %s, %s, %s, %s) RETURNING id""",
                    (mid, mid, name, content, len(content.encode("utf-8")), digest, host),
                )
                source_id, src_note = cur.fetchone()["id"], "원본 저장"
        # 요약 회의록: 새 버전으로 쌓음
        content, todos = summary_from_json(raw)
        summary = insert_summary(cur, meeting_id=mid, content=content, todos=todos,
                                 origin="ai", created_by=None, analysis_id=analysis_id, source_id=source_id)
        # 참석자 = 주관자 + 화자 매핑으로 사람이 정해진 화자
        for i, uid in enumerate(dict.fromkeys(u for u in [host, *speakers.values()] if u)):
            cur.execute("""INSERT INTO meeting_attendees (meeting_id, user_id, position) VALUES (%s, %s, %s)
                           ON CONFLICT DO NOTHING""", (mid, uid, i))
        agendas, actions = content["agendas"], todos
        # 원문: 지우고 다시 넣기
        cur.execute("DELETE FROM transcript_lines WHERE meeting_id = %s", (mid,))
        cur.executemany(  # executemany: 같은 INSERT 를 여러 행에 한 번에
            """INSERT INTO transcript_lines (meeting_id, line_id, position, speaker, speaker_role, text, source_line)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            [(mid, str(u["utterance_index"]), i, u.get("speaker", ""), u.get("speaker_role", ""), u["text"],
              u.get("line_number")) for i, u in enumerate(transcript)],
        )
        cur.execute("DELETE FROM meeting_speakers WHERE meeting_id = %s", (mid,))
        for label, role in (info.get("speakers") or {}).items():
            cur.execute("INSERT INTO meeting_speakers (meeting_id, label, role_text, user_id) VALUES (%s, %s, %s, %s)",
                        (mid, label, role, speakers.get(label)))
    print(f"  {mid}: 발화 {len(transcript)}줄 · 화자 {len(info.get('speakers') or {})}명 · "
          f"요약 v{summary['version']} (안건 {len(agendas)} · 할 일 {len(actions)}) · {src_note}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", type=Path, help="*_summary.json 파일 또는 그 파일들이 들어 있는 폴더")
    ap.add_argument("--folder", help="넣을 프로젝트 id (meetings.project_id). 안 주면 회의 id 끝 글자를 뗀 값 (TS3003a → TS3003)")
    ap.add_argument("--host", help="주관자 user_id (안 주면 화자 A 에 연결된 사람)")
    ap.add_argument("--speakers", default="A=u1,C=u2,B=u3,D=u4",
                    help="화자 표기 → user_id (기본값은 ontology/data/users.json 의 meeting_attendees 순서)")
    args = ap.parse_args()

    speakers = dict(pair.split("=", 1) for pair in args.speakers.split(",") if "=" in pair)
    files = sorted(args.path.glob("*/*_summary.json")) if args.path.is_dir() else [args.path]
    if not files:
        sys.exit(f"summary.json 을 찾지 못했어요: {args.path}")
    for f in files:
        mid = f.name.removesuffix("_summary.json")
        folder = args.folder or (mid[:-1] if mid[-1:].isalpha() and mid[-1:].islower() else None)
        import_one(f, folder=folder, host=args.host or speakers.get("A"), speakers=speakers)
    print(f"✅ {len(files)}개 회의 가져오기 완료")


if __name__ == "__main__":
    main()
