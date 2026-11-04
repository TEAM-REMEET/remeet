"""Reuse BGE window scores to measure boundary distances without model calls."""
import bisect
import csv
import hashlib
import itertools
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

OUT = Path(__file__).resolve().parent
BASE = OUT.parent
HYUNGJIN = BASE.parent
ARCHIVE = HYUNGJIN.parents[2] / "hyungjin/chunking_method_archive_20261027/runs/BAAI--bge-m3"
sys.path.insert(0, str(HYUNGJIN / "embedding-test"))
from pipeline import prepare_input


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def local_minima(values):
    if len(values) < 2:
        return []
    result = []
    for i, value in enumerate(values):
        left = values[i - 1] if i else None
        right = values[i + 1] if i + 1 < len(values) else None
        if ((left is None or value <= left) and (right is None or value <= right)
                and ((left is not None and value < left) or (right is not None and value < right))):
            result.append(i)
    return result


def merge(candidates, gap, maximum):
    groups = []
    for candidate in candidates:
        p = candidate["candidate_char"]
        if groups and p - groups[-1][-1]["candidate_char"] <= gap and p - groups[-1][0]["candidate_char"] <= maximum:
            groups[-1].append(candidate)
        else:
            groups.append([candidate])
    return groups


def nearest_utterance(p, starts):
    i = bisect.bisect_left(starts, p)
    options = [j for j in (i - 1, i) if 0 <= j < len(starts)]
    return min(options, key=lambda j: (abs(starts[j] - p), j)) + 1


def output_csv(name, rows):
    with (OUT / name).open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def self_check():
    assert local_minima([0.8, 0.2, 0.8, 0.1, 0.9]) == [1, 3]
    c = [{"candidate_char": p, "depth": d} for p, d in [(100, .2), (250, .3), (410, .1), (900, .4)]]
    assert [len(g) for g in merge(c, 200, 600)] == [3, 1]
    assert [len(g) for g in merge(c, 200, 200)] == [2, 1, 1]
    assert nearest_utterance(15, [10, 20]) == 1


