"""Run the planned TS3003c candidate-context comparison using the OpenAI API."""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
MODEL = "gpt-6-luna"
CONFIG = {
    "base_url": "https://api.openai.com/v1/chat/completions",
    "reasoning_effort": "low",
    "max_completion_tokens": 16384,
    "service_tier": "default",
    "window_chars": 200,
    "overlap_chars": 100,
    "candidate_depth_threshold": 0.10,
}
CONDITIONS = {
    "baseline": "baseline.json",
    "merge_900": "preserve_900.json",
    "merge_1200": "preserve_1200.json",
}
PRICING = {"input": 0.10, "output": 0.50, "per_tokens": 1_000_000}
OUT = HERE  # v1 outputs live in ../v1_boundary_options/
# Same layout as test/llm_golden/<meeting>.json, one directory per condition.
RESULTS_DIR = OUT / "results"
# Counted as citations: [130], [u0130] (after v1) and ranges [81-89] (after v2); evidence is never rewritten.
CITATION_RE = re.compile(r"\[u?(\d+)(?:\s*[-~–]\s*u?(\d+))?\]")


def citations(text):
    """Cited utterance numbers; a range contributes its two endpoints."""
    return [int(n) for pair in CITATION_RE.findall(text) for n in pair if n]
# verification.rule strings used by test/llm_golden.
GOLDEN_RULES = {
    "exclusion": "판정 질문의 참 여부보다 경계 제외 기준을 우선 적용했다.",
    "new_agenda": "제외 기준에 해당하지 않으며 하나 이상의 판정 질문이 참이다.",
    "no_shift": "세 판정 질문이 모두 거짓이다.",
}


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_env():
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8-sig").splitlines():
            match = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$", line)
            if match and match.group(1) not in os.environ:
                value = match.group(2).strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                os.environ[match.group(1)] = value
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key or key.lower() in {"your_api_key", "your_openai_api_key", "<your_api_key>"}:
        raise RuntimeError("OPENAI_API_KEY is missing or is an obvious placeholder")
    return key


def parse_source(source: str):
    source = source.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    spans, cursor = [], 0
    for sep in re.finditer(r"\n[ \t]*\n(?:[ \t]*\n)*", source):
        spans.append((cursor, sep.start()))
        cursor = sep.end()
    spans.append((cursor, len(source)))
    spans = [(a, b) for a, b in spans if source[a:b].strip()]
    if not spans:
        raise ValueError("Transcript is empty")
    rows = []
    for a, b in spans[1:]:
        a += len(source[a:b]) - len(source[a:b].lstrip())
        b = a + len(source[a:b].rstrip())
        rows.append({"position": len(rows) + 1, "start_char": a, "end_char": b,
                     "uid": f"u{len(rows) + 1:04d}", "text": source[a:b]})
    if not rows:
        raise ValueError("Transcript has no utterances")
    return source, rows


def make_payload(q, source, rows, id_to_index):
    """Local record of one request; only user_message_v2(payload) is sent to the API."""
    candidates = [{"candidate_id": id_to_index[cid], "source_candidate_id": cid, "candidate_char": char,
                   "position": expected_boundary(char, rows)}
                  for cid, char in zip(q["original_candidate_ids"], q["original_candidate_chars"], strict=True)]
    # Utterances cut by the chunk edge keep only their visible part.
    visible = [{"position": r["position"], "text": source[max(r["start_char"], q["start"]):min(r["end_char"], q["end"])]}
               for r in rows if r["end_char"] > q["start"] and r["start_char"] < q["end"]]
    shown = {u["position"] for u in visible}
    for c in candidates:
        if not {c["position"] - 1, c["position"]} <= shown:
            raise ValueError(f"Expected boundary outside chunk: {q['condition']}/{q['request_id']} {c}")
    return {"request_id": q["request_id"], "condition": q["condition"],
            "visible_utterances": visible, "candidates": candidates}


