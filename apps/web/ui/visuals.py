"""시각화 부품 (결과 검토 · 회의 목록에서 같이 씀).

- 회의 시간 계산: 안건별 논의 시간, 회의 길이
- 키워드: 회의 문장에서 자주 나온 단어
- 회의 흐름: 키워드가 겹치는 이전 회의 찾기
- 그림: 많이 논의된 주제 지도(SVG), 논의 비중 막대(HTML)
- hotspots: SVG 위에 투명 버튼을 겹쳐서 그림의 원을 누를 수 있게 합니다.
"""
from __future__ import annotations

import re
from collections import Counter

import streamlit as st

from ui.components import html
from utils.text import esc

# ---------------------------------------------------------------- 시간

def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _clock(total: int) -> str:
    return f"{total // 60:02d}:{total % 60:02d}"


def meeting_minutes(m: dict) -> int:
    return max(1, _minutes(m["end"]) - _minutes(m["start"]))


def topic_times(m: dict) -> list[dict]:
    """안건별 논의 시간(분). 발언 시각이 없어 ‘안건이 차지한 문단 수 비율 × 회의 길이’로 추정합니다.
    녹취 타임스탬프가 생기면 이 함수만 바꾸면 돼요."""
    d = m.get("detail")
    if not d or not d["topics"]:
        return []
    total = meeting_minutes(m)
    seg_n = max(len(d["segments"]), max(t["to"] for t in d["topics"]))
    out, start, acc = [], _minutes(m["start"]), 0.0
    for no, t in enumerate(d["topics"], 1):
        share = (t["to"] - t["from"] + 1) / seg_n
        x = {**t, "no": no, "min": total * share, "share": share, "start": _clock(round(start + acc))}
        acc += x["min"]
        x["end"] = _clock(round(start + acc))
        out.append(x)
    for rank, x in enumerate(sorted(out, key=lambda t: -t["min"])):
        x["rank"] = rank
    return out


# ---------------------------------------------------------------- 키워드

_PARTICLE = re.compile(r"(으로|에서|에게|까지|부터|한다|하기|하고|은|는|이|가|을|를|의|에|와|과|도|만)$")
STOPWORDS = {"작성", "정리", "추가", "단계", "개선안", "요청", "검토", "확정", "다음", "회의", "여부", "이후", "시점", "초안", "가능",
             "기준", "진행", "변경", "있게", "있는지", "후", "시", "수", "건", "대응", "공유", "참석자", "확인", "원인", "항목", "결과",
             "적용", "순서", "최종", "비교", "방안", "개발", "설계", "안내", "문구", "정한다", "종료", "완료로", "배포", "계산", "로직",
             "선택", "직후", "단계별", "시안", "버튼", "화면", "나중에", "나중", "건너뛸", "발송", "측정", "수집", "가지", "구조", "계획", "규모",
             "셋째", "준비", "반영", "늘린다", "가지로", "회원", "개선", "구축"}
TOPIC_EXCLUDE = {"실패", "가입", "인증"}   # 주제 지도에서만 빼는 넓은 말 (‘본인인증’처럼 구체적인 말이 대신 보여요)


def _rows(m: dict) -> list[dict]:
    d = m["detail"]
    rows = [{"type": "topic", "text": t["title"]} for t in d["topics"]]
    rows += [{"type": "item", "text": x["text"]} for x in d["decisions"]]
    rows += [{"type": "item", "text": a["title"]} for a in d["actions"] if a["review"] != "rejected"]
    rows += [{"type": "item", "text": x["title"]} for x in d["issues"]]
    return rows


def keywords(rows: list[dict], limit: int = 12) -> list[tuple[str, int]]:
    """문장들에서 자주 나온 단어. 조사를 떼고 흔한 업무 단어(STOPWORDS)는 뺍니다. 안건 제목의 단어는 2배로 셉니다."""
    cnt: Counter = Counter()
    for r in rows:
        weight = 2 if r["type"] == "topic" else 1
        for raw in re.split(r"[\s,.·‘’'\"()?]+", r["text"]):
            w = _PARTICLE.sub("", raw)
            if len(w) >= 2 and w not in STOPWORDS and not w[0].isdigit():
                cnt[w] += weight
    return cnt.most_common(limit)


def meeting_words(m: dict) -> set[str]:
    return {w for w, _ in keywords(_rows(m), 15)} if m.get("detail") else set()