def main():
    self_check()
    manifest = read(BASE / "embedding_experiment/dataset_manifest.json")
    assert len(manifest["meetings"]) == 20
    prepared = []
    for item in manifest["meetings"]:
        meeting = item["meeting"]
        source_path = HYUNGJIN / item["input"]
        gold_path = HYUNGJIN / item["gold"]
        raw, gold_raw = source_path.read_bytes(), gold_path.read_bytes()
        assert digest(raw) == item["input_sha256"] and digest(gold_raw) == item["gold_sha256"], meeting
        source, blocks, _ = prepare_input(raw, source_path.name)
        assert digest(source.encode("utf-8")) == item["source_sha256"]
        starts = [b["start_char"] for b in blocks]
        gold = read(gold_path)["boundary_positions"]
        assert {f"u{x:04d}": starts[x - 1] for x in gold} == item["boundary_chars"]
        run = ARCHIVE / meeting / "w200_o100_sentence"
        scores, windows, info = read(run / "scores.json"), read(run / "windows.json"), read(run / "embedding_info.json")
        assert info["model"] == "BAAI/bge-m3" and len(scores) == len(windows) - 1
        assert all(w["start_char"] < w["end_char"] <= len(source) for w in windows)
        assert all(s["candidate_char"] == (windows[i+1]["start_char"] + windows[i]["end_char"]) // 2 for i, s in enumerate(scores))
        # A fixed-threshold replay verifies the archived scores against the saved selection.
        mins = local_minima([s["similarity"] for s in scores])
        replay = [scores[i]["candidate_char"] for i in mins if scores[i]["depth"] >= .10]
        old = read(BASE / "embedding_experiment/runs/BAAI--bge-m3" / meeting / "selections/w200_o100_sentence_depth_0.10.json")
        assert replay == old["candidate_positions"], meeting
        prepared.append((item, starts, gold, scores, mins))

    conditions, meetings, distances, all_groups = [], [], [], []
    for k, gap, cap in itertools.product((0.0, .2), (200, 300, 400), (600, 700, 800)):
        cid = f"k{k:.1f}_gap{gap}_span{cap}"
        c_meetings, c_distances = [], []
        for item, starts, gold, scores, mins in prepared:
            depths = [s["depth"] for s in scores]
            threshold = statistics.fmean(depths) + k * statistics.pstdev(depths)
            candidates = [scores[i] for i in mins if scores[i]["depth"] >= threshold]
            groups = merge(candidates, gap, cap)
            reps = [min(g, key=lambda s: (-s["depth"], s["candidate_char"])) for g in groups]
            assert all(g[-1]["candidate_char"] - g[0]["candidate_char"] <= cap for g in groups)
            counts = Counter()
            for position in gold:
                gp = starts[position - 1]
                if reps:
                    index = min(range(len(reps)), key=lambda i: (abs(reps[i]["candidate_char"] - gp), reps[i]["candidate_char"]))
                    rep = reps[index]
                    predicted_utterance = nearest_utterance(rep["candidate_char"], starts)
                    counts[index] += 1
                    row = dict(condition=cid, meeting=item["meeting"], gold_utterance=position, gold_char=gp,
                               group=index + 1, representative_char=rep["candidate_char"], representative_utterance=predicted_utterance,
                               signed_char=rep["candidate_char"]-gp, distance_chars=abs(rep["candidate_char"]-gp),
                               signed_utterances=predicted_utterance-position, distance_utterances=abs(predicted_utterance-position))
                else:
                    row = dict(condition=cid, meeting=item["meeting"], gold_utterance=position, gold_char=gp,
                               group="", representative_char="", representative_utterance="", signed_char="", distance_chars="",
                               signed_utterances="", distance_utterances="")
                c_distances.append(row)
            for i, (g, rep) in enumerate(zip(groups, reps)):
                all_groups.append(dict(condition=cid, meeting=item["meeting"], group=i+1, first_char=g[0]["candidate_char"],
                    last_char=g[-1]["candidate_char"], span_chars=g[-1]["candidate_char"]-g[0]["candidate_char"],
                    representative_char=rep["candidate_char"], representative_depth=rep["depth"],
                    candidate_count=len(g), candidate_chars=";".join(str(x["candidate_char"]) for x in g),
                    gold_between_endpoints=sum(g[0]["candidate_char"] <= starts[x-1] <= g[-1]["candidate_char"] for x in gold),
                    nearest_gold_count=counts[i]))
            ds = [r for r in c_distances if r["meeting"] == item["meeting"] and r["distance_chars"] != ""]
            mr = dict(condition=cid, k=k, gap=gap, max_span=cap, meeting=item["meeting"], threshold=threshold,
                      candidate_count=len(candidates), groups=len(groups), estimated_summary_chunks=len(groups)+1,
                      groups_10_15=int(10 <= len(groups) <= 15), gold_count=len(gold), no_representative=len(gold)-len(ds),
                      mean_chars=statistics.fmean(r["distance_chars"] for r in ds) if ds else "",
                      median_chars=statistics.median(r["distance_chars"] for r in ds) if ds else "",
                      max_chars=max((r["distance_chars"] for r in ds), default=""),
                      mean_utterances=statistics.fmean(r["distance_utterances"] for r in ds) if ds else "",
                      median_utterances=statistics.median(r["distance_utterances"] for r in ds) if ds else "",
                      max_utterances=max((r["distance_utterances"] for r in ds), default=""),
                      shared_nearest_groups=sum(v >= 2 for v in counts.values()),
                      shared_gold=sum(v for v in counts.values() if v >= 2))
            c_meetings.append(mr)
        valid = [r for r in c_distances if r["distance_chars"] != ""]
        conditions.append(dict(condition=cid, k=k, gap=gap, max_span=cap, gold_count=len(c_distances),
            no_representative=len(c_distances)-len(valid), total_groups=sum(r["groups"] for r in c_meetings),
            mean_groups=statistics.fmean(r["groups"] for r in c_meetings), median_groups=statistics.median(r["groups"] for r in c_meetings),
            max_groups=max(r["groups"] for r in c_meetings), meetings_10_15=sum(r["groups_10_15"] for r in c_meetings),
            meetings_le15=sum(r["groups"] <= 15 for r in c_meetings),
            mean_chars=statistics.fmean(r["distance_chars"] for r in valid), median_chars=statistics.median(r["distance_chars"] for r in valid),
            max_chars=max(r["distance_chars"] for r in valid), mean_utterances=statistics.fmean(r["distance_utterances"] for r in valid),
            median_utterances=statistics.median(r["distance_utterances"] for r in valid), max_utterances=max(r["distance_utterances"] for r in valid),
            shared_nearest_groups=sum(r["shared_nearest_groups"] for r in c_meetings), shared_gold=sum(r["shared_gold"] for r in c_meetings)))
        meetings.extend(c_meetings)
        distances.extend(c_distances)
    output_csv("aggregate.csv", conditions)
    output_csv("per_meeting.csv", meetings)
    output_csv("gold_distances.csv", distances)
    output_csv("groups.csv", all_groups)
    print(json.dumps({"conditions": len(conditions), "meetings": len(prepared), "gold": len(prepared) and sum(len(x[2]) for x in prepared)}))


if __name__ == "__main__":
    main()