def build_schema():
    check_schema = {"type": "object", "additionalProperties": False,
                    "required": ["subject_change", "closure_signal", "independent_summary"],
                    "properties": {k: {"type": "boolean"} for k in
                                   ("subject_change", "closure_signal", "independent_summary")}}
    judgment = {"type": "object", "additionalProperties": False,
                "required": ["candidate_id", "before_subject", "after_subject", "checks",
                             "shift_type", "shift_result", "verdict", "evidence"],
                "properties": {
                    "candidate_id": {"type": "integer"},
                    "before_subject": {"type": "string"}, "after_subject": {"type": "string"},
                    "checks": check_schema,
                    "shift_type": {"type": "string", "enum": ["same_subject_aspect", "digression_return", "re_discussion", "new_agenda", "no_shift"]},
                    "shift_result": {"type": "boolean"}, "verdict": {"type": "string", "enum": ["O", "X"]},
                    "evidence": {"type": "string"},
                }}
    return {"type": "json_schema", "json_schema": {"name": "ts3003c_candidate_review", "strict": True,
                                                       "schema": {"type": "object", "additionalProperties": False,
                                                                  "required": ["judgments"],
                                                                  "properties": {"judgments": {"type": "array", "items": judgment}}}}}


def extract_system_prompt(plan: str):
    match = re.search(r'SYSTEM_PROMPT = """(.*?)"""', plan, flags=re.S)
    if not match:
        raise ValueError("Could not extract the exact SYSTEM_PROMPT from plan")
    return match.group(1)


def build_experiment():
    plan_path = HERE / "experiment_plan.md"
    base = ROOT / "chunking_method"
    source_path = ROOT / "test/input/TS3003c.txt"
    gold_path = ROOT / "test/llm_golden/TS3003c.json"
    meetings_path = ROOT / "test/meetings/TS3003c.json"
    selection_path = base / "embedding_experiment/runs/BAAI--bge-m3/TS3003c/selections/w200_o100_sentence_depth_0.10.json"
    bundle_dir = base / "merge_experiment/inputs_not_sent/TS3003c"
    plan = plan_path.read_text(encoding="utf-8")
    system = extract_system_prompt(plan)
    source, rows = parse_source(source_path.read_text(encoding="utf-8"))
    gold = read_json(gold_path)
    meeting = read_json(meetings_path)
    if len(rows) != gold["utterance_count"]:
        raise ValueError(f"Utterance count mismatch: {len(rows)} vs {gold['utterance_count']}")
    # Golden files map positions through meetings/<id>.json; keep the same mapping.
    if [(u["uid"], u["text_ko"]) for u in meeting["utterances"]] != [(r["uid"], r["text"]) for r in rows]:
        raise ValueError("Source utterances do not match meetings/TS3003c.json")
    candidate_ids = sorted({cid for filename in CONDITIONS.values() for q in read_json(bundle_dir / filename)
                            for cid in q["original_candidate_ids"]})
    id_to_index = {cid: i for i, cid in enumerate(candidate_ids)}
    requests_by_condition = {}
    for cond, filename in CONDITIONS.items():
        bundle = read_json(bundle_dir / filename)
        records = []
        for q in bundle:
            if q["context"] != source[q["start"]:q["end"]]:
                raise ValueError(f"Context mismatch: {cond}/{q['request_id']}")
            q = {**q, "condition": cond}
            payload = make_payload(q, source, rows, id_to_index)
            records.append({"request": q, "payload": payload, "user_message": user_message_v2(payload)})
        requests_by_condition[cond] = records
    if len(candidate_ids) != 39:
        raise ValueError(f"Expected 39 distinct candidates, got {len(candidate_ids)}")
    schedule = []
    max_n = max(map(len, requests_by_condition.values()))
    for i in range(max_n):
        for cond in CONDITIONS:
            if i < len(requests_by_condition[cond]):
                schedule.append(requests_by_condition[cond][i])
    # Gold is intentionally absent from the sent message; it is only compared locally.
    expected = sorted({c["position"] for it in requests_by_condition["baseline"] for c in it["payload"]["candidates"]})
    coverage = {"expected_boundaries": expected,
                "gold_exact": [g for g in gold["boundary_positions"] if g in expected],
                "gold_within_3": [g for g in gold["boundary_positions"] if any(abs(g - p) <= 3 for p in expected)]}
    return {"system": system, "schema": build_schema(), "rows": rows, "gold": gold, "coverage": coverage,
            "candidate_ids": candidate_ids, "schedule": schedule, "requests_by_condition": requests_by_condition,
            "paths": {"source": source_path, "gold": gold_path, "meetings": meetings_path,
                      "selection": selection_path, "bundles": bundle_dir}}


def expected_boundary(char, rows):
    """Snap an embedding candidate character to the nearest utterance start (ties go later)."""
    return min(rows[1:], key=lambda r: (abs(r["start_char"] - char), -r["start_char"]))["position"]


