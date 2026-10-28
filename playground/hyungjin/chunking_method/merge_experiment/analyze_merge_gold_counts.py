"""Count gold boundaries per saved request without calling any model."""
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

OUT = Path(__file__).resolve().parent
HERE = OUT.parent


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def gold_ids(request, gold):
    options = {o["start_uid"] for o in request["boundary_options"]}
    return sorted(uid for uid, pos in gold.items()
                  if request["start"] <= pos < request["end"] and uid in options)


def write_csv(name, rows):
    with (OUT / name).open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main():
    # Count each selectable ID once, even when options are duplicated; end is exclusive.
    assert gold_ids({"start": 10, "end": 20, "boundary_options": [
        {"start_uid": "a"}, {"start_uid": "a"}, {"start_uid": "b"}]},
        {"a": 10, "b": 20, "c": 15}) == ["a"]
    dataset = read_json(HERE / "embedding_experiment/dataset_manifest.json")
    manifest = read_json(OUT / "manifest.json")
    with (OUT / "aggregate.csv").open(encoding="utf-8-sig", newline="") as stream:
        aggregate = {r["condition"]: r for r in csv.DictReader(stream)}
    details, summary, distributions = [], [], []
    for condition in manifest["conditions"]:
        condition_rows = []
        unique_gold = set()
        for meeting in dataset["meetings"]:
            gold_path = HERE.parent / meeting["gold"]
            assert hashlib.sha256(gold_path.read_bytes()).hexdigest() == meeting["gold_sha256"]
            requests = read_json(OUT / "inputs_not_sent" / meeting["meeting"] / f"{condition}.json")
            for request in requests:
                ids = gold_ids(request, meeting["boundary_chars"])
                unique_gold.update((meeting["meeting"], uid) for uid in ids)
                condition_rows.append({"condition": condition, "meeting": meeting["meeting"],
                    "request_id": request["request_id"], "gold_count": len(ids),
                    "inclusive_gold_count": sum(request["start"] <= pos <= request["end"] for pos in meeting["boundary_chars"].values()),
                    "gold_uids": ";".join(ids), "start_char": request["start"],
                    "end_char": request["end"], "context_chars": len(request["context"]),
                    "original_candidate_count": len(request["original_candidate_ids"])})
        expected = aggregate[condition]
        counts = Counter(r["gold_count"] for r in condition_rows)
        multi = [r for r in condition_rows if r["gold_count"] >= 2]
        nonempty = len(condition_rows)-counts[0]
        assert len(condition_rows) == int(expected["requests"])
        assert len(unique_gold) == int(expected["selectable_gold"])
        geometric = Counter(r["inclusive_gold_count"] for r in condition_rows)
        assert sum(n for k, n in geometric.items() if k >= 2) == int(expected["multi_gold_requests"])
        assert geometric[0] == int(expected["requests_without_gold"])
        assert max(geometric) == int(expected["max_gold_per_request"])
        summary.append({"condition": condition, "requests": len(condition_rows),
            "nonempty_requests": nonempty, "multi_gold_requests": len(multi),
            "multi_rate_all": len(multi)/len(condition_rows),
            "multi_rate_nonempty": len(multi)/nonempty if nonempty else 0,
            "mean_gold_per_request": sum(r["gold_count"] for r in condition_rows)/len(condition_rows),
            "mean_gold_per_multi_request": sum(r["gold_count"] for r in multi)/len(multi) if multi else 0,
            "max_gold_per_request": max(counts), "unique_gold_covered": len(unique_gold),
            "gold_occurrences": sum(r["gold_count"] for r in condition_rows)})
        summary[-1]["endpoint_affected_requests"] = sum(r["gold_count"] != r["inclusive_gold_count"] for r in condition_rows)
        distributions.extend({"condition": condition, "gold_count": count,
                              "requests": counts[count]} for count in range(max(counts)+1))
        details.extend(condition_rows)
    write_csv("request_gold_counts.csv", details)
    write_csv("multi_gold_cases.csv", [r for r in details if r["gold_count"] >= 2])
    write_csv("gold_count_distribution.csv", distributions)
    write_csv("gold_count_summary.csv", summary)
    labels = {"baseline": "미병합", "preserve_900": "범위 보존 900자",
              "preserve_1200": "범위 보존 1,200자", "recenter_300": "쌍별 중앙 ±300자",
              "recenter_500": "쌍별 중앙 ±500자", "component_300": "그룹 중앙 ±300자(보조)",
              "component_500": "그룹 중앙 ±500자(보조)"}
    lines = ["# 묶음당 정답 경계 개수 분석", "", "분석일: 2026-10-27", "",
        "저장된 요청의 실제 본문 범위 [start,end)에 있고 boundary_options에서 선택 가능한 정답 발화 ID를 묶음별로 중복 제거해 셌다. 후보 수가 아니라 정답 경계 수다. 동일 정답이 서로 다른 묶음에 반복 등장할 수 있으므로 정답 출현 횟수 합계를 고유 정답 153개와 혼동하면 안 된다.", "",
        "## 정답 개수 분포", "", "| 조건 | 전체 묶음 | 0개 | 1개 | 2개 | 3개 | 4개 | 최대 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for s in summary:
        counts = {r["gold_count"]: r["requests"] for r in distributions if r["condition"] == s["condition"]}
        lines.append(f"| {labels[s['condition']]} | {s['requests']:,} | " +
                     " | ".join(str(counts.get(k, 0)) for k in range(5)) + f" | {s['max_gold_per_request']} |")
    lines += ["", "## 복수 정답 묶음", "",
        "| 조건 | 2개 이상 묶음 | 전체 중 비율 | 정답 있는 묶음 중 비율 | 복수 묶음당 평균 정답 |",
        "|---|---:|---:|---:|---:|"]
    for s in summary:
        lines.append(f"| {labels[s['condition']]} | {s['multi_gold_requests']} | {s['multi_rate_all']:.1%} | {s['multi_rate_nonempty']:.1%} | {s['mean_gold_per_multi_request']:.2f} |")
    lines += ["", "## 범위 보존 1,200자: 복수 정답 전체 목록", "",
        "| 회의 | 묶음 ID | 원래 후보 수 | 본문 길이 | 정답 수 | 정답 발화 ID |",
        "|---|---|---:|---:|---:|---|"]
    selected = [r for r in details if r["condition"] == "preserve_1200" and r["gold_count"] >= 2]
    for r in sorted(selected, key=lambda r: (-r["gold_count"], r["meeting"], r["request_id"])):
        lines.append(f"| {r['meeting']} | {r['request_id']} | {r['original_candidate_count']} | {r['context_chars']} | {r['gold_count']} | {r['gold_uids'].replace(';', ', ')} |")
    chosen = next(s for s in summary if s["condition"] == "preserve_1200")
    lines += ["", "## LLM 설계에 대한 의미", "",
        f"권고 조건인 범위 보존 1,200자는 {chosen['requests']}개 묶음 중 {chosen['multi_gold_requests']}개에 복수 정답이 있다. 정답이 있는 {chosen['nonempty_requests']}개 묶음 중 {chosen['multi_rate_nonempty']:.1%}다. 복수 정답 묶음에는 평균 {chosen['mean_gold_per_multi_request']:.2f}개, 최대 {chosen['max_gold_per_request']}개가 들어 있다.", "",
        "따라서 LLM 출력은 경계 ID 배열이어야 하며 0개·1개·여러 개를 허용해야 한다. 한 묶음에서 한 경계만 선택하도록 제한하면 안 된다. 관측 최대 4개를 향후 출력 개수 상한으로 고정하지 않는다. 서로 다른 묶음이 같은 경계를 반환하면 회의 ID와 발화 ID로 중복 제거한다.", "",
        "기존 aggregate.csv의 묶음별 집계는 양끝 포함 좌표 기준이다. 이를 별도로 재현하여 일치를 확인했고, 이번 분포는 실제 문자열 [start,end)와 선택지 기준으로 계산했다. 오른쪽 끝점에 정확히 걸린 정답은 해당 묶음에서 제외되므로 일부 묶음별 수치는 기존 집계와 다를 수 있다. 전체 고유 선택 가능 정답 수는 기존 집계와 일치한다.", "",
        "끝점 차이가 있는 묶음 수: " + ", ".join(f"{labels[s['condition']]} {s['endpoint_affected_requests']}개" for s in summary) + ".", "",
        "LLM 호출이나 임베딩 재실행은 하지 않았다. 실제 다중 경계 추출 성공률은 아직 측정하지 않았다.", "",
        "산출물: request_gold_counts.csv(모든 묶음), multi_gold_cases.csv(복수 정답 묶음), gold_count_distribution.csv(분포), gold_count_summary.csv(요약).",
        "재현: 저장소 루트에서 `.venv/Scripts/python.exe remeet/playground/hyungjin/chunking_method/merge_experiment/analyze_merge_gold_counts.py`.", ""]
    (OUT / "gold_count_report.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("PASS: duplicate/endpoint self-check; all 7 conditions match existing aggregates")


if __name__ == "__main__":
    main()
