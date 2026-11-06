"""요약 · 액션 아이템 추출 실행 (전사문 파일 → summary.json)

실행 (backend 폴더에서)
    PYTHONPATH=src python -m followup.entrypoints.cli 경로/TS3003c.txt
    PYTHONPATH=src python -m followup.entrypoints.cli 경로/a.txt 경로/b.txt --no-cache

회의 ID는 파일 이름. 실패하면 같은 명령으로 다시 실행해 실패한 단계부터 진행함.
"""

import argparse
import logging
import sys
from pathlib import Path

from followup.adapters.parsers.transcript import read_transcript
from followup.application.summarize_meeting import summarize_meeting
from followup.entrypoints.bootstrap import build_llm, build_store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="요약 · 액션 아이템 추출")
    parser.add_argument("sources", nargs="+", type=Path, help="전사문 파일 (.txt · .docx · .hwpx)")
    parser.add_argument("--no-save", action="store_true", help="결과를 저장하지 않음")
    parser.add_argument("--no-cache", action="store_true", help="저장된 단계별 응답을 쓰지 않고 다시 호출")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout, force=True)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    llm, store = build_llm(), build_store()

    failed = []
    for source in args.sources:     # 한 회의가 실패해도 다음 회의는 계속 실행
        try:
            summary = summarize_meeting(source.stem, read_transcript(source), llm=llm, store=store,
                                        source_name=source.name, save=not args.no_save,
                                        use_cache=False if args.no_cache else None)
            print(f"[완료] {summary.meeting_id}: {summary.verification.status}")
        except Exception as e:
            failed.append(str(source))
            print(f"[실패] {source}: {type(e).__name__}: {e}")
    if failed:
        print(f"실패한 입력 {len(failed)}개: {failed}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
