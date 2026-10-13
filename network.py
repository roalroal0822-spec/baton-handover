"""공공기관 특화 '휴먼 네트워크 맵'(암묵지 시각화)과 '노하우 카드'.

담당 업무(가운데) ─ 세부 업무(안쪽 고리) ─ 협업 상대(바깥 고리)를 그래프로 만든다. 협업 상대는 사람 이름이 아니라
역할로만 적는다(예: '경영지원팀 주임', '(주)그린공조 팀장', '의회 전문위원실', '반복 민원').
노하우 카드는 자료 속 '사전 협의 필수', '마감 3일 전 재확인' 같은 실무 요령 문장을 자동으로 뽑고,
전임자가 직접 적은 카드를 더해 협업 상대(노드)에 붙인다.
"""
import hashlib
import re

from core.textutil import similarity, split_sentences

GROUPS = {
    "team": {"name": "우리 팀", "color": "#1d4ed8"},
    "dept": {"name": "기관 내 협업 부서", "color": "#0891b2"},
    "gov": {"name": "외부 기관·의회", "color": "#7c3aed"},
    "vendor": {"name": "업체", "color": "#ea580c"},
    "civil": {"name": "민원", "color": "#dc2626"},
}
VENDOR_RX = re.compile(r"\(주\)|㈜|주식회사|\(유\)|\(재\)|업체|상사|산업|기술|테크|서비스|건설|엔지니어링|시스템|솔루션|보험사")
GOV_RX = re.compile(r"구청|시청|군청|도청|의회|연구원|보건|소방서|경찰|교육청|위원회|행정복지센터|주민센터|[시도군구]\s|부$|처$|청$|공단$|공사$")
DEPT_RX = re.compile(r"(팀|과|실|계|센터|본부|단)$")
TIP_RX = re.compile(r"필수|반드시|꼭|사전\s*협의|미리|주의|재확인|다시\s*확인|한\s*번\s*더|까다|깐깐|선호|싫어|자주|항상|무조건|요령|"
                    r"\d+\s*일\s*전|반려|빠뜨리|잊지|기록|챙기|먼저|반복\s*제기|민원\s*많|협의해야|협의\s*후")
NOT_TIP = re.compile(r"^(연번|계약명|사업명|이름|Subject|From|To|Date)\s*[:|]|^\[(연락처|담당업무)\]")
KINDS = ("사전 협의", "재확인", "주의", "요령")


def _id(prefix, text):
    return f"{prefix}-{hashlib.sha1(text.encode('utf-8')).hexdigest()[:8]}"


def _core(org):
    """'(주)그린공조' → '그린공조', '기관 경영지원팀' → '경영지원팀'"""
    o = re.sub(r"\(주\)|㈜|주식회사|\(유\)|\(재\)|^기관\s+", "", org or "").strip()
    return o


def _team(h):
    S = h["draft"]["sections"]
    return next((c.get("org") for c in S["contacts"] if re.search(r"(팀|과|실)$", c.get("org") or "")
                 and "결재권자" in " ".join(c.get("topics") or [])), "") or \
        next((m.group(1) for m in [re.match(r"^(\S+(?:팀|과|실))\s", h.get("title") or "")] if m), "")


def group_of(c, team=""):
    org, title = c.get("org") or "", c.get("title") or ""
    topics = " ".join(c.get("topics") or [])
    if VENDOR_RX.search(org) or title in ("대표", "소장") and not GOV_RX.search(org):
        return "vendor"
    if team and _core(org) == _core(team) or re.search(r"결재권자|부재 시|업무 대행", topics):
        return "team"
    if GOV_RX.search(org + " "):
        return "gov"
    if DEPT_RX.search(_core(org)):
        return "dept"
    return "gov" if org else "dept"


def _texts(h, blocks):
    """노하우·민원·의회를 찾을 글: 원문 블록 + 이어받은 메모 + 현안 다음 할 일"""
    out = []
    for b in blocks or []:
        for line in re.split(r"\n+", b.get("text") or ""):
            line = line.strip(" -•·\t")
            src = {"file": b.get("file", ""), "loc": b.get("loc", ""), "block_id": b.get("id", ""),
                   "line": line if len(line) <= 160 else ""}
            for s in split_sentences(line):
                out.append((s.strip(" -•·\t"), src))
    for n in h["draft"].get("carried_notes") or []:
        out.append((n.get("text", ""), {"file": "이전 인수인계(바통)", "loc": "메모", "block_id": "", "line": ""}))
    return [(t, s) for t, s in out if 6 <= len(t) <= 220]


