"""Run a baseline experiment in a new directory, preserving its inputs and prompts."""
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from langgraph.graph import StateGraph, START, END

from followup.adapters.parsers.transcript import read_transcript
from followup.analysis.baseline import ACTION_FORMAT, MERGE_PROMPT, parse_actions, split_chunks

BACKEND = Path(__file__).resolve().parents[3]


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def analyze_meeting(source, output, model, call, metadata=None, chunk_size=1000):
    source, output = Path(source), Path(output)
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    manifest = {"status": "실행 중", "model": model, "chunk_size": chunk_size,
                "overlap": 0, "metadata": metadata or {}, "source": str(source.resolve()),
                "started_at": datetime.now(timezone.utc).isoformat(), "stage": "input"}
    def prepare(state):
        write_json(output / "run.json", manifest)
        text = read_transcript(source)
        manifest["source_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
        (output / "transcript.txt").write_text(text, encoding="utf-8")
        prompts = {
            "summary": (BACKEND / "summary_base.md").read_text(encoding="utf-8"),
            "actions": (BACKEND / "action-extract_base.md").read_text(encoding="utf-8") + "\n" + ACTION_FORMAT,
            "merge": MERGE_PROMPT,
        }
        write_json(output / "prompts.json", prompts)
        chunks = split_chunks(text, chunk_size)
        write_json(output / "chunks.json", chunks)
        context = json.dumps(metadata or {}, ensure_ascii=False)
        return {"chunks": chunks, "prompts": prompts, "context": context,
                "index": 0, "summaries": [], "actions": []}

    def summarize(state):
        chunk = state["chunks"][state["index"]]
        prompts, context = state["prompts"], state["context"]
        manifest["stage"] = f"summary:{chunk['id']}"
        user = f"메타데이터: {context}\n\n전사록:\n{chunk['text']}"
        summary = call(prompts["summary"], user)
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError("Empty summary")
        summaries = state["summaries"] + [summary]
        (output / f"chunk_{chunk['id']:04d}_summary.md").write_text(summary, encoding="utf-8")
        return {**state, "summaries": summaries}

    def extract_actions(state):
        chunk = state["chunks"][state["index"]]
        prompts, context = state["prompts"], state["context"]
        user = f"메타데이터: {context}\n\n전사록:\n{chunk['text']}"
        manifest["stage"] = f"actions:{chunk['id']}"
        raw = call(prompts["actions"], user)
        (output / f"chunk_{chunk['id']:04d}_actions.txt").write_text(raw, encoding="utf-8")
        actions = state["actions"] + parse_actions(raw)
        return {**state, "actions": actions, "index": state["index"] + 1}

    def merge_summary(state):
        prompts, context, summaries = state["prompts"], state["context"], state["summaries"]
        manifest["stage"] = "merge"
        # shortcut: context overflow is reported by the provider, add local counting only if needed.
        summary = call(prompts["merge"], f"메타데이터: {context}\n\n" + "\n\n".join(summaries))
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError("Empty final summary")
        return {**state, "summary": summary}

    def save_results(state):
        summary, actions = state["summary"], state["actions"]
        (output / f"{source.stem}_요약.md").write_text(summary, encoding="utf-8")
        (output / f"{source.stem}_액션.jsonl").write_text(
            "".join(json.dumps(a, ensure_ascii=False) + "\n" for a in actions), encoding="utf-8")
        manifest.update(status="성공", stage="complete", action_count=len(actions))
        return state

    graph = StateGraph(dict)
    for name, node in [("prepare", prepare), ("summarize", summarize),
                       ("extract_actions", extract_actions), ("merge_summary", merge_summary),
                       ("save_results", save_results)]:
        graph.add_node(name, node)
    graph.add_edge(START, "prepare")
    graph.add_edge("prepare", "summarize")
    graph.add_edge("summarize", "extract_actions")
    graph.add_conditional_edges("extract_actions", lambda state:
        "summarize" if state["index"] < len(state["chunks"]) else "merge_summary",
        ["summarize", "merge_summary"])
    graph.add_edge("merge_summary", "save_results")
    graph.add_edge("save_results", END)
    try:
        # Two nodes per chunk; allow long meetings beyond LangGraph's default step limit.
        graph.compile().invoke({}, config={"recursion_limit": max(100, source.stat().st_size // max(chunk_size, 1) * 2 + 20)})
    except Exception as error:
        # Do not persist exception text: provider responses can contain credentials or source text.
        manifest.update(status="실패", error_type=type(error).__name__)
    finally:
        manifest["elapsed_seconds"] = round(time.monotonic() - started, 3)
        manifest["calls"] = getattr(call, "calls", [])
        write_json(output / "run.json", manifest)
    return manifest