def user_message_v2(payload):
    """Plain-text input: the chunk's source text with utterance numbers and the expected boundaries."""
    chunk = "\n".join(f"[{u['position']}] {u['text']}" for u in payload["visible_utterances"])
    boundaries = "\n".join(f"- 후보 {c['candidate_id']}: [{c['position'] - 1}] 다음, [{c['position']}] 앞"
                           for c in payload["candidates"])
    return f"## 청크 원문\n{chunk}\n\n## 예상 경계\n{boundaries}"


def validate_result(result, payload):
    expected = {c["candidate_id"]: c for c in payload["candidates"]}
    judgments = result.get("judgments")
    if not isinstance(judgments, list) or len(judgments) != len(expected):
        raise ValueError("wrong number of candidate judgments")
    seen = set()
    visible_positions = {u["position"] for u in payload["visible_utterances"]}
    for j in judgments:
        cid = j["candidate_id"]
        if cid not in expected or cid in seen:
            raise ValueError(f"unknown or duplicate candidate_id: {cid}")
        seen.add(cid)
        checks = j["checks"]
        if not all(type(checks.get(k)) is bool for k in ("subject_change", "closure_signal", "independent_summary")):
            raise ValueError(f"invalid checks for candidate {cid}")
        if j["shift_result"] != (j["shift_type"] == "new_agenda") or j["verdict"] != ("O" if j["shift_result"] else "X"):
            raise ValueError(f"decision rule mismatch for candidate {cid}")
        if j["shift_type"] == "new_agenda" and not any(checks.values()):
            raise ValueError(f"new_agenda without any positive check for candidate {cid}")
        if j["shift_type"] == "no_shift" and any(checks.values()):
            raise ValueError(f"no_shift with a positive check for candidate {cid}")
        if not (j["before_subject"].strip() and j["after_subject"].strip()):
            raise ValueError(f"empty subject for candidate {cid}")
        cites = citations(j["evidence"])
        # The input no longer states a citation count, so one in-chunk citation is enough.
        if not cites or any(x not in visible_positions for x in cites):
            raise ValueError(f"evidence citations missing or outside visible speech for candidate {cid}")
    if seen != set(expected):
        raise ValueError("candidate judgment set mismatch")


def api_call(key, system, schema, message):
    body = {"model": MODEL, "messages": [
        {"role": "system", "content": system},
        {"role": "user", "content": message},
    ], "response_format": schema, "reasoning_effort": CONFIG["reasoning_effort"],
        "max_completion_tokens": CONFIG["max_completion_tokens"]}
    request = urllib.request.Request(CONFIG["base_url"], data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                                     method="POST")
    attempts = 0
    while True:
        attempts += 1
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=240) as response:
                raw_bytes = response.read()
            return json.loads(raw_bytes), time.perf_counter() - started, attempts
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace")
            transient = exc.code in (408, 409, 429) or 500 <= exc.code < 600
            if not transient or attempts >= 3:
                raise RuntimeError(f"HTTP {exc.code}: {body_text[:1000]}") from exc
            time.sleep(min(2 ** attempts, 10))


def load_completed(call_dir, request_id, payload):
    """Return (raw, result, error) for a request already dispatched, or None if never sent.

    Failed requests are not re-sent: format errors stay failures as the plan requires.
    """
    response_path = call_dir / f"{request_id}.response.json"
    state_path = call_dir / f"{request_id}.state.json"
    if response_path.exists():
        raw = read_json(response_path)
        result, error = check_response(raw["response"], payload)
        return raw, result, error
    if state_path.exists():
        state = read_json(state_path)
        if state.get("status") == "dispatching":
            raise RuntimeError(f"{request_id} has an ambiguous prior dispatch; inspect before retrying")
        if state.get("status") == "failed":
            return None, None, state.get("error", "failed")
    return None


def check_response(response, payload):
    try:
        if not response.get("choices") or response["choices"][0].get("finish_reason") != "stop":
            raise ValueError(f"Incomplete response: {response.get('choices', [{}])[0].get('finish_reason')}")
        result = json.loads(response["choices"][0]["message"]["content"])
        validate_result(result, payload)
        return result, None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {str(exc)[:1000]}"