def _kind(t):
    if re.search(r"사전\s*협의|협의\s*(필수|해야|후)|미리\s*협의|먼저\s*(보고|협의)|보고\s*후", t):
        return "사전 협의"
    if re.search(r"재확인|다시\s*확인|한\s*번\s*더|\d+\s*일\s*전|확인\s*필요", t):
        return "재확인"
    if re.search(r"주의|반려|민원|반복|지적|싫어|까다|깐깐|빠뜨리|잊지", t):
        return "주의"
    return "요령"


def build(h, blocks=None):
    from report import live
    d = h["draft"]
    S = d["sections"]
    team = _team(h)
    center = {"id": "center", "label": h.get("title") or "담당 업무", "group": "center"}
    duties = [{"id": f"d{i}", "label": r["duty"], "group": "duty"}
              for i, r in enumerate([r for r in live(S["rnr"]) if not re.search(r"참고사항|메모", r["duty"])][:8])]

    def best_duty(text, prefer=None):
        if prefer:
            hit = next((x for x in duties if re.search(prefer, x["label"])), None)
            if hit:
                return hit["id"]
        if not duties or not text:
            return "center"
        sc = max(duties, key=lambda x: similarity(x["label"], text))
        return sc["id"] if similarity(sc["label"], text) >= 0.08 else "center"

    nodes = []
    for c in live(S["contacts"]):
        g = group_of(c, team)
        topics = [t for t in c.get("topics") or [] if t][:4]
        prefer = r"계약|용역" if g == "vendor" else r"예산" if re.search(r"예산", " ".join(topics) + c.get("org", "")) else \
            r"민원" if "민원" in " ".join(topics) else None
        nodes.append({"id": _id("n", c["name"]), "label": c["name"], "group": g, "org": c.get("org") or "", "title": c.get("title") or "",
                      "phones": c.get("phones") or [], "emails": c.get("emails") or [], "topics": topics,
                      "link": best_duty(" ".join(topics), prefer) if g != "team" else "center",
                      "source": "; ".join(f"{s['file'].split('/')[-1]} {s['loc']}" for s in c.get("sources", [])[:1]),
                      "keys": [k for k in {c["name"], _core(c.get("org")), *[t for t in topics[:2]]} if k and len(k) >= 2]})
    texts = _texts(h, blocks)
    # 의회·민원: 연락처 표에는 없지만 공공기관 업무에서 빠질 수 없는 상대. 자료에 나오면 역할 노드로 만든다
    council = [t for t, _ in texts if re.search(r"의회|전문위원|행정사무감사|의원", t)]
    if council and not any(n["group"] == "gov" and "의회" in n["org"] for n in nodes):
        nodes.append({"id": "n-council", "label": "의회(전문위원실)", "group": "gov", "org": "지방의회", "title": "전문위원",
                      "phones": [], "emails": [], "topics": _uniq(council)[:3], "link": best_duty(" ".join(council), r"예산|민원"),
                      "source": "자료 속 언급", "keys": ["의회", "전문위원", "행정사무감사", "의원"]})
    civil = [t for t, _ in texts if re.search(r"민원", t)]
    civil += [r["duty"] + (" — " + r["detail"] if r.get("detail") else "") for r in live(S["rnr"]) if "민원" in r["duty"]]
    if civil:
        nodes.append({"id": "n-civil", "label": "반복 민원(이용자)", "group": "civil", "org": "민원인(개인정보 제외)", "title": "",
                      "phones": [], "emails": [], "topics": _uniq(civil)[:4], "link": best_duty(" ".join(civil), r"민원"),
                      "source": "자료 속 언급", "keys": ["민원", "이용자", "민원인"]})

    # 노하우 카드: 자동(자료 문장) + 직접 적은 카드
    hidden = set(h.get("knowhow_hidden") or [])
    cards, seen = [], []
    for t, src in texts:
        if NOT_TIP.search(t) or not TIP_RX.search(t) and not t.startswith("[노하우"):
            continue
        # 메모 한 줄은 한 가지 생각: 짧은 줄이면 줄 전체를 카드로(앞 문장의 맥락을 잃지 않게)
        t = re.sub(r"^\[노하우[^\]]*\]\s*", "", src.get("line") or t)
        target = _target(t, nodes)
        if not target and (len(t) < 15 or not re.search(r"협의|업체|민원|의회|결재|팀장|과장|부서|담당|전에|마감|보고", t)):
            continue  # 사람·관계와 무관한 일반 메모(예: '10월엔 탁도 꼭 다시 볼 것')는 일정·현안 쪽에 있다
        if any(similarity(t, x) >= 0.6 for x in seen):
            continue
        seen.append(t)
        cid = _id("k", t)
        if cid in hidden:
            continue
        cards.append({"id": cid, "target": target, "kind": _kind(t), "text": t, "auto": True,
                      "source": f"{src['file'].split('/')[-1]} {src['loc']}".strip(), "block_id": src.get("block_id", "")})
    for k in h.get("knowhow") or []:
        cards.append({**k, "auto": False, "target": k.get("target") if any(n["id"] == k.get("target") for n in nodes) else ""})
    for n in nodes:
        n["tips"] = [c["id"] for c in cards if c["target"] == n["id"]]
        n.pop("keys", None)
    edges = [{"from": "center", "to": x["id"]} for x in duties] + [{"from": n["link"], "to": n["id"]} for n in nodes]
    return {"center": center, "duties": duties, "nodes": nodes, "edges": edges, "cards": cards,
            "groups": {k: v for k, v in GROUPS.items() if any(n["group"] == k for n in nodes)},
            "counts": {"nodes": len(nodes), "cards": len(cards), "manual": sum(1 for c in cards if not c["auto"])}}


