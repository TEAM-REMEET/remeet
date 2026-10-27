"""Compare capped unions and component-midpoint recentering; no model calls."""
import csv
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from statistics import mean, median
from zoneinfo import ZoneInfo

OUT = Path(__file__).resolve().parent
HERE = OUT.parent
sys.path.insert(0, str(HERE.parent / "embedding-test"))
from pipeline import prepare_input

CONFIG = "BAAI--bge-m3__w200_o100_sentence_depth_0.10"
CONDITIONS = ("baseline", "preserve_900", "preserve_1200", "recenter_300", "recenter_500",
              "component_300", "component_500")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def write_csv(path, rows, fields=None):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields or list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def make_intervals(points, start, end):
    return [{"id": f"C{i+1:04d}", "center": p, "start": max(start, p-300),
             "end": min(end, p+300)} for i, p in enumerate(points)]


def group_intervals(intervals, cap=None):
    """Sweep the full union end, so both-sided and transitive overlaps are included."""
    groups = []
    for item in intervals:
        if groups and item["start"] <= groups[-1]["end"] and (
                cap is None or max(groups[-1]["end"], item["end"]) - groups[-1]["start"] <= cap):
            group = groups[-1]
            group["members"].append(item)
            group["end"] = max(group["end"], item["end"])
        else:
            groups.append({"start": item["start"], "end": item["end"], "members": [item]})
    assert [x["id"] for g in groups for x in g["members"]] == [x["id"] for x in intervals]
    assert cap is None or all(g["end"] - g["start"] <= cap for g in groups)
    return groups


def ranges_for(intervals, start, end, condition):
    if condition.startswith("recenter"):
        # Every original overlapping pair contributes a center, including both neighbors.
        centers, matched = {}, set()
        for i, left in enumerate(intervals):
            for right in intervals[i+1:]:
                if right["start"] > left["end"]:
                    break
                lo, hi = max(left["start"], right["start"]), min(left["end"], right["end"])
                center = (lo+hi)//2
                centers.setdefault(center, {}).update({x["id"]: x for x in (left, right)})
                matched.update((left["id"], right["id"]))
        for item in intervals:
            if item["id"] not in matched:
                centers.setdefault(item["center"], {})[item["id"]] = item
        output = []
        radius = int(condition.split("_")[1])
        for i, (center, member_map) in enumerate(sorted(centers.items())):
            members = sorted(member_map.values(), key=lambda x: x["center"])
            output.append({"request_id": f"R{i+1:04d}", "start": max(start, center-radius),
                           "end": min(end, center+radius), "new_center": center,
                           "original_union_start": min(x["start"] for x in members),
                           "original_union_end": max(x["end"] for x in members),
                           "original_candidate_ids": [x["id"] for x in members],
                           "original_candidate_chars": [x["center"] for x in members]})
        assert {uid for r in output for uid in r["original_candidate_ids"]} == {x["id"] for x in intervals}
        return output
    if condition == "baseline":
        groups = [{"start": x["start"], "end": x["end"], "members": [x]} for x in intervals]
    elif condition.startswith("preserve"):
        groups = group_intervals(intervals, int(condition.split("_")[1]))
    else:
        groups = group_intervals(intervals)
    output = []
    for i, group in enumerate(groups):
        members = group["members"]
        center = (members[0]["center"] + members[-1]["center"]) // 2
        lo, hi = group["start"], group["end"]
        if condition.startswith("component"):
            radius = int(condition.split("_")[1])
            lo, hi = max(start, center-radius), min(end, center+radius)
        output.append({"request_id": f"R{i+1:04d}", "start": lo, "end": hi,
                       "new_center": center, "original_union_start": group["start"],
                       "original_union_end": group["end"],
                       "original_candidate_ids": [x["id"] for x in members],
                       "original_candidate_chars": [x["center"] for x in members]})
    return output