def call_all(exp):
    key = load_env()
    call_dir = OUT / "calls"
    call_dir.mkdir(exist_ok=True)
    usage_records, failures, valid = [], {}, {}
    manifest_path = OUT / "manifest.json"
    manifest = read_json(manifest_path)
    manifest["status"] = "running"
    write_json(manifest_path, manifest)
    for i, item in enumerate(exp["schedule"], 1):
        payload = item["payload"]
        request_id = f"{payload['condition']}__{payload['request_id']}"
        saved = load_completed(call_dir, request_id, payload)
        if saved:
            raw, result, error = saved
            if raw:  # re-validation under changed rules can flip the outcome; keep the state file in sync
                write_json(call_dir / f"{request_id}.state.json",
                           {"status": "failed" if error else "received", "request_id": request_id,
                            "seconds": raw["seconds"], "attempts": raw["attempts"], **({"error": error} if error else {})})
        else:
            write_json(call_dir / f"{request_id}.state.json", {"status": "dispatching", "request_id": request_id,
                                                                "started_at": datetime.now(timezone.utc).isoformat()})
            try:
                response, seconds, attempts = api_call(key, exp["system"], exp["schema"], item["user_message"])
            except Exception as exc:
                raw, result, error = None, None, f"{type(exc).__name__}: {str(exc)[:1000]}"
            else:
                # Save the raw response before validation so failures stay inspectable.
                raw = {"request_id": request_id, "condition": payload["condition"], "payload": payload,
                       "user_message": item["user_message"],
                       "response": response, "seconds": seconds, "attempts": attempts,
                       "model_requested": MODEL, "system_prompt_sha256": sha256(exp["system"].encode())}
                write_json(call_dir / f"{request_id}.response.json", raw)
                result, error = check_response(response, payload)
            state = {"status": "failed" if error else "received", "request_id": request_id}
            if raw:
                state.update(seconds=raw["seconds"], attempts=raw["attempts"])
            if error:
                state["error"] = error
            write_json(call_dir / f"{request_id}.state.json", state)
        if raw:
            usage_records.append({"request_id": request_id, "condition": payload["condition"],
                                  **response_usage(raw["response"]), "seconds": raw["seconds"],
                                  "attempts": raw["attempts"], "valid": error is None})
        if error:
            failures[request_id] = {"error": error, "usage_known": raw is not None}
            print(f"{i}/{len(exp['schedule'])} {request_id}: FAILED {error[:200]}", flush=True)
        else:
            valid[request_id] = result
            print(f"{i}/{len(exp['schedule'])} {request_id}: {len(result['judgments'])} candidate judgments", flush=True)
        write_json(OUT / "usage.json", {"calls": usage_records, "summary": summarize_usage(usage_records),
                                         "failed_requests": failures})
        manifest.update(completed_requests=len(valid), failed_requests=failures)
        write_json(manifest_path, manifest)
    manifest["status"] = "partial" if failures else "complete"
    manifest["completed_at"] = datetime.now(timezone(timedelta(hours=9))).isoformat()
    write_json(manifest_path, manifest)
    for cond in CONDITIONS:
        records = []
        for item in exp["requests_by_condition"][cond]:
            rid = f"{cond}__{item['payload']['request_id']}"
            record = {"request_id": item["payload"]["request_id"], "status": "failed" if rid in failures else "valid",
                      "judgments": valid[rid]["judgments"] if rid in valid else []}
            if rid in failures:
                record["error"] = failures[rid]["error"]
            records.append(record)
        write_json(RESULTS_DIR / cond / "candidate_judgments.json", records)
    evaluate(exp)


def response_usage(response):
    u = response.get("usage") or {}
    details = u.get("completion_tokens_details") or {}
    prompt_details = u.get("prompt_tokens_details") or {}
    return {"model": response.get("model"), "input_tokens": u.get("prompt_tokens"),
            "output_tokens": u.get("completion_tokens"), "reasoning_tokens": details.get("reasoning_tokens", 0),
            "cached_input_tokens": prompt_details.get("cached_tokens", 0)}


def summarize_usage(records):
    inp = sum(x.get("input_tokens") or 0 for x in records)
    out = sum(x.get("output_tokens") or 0 for x in records)
    return {"requests": len(records), "input_tokens": inp, "output_tokens": out,
            "reasoning_tokens": sum(x.get("reasoning_tokens") or 0 for x in records),
            "retries": sum(max(0, x["attempts"] - 1) for x in records),
            "seconds_total": round(sum(x["seconds"] for x in records), 2),
            "estimated_cost_usd": round((inp * PRICING["input"] + out * PRICING["output"]) / PRICING["per_tokens"], 6)}


