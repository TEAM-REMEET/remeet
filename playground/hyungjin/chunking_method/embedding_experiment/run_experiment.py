"""Run the planned embedding/window/candidate comparison without LLM calls."""
import csv
import gc
import hashlib
import itertools
import json
import math
import os
import re
import sys
import time
import traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import psutil
import torch
# This run can only use local weights: model downloads are unavailable in this environment.
if os.environ.get("CHUNKING_ALLOW_HF_DOWNLOAD") == "1":
    os.environ.pop("HF_HUB_OFFLINE", None)
else:
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
from huggingface_hub import try_to_load_from_cache

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PIPELINE_DIR = ROOT / "embedding-test"
sys.path.insert(0, str(PIPELINE_DIR))
import pipeline as p
from sentence_windows import build_sentence_windows

MODELS = {
    "BAAI/bge-m3": "BGE-m3",
    "Gameselo/STS-multilingual-mpnet-base-v2": "STS multilingual MPNet",
    "jinaai/jina-embeddings-v5-text-small-text-matching": "Jina embeddings v5 small (text-matching)",
    "alphabrothers/alpha-sts-v0": "Alpha STS",
}
WINDOWS = (200, 300, 400, 500)
OVERLAPS = (50, 100, 150)
METHODS = ("depth", "quantile")
THRESHOLDS = (0.02, 0.05, 0.10)
RADII = (200, 300)
BATCH_SIZE = 32
DEPTH_RADIUS = 3
torch.set_num_threads(min(16, torch.get_num_threads()))


LOADED_MODELS = {}
MODEL_LOAD_SECONDS = {}


def load_model(model):
    if model in LOADED_MODELS:
        return LOADED_MODELS[model]
    from sentence_transformers import SentenceTransformer
    started = time.perf_counter()
    cache_folder = None if model == "BAAI/bge-m3" else str(HERE / "hf_cache" / "hub")
    result = SentenceTransformer(model, device="cpu", cache_folder=cache_folder)
    MODEL_LOAD_SECONDS[model] = time.perf_counter() - started
    LOADED_MODELS[model] = result
    return result


def embed_windows(model, texts):
    encoder = load_model(model)
    started = time.perf_counter()
    token_counts = []
    for start in range(0, len(texts), BATCH_SIZE):
        batch = encoder.tokenizer(texts[start:start + BATCH_SIZE], truncation=False,
                                  add_special_tokens=True)["input_ids"]
        token_counts.extend(len(ids) for ids in batch)
    too_long = [(i, n) for i, n in enumerate(token_counts) if n > int(encoder.max_seq_length)]
    if too_long:
        raise ValueError(f"Input token limit {encoder.max_seq_length} exceeded at windows {too_long[:10]}")
    vectors = encoder.encode(texts, batch_size=BATCH_SIZE, show_progress_bar=False,
        convert_to_numpy=True, normalize_embeddings=False,
        **({"task": "text-matching"} if model == "jinaai/jina-embeddings-v5-text-small" else {}))
    info = {"model": model, "task": "text-matching" if model.startswith("jinaai/") else None,
        "provider": "huggingface", "device": str(encoder.device), "batch_size": BATCH_SIZE,
        "input_token_limit": int(encoder.max_seq_length), "input_token_counts": token_counts,
        "dimensions": int(vectors.shape[1]), "hf_revision": getattr(encoder[0].auto_model.config, "_commit_hash", None),
        "model_load_seconds": MODEL_LOAD_SECONDS.get(model, 0.0), "total_seconds": time.perf_counter() - started}
    return np.asarray(vectors, dtype=np.float32), info


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    tmp.replace(path)