def _uniq(xs):
    out = []
    for x in xs:
        if all(similarity(x, y) < 0.6 for y in out):
            out.append(x)
    return out


def _target(t, nodes):
    best, score = "", 0
    for n in nodes:
        s = 0
        for k in n.get("keys") or []:
            if k and k in t:
                s = max(s, len(k) + (5 if k == n["label"] else 0))
        if s > score:
            best, score = n["id"], s
    return best


def who_knows(h, q, blocks=None):
    """AI 분신이 답을 못 찾았을 때: 물어볼 만한 협업 상대(역할) 2곳."""
    net = build(h, blocks or [])
    scored = []
    for n in net["nodes"]:
        if n["group"] == "civil":
            continue  # 민원인은 물어볼 상대가 아니다
        text = " ".join([n["label"], n["org"], *n["topics"]])
        s = similarity(text, q) + sum(0.3 for w in re.findall(r"[가-힣A-Za-z]{2,}", q) if w in text)
        if s > 0.12:
            scored.append((s, n))
    scored.sort(key=lambda x: -x[0])
    return [{"label": n["label"], "group": GROUPS[n["group"]]["name"], "phone": ", ".join(n["phones"][:1]),
             "why": ", ".join(n["topics"][:1])} for _, n in scored[:2]]


def blocks_for_doc(net):
    """인수인계서용: 협업 네트워크 표 + 노하우 카드 표"""
    label = {n["id"]: n["label"] for n in net["nodes"]}
    duty = {x["id"]: x["label"] for x in net["duties"]}
    rows = [[GROUPS[n["group"]]["name"], n["label"], duty.get(n["link"], "업무 전반"), ", ".join(n["topics"][:2]),
             ", ".join(n["phones"][:1] + n["emails"][:1]), str(len(n["tips"]))] for n in
            sorted(net["nodes"], key=lambda n: list(GROUPS).index(n["group"]))]
    b = [("table", ["구분", "협업 상대(역할)", "연결 업무", "이럴 때", "연락처", "노하우"], rows, [1, 2, 2, 3, 2, 1])]
    if net["cards"]:
        b += [("h2", f"노하우 카드 ({len(net['cards'])}장)"),
              ("table", ["유형", "대상", "실무 요령", "출처"],
               [[c["kind"], label.get(c["target"], "공통"), c["text"], c.get("source") or ("전임자 작성" if not c["auto"] else "")]
                for c in net["cards"]], [1, 2, 5, 2])]
    b.append(("note", "※ 개인정보 보호를 위해 사람 이름은 적지 않고 소속·직위 등 역할로만 표시합니다."))
    return b