def one_to_one_scores(pred, truth, tolerance=0):
    # Maximum cardinality matching in sorted one-dimensional positions.
    p, t, i, j = sorted(pred), sorted(truth), 0, 0
    matched = 0
    while i < len(p) and j < len(t):
        if abs(p[i] - t[j]) <= tolerance:
            matched += 1; i += 1; j += 1
        elif p[i] < t[j] - tolerance:
            i += 1
        else:
            j += 1
    precision = matched / len(p) if p else 0.0
    recall = matched / len(t) if t else 0.0
    return {"tp": matched, "fp": len(p)-matched, "fn": len(t)-matched,
            "precision": precision, "recall": recall,
            "f1": (2*precision*recall/(precision+recall)) if precision+recall else 0.0}


def build_prediction(exp, cond, results):
    """Convert per-request judgments into the test/llm_golden/<meeting>.json layout.

    Golden candidates are keyed by the first utterance of the proposed segment, which is the
    expected boundary shown to the model. Judgments landing on the same position become one
    candidate; a valid O wins over X. Every original judgment stays in verification.
    """
    info = {c["candidate_id"]: c for it in exp["requests_by_condition"][cond] for c in it["payload"]["candidates"]}
    by_pos = {}
    judged = set()
    for req in results:
        for j in req["judgments"]:
            judged.add(j["candidate_id"])
            c = info[j["candidate_id"]]
            support = {"candidate_id": j["candidate_id"], "source_candidate_id": c["source_candidate_id"],
                       "request_id": req["request_id"], "candidate_char": c["candidate_char"],
                       "verdict": j["verdict"], "shift_type": j["shift_type"]}
            by_pos.setdefault(c["position"], []).append((j, support))
    missing = sorted(set(info) - judged)
    failed = [r["request_id"] for r in results if r.get("status") == "failed"]
    partial_note = (f" 처리 실패 요청 {len(failed)}개({', '.join(failed)})로 알고리즘 후보 {len(missing)}개"
                    f"({', '.join(info[c]['source_candidate_id'] for c in missing)})의 판정이 없는 부분 완료 결과다."
                    if missing else "")
    candidates = []
    for pos in sorted(by_pos):
        entries = by_pos[pos]
        winners = [e for e in entries if e[0]["verdict"] == "O"] or entries
        j = min(winners, key=lambda e: e[1]["candidate_id"])[0]
        candidates.append({
            "candidate_id": len(candidates), "position": pos,
            "before_subject": j["before_subject"], "after_subject": j["after_subject"],
            "checks": j["checks"], "shift_type": j["shift_type"], "shift_result": j["shift_result"],
            "verdict": j["verdict"], "evidence": j["evidence"],
            "verification": {"checked": True, "rule": GOLDEN_RULES.get(j["shift_type"], GOLDEN_RULES["exclusion"]),
                             "conflict": len({e[0]["verdict"] for e in entries}) > 1,
                             "judgments": [e[1] for e in sorted(entries, key=lambda e: e[1]["candidate_id"])]},
        })
    boundaries = [c["position"] for c in candidates if c["shift_result"]]
    starts = [1] + boundaries
    ends = [x - 1 for x in boundaries] + [len(exp["rows"])]
    by_start = {c["position"]: c for c in candidates if c["shift_result"]}
    segments = []
    for ix, (start, end) in enumerate(zip(starts, ends), 1):
        if start in by_start:
            subject = by_start[start]["after_subject"]
            summary = f"LLM 구간 요약은 생성하지 않았다. 시작 경계 근거: {by_start[start]['evidence']}"
        else:
            # Meeting start: describe it from the first boundary's before side when one exists.
            first = by_start[boundaries[0]] if boundaries else (candidates[0] if candidates else None)
            subject = first["before_subject"] if first else "판정된 후보가 없는 구간"
            summary = "LLM 구간 요약은 생성하지 않았다. 회의 시작부터 첫 예측 경계 전까지의 구간이다."
        excluded = [c["position"] for c in candidates if not c["shift_result"] and start <= c["position"] <= end]
        segments.append({"start_utterance": start, "subject": subject, "summary": summary,
                         "segment_id": ix, "end_utterance": end, "excluded_candidate_positions": excluded})
    labels = {"baseline": "미병합(후보 좌우 300자)", "merge_900": "범위 보존 최대 900자 병합",
              "merge_1200": "범위 보존 최대 1,200자 병합"}
    return {
        "meeting_id": "TS3003c",
        "created_date": datetime.now(timezone(timedelta(hours=9))).date().isoformat(),
        "annotation_basis": "input/criteria.md 기준을 옮긴 시스템 프롬프트(chunking_method/llm_ts3003c/system_prompt.txt)",
        "source_transcript": "input/TS3003c.txt",
        "utterance_mapping_source": "meetings/TS3003c.json",
        "utterance_count": len(exp["rows"]),
        "position_definition": "메타데이터 3행과 빈 줄을 제외한 발화의 1부터 시작하는 순서. position은 새 구간의 첫 발화이며 경계는 position-1과 position 사이이다. 후보 position은 임베딩 후보 문자 위치에서 가장 가까운 발화 시작점(같은 거리면 뒤쪽)이며, LLM에 예상 경계로 제시한 위치와 같다. LLM은 이 위치의 O/X만 판정했다.",
        "annotation_scope": f"{MODEL}({CONFIG['reasoning_effort']})가 {labels[cond]} 조건으로 판정한 결과다. BAAI/bge-m3, 목표 윈도우 {CONFIG['window_chars']}자, 오버랩 {CONFIG['overlap_chars']}자, 문장 보존, depth ≥ {CONFIG['candidate_depth_threshold']:.2f}로 선출한 알고리즘 후보 {len(info)}개 중 {len(info) - len(missing)}개를 판정했으며 같은 position의 판정은 한 후보로 합쳤다. boundary_positions에 없는 위치에는 경계를 부여하지 않는다.{partial_note}",
        "segment_note": "구간은 채택 경계로 나눈 연속 범위다. subject는 시작 경계 후보의 after_subject(첫 구간은 첫 경계의 before_subject)이며, LLM에 구간 요약을 요청하지 않았으므로 summary에는 시작 경계 근거만 옮겼다.",
        "verification_note": "출력 계약(후보 ID 완전성, 판정 규칙, 청크 안 발화 번호로 1개 이상 근거 인용)을 자동 검사했다. 골든은 근거 인용 2개 이상을 요구하지만 이번 입력에는 인용 개수 지시가 없으므로 1개 이상만 검사했다. 검사에 실패한 응답은 보정하지 않고 결과에서 제외했으며 실패로 기록했다. 정답은 API 요청에 넣지 않았고 호출 완료 후 평가에만 사용했다. 의미 판정의 사람 검수는 수행하지 않았다." + partial_note,
        "boundary_positions": boundaries, "segments": segments, "candidates": candidates,
    }


