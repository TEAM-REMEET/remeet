"""폴더 그래프 탭: Neo4j Browser 처럼 노드 · 엣지를 그대로 보여 주는 화면 (vis-network, iframe 안에서 전부 처리).

클릭 = 오른쪽에 내용 · 더블클릭 = 이어진 노드 펼치기 · 라벨 칩 = 그 종류만 · 검색 = 글자가 들어간 노드만 강조, 나머지 회색.
vis-network 는 assets/vendor 에 넣어 둔 파일을 그대로 넣음 (인터넷 없이도 뜨게 · 9.1.9, Apache-2.0 OR MIT).
데이터의 글자는 전부 textContent 로만 넣음 (회의록 문장에 HTML 이 섞여 있어도 실행되지 않게).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import streamlit.components.v1 as components

_VIS = Path(__file__).parent.parent / "assets" / "vendor" / "vis-network.min.js"


@lru_cache(maxsize=1)
def _vis_js() -> str:
    return _VIS.read_text(encoding="utf-8")


_TEMPLATE = r"""<!doctype html><html><head><meta charset="utf-8"><style>
*{box-sizing:border-box}
html,body{margin:0;height:100%;background:#1F2023;color:#E8E9EB;font-family:"Noto Sans KR",-apple-system,"Apple SD Gothic Neo",sans-serif;font-size:14px}
#app{display:flex;flex-direction:column;height:100%;border:1px solid #2E3036;border-radius:6px;overflow:hidden}
.top{display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:10px 12px;background:#25262B;border-bottom:1px solid #2E3036}
.chip{border:0;border-radius:99px;padding:5px 13px;font:inherit;font-size:13px;font-weight:700;cursor:pointer;color:#1A1A1A;opacity:.55}
.chip.on{opacity:1;box-shadow:0 0 0 2px #E8E9EB}
.chip:focus-visible,button:focus-visible,input:focus-visible{outline:2px solid #7FE7F0;outline-offset:2px}
.search{margin-left:auto;display:flex;gap:8px;align-items:center}
.search input{width:220px;padding:6px 10px;border-radius:5px;border:1px solid #3A3C42;background:#1F2023;color:#E8E9EB;font:inherit}
.count{font-size:12px;color:#9A9DA3;min-width:56px}
.ghost{border:1px solid #3A3C42;background:none;color:#C9CBD0;border-radius:5px;padding:5px 10px;font:inherit;font-size:13px;cursor:pointer}
.main{flex:1;display:flex;min-height:0}
#net{flex:1;min-width:0}
aside{width:340px;flex:none;background:#25262B;border-left:1px solid #2E3036;overflow-y:auto;padding:16px}
aside h3{margin:6px 0 14px;font-size:17px;line-height:1.4}
.kind{display:inline-block;border-radius:99px;padding:2px 10px;font-size:12px;font-weight:700;color:#1A1A1A}
.f{margin-bottom:14px}
.f b{display:block;font-size:12px;color:#9A9DA3;font-weight:600;margin-bottom:3px}
.f p{margin:0;line-height:1.6}
.f ul{margin:0;padding:0;list-style:none;display:flex;flex-direction:column;gap:6px}
.f li{line-height:1.55;padding-left:10px;border-left:2px solid #3A3C42}
.tag{display:inline-block;margin-right:6px;padding:0 6px;border-radius:3px;font-size:11.5px;font-weight:700;background:#3A3C42;color:#E8E9EB}
.tag.con{background:#5A2A28;color:#FFB4AB}.tag.prop{background:#22355A;color:#A8C7FA}.tag.dec{background:#1F3A2A;color:#8FD9A8}.tag.hold{background:#4A3F14;color:#F2D675}
.links{display:flex;flex-direction:column;gap:4px}
.links button{text-align:left;background:#1F2023;border:1px solid #2E3036;border-radius:5px;color:#E8E9EB;padding:6px 8px;font:inherit;font-size:13px;cursor:pointer}
.links button span{color:#9A9DA3;font-size:11px;margin-right:6px}
.empty{color:#9A9DA3;line-height:1.6}
.hint{padding:6px 12px;font-size:12px;color:#9A9DA3;background:#25262B;border-top:1px solid #2E3036}
@media (max-width:760px){aside{display:none}}
</style></head><body><div id="app">
<div class="top"><div id="chips" style="display:flex;gap:6px;flex-wrap:wrap"></div>
<div class="search"><input id="q" type="search" placeholder="보이는 노드에서 검색" aria-label="보이는 노드에서 검색"><span class="count" id="count"></span>
<button class="ghost" id="reset" type="button">처음으로</button></div></div>
<div class="main"><div id="net"></div><aside id="panel" aria-live="polite"></aside></div>
<div class="hint">클릭: 내용 보기 · 더블클릭: 이어진 노드 펼치기 · 드래그: 옮기기 · 휠: 확대/축소</div>
</div>
<script>__VIS__</script>
<script>
const G = __DATA__;
const COLOR = {Project:"#57C7E3", Meeting:"#8FAADC", Agenda:"#C9C6FF", Topic:"#FFC2D1", Discussion:"#C8C26B",
               ActionItem:"#F7B07A", Decision:"#9EE0A8", Person:"#D9C8AE"};
const CHIPS = [["Topic","주제"],["Agenda","안건"],["ActionItem","할 일"],["*","전체"]];
const TAG = {"반대·우려":"con","제안":"prop","결정":"dec","보류":"hold"};
const byId = new Map(G.nodes.map(n => [n.id, n]));
const adj = new Map(G.nodes.map(n => [n.id, []]));
G.edges.forEach((e, i) => { e.id = i; adj.get(e.from).push({e, other: e.to, out: true}); adj.get(e.to).push({e, other: e.from, out: false}); });
const hay = new Map(G.nodes.map(n => [n.id, [n.caption, ...n.fields.flatMap(f => [].concat(f.value)), ...(n.lines || []).map(l => l.text)].join(" ").toLowerCase()]));

let label = "Topic", query = "", selected = null, visible = new Set();
const nodes = new vis.DataSet(), edges = new vis.DataSet();
const net = new vis.Network(document.getElementById("net"), {nodes, edges}, {
  nodes: {shape: "circle", borderWidth: 2, font: {size: 11, face: "Noto Sans KR, sans-serif"}, widthConstraint: {minimum: 72, maximum: 72}},
  edges: {arrows: {to: {enabled: true, scaleFactor: .5}}, color: {color: "#55585F", highlight: "#7FE7F0"}, width: 1.2,
          font: {size: 9, color: "#9A9DA3", strokeWidth: 0, align: "middle"}, smooth: {type: "continuous"}},
  physics: {solver: "forceAtlas2Based", forceAtlas2Based: {gravitationalConstant: -110, springLength: 150, avoidOverlap: 0.8}, stabilization: {iterations: 150}},
  interaction: {hover: true, doubleClickZoom: false}
});

const short = s => s.length > 18 ? s.slice(0, 17) + "…" : s;
const matches = id => !query || hay.get(id).includes(query);
function look(n) {
  const hit = matches(n.id), on = n.id === selected;
  return {id: n.id, label: short(n.caption),
    color: {background: hit ? COLOR[n.label] : "#3A3C42", border: on ? "#7FE7F0" : (query && hit ? "#FFFFFF" : (hit ? COLOR[n.label] : "#4A4C52")),
            highlight: {background: hit ? COLOR[n.label] : "#4A4C52", border: "#7FE7F0"}, hover: {background: hit ? COLOR[n.label] : "#4A4C52", border: "#E8E9EB"}},
    borderWidth: on ? 4 : (query && hit ? 3 : 2), font: {color: hit ? "#1A1A1A" : "#8A8D93"}};
}
function render() {
  const keep = new Set(visible);
  nodes.remove(nodes.getIds().filter(id => !keep.has(id)));
  nodes.update([...keep].map(id => look(byId.get(id))));
  const want = G.edges.filter(e => keep.has(e.from) && keep.has(e.to));
  const wantIds = new Set(want.map(e => e.id));
  edges.remove(edges.getIds().filter(id => !wantIds.has(id)));
  edges.update(want.map(e => ({id: e.id, from: e.from, to: e.to, label: e.type,
    color: {color: query && !(matches(e.from) || matches(e.to)) ? "#33353A" : "#55585F", highlight: "#7FE7F0"}})));
  const c = document.getElementById("count");
  c.textContent = query ? [...keep].filter(matches).length + "개 찾음" : "노드 " + keep.size + "개";
}
function chips() {
  const box = document.getElementById("chips"); box.textContent = "";
  CHIPS.forEach(([key, name]) => {
    const b = document.createElement("button"); b.type = "button"; b.className = "chip" + (label === key ? " on" : "");
    b.style.background = COLOR[key] || "#E8E9EB"; b.textContent = name;
    b.setAttribute("aria-pressed", label === key);
    b.onclick = () => { label = key; reset(); }; box.appendChild(b);
  });
}
function reset() {
  visible = new Set(G.nodes.filter(n => label === "*" || n.label === label).map(n => n.id));
  selected = null; chips(); render(); panel(); net.once("stabilized", () => net.fit({animation: true})); setTimeout(() => net.fit(), 1200);  // stabilized 가 안 올 때(변화 없음) 대비
}
function el(tag, cls, text) { const x = document.createElement(tag); if (cls) x.className = cls; if (text != null) x.textContent = text; return x; }
function panel() {
  const p = document.getElementById("panel"); p.textContent = "";
  const n = selected && byId.get(selected);
  if (!n) { p.appendChild(el("p", "empty", "노드를 누르면 내용이 여기에 보여요. 더블클릭하면 이어진 노드가 펼쳐져요.")); return; }
  const k = el("span", "kind", n.kind); k.style.background = COLOR[n.label]; p.appendChild(k);
  p.appendChild(el("h3", null, n.caption));
  n.fields.slice(1).forEach(f => {
    const box = el("div", "f"); box.appendChild(el("b", null, f.name));
    if (Array.isArray(f.value)) { const ul = el("ul"); f.value.forEach(v => ul.appendChild(el("li", null, v))); box.appendChild(ul); }
    else box.appendChild(el("p", null, f.value));
    p.appendChild(box);
  });
  if (n.lines && n.lines.length) {
    const box = el("div", "f"); box.appendChild(el("b", null, "논의")); const ul = el("ul");
    n.lines.forEach(l => { const li = el("li");
      [l.tag, l.decision_tag].filter(Boolean).forEach(t => li.appendChild(el("span", "tag " + (TAG[t] || ""), t)));
      li.appendChild(document.createTextNode(l.text)); ul.appendChild(li); });
    box.appendChild(ul); p.appendChild(box);
  }
  const near = adj.get(n.id);
  const box = el("div", "f"); box.appendChild(el("b", null, "이어진 노드 " + near.length + "개"));
  const links = el("div", "links");
  near.forEach(({e, other, out}) => { const o = byId.get(other); const b = el("button"); b.type = "button";
    b.appendChild(el("span", null, (out ? e.type + " →" : "← " + e.type) + " " + o.kind)); b.appendChild(document.createTextNode(short(o.caption)));
    b.onclick = () => { visible.add(other); select(other); net.focus(other, {scale: 1.1, animation: true}); };
    links.appendChild(b); });
  box.appendChild(links); p.appendChild(box);
}
function select(id) { selected = id; render(); panel(); net.selectNodes([id]); }
net.on("click", ev => { if (ev.nodes.length) select(ev.nodes[0]); });
net.on("doubleClick", ev => { if (!ev.nodes.length) return; const id = ev.nodes[0];
  adj.get(id).forEach(({other}) => visible.add(other)); select(id); });
document.getElementById("q").addEventListener("input", ev => { query = ev.target.value.trim().toLowerCase(); render(); });
document.getElementById("reset").onclick = () => { document.getElementById("q").value = ""; query = ""; reset(); };
reset();
</script></body></html>"""


def folder_graph(data: dict, *, height: int = 680) -> None:
    """data: GET /api/projects/{id}/graph 결과 {nodes: [{id, label, kind, caption, fields, lines?}], edges: [{from, to, type}]}."""
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")  # 문장 속 </script> 가 태그를 닫지 않게
    components.html(_TEMPLATE.replace("__VIS__", _vis_js()).replace("__DATA__", payload), height=height)