def covered(ranges, positions, inclusive=True):
    return {uid for uid, p in positions.items() if any(
        r["start"] <= p and (p <= r["end"] if inclusive else p < r["end"]) for r in ranges)}


def union_length(ranges):
    total, right = 0, -1
    for r in sorted(ranges, key=lambda x: (x["start"], x["end"])):
        total += max(0, r["end"] - max(right, r["start"]))
        right = max(right, r["end"])
    return total


def self_check():
    intervals = make_intervals([1000, 1400, 1800], 0, 3000)
    # A-B and B-C overlap, A-C do not; the entire chain must still be grouped.
    assert len(group_intervals(intervals)) == 1
    assert len(group_intervals(intervals, 900)) == 3
    assert len(group_intervals(intervals, 1200)) == 2
    pairwise = ranges_for(intervals, 0, 3000, "recenter_300")
    assert [r["new_center"] for r in pairwise] == [1200, 1600]
    dense = ranges_for(make_intervals([1000, 1200, 1400], 0, 3000), 0, 3000, "recenter_300")
    assert [r["new_center"] for r in dense] == [1100, 1200, 1300]
    duplicate = ranges_for(make_intervals([1000, 1100, 1200, 1300], 0, 3000), 0, 3000, "recenter_300")
    assert len(duplicate) == 5  # Six pairs, one shared midpoint.
    central = ranges_for(intervals, 0, 3000, "component_300")
    assert (central[0]["new_center"], central[0]["start"], central[0]["end"]) == (1400, 1100, 1700)
    assert len(central[0]["original_candidate_ids"]) == 3
    gold = {"left": 700, "middle": 1400, "right": 2100}
    assert covered(central, gold) == {"middle"}
    for cap in (900, 1200):
        merged = ranges_for(intervals, 0, 3000, f"preserve_{cap}")
        assert covered(merged, gold) == set(gold)
        assert union_length(merged) == union_length(intervals)
    assert len(group_intervals(make_intervals([500, 1100], 0, 2000))) == 1
    assert len(group_intervals(make_intervals([500, 1101], 0, 2000))) == 2
    assert covered([{"start": 0, "end": 5}], {"end": 5}) == {"end"}
    assert covered([{"start": 0, "end": 5}], {"end": 5}, False) == set()
    assert ranges_for([], 0, 1000, "preserve_900") == []
    clipped = ranges_for(make_intervals([50, 950], 0, 1000), 0, 1000, "recenter_500")
    assert [(r["start"], r["end"]) for r in clipped] == [(0, 550), (450, 1000)]
    assert union_length(clipped) == 1000