def validate_golden_format(obj, utterance_count):
    """Python port of the structural checks in test/llm_golden/validate.ps1."""
    previous, accepted = 1, []
    for c in obj["candidates"]:
        assert previous < c["position"] <= utterance_count, f"candidate order/range: {c['position']}"
        previous = c["position"]
        assert c["before_subject"] and c["after_subject"] and c["evidence"], "missing description"
        assert all(type(c["checks"][k]) is bool for k in ("subject_change", "closure_signal", "independent_summary"))
        is_boundary = c["shift_type"] == "new_agenda"
        assert c["shift_result"] is is_boundary and c["verdict"] == ("O" if is_boundary else "X"), "decision rule"
        if is_boundary:
            assert any(c["checks"].values()), "new_agenda without a true check"
            accepted.append(c["position"])
        if c["shift_type"] == "no_shift":
            assert not any(c["checks"].values()), "no_shift with a true check"
        cites = citations(c["evidence"])
        # validate.ps1 requires two citations; the O/X input does not ask for a count, so one suffices.
        assert cites and all(1 <= x <= utterance_count for x in cites), "citations"
        assert c["verification"]["checked"] is True
    assert accepted == obj["boundary_positions"], "boundary list"
    assert len(obj["segments"]) == len(accepted) + 1, "segment count"
    next_start = 1
    for s in obj["segments"]:
        assert s["start_utterance"] == next_start and s["end_utterance"] >= s["start_utterance"], "segment gap/overlap"
        assert s["subject"] and s["summary"], "segment description"
        excluded = [c["position"] for c in obj["candidates"] if not c["shift_result"]
                    and s["start_utterance"] <= c["position"] <= s["end_utterance"]]
        assert excluded == s["excluded_candidate_positions"], "excluded candidate link"
        next_start = s["end_utterance"] + 1
    assert next_start == utterance_count + 1, "last segment range"