def write_csv(path, rows, fields=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if fields is None:
        fields = list(rows[0]) if rows else []
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def load_dataset():
    records, manifest = [], []
    inputs = sorted((ROOT / "test" / "input").glob("*.txt"))
    assert len(inputs) == 20, f"Expected 20 transcripts; found {len(inputs)}"
    for path in inputs:
        raw = path.read_bytes()
        source, blocks, _ = p.prepare_input(raw, path.name)
        gold_path = ROOT / "test" / "llm_golden" / f"{path.stem}.json"
        gold_raw = gold_path.read_bytes()
        gold = json.loads(gold_raw)
        if len(blocks) != gold.get("utterance_count"):
            raise ValueError(f"{path.stem}: utterance count mismatch")
        positions = gold.get("boundary_positions")
        if not isinstance(positions, list) or len(positions) != len(set(positions)):
            raise ValueError(f"{path.stem}: boundary_positions missing or duplicated")
        if any(type(n) is not int or not 2 <= n <= len(blocks) for n in positions):
            raise ValueError(f"{path.stem}: boundary position outside utterances")
        boundary_chars = {f"u{n:04d}": blocks[n - 1]["start_char"] for n in positions}
        if list(boundary_chars.values()) != sorted(boundary_chars.values()):
            raise ValueError(f"{path.stem}: gold boundary positions are not ordered")
        start, end = blocks[0]["start_char"], blocks[-1]["end_char"]
        records.append({"meeting": path.stem, "source": source, "blocks": blocks,
                        "body_start": start, "body_end": end, "gold": boundary_chars,
                        "source_sha256": digest(source.encode("utf-8")),
                        "input_sha256": digest(raw), "gold_sha256": digest(gold_raw)})
        manifest.append({"meeting": path.stem, "input": str(path.relative_to(ROOT)),
                         "gold": str(gold_path.relative_to(ROOT)), "input_sha256": digest(raw),
                         "source_sha256": digest(source.encode("utf-8")), "gold_sha256": digest(gold_raw),
                         "utterances": len(blocks), "body_chars": end - start,
                         "gold_boundaries": len(boundary_chars),
                         "boundary_positions": positions,
                         "boundary_chars": boundary_chars})
    assert sum(len(x["gold"]) for x in records) == 153
    return records, manifest


def candidate_rows(similarities, windows):
    result = []
    for i, sim in enumerate(similarities):
        left = max(similarities[max(0, i - DEPTH_RADIUS):i + 1])
        right = max(similarities[i:min(len(similarities), i + DEPTH_RADIUS + 1)])
        lo, hi = windows[i + 1]["start_char"], windows[i]["end_char"]
        result.append({"pair_index": i, "left_window_index": i, "right_window_index": i + 1,
                       "candidate_char": (lo + hi) // 2, "region_start_char": lo,
                       "region_end_char": hi, "similarity": float(sim),
                       "depth": max(0.0, float(left + right - 2 * sim))})
    return result


def select(scores, method, threshold):
    values = np.asarray([r["similarity"] for r in scores], dtype=np.float64)
    if not len(values):
        return []
    if method == "quantile":
        cutoff = float(np.quantile(values, threshold))
        return [r for r, value in zip(scores, values) if value <= cutoff]
    minima = np.zeros(len(values), dtype=bool)
    if len(values) >= 2:
        minima[0], minima[-1] = values[0] < values[1], values[-1] < values[-2]
    if len(values) >= 3:
        minima[1:-1] = ((values[1:-1] <= values[:-2]) & (values[1:-1] <= values[2:])
                        & ((values[1:-1] < values[:-2]) | (values[1:-1] < values[2:])))
    selected = [r for i, r in enumerate(scores) if minima[i] and r["depth"] >= threshold]
    return sorted(selected, key=lambda r: (-r["depth"], r["similarity"], r["pair_index"]))


def dedupe_positions(rows, body_start, body_end):
    return sorted({int(r["candidate_char"]): r for r in rows
                   if body_start < int(r["candidate_char"]) < body_end}.values(),
                  key=lambda r: r["candidate_char"])


def measure(record, candidates, radius):
    gold = record["gold"]
    points = [int(c["candidate_char"]) for c in candidates]
    hit = {uid: min((abs(pos - candidate) for candidate in points), default=None)
           for uid, pos in gold.items()}
    covered = {uid: distance is not None and distance <= radius for uid, distance in hit.items()}
    false_candidates = sum(not any(abs(pos - pnt) <= radius for pos in gold.values()) for pnt in points)
    return {"gold_total": len(gold), "gold_covered": sum(covered.values()),
            "coverage": sum(covered.values()) / len(gold) if gold else 0.0,
            "candidate_count": len(points), "candidate_density_per_1000_chars":
                len(points) * 1000 / (record["body_end"] - record["body_start"]),
            "candidates_without_gold": false_candidates,
            "candidate_without_gold_rate": false_candidates / len(points) if points else 0.0,
            "mean_nearest_candidate_distance":
                (sum(v for v in hit.values() if v is not None) / len(hit)
                 if points and hit else None),
            "nearest_candidate_distances": hit, "covered_gold_ids": sorted(uid for uid, ok in covered.items() if ok)}


def self_check():
    # Inclusive radius, duplicate coordinates, multiple gold boundaries, and no candidates.
    record = {"body_start": 0, "body_end": 1000, "gold": {"u2": 100, "u3": 180}}
    assert measure(record, [{"candidate_char": 300}], 200)["gold_covered"] == 2
    assert measure(record, [{"candidate_char": 381}], 200)["gold_covered"] == 0
    assert measure(record, [], 300)["mean_nearest_candidate_distance"] is None
    assert len(dedupe_positions([{"candidate_char": 5}, {"candidate_char": 5}], 0, 10)) == 1
    assert select([{"pair_index": 0, "similarity": 0.5, "depth": 0.0},
                   {"pair_index": 1, "similarity": 0.2, "depth": 0.1},
                   {"pair_index": 2, "similarity": 0.5, "depth": 0.0}], "depth", 0.1)


def config_id(model, size, overlap, split, method, threshold):
    t = f"{threshold:.2f}" if method == "depth" else f"{int(round(threshold * 100))}"
    return f"{model.replace('/', '--')}__w{size}_o{overlap}_{split}_{method}_{t}"


def main():
    self_check()
    records, dataset_rows = load_dataset()
    write_json(HERE / "dataset_manifest.json", {"created_at": datetime.now(timezone(timedelta(hours=9))).isoformat(),
        "input_root": str((ROOT / "test" / "input").resolve()),
        "gold_root": str((ROOT / "test" / "llm_golden").resolve()),
        "input_count": len(records), "gold_boundary_count": sum(len(r["gold"]) for r in records),
        "exclusions": ["criteria.md", "README.md"], "meetings": dataset_rows})
    write_csv(HERE / "dataset_manifest.csv", [{k: v for k, v in row.items() if k != "boundary_chars"}
                                                   for row in dataset_rows])
    matrix = []
    for model, (size, overlap, split, method, threshold) in itertools.product(
            MODELS, itertools.product(WINDOWS, OVERLAPS, ("sentence",), METHODS, THRESHOLDS)):
        matrix.append({"config_id": config_id(model, size, overlap, split, method, threshold),
                       "model": model, "window_chars": size, "overlap_chars": overlap,
                       "split": split, "method": method, "threshold": threshold,
                       "meeting_count": 20, "status": "pending", "error": ""})
    write_csv(HERE / "experiment_matrix.csv", matrix)
    run_rows, aggregate_rows, miss_rows = [], [], []
    model_errors = {}
    embeddings_cache = {}
    cache_root = HERE / "runs"
    write_json(HERE / "run_manifest.json", {"status": "running", "started_at": datetime.now(timezone(timedelta(hours=9))).isoformat(),
        "models": MODELS, "batch_size": BATCH_SIZE, "depth_radius": DEPTH_RADIUS, "device": "cpu",
        "jina_task": "text-matching", "llm_calls": 0, "embedding_api_calls": 0,
        "dataset_sha256": digest(json.dumps(dataset_rows, sort_keys=True, ensure_ascii=False).encode())})

    total_model_meetings = len(MODELS) * len(records) * len(WINDOWS) * len(OVERLAPS)
    finished_model_meetings = 0
    for model in MODELS:
        try:
            load_model(model)
        except Exception as exc:
            model_errors[model] = f"{type(exc).__name__}: {str(exc)[:500]}"
            print(f"MODEL UNAVAILABLE {model}: {model_errors[model]}", flush=True)
        for record in records:
            meeting = record["meeting"]
            if model_errors.get(model):
                for size, overlap, split, method, threshold in itertools.product(
                        WINDOWS, OVERLAPS, ("sentence",), METHODS, THRESHOLDS):
                    for radius in RADII:
                        run_rows.append({"config_id": config_id(model, size, overlap, split, method, threshold),
                            "model": model, "meeting": meeting, "window_chars": size,
                            "overlap_chars": overlap, "split": split, "method": method,
                            "threshold": threshold, "radius": radius, "status": "failed",
                            "error": model_errors[model]})
                finished_model_meetings += len(WINDOWS) * len(OVERLAPS)
                continue
            for size, overlap, split in itertools.product(WINDOWS, OVERLAPS, ("sentence",)):
                key = (model, meeting, size, overlap, split)
                folder = cache_root / model.replace("/", "--") / meeting / f"w{size}_o{overlap}_{split}"
                scores_path, vectors_path = folder / "scores.json", folder / "embeddings.npz"
                try:
                    if scores_path.exists() and vectors_path.exists():
                        scores = json.loads(scores_path.read_text(encoding="utf-8"))
                        info = json.loads((folder / "embedding_info.json").read_text(encoding="utf-8"))
                        windows = json.loads((folder / "windows.json").read_text(encoding="utf-8"))
                        assert info["source_sha256"] == record["source_sha256"]
                    else:
                        windows = build_sentence_windows(record["blocks"], record["source"], size, overlap)
                        started = time.perf_counter()
                        vectors, info = embed_windows(model, [w["text"] for w in windows])
                        vectors = p.normalize_embeddings(vectors, len(windows))
                        if len(info.get("input_token_counts") or []) != len(windows):
                            raise ValueError("Token counts do not match window count")
                        if max(info["input_token_counts"]) > info["input_token_limit"]:
                            raise ValueError("Input token limit exceeded")
                        similarities = np.clip(np.sum(vectors[:-1] * vectors[1:], axis=1), -1, 1).tolist()
                        scores = candidate_rows(similarities, windows)
                        info.update(source_sha256=record["source_sha256"], dimensions=int(vectors.shape[1]),
                                    elapsed_seconds=time.perf_counter() - started,
                                    model_label=MODELS[model], task=info.get("task", "default"))
                        folder.mkdir(parents=True, exist_ok=True)
                        with vectors_path.open("wb") as stream:
                            np.savez_compressed(stream, embeddings=vectors)
                        write_json(folder / "windows.json", windows)
                        write_json(folder / "scores.json", scores)
                        write_json(folder / "embedding_info.json", info)
                        (folder / "failure.json").unlink(missing_ok=True)
                    embeddings_cache[key] = (windows, scores, info)
                except Exception as exc:
                    err = f"{type(exc).__name__}: {str(exc)[:500]}"
                    model_errors[model] = err if not isinstance(exc, (ValueError, AssertionError)) else None
                    # A local input-limit failure affects only this window condition; a model load/access
                    # failure affects all remaining conditions for this model.
                    global_model_error = ("LocalEntryNotFoundError" in type(exc).__name__ or
                                          "RepositoryNotFoundError" in type(exc).__name__ or
                                          "GatedRepoError" in type(exc).__name__ or
                                          "ConnectionError" in type(exc).__name__ or
                                          "OfflineModeIsEnabled" in type(exc).__name__)
                    write_json(folder / "failure.json", {"model": model, "meeting": meeting,
                        "window_chars": size, "overlap_chars": overlap, "split": split,
                        "error_type": type(exc).__name__, "error": str(exc)[:1500],
                        "traceback": traceback.format_exc(limit=4)})
                    if global_model_error:
                        model_errors[model] = err
                    if not global_model_error:
                        embeddings_cache[key] = (None, None, {"failure": err})
                for method, threshold in itertools.product(METHODS, THRESHOLDS):
                    cid = config_id(model, size, overlap, split, method, threshold)
                    cached = embeddings_cache.get(key)
                    if cached is None or cached[1] is None:
                        for radius in RADII:
                            run_rows.append({"config_id": cid, "model": model, "meeting": meeting,
                                "window_chars": size, "overlap_chars": overlap, "split": split,
                                "method": method, "threshold": threshold, "radius": radius,
                                "status": "failed",
                                "error": (cached[2].get("failure") if cached else model_errors.get(model)) or "Model unavailable"})
                        continue
                    windows, scores, info = cached
                    candidate_started = time.perf_counter()
                    chosen = dedupe_positions(select(scores, method, threshold), record["body_start"], record["body_end"])
                    radius_metrics = {}
                    for radius in RADII:
                        metrics = measure(record, chosen, radius)
                        distances = metrics.pop("nearest_candidate_distances")
                        covered_ids = metrics.pop("covered_gold_ids")
                        radius_metrics[radius] = (metrics, distances, covered_ids)
                    candidate_seconds = time.perf_counter() - candidate_started
                    memory_rss_bytes = psutil.Process().memory_info().rss
                    candidate_path = cache_root / model.replace("/", "--") / meeting / "selections" / f"w{size}_o{overlap}_{split}_{method}_{threshold:.2f}.json"
                    write_json(candidate_path, {"config_id": cid, "candidate_positions": [x["candidate_char"] for x in chosen],
                        "candidates": chosen})
                    for radius in RADII:
                        metrics, distances, covered_ids = radius_metrics[radius]
                        run_rows.append({"config_id": cid, "model": model, "meeting": meeting,
                            "window_chars": size, "overlap_chars": overlap, "split": split,
                            "method": method, "threshold": threshold, "radius": radius,
                            "status": "complete", "error": "", "window_count": len(windows),
                            "embedding_seconds": info.get("total_seconds"),
                            "model_load_seconds": info.get("model_load_seconds"),
                            "candidate_seconds": candidate_seconds,
                            "memory_rss_bytes": memory_rss_bytes, **metrics})
                        for uid, pos in record["gold"].items():
                            distance = distances[uid]
                            miss_rows.append({"config_id": cid, "model": model, "meeting": meeting,
                                "window_chars": size, "overlap_chars": overlap, "split": split,
                                "method": method, "threshold": threshold, "radius": radius,
                                "gold_id": uid, "gold_char": pos,
                                "covered": uid in covered_ids, "nearest_candidate_distance": distance,
                                "nearest_candidate_char": (pos - distance if distance is not None and
                                    any(pos - pnt == distance for pnt in [x["candidate_char"] for x in chosen]) else
                                    pos + distance if distance is not None else None)})
                finished_model_meetings += 1
                print(f"[{finished_model_meetings}/{total_model_meetings}] {model} {meeting} w{size}/o{overlap}/{split}", flush=True)
            # Write an incremental checkpoint after each meeting so interruptions remain recoverable.
            write_csv(HERE / "per_meeting.csv", run_rows)
            write_csv(HERE / "missed_boundaries.csv", miss_rows)
        # Refresh status table after every model.
        for row in matrix:
            if row["model"] == model:
                related = [x for x in run_rows if x["config_id"] == row["config_id"]]
                row["status"] = "complete" if len(related) == 40 and all(x["status"] == "complete" for x in related) else (
                    "failed" if related and all(x["status"] == "failed" for x in related) else "partial")
                row["error"] = model_errors.get(model) or next((x["error"] for x in related if x.get("error")), "")
        write_csv(HERE / "experiment_matrix.csv", matrix)
        write_json(HERE / "run_manifest.json", {"status": "running", "updated_at": datetime.now(timezone(timedelta(hours=9))).isoformat(),
            "models": MODELS, "batch_size": BATCH_SIZE, "depth_radius": DEPTH_RADIUS, "device": "cpu",
            "jina_task": "text-matching", "llm_calls": 0, "embedding_api_calls": 0,
            "model_errors": model_errors, "completed_model_meetings": finished_model_meetings,
            "dataset_sha256": digest(json.dumps(dataset_rows, sort_keys=True, ensure_ascii=False).encode())})
        LOADED_MODELS.pop(model, None)
        gc.collect()

    # All model/windows/method/radius combinations, including failed model runs, appear in the summary.
    grouped = {}
    for row in run_rows:
        grouped.setdefault((row["config_id"], row["radius"]), []).append(row)
    for config, radius in itertools.product(matrix, RADII):
        cid = config["config_id"]
        rows = grouped.get((cid, radius), [])
        good = [r for r in rows if r["status"] == "complete"]
        all_meeting_ids = {r["meeting"] for r in good}
        if not good:
            aggregate_rows.append({**{k: config[k] for k in ("config_id", "model", "window_chars", "overlap_chars", "split", "method", "threshold")},
                "radius": radius, "status": "failed", "error": config.get("error", "No successful meetings"),
                "meetings_completed": 0, "gold_total": 0, "gold_covered": 0, "overall_coverage": None,
                "mean_meeting_coverage": None, "minimum_meeting_coverage": None,
                "fully_covered_meetings": 0, "candidate_count": 0, "candidate_density_per_1000_chars": None,
                "candidate_without_gold_rate": None, "embedding_seconds": None,
                "candidate_seconds": None, "memory_rss_bytes": None,
                "pareto_dominated": None})
            continue
        total_gold = sum(r["gold_total"] for r in good)
        covered_gold = sum(r["gold_covered"] for r in good)
        total_candidates = sum(r["candidate_count"] for r in good)
        total_chars = sum(next(d["body_chars"] for d in dataset_rows if d["meeting"] == r["meeting"]) for r in good)
        misses = [r for r in miss_rows if r["config_id"] == cid and r["radius"] == radius]
        per_meeting_cov = [r["coverage"] for r in good]
        aggregate_rows.append({**{k: config[k] for k in ("config_id", "model", "window_chars", "overlap_chars", "split", "method", "threshold")},
            "radius": radius, "status": "complete" if len(good) == 20 else "partial",
            "error": config.get("error", ""), "meetings_completed": len(all_meeting_ids),
            "gold_total": total_gold, "gold_covered": covered_gold,
            "overall_coverage": covered_gold / total_gold if total_gold else None,
            "mean_meeting_coverage": float(np.mean(per_meeting_cov)),
            "minimum_meeting_coverage": min(per_meeting_cov),
            "fully_covered_meetings": sum(r["gold_covered"] == r["gold_total"] for r in good),
            "candidate_count": total_candidates,
            "candidate_density_per_1000_chars": total_candidates * 1000 / total_chars if total_chars else None,
            "candidates_without_gold": sum(r["candidates_without_gold"] for r in good),
            "candidate_without_gold_rate": sum(r["candidates_without_gold"] for r in good) / total_candidates if total_candidates else 0,
            "mean_nearest_candidate_distance": float(np.mean([r["mean_nearest_candidate_distance"] for r in good if r["mean_nearest_candidate_distance"] is not None])) if any(r["mean_nearest_candidate_distance"] is not None for r in good) else None,
            "embedding_seconds": sum(r["embedding_seconds"] or 0 for r in good),
            "candidate_seconds": sum(r["candidate_seconds"] or 0 for r in good),
            "memory_rss_bytes": max((r["memory_rss_bytes"] or 0 for r in good), default=None),
            "missed_gold_boundaries": sum(not r["covered"] for r in misses),
            "pareto_dominated": None})
    for row in aggregate_rows:
        peers = [x for x in aggregate_rows if x["radius"] == row["radius"] and x["status"] == "complete"]
        if row["status"] == "complete":
            row["pareto_dominated"] = any(x["overall_coverage"] >= row["overall_coverage"]
                and x["candidate_count"] <= row["candidate_count"]
                and (x["overall_coverage"] > row["overall_coverage"] or x["candidate_count"] < row["candidate_count"])
                for x in peers)
    by_config_radius = {(r["config_id"], r["radius"]): r for r in aggregate_rows}
    for config in matrix:
        small, large = (by_config_radius.get((config["config_id"], radius)) for radius in RADII)
        if small and large and small["status"] == large["status"] == "complete":
            assert large["gold_covered"] >= small["gold_covered"], config["config_id"]
    write_csv(HERE / "aggregate.csv", aggregate_rows)
    write_csv(HERE / "per_meeting.csv", run_rows)
    write_csv(HERE / "missed_boundaries.csv", [r for r in miss_rows if not r["covered"]])
    write_csv(HERE / "experiment_matrix.csv", matrix)
    write_json(HERE / "run_manifest.json", {"status": "complete" if all(r["status"] == "complete" for r in matrix) else "partial",
        "finished_at": datetime.now(timezone(timedelta(hours=9))).isoformat(), "models": MODELS,
        "model_errors": model_errors, "device": "cpu", "batch_size": BATCH_SIZE,
        "depth_radius": DEPTH_RADIUS, "llm_calls": 0, "embedding_api_calls": 0,
        "completed_conditions": sum(r["status"] == "complete" for r in matrix),
        "total_conditions": len(matrix), "dataset_sha256": digest(json.dumps(dataset_rows, sort_keys=True, ensure_ascii=False).encode())})
    make_report(aggregate_rows, matrix, model_errors)
    print(f"Finished: {sum(r['status'] == 'complete' for r in matrix)}/{len(matrix)} conditions; LLM/API calls=0", flush=True)


def make_report(aggregates, matrix, model_errors):
    lines = ["# 경계 후보 추출 실험 결과", "", f"실행일: {datetime.now(timezone(timedelta(hours=9))).date().isoformat()}",
             "", "## 실행 개요", "", f"- 조건: {sum(r['status'] == 'complete' for r in matrix)}/{len(matrix)} 전체 조건 완료.",
             "- 입력 20개, 정답 경계 153개. LLM 및 유료 임베딩 API 호출은 0회.",
             "- 평가는 ±200자와 ±300자를 별도로 집계.", "", "## 모델 실행 상태", ""]
    for model in MODELS:
        counts = {status: sum(r["model"] == model and r["status"] == status for r in matrix)
                  for status in ("complete", "partial", "failed")}
        lines.append(f"- {model}: 완전 {counts['complete']}, 부분 {counts['partial']}, 실패 {counts['failed']}/72"
                     + (f" — {model_errors[model]}" if model_errors.get(model) else ""))
    for radius in RADII:
        rows = [r for r in aggregates if r["radius"] == radius and r["status"] == "complete"]
        lines.extend(["", f"## ±{radius}자", "", "| 순위 | 모델 | 윈도우 | 오버랩 | 분할 | 방식 | 임계값 | 포함률 | 완전 포함 회의 | 후보 수 |", "|---:|---|---:|---:|---|---|---:|---:|---:|---:|"])
        rows.sort(key=lambda r: (-r["overall_coverage"], -r["fully_covered_meetings"], r["candidate_count"], r["embedding_seconds"] or math.inf))
        for i, r in enumerate(rows[:15], 1):
            lines.append(f"| {i} | {r['model']} | {r['window_chars']} | {r['overlap_chars']} | {r['split']} | {r['method']} | {r['threshold']:.2f} | {r['overall_coverage']:.1%} ({r['gold_covered']}/{r['gold_total']}) | {r['fully_covered_meetings']}/20 | {r['candidate_count']} |")
        if not rows:
            lines.append("| — | 실행 완료 조건 없음 | — | — | — | — | — | — | — | — |")
            continue
        best = rows[0]
        lines.extend(["", "### 모델별 최고 조건", "", "| 모델 | 설정 | 포함률 | 완전 포함 회의 | 후보 수 |", "|---|---|---:|---:|---:|"])
        for model in MODELS:
            best_model = next((r for r in rows if r["model"] == model), None)
            if best_model:
                setup = f"{best_model['window_chars']}/{best_model['overlap_chars']}자 {best_model['split']} {best_model['method']} {best_model['threshold']:.2f}"
                lines.append(f"| {model} | {setup} | {best_model['overall_coverage']:.1%} | {best_model['fully_covered_meetings']}/20 | {best_model['candidate_count']} |")
            else:
                partial = sum(r["model"] == model and r["status"] == "partial" for r in matrix)
                failed = sum(r["model"] == model and r["status"] == "failed" for r in matrix)
                lines.append(f"| {model} | 완전 실행 조건 없음 ({partial}개 부분, {failed}개 실패) | — | — | — |")
        baseline = [r for r in rows if r["window_chars"] == 300 and r["overlap_chars"] == 100 and r["split"] == "sentence"]
        lines.extend(["", "### 공통 설정에서 모델 비교 (300자/100자/문장 보존)", "", "| 방법·임계값 | " + " | ".join(MODELS) + " |", "|---|" + "---:|" * len(MODELS)])
        for method, threshold in itertools.product(METHODS, THRESHOLDS):
            line = f"| {method} {threshold:.2f}"
            for model in MODELS:
                item = next((r for r in baseline if r["model"] == model and r["method"] == method and r["threshold"] == threshold), None)
                line += f" | {item['overall_coverage']:.1%}; {item['candidate_count']}" if item else " | —"
            lines.append(line + " |")
        chart_rows = [r for r in rows if not r["pareto_dominated"]][:8]
        lines.extend(["", "### 후보 수 대비 포함률", "", "파레토 우위 조건 중 포함률 상위 8개입니다. 막대 하나는 5%p입니다.", ""])
        for r in chart_rows:
            label = f"{r['model']} w{r['window_chars']}/o{r['overlap_chars']} {r['split']} {r['method']} {r['threshold']:.2f}"
            lines.append(f"- `{label}` `{'█' * round(r['overall_coverage'] * 20)}` {r['overall_coverage']:.1%}, 후보 {r['candidate_count']}")
        best_label = f"{best['model']} / {best['window_chars']}자 / 오버랩 {best['overlap_chars']}자 / {best['split']} / {best['method']} {best['threshold']:.2f}"
        lines.extend(["", f"**권고:** ±{radius}자 기준으로 {best_label} 조건이 우선입니다. 포함률 {best['overall_coverage']:.1%}, 완전 포함 {best['fully_covered_meetings']}/20, 후보 {best['candidate_count']}개입니다."])
    lines.extend(["", "## 결과 해석", "", "MPNet과 Alpha STS는 128토큰 입력 한도를 넘은 회의가 있어 완전 실행 조건이 없으며, 일부 짧은 윈도우 설정만 부분 실행됐습니다. 포함률 100% 미달 조건의 누락 경계와 최근접 후보 거리는 `missed_boundaries.csv`에서 확인할 수 있습니다. 모델 접근 실패·입력 한도 초과 등 실행 불가 조건은 `experiment_matrix.csv`에 사유를 기록했습니다.",
        "임베딩 시간·모델 로드 시간·후보 계산 시간은 `per_meeting.csv`와 `aggregate.csv`에 기록했습니다. 메모리는 후보 계산 중 프로세스 RSS 표본으로 기록했으며 모델 가중치를 포함합니다.",
        "", "## 한계", "", "- 결과는 같은 20개 회의에서 설정을 비교한 것이며 독립 시험 성능이 아닙니다.",
        "- 문장 분할은 기존의 문장부호 기반 규칙을 사용하며 약어를 별도로 판별하지 않습니다.",
        "- 처리 시간은 로컬 CPU 실행 기준이며 모델 로드와 임베딩 시간은 per_meeting.csv에 기록합니다.", ""])
    (HERE / "report.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
