"""Prepare readable, API-ready handoff files for the 900/1,200-char merges.

This script only reads and writes local files. It does not call an API.
"""

import json
import re
from pathlib import Path


HERE = Path(__file__).resolve().parent
RUN = HERE / "v2"
CONDITIONS = {
    "merge_900": {"expected_requests": 26, "max_chars": 900},
    "merge_1200": {"expected_requests": 16, "max_chars": 1200},
}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    manifest = read_json(RUN / "manifest.json")
    prepared = read_json(RUN / "sent_payloads.json")
    system_prompt = (RUN / "system_prompt.txt").read_text(encoding="utf-8")
    output_dir = HERE / "handoff"

    for condition, rules in CONDITIONS.items():
        requests = [item for item in prepared if item["condition"] == condition]
        assert len(requests) == rules["expected_requests"], (condition, len(requests))
        assert len({item["request_id"] for item in requests}) == len(requests)
        assert sum(len(re.findall(r"(?m)^- 후보 \d+:", item["user_message"])) for item in requests) == 39

        package = {
            "condition": condition,
            "model": manifest["model"],
            "settings": manifest["settings"],
            "merge_limit_chars": rules["max_chars"],
            "system_prompt": system_prompt,
            "requests": requests,
            "gold_data_included": False,
            "api_calls_performed_by_this_script": False,
        }
        write_json(output_dir / f"{condition}.json", package)
        print(f"{condition}: {len(requests)} requests, 39 candidate judgments prepared")


if __name__ == "__main__":
    main()