def main():
    self_check()
    OUT.mkdir(exist_ok=True)
    manifest = read_json(HERE / "embedding_experiment/dataset_manifest.json")
    assert len(manifest["meetings"]) == 20
    with (HERE / "embedding_experiment/per_meeting.csv").open(encoding="utf-8-sig", newline="") as stream:
        baseline_rows = {r["meeting"]: r for r in csv.DictReader(stream)
                         if r["config_id"] == CONFIG and r["radius"] == "300"}
    rows, diagnostics, pairs, components, inputs = [], [], [], [], []
    all_sizes = {name: [] for name in CONDITIONS}
    for item in manifest["meetings"]:
        meeting = item["meeting"]
        input_path = HERE.parent / item["input"]
        gold_path = HERE.parent / item["gold"]
        assert digest(input_path) == item["input_sha256"], meeting
        assert digest(gold_path) == item["gold_sha256"], meeting
        source, blocks, _ = prepare_input(input_path.read_bytes(), input_path.name)
        assert hashlib.sha256(source.encode("utf-8")).hexdigest() == item["source_sha256"]
        gold = read_json(gold_path)
        assert len(blocks) == item["utterances"] == gold["utterance_count"]
        gold_positions = {f"u{n:04d}": blocks[n-1]["start_char"] for n in gold["boundary_positions"]}
        assert gold_positions == item["boundary_chars"]
        positions = {f"u{i+1:04d}": b["start_char"] for i, b in enumerate(blocks) if i > 0}
        start, end = blocks[0]["start_char"], blocks[-1]["end_char"]
        selection_path = HERE / "embedding_experiment/runs" / "BAAI--bge-m3" / meeting / "selections" / "w200_o100_sentence_depth_0.10.json"
        selection = read_json(selection_path)
        assert selection["config_id"] == CONFIG
        points = selection["candidate_positions"]
        assert points == sorted(set(points)) and all(start < p < end for p in points)
        original = make_intervals(points, start, end)
        assert len(points) == int(baseline_rows[meeting]["candidate_count"])
        baseline_gold = covered(original, gold_positions)
        assert len(baseline_gold) == int(baseline_rows[meeting]["gold_covered"]) == len(gold_positions)
        base_options = covered(original, positions, False)
        for group_index, group in enumerate(group_intervals(original), 1):
            members = group["members"]
            components.append({"meeting": meeting, "group_id": group_index, "members": len(members),
                               "start": group["start"], "end": group["end"],
                               "union_chars": group["end"]-group["start"],
                               "common_overlap_chars": max(0, min(x["end"] for x in members)-max(x["start"] for x in members)),
                               "candidate_ids": ";".join(x["id"] for x in members)})
            for i, left in enumerate(members):
                for right in members[i+1:]:
                    if right["start"] > left["end"]:
                        break
                    lo, hi = max(left["start"], right["start"]), min(left["end"], right["end"])
                    pairs.append({"meeting": meeting, "group_id": group_index,
                                  "left_id": left["id"], "right_id": right["id"],
                                  "overlap_start": lo, "overlap_end": hi,
                                  "overlap_chars": hi-lo, "overlap_midpoint": (lo+hi)//2})
        for condition in CONDITIONS:
            ranges = ranges_for(original, start, end, condition)
            hits = covered(ranges, gold_positions)
            options = covered(ranges, positions, False)
            selectable = hits & options
            if condition.startswith("preserve"):
                assert hits == baseline_gold
                assert options == base_options
                assert union_length(ranges) == union_length(original)
            sizes = [r["end"]-r["start"] for r in ranges]
            all_sizes[condition].extend(sizes)
            for r in ranges:
                r["context"] = source[r["start"]:r["end"]]
                r["boundary_options"] = [{"start_uid": uid, "start_char": pos}
                                         for uid, pos in positions.items() if r["start"] <= pos < r["end"]]
                assert len(r["context"]) == r["end"] - r["start"]
            write_json(OUT / "inputs_not_sent" / meeting / f"{condition}.json", ranges)
            gold_per_request = [len(covered([r], gold_positions)) for r in ranges]
            rows.append({"meeting": meeting, "condition": condition, "original_candidates": len(points),
                         "requests": len(ranges), "gold_total": len(gold_positions), "gold_covered": len(hits),
                         "coverage": len(hits)/len(gold_positions), "selectable_gold": len(selectable),
                         "baseline_option_ids": len(base_options), "retained_option_ids": len(options & base_options),
                         "lost_option_ids": len(base_options-options), "new_option_ids": len(options-base_options),
                         "total_context_chars": sum(sizes), "unique_context_chars": union_length(ranges),
                         "max_context_chars": max(sizes, default=0),
                         "requests_without_gold": sum(n == 0 for n in gold_per_request),
                         "multi_gold_requests": sum(n >= 2 for n in gold_per_request),
                         "max_gold_per_request": max(gold_per_request, default=0),
                         "max_members_per_request": max((len(r["original_candidate_ids"]) for r in ranges), default=0)})
            for uid, pos in gold_positions.items():
                diagnostics.append({"meeting": meeting, "condition": condition, "gold_uid": uid,
                                    "gold_char": pos, "included": uid in hits, "selectable": uid in selectable,
                                    "distance_to_context": min((max(r["start"]-pos, pos-r["end"], 0) for r in ranges), default=None)})
        inputs.append({"meeting": meeting, "input_sha256": digest(input_path), "gold_sha256": digest(gold_path),
                       "selection_sha256": digest(selection_path)})
    aggregate = []
    sums = ("original_candidates", "requests", "gold_total", "gold_covered", "selectable_gold",
            "baseline_option_ids", "retained_option_ids", "lost_option_ids", "new_option_ids",
            "total_context_chars", "unique_context_chars", "requests_without_gold", "multi_gold_requests")
    for condition in CONDITIONS:
        rr = [r for r in rows if r["condition"] == condition]
        result = {"condition": condition, **{key: sum(r[key] for r in rr) for key in sums}}
        assert result["original_candidates"] == 915 and result["gold_total"] == 153
        result.update(coverage=result["gold_covered"]/153, call_reduction=1-result["requests"]/915,
                      fully_covered_meetings=sum(r["gold_covered"] == r["gold_total"] for r in rr),
                      mean_context_chars=mean(all_sizes[condition]), median_context_chars=median(all_sizes[condition]),
                      max_context_chars=max(all_sizes[condition]),
                      max_members_per_request=max(r["max_members_per_request"] for r in rr),
                      max_gold_per_request=max(r["max_gold_per_request"] for r in rr))
        aggregate.append(result)
    write_csv(OUT / "aggregate.csv", aggregate)
    write_csv(OUT / "per_meeting.csv", rows)
    write_csv(OUT / "gold_diagnostics.csv", diagnostics)
    write_csv(OUT / "overlap_pairs.csv", pairs)
    write_csv(OUT / "overlap_components.csv", components)
    write_json(OUT / "manifest.json", {"created_at": datetime.now(ZoneInfo("Asia/Seoul")).isoformat(),
               "status": "complete", "config_id": CONFIG, "original_radius": 300,
               "conditions": CONDITIONS, "overlap_rule": "original intervals overlap or touch; all transitive connections",
               "recenter_rule": "all original overlapping pairs, midpoint of clipped intersection; isolated points retained; identical centers deduplicated; no regrouping",
               "component_rule": "diagnostic only: one center per transitive component, floor((first center+last center)/2)",
               "endpoint_rule": "closed geometric coverage; half-open context and selectable ID coverage also reported",
               "llm_calls": 0, "embedding_calls": 0, "self_check": "passed",
               "script_sha256": digest(Path(__file__)), "inputs": inputs})
    write_report(aggregate, rows, components, pairs)
    print(json.dumps(aggregate, ensure_ascii=False, indent=2))
    print("PASS: self-checks, 20 input/gold hashes, 915 candidates, 153 gold, preservation invariants")


def write_report(aggregate, rows, components, pairs):
    labels = {"baseline": "미병합 ±300자", "preserve_900": "범위 보존 최대 900자",
              "preserve_1200": "범위 보존 최대 1,200자", "recenter_300": "쌍별 중앙 ±300자",
              "recenter_500": "쌍별 중앙 ±500자", "component_300": "연쇄 그룹 중앙 ±300자(보조)",
              "component_500": "연쇄 그룹 중앙 ±500자(보조)"}
    base = aggregate[0]
    lines = ["# BGE-m3 후보 병합·중앙 재배치 실험", "", "실험일: 2026-10-27", "",
             "## 고정 조건과 방법", "",
             "BGE-m3 / 목표 200자 / 오버랩 100자 / 문장 보존 / depth≥0.10의 저장된 후보 915개와 정답 153개를 재사용했다. 임베딩과 LLM 호출은 0회다. 원래 후보 문맥은 좌우 각각 300자다.", "",
             "- 범위 보존: 위치순으로 겹치거나 맞닿는 문맥을 합친다. 전체 길이가 900자 또는 1,200자를 넘으면 새 묶음으로 시작하며 원래 문맥을 자르지 않는다. 상한으로 나뉜 묶음끼리 중복 문맥은 허용한다.",
             "- 중앙 재배치(주 실험): 원래 ±300자 문맥이 겹치거나 맞닿는 모든 후보 쌍에서 교집합 중간점을 내림하여 새 중심으로 만든다. A–B와 B–C가 겹치면 두 중간점을 모두 만들고, A–C도 겹치면 그 중간점도 만든다. 한 후보를 앞쪽 계산에 사용했다고 뒤쪽 계산에서 제외하지 않는다. 겹침이 없는 후보는 원래 중심을 유지한다. 완전히 같은 새 중심만 중복 제거하고 좌우 각각 300자 또는 500자(총 최대 600·1,000자)를 제공한다.",
             "- 연쇄 그룹 중앙(보조 진단): A–B, B–C로 연결되면 A·B·C 전체를 한 그룹으로 보고 최초·최종 후보 좌표 평균을 내림해 하나의 중심으로 만든다. 모든 구간의 공통 교집합이 없을 수도 있으므로 쌍별 교집합 중앙과 구분한다.",
             "- 모든 그룹과 중간점은 원래 후보로 한 번 계산한다. 새 문맥이 겹친다고 재귀적으로 재배치하지 않는다. 반경 300·500 조건은 같은 새 중심을 사용하여 문맥 크기의 효과만 비교한다.",
             "- 모든 겹침 쌍(앞·뒤 및 비인접 후보)을 overlap_pairs.csv에 저장했다. 포함률은 양끝 포함 좌표로 계산하며, 실제 문자열 [start,end)의 발화 ID 선택 가능 포함률도 별도로 검사했다. 정답은 그룹 구성에 사용하지 않았다.", "",
             "## 전체 결과", "", "| 조건 | 예상 호출 | 감소율 | 정답 포함 | 완전 포함 회의 | 본문 문자 합계 | 최대 문맥 |", "|---|---:|---:|---:|---:|---:|---:|"]
    for r in aggregate:
        lines.append(f"| {labels[r['condition']]} | {r['requests']:,} | {r['call_reduction']:.1%} | {r['gold_covered']}/153 ({r['coverage']:.1%}) | {r['fully_covered_meetings']}/20 | {r['total_context_chars']:,} | {r['max_context_chars']:,} |")
    lines += ["", "호출 수는 묶음당 요청 1회를 가정한 예상값이다. 본문 문자 합계는 실제 본문 슬라이스 길이의 합이며, 프롬프트·선택지·토큰 비용은 포함하지 않는다.", "",
              "## 앞뒤 연쇄 중복과 선택지 보존", "",
              f"원래 후보는 {len(components)}개 연결 그룹을 이룬다. 겹치거나 맞닿는 후보 쌍은 {len(pairs):,}개이며, 가장 큰 그룹은 후보 {max(c['members'] for c in components)}개·본문 {max(c['union_chars'] for c in components):,}자다. 세 후보 이상인 그룹은 {sum(c['members'] >= 3 for c in components)}개다. 따라서 연쇄 그룹을 하나의 중앙 문맥으로 축소하면 양끝의 내용이 대량으로 사라질 수 있다.", "",
              "| 조건 | 실제 선택 가능한 정답 | 기존 발화 ID 보존 | 유실 ID | 새 ID | 정답 없는 요청 | 복수 정답 요청 |", "|---|---:|---:|---:|---:|---:|---:|"]
    for r in aggregate:
        lines.append(f"| {labels[r['condition']]} | {r['selectable_gold']}/153 | {r['retained_option_ids']}/{r['baseline_option_ids']} | {r['lost_option_ids']} | {r['new_option_ids']} | {r['requests_without_gold']} | {r['multi_gold_requests']} |")
    lines += ["", "발화 ID는 회의별로 중복 제거하여 합산했다. 정답 없는 요청 비율은 최종 LLM의 오탐률이 아니다.", "", "## 회의별 비교", "",
              "| 회의 | 정답 | 미병합 호출 | 보존 900 호출/포함 | 보존 1200 호출/포함 | 쌍별 ±300 호출/포함 | 쌍별 ±500 호출/포함 | 그룹 ±300 호출/포함 | 그룹 ±500 호출/포함 |",
              "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for meeting in sorted({r["meeting"] for r in rows}):
        rr = {r["condition"]: r for r in rows if r["meeting"] == meeting}
        tail = " | ".join(f"{rr[c]['requests']}/{rr[c]['gold_covered']}" for c in CONDITIONS[1:])
        lines.append(f"| {meeting} | {rr['baseline']['gold_total']} | {rr['baseline']['requests']} | {tail} |")
    eligible = [r for r in aggregate[1:] if r["gold_covered"] == 153 and r["selectable_gold"] == 153 and r["lost_option_ids"] == 0]
    winner = min(eligible, key=lambda r: (r["requests"], r["total_context_chars"]))
    lines += ["", "## 판단과 한계", "",
              f"이번 후보 포함률 실험에서는 **{labels[winner['condition']]}**을 다음 LLM 비교의 우선 조건으로 권고한다. 예상 호출은 915회에서 {winner['requests']}회로 {winner['call_reduction']:.1%} 줄고, 정답 153개와 기존 선택 가능한 발화 ID 전체를 보존했다. 본문 합계는 {winner['total_context_chars']:,}자로 기준선보다 {1-winner['total_context_chars']/base['total_context_chars']:.1%} 줄었다.",
              "",
              "쌍별 중앙 재배치는 앞뒤 모든 겹침을 계산하므로 원래 후보보다 새 후보가 많아질 수 있다. 연쇄 그룹 중앙은 반대로 호출을 크게 줄이지만 긴 그룹의 양끝 문맥을 잃는다. 누락 ID와 문맥까지의 거리는 gold_diagnostics.csv에서 확인할 수 있다. 범위 보존 방식도 한 요청에서 복수 경계를 모두 반환하도록 LLM을 검증해야 한다. 포함률 보존은 추출 정확도 보존을 증명하지 않는다.",
              "",
              "임베딩·LLM 재실행 및 토큰 비용 측정은 수행하지 않았다. 선정용 20개 회의를 재사용했으며 독립 시험 결과가 아니다. 기존 무제한 병합 분석과는 최대 길이·중앙 재배치 조건이 다르다.", "",
              "## 검증 및 재현", "",
              "자체 검사는 연쇄·양방향 중복, 상한 초과, 맞닿음, 비겹침, 회의 양끝, 빈 후보, 포함 끝점, 재배치 후 신규 중복을 다룬다. 실제 입력·정답 해시와 발화 좌표를 기존 명세와 대조했고, 기준선 915개·153/153을 재현했다. 범위 보존 조건의 원문 합집합과 선택지 ID 합집합 일치를 검사했다.", "",
              "실행: 저장소 루트에서 `.venv/Scripts/python.exe remeet/playground/hyungjin/chunking_method/merge_experiment/run_merge_experiment.py`", "",
              "- aggregate.csv / per_meeting.csv: 조건별·회의별 결과.",
              "- overlap_pairs.csv / overlap_components.csv: 모든 중복 쌍과 연결 그룹.",
              "- gold_diagnostics.csv: 정답별 포함·선택 가능 여부와 문맥까지의 거리.",
              "- inputs_not_sent/: 원문 문맥·발화 선택지·원래 후보 ID·새 중심을 담은 미전송 자료. 정답 라벨은 넣지 않았다.",
              "- manifest.json: 설정·해시·실행 상태·호출 0회 기록.", ""]
    (OUT / "report.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
