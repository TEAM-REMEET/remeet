"""Command-line entry point for the baseline, independent of the demo web app."""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

BACKEND = Path(__file__).resolve().parent
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND / "src"))

from followup.adapters.llm.openai_client import OpenAIClient
from followup.application.analyze_meeting import analyze_meeting


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", nargs="?", type=Path,
                        default=ROOT / "evals/datasets/TS3003c/TS3003c.txt")
    parser.add_argument("--metadata", type=Path, help="JSON object containing title, date, participants")
    parser.add_argument("--model", default="gpt-6-luna")
    parser.add_argument("--chunk-size", type=int, default=1000)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    load_dotenv(ROOT.parent / ".env", override=False, encoding="utf-8-sig")
    metadata_path = args.metadata or args.source.with_suffix(".metadata.json")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig")) if metadata_path.exists() else {}
    if args.metadata and not metadata_path.exists():
        parser.error("Metadata file does not exist")
    if not isinstance(metadata, dict):
        parser.error("Metadata must be a JSON object")
    output = args.output or ROOT / "outputs/base-agent" / (
        args.source.stem + "_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    result = analyze_meeting(args.source, output, args.model, OpenAIClient(args.model), metadata, args.chunk_size)
    print(json.dumps({"status": result["status"], "output": str(output)}, ensure_ascii=False))
    return 0 if result["status"] == "성공" else 1


if __name__ == "__main__":
    raise SystemExit(main())