def week_topics(meetings: list[dict], limit: int = 6) -> list[dict]:
    """여러 회의에서 많이 논의된 주제와 비중(%).
    결과 문장에서 후보 단어를 뽑고, 원문 발언까지 포함해 몇 번 나왔는지로 순위를 매겨요.
    거의 항상 붙어 나오는 두 단어는 한 덩어리로 묶어요: ‘웰컴’ + ‘메일’ → ‘웰컴 메일’."""
    done = [m for m in meetings if m.get("detail")]
    rows = [r for m in done for r in _rows(m)]
    texts = [r["text"] for r in rows] + [s["text"] for m in done for s in m["detail"]["segments"]]
    occ = lambda w: sum(t.count(w) for t in texts)  # noqa: E731
    cands = {w: w for w, _ in keywords(rows, 24) if w not in TOPIC_EXCLUDE}   # 기준 단어 → 화면에 보일 이름
    for x in list(cands):
        for y in list(cands):
            if x == y or x not in cands or y not in cands:
                continue
            phrase = f"{x} {y}"
            if occ(phrase) >= 2 and occ(phrase) >= 0.6 * min(occ(x), occ(y)):
                base = x if occ(x) <= occ(y) else y
                cands.pop(x if base == y else y)
                cands[base] = phrase
    scored = sorted(((occ(base), label) for base, label in cands.items()), reverse=True)[:limit]
    total = sum(c for c, _ in scored) or 1
    base_of = {label: base for base, label in cands.items()}
    return [{"word": label, "base": base_of[label], "count": c, "pct": round(c / total * 100)} for c, label in scored if c]


def topic_hits(m: dict, base: str) -> dict | None:
    """회의 하나에서 주제 단어가 나온 곳: {"topics": [안건 번호], "decisions": n, "actions": n, "issues": n, "talk": 원문 등장 수}. 없으면 None."""
    d = m.get("detail")
    if not d:
        return None
    hit = {"topics": [i for i, t in enumerate(d["topics"], 1) if base in t["title"] or base in t["note"]],
           "decisions": sum(1 for x in d["decisions"] if base in x["text"]),
           "actions": sum(1 for a in d["actions"] if a["review"] != "rejected" and base in a["title"]),
           "issues": sum(1 for x in d["issues"] if base in x["title"]),
           "talk": sum(s["text"].count(base) for s in d["segments"])}
    return hit if any(hit.values()) else None


def previous_meeting(m: dict, all_meetings: list[dict]) -> tuple[dict | None, list[str]]:
    """이 회의보다 앞선 회의 중 키워드가 가장 많이 겹치는 회의 (회의 흐름의 ‘이전 회의’)."""
    mine = meeting_words(m)
    best, best_common = None, []
    for x in all_meetings:
        if x["id"] == m["id"] or not x.get("detail") or (x["date"], x["start"]) >= (m["date"], m["start"]):
            continue
        theirs = meeting_words(x)
        # 같은 말이거나 한쪽이 다른 쪽을 포함하면 겹친다고 봐요 (‘인증’ ↔ ‘본인인증’)
        common = sorted({a if len(a) <= len(b) else b for a in mine for b in theirs if a in b or b in a})
        if len(common) < 2:   # 겹치는 말이 2개 이상일 때만 이어진 회의로 봐요
            continue
        if len(common) > len(best_common) or (common and len(common) == len(best_common) and x["date"] > best["date"]):
            best, best_common = x, common
    return best, best_common


# ---------------------------------------------------------------- hotspot: 그림 위에 투명 버튼

def hotspots(key: str, svg: str, size: tuple[int, int], spots: list[dict]) -> None:
    """spots: {"box": (x, y, w, h) — SVG 좌표, "label", "on_click", "args"}. 버튼 위치는 SVG 크기 대비 %로 맞춥니다."""
    vw, vh = size
    css = []
    with st.container(key=f"hs_{key}"):
        html(svg)
        for i, s in enumerate(spots):
            x, y, w, h = s["box"]
            k = f"hsb_{key}_{i}"
            css.append(f".st-key-{k}{{left:{x / vw * 100:.2f}%;top:{y / vh * 100:.2f}%;width:{w / vw * 100:.2f}%!important;height:{h / vh * 100:.2f}%}}")
            st.button(s["label"], key=k, on_click=s["on_click"], args=s.get("args", ()), help=s.get("help"))
    html(f"<style>{''.join(css)}</style>")


# ---------------------------------------------------------------- 많이 논의된 주제 지도

MM_W, MM_H = 520, 300
_SLOTS = [(140, 70), (380, 66), (92, 196), (420, 200), (262, 262), (470, 132)]   # 순위별 자리 (가운데 = 기간)
_RX = [82, 74, 66, 60, 54, 48]
_FILL = [("#1B5BD4", "#fff"), ("#3B76E0", "#fff"), ("#6E9BEA", "#fff"), ("#B9CDF2", "#1B4FB8"), ("#DCE7FB", "#1B4FB8"), ("#EEF4FF", "#1B4FB8")]