def evaluate(exp):
    gold = exp["gold"]
    all_eval = {}
    gold_set = set(gold["boundary_positions"])
    for cond in CONDITIONS:
        results = read_json(RESULTS_DIR / cond / "candidate_judgments.json")
        pred_obj = build_prediction(exp, cond, results)
        validate_golden_format(pred_obj, len(exp["rows"]))
        write_json(RESULTS_DIR / cond / "TS3003c.json", pred_obj)
        predicted_pos = pred_obj["boundary_positions"]
        scores = {str(t): one_to_one_scores(predicted_pos, sorted(gold_set), t) for t in (0, 3)}
        failed = [r["request_id"] for r in results if r.get("status") == "failed"]
        all_eval[cond] = {"settings": {**CONFIG, "model": MODEL}, "requests": len(results),
                          "status": "partial" if failed else "complete", "failed_requests": failed,
                          "candidate_judgments": sum(len(r["judgments"]) for r in results),
                          "golden_candidates": len(pred_obj["candidates"]),
                          "conflicts": [c["position"] for c in pred_obj["candidates"] if c["verification"]["conflict"]],
                          "predicted_boundary_uids": [f"u{p:04d}" for p in predicted_pos], "scores": scores}
    usage = read_json(OUT / "usage.json")
    write_json(OUT / "evaluation.json", {"meeting_id": "TS3003c", "gold_boundary_count": len(gold_set),
                                          "gold_positions": sorted(gold_set), "expected_boundary_coverage": exp["coverage"],
                                          "conditions": all_eval, "usage": usage["summary"]})
    write_report(all_eval, usage["summary"], exp["coverage"])


