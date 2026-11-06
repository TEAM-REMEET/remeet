"""AI 분석 결과(*_summary.json) 중 PostgreSQL 담당 부분을 넣는 스크립트.

같은 파일을 Neo4j 쪽 load.py(playground/eunje/ontology/scripts/load.py)도 읽습니다. 나눠 갖는 방식:

    summary.json
     ├─ meeting_info.title / date, agendas, subtopics, major_discussions, action items ─→ Neo4j (load.py)
     └─ transcript(발화), 화자 매핑, config · source_document · 검토 이력, 파일 전체   ─→ PostgreSQL (이 스크립트)

실행 (이 폴더에서):
    python import_summary.py ../../eunje/ontology/data/summary_action      # 폴더 안 */*_summary.json 전부
    python import_summary.py path/to/TS3003a_summary.json --folder TS3003 --host u1

같은 회의를 다시 넣으면 원문 · 화자는 새로 덮어쓰고, 분석 기록(meeting_analyses)은 한 줄 더 쌓입니다.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import paths  # noqa: F401  (.env 읽기 — 꼭 맨 먼저)
from db import transaction


def import_one(path: Path, *, folder: str | None, host: str | None, speakers: dict[str, str]) -> None:
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
                print(f"  (폴더 {folder} 가 없어서 폴더 없이 넣어요. 웹에서 폴더를 만든 뒤 meetings.folder_id 를 채우세요)")
                folder = None
        # 회의 관리 행: 없으면 만들고, 있으면 그대로 둠 (상태 · 폴더 등 화면에서 바꾼 값 보존)
        cur.execute(
            """INSERT INTO meetings (id, folder_id, host_user_id, file_name, time_label)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (id) DO NOTHING""",
            (mid, folder, host or "", re.split(r"[\\\\/]", raw.get("source_document", {}).get("input_path", ""))[-1],  # 윈도우 경로(\\)도 파일 이름만
             info.get("duration", "")),
        )
        cur.execute(
            """INSERT INTO meeting_analyses (meeting_id, created_at, model, reasoning_effort, config,
                                             source_document, meeting_info, postprocess_log, raw)
               VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb)""",
            (mid, raw.get("created_at"), raw.get("config", {}).get("model"), raw.get("config", {}).get("reasoning_effort"),
             json.dumps(raw.get("config", {}), ensure_ascii=False),
             json.dumps(raw.get("source_document", {}), ensure_ascii=False),
             json.dumps(info, ensure_ascii=False),
             json.dumps(raw.get("postprocess_log", []), ensure_ascii=False),
             json.dumps(rest, ensure_ascii=False)),
        )
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
    print(f"  {mid}: 발화 {len(transcript)}줄 · 화자 {len(info.get('speakers') or {})}명")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", type=Path, help="*_summary.json 파일 또는 그 파일들이 들어 있는 폴더")
    ap.add_argument("--folder", help="넣을 폴더 id (= Neo4j Project id). 안 주면 회의 id 끝 글자를 뗀 값 (TS3003a → TS3003)")
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