def topic_map(topics: list[dict], center_label: str, on_pick, center_title: str = "이번 주", selected: str | None = None) -> None:
    """가운데 기간(이번 주 등), 주변에 주제 타원. 많이 논의된 주제일수록 크고 진해요. 누르면 on_pick(word). 고른 주제는 노란 테두리."""
    cx, cy = MM_W / 2, MM_H / 2
    links, nodes, spots = [], [], []
    for i, t in enumerate(topics[:len(_SLOTS)]):
        x, y = _SLOTS[i]
        rx, ry = _RX[i], _RX[i] * 0.48
        fill, ink = _FILL[i]
        links.append(f'<path d="M{cx} {cy} Q{(cx + x) / 2} {y} {x} {y}" fill="none" stroke="#B9CDF2" stroke-width="1.6"/>')
        fs = 16 if i < 2 else 14 if i < 4 else 13
        ring = ' stroke="#E8A400" stroke-width="4"' if selected == t["word"] else ""
        nodes.append(f'<g class="tm-node"><ellipse cx="{x}" cy="{y}" rx="{rx}" ry="{ry:.0f}" fill="{fill}"{ring}/>'
                     f'<text x="{x}" y="{y - 2}" text-anchor="middle" font-size="{fs}" font-weight="700" fill="{ink}">{esc(t["word"])}</text>'
                     f'<text x="{x}" y="{y + 16}" text-anchor="middle" font-size="12" fill="{ink}">{t["pct"]}%</text></g>')
        spots.append({"box": (x - rx, y - ry, 2 * rx, 2 * ry), "label": f"{t['word']} 주제 보기", "on_click": on_pick, "args": (t["word"],)})
    center = (f'<circle cx="{cx}" cy="{cy}" r="44" fill="#fff" stroke="#1B5BD4" stroke-width="2.5"/>'
              f'<text x="{cx}" y="{cy - 2}" text-anchor="middle" font-size="14" font-weight="700" fill="#191F28">{esc(center_title)}</text>'
              f'<text x="{cx}" y="{cy + 16}" text-anchor="middle" font-size="12" fill="#636E7B">{esc(center_label)}</text>')
    svg = f'<svg viewBox="0 0 {MM_W} {MM_H}" width="100%" role="img" aria-label="많이 논의된 주제">{"".join(links)}{center}{"".join(nodes)}</svg>'
    hotspots("topicmap", svg, (MM_W, MM_H), spots)


# ---------------------------------------------------------------- 막대

SHADES = ["#1B5BD4", "#3B76E0", "#6E9BEA", "#B9CDF2", "#DCE7FB", "#EEF4FF"]   # 오래 논의한 안건일수록 진하게


def share_bar_html(t: dict, longest: float) -> str:
    """결과 검토 요약 탭: 안건 한 줄 (이름 · 막대 · 분 · %)."""
    top = t["rank"] == 0
    return (f'<div class="sh-row{" top" if top else ""}"><span class="sh-no">{t["no"]}</span>'
            f'<span class="sh-t">{esc(t["title"])}{" <em>최다</em>" if top else ""}</span>'
            f'<span class="sh-bar"><i style="width:{t["min"] / longest * 100:.1f}%;background:{SHADES[min(t["rank"], 2)]}"></i></span>'
            f'<span class="sh-v"><b>{round(t["min"])}분</b> {round(t["share"] * 100)}%</span></div>')


def stacked_bar_html(m: dict, longest: int, label: str, when: str = "") -> str:
    """회의 하나의 막대. 칸 = 안건, 진할수록 오래 논의. (지금은 쓰지 않지만 다른 화면에서 쓸 수 있게 남겨둠)"""
    total = meeting_minutes(m)
    times = topic_times(m)
    if times:
        segs = "".join(f'<span title="{esc(t["title"])} · {round(t["min"])}분" style="flex:{t["share"]:.4f} 1 0;background:{SHADES[min(t["rank"], 3)]}"></span>'
                       for t in times)
        top = min(times, key=lambda t: t["rank"])
        note = f'가장 오래: {esc(top["title"])} {round(top["min"])}분'
    else:
        segs, note = '<span style="flex:1;background:var(--line)"></span>', "분석이 끝나면 안건별로 나눠 보여줘요"
    return (f'<div class="sb-row"><span class="sb-t">{label}</span>'
            f'<span class="sb-track"><span class="sb-bar" style="width:{total / longest * 100:.1f}%">{segs}</span></span>'
            f'<span class="sb-v">{total}분</span></div><div class="sb-note">{when}{" · " if when else ""}{note}</div>')