def write_report(results, usage, coverage):
    lines = ["# TS3003c 경계 후보 LLM 검증 실험 결과", "",
             "BAAI/bge-m3 · 문장 보존 목표 윈도우 200자 / 오버랩 100자 · depth≥0.10 후보 39개. 동일 프롬프트와 gpt-6-luna(low)로 미병합, 최대 900자, 최대 1,200자 청크를 비교했다. 입력은 시스템 프롬프트, 발화 번호를 붙인 청크 원문, 예상 경계(후보 문자 위치에서 가장 가까운 발화 시작점)이며 LLM은 예상 경계마다 O/X만 판정했다. 정답은 API에 보내지 않고 로컬 평가에만 사용했다.", "",
             "## 경계 정확도", "",
             f"주 지표는 ±3발화 허용 일대일 대응이다. 예상 경계 위치를 그대로 쓰므로 정확한 발화 일치는 경계 위치 추출 성능이 아니라 참고값이다. 예상 경계가 정답과 정확히 일치할 수 있는 정답은 {len(coverage['gold_exact'])}/11개, ±3 이내는 {len(coverage['gold_within_3'])}/11개다(재현율 상한).", "",
             "| 조건 | 상태 | 윈도우 | 오버랩 | 요청(실패) | 판정 후보 | 예측 경계 | ±3 P/R/F1 | 정확 P/R/F1(참고) |", "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for cond, r in results.items():
        exact, near = r["scores"]["0"], r["scores"]["3"]
        status = "부분 완료" if r["failed_requests"] else "완료"
        lines.append(f"| {cond} | {status} | 200자 | 100자 | {r['requests']}({len(r['failed_requests'])}) | {r['candidate_judgments']} | {len(r['predicted_boundary_uids'])} | {near['precision']:.1%}/{near['recall']:.1%}/{near['f1']:.1%} | {exact['precision']:.1%}/{exact['recall']:.1%}/{exact['f1']:.1%} |")
    lines += ["", "근거 인용은 `[번호]`, `[uNNNN]`과 함께 범위 표기 `[81-89]`도 인정했다(범위는 양 끝 번호가 청크 안에 있어야 한다). 범위 인정은 v2 1차 실행에서 범위 인용 2개 요청이 실패한 뒤 사용자 결정으로 추가한 사후 규칙이며, 추가 호출 없이 저장된 응답을 다시 검증했다. 응답 원문은 수정하지 않았다."]
    redispatched = sorted(p.name.split(".response.")[0] for p in (OUT / "calls").glob("*.response.attempt*.json"))
    if redispatched:
        lines += ["", f"재호출 요청: {', '.join(redispatched)}. 검증에 실패한 요청을 사용자 결정으로 다시 보냈다. 이전 응답은 `calls/*.attempt*.json`에 보존했으며 그 사용량은 사용량 집계에 포함하지 않았다."]
    failed = [f"{cond}/{rid}" for cond, r in results.items() for rid in r["failed_requests"]]
    if failed:
        lines += ["", f"처리 실패 요청: {', '.join(failed)}. 해당 요청의 후보는 판정 없이 남았으므로 부분 완료 조건의 지표는 완료 조건과 직접 비교할 수 없다. 실패 내용은 `calls/*.state.json`과 `usage.json`의 `failed_requests`에 있다."]
    lines += ["", "## 사용량", "", f"호출 {usage['requests']}회, 재시도 {usage['retries']}회, 입력 {usage['input_tokens']:,} 토큰, 출력 {usage['output_tokens']:,} 토큰, 추론 {usage['reasoning_tokens']:,} 토큰, 누적 지연 {usage['seconds_total']}초. 단가 기반 추정 비용은 ${usage['estimated_cost_usd']:.6f} USD이며 실제 청구서가 아니다.", "",
              "## 파일", "", "- `results/<조건>/TS3003c.json`: `test/llm_golden/TS3003c.json`과 같은 최상위 키·`segments`·`candidates` 구조이며 골든 검증 규칙을 통과했다. 윈도우·오버랩은 `annotation_scope`와 `evaluation.json`에 기록했다.",
              "- `results/<조건>/candidate_judgments.json`: 요청별 후보 39개 판정 원본, `calls/`: API 요청·원응답·사용량을 포함한 원자료.",
              "- `system_prompt.txt`, `manifest.json`: 시스템 프롬프트 원문과 입력·설정 해시.", "",
              "## 제한", "", "단일 회의의 단회 탐색 실험이며 전체 회의에 대한 일반화 성능을 확정하지 않는다. 경계 위치는 임베딩 후보에서 정해지므로 LLM은 위치를 보정하지 않는다. 이전 선택지 방식 실행 결과는 `../v1_boundary_options/`에 보존했다.", ""]
    (OUT / "report.md").write_text("\n".join(lines), encoding="utf-8")


def prepare():
    exp = build_experiment()
    load_env()
    system = exp["system"]
    (OUT / "system_prompt.txt").write_text(system, encoding="utf-8")
    sent = [{"request_id": x["payload"]["request_id"], "condition": x["payload"]["condition"],
             "user_message": x["user_message"]} for x in exp["schedule"]]
    write_json(OUT / "sent_payloads.json", sent)
    manifest = {
        "status": "prepared", "created_at": datetime.now(timezone(timedelta(hours=9))).isoformat(),
        "model": MODEL, "settings": CONFIG, "pricing": PRICING,
        "input_sha256": sha256(exp["paths"]["source"].read_bytes()),
        "gold_sha256_local_only": sha256(exp["paths"]["gold"].read_bytes()),
        "selection_sha256": sha256(exp["paths"]["selection"].read_bytes()),
        "system_prompt_sha256": sha256(system.encode()),
        "bundle_sha256": {c: sha256((exp["paths"]["bundles"] / f).read_bytes()) for c, f in CONDITIONS.items()},
        "counts": {c: {"requests": len(exp["requests_by_condition"][c]), "candidates": 39,
                        "context_chars": sum(len(i["request"]["context"]) for i in exp["requests_by_condition"][c])}
                   for c in CONDITIONS},
        "schedule_requests": len(sent), "gold_in_api_payload": False,
        "input_contract": "system prompt + numbered chunk source text + expected boundaries (nearest utterance start to each embedding candidate)",
        "output_contract": "per request: one O/X judgment per expected boundary in the gold candidate fields; no position adjustment",
        "expected_boundary_coverage": exp["coverage"],
        "result_format": "results/<condition>/TS3003c.json in the test/llm_golden/TS3003c.json layout",
        "meetings_sha256": sha256(exp["paths"]["meetings"].read_bytes()),
    }
    write_json(OUT / "manifest.json", manifest)
    chars = sum(len(x["user_message"]) for x in sent) + len(system) * len(sent)
    estimate = {"input_chars_including_system": chars, "approx_input_tokens_chars_div_2": (chars + 1) // 2,
                "max_output_tokens": len(sent) * CONFIG["max_completion_tokens"],
                "max_cost_usd_at_output_cap": round(len(sent) * CONFIG["max_completion_tokens"] * PRICING["output"] / PRICING["per_tokens"], 4)}
    write_json(OUT / "preflight_estimate.json", estimate)
    print(json.dumps({"status": "prepared", "request_count": len(sent), "conditions": manifest["counts"],
                      "candidate_ids": len(exp["candidate_ids"]), "expected_boundary_coverage": exp["coverage"],
                      "gold_in_api_payload": False, "api_key_present": True,
                      "estimate": estimate}, ensure_ascii=True))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare", action="store_true")
    args = parser.parse_args()
    if args.prepare:
        prepare()
    else:
        exp = build_experiment()
        call_all(exp)
