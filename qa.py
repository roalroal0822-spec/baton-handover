"""후임자 질문 답변: 전임자 자료와 확인된 인수인계 내용만 근거로 답한다.

규칙 기반 답변은 질문의 핵심 낱말(사람·대상어)이 실제로 들어 있는 원문 문장과 정리된 항목만 근거로 쓴다.
핵심 낱말이 들어 있는 근거가 없으면 관련 없는 목록을 늘어놓지 않고 '자료에서 찾지 못했다'고 답한다.
"""
import math
import re

from core.search import BM25
from core.textutil import split_sentences, strip_josa

QA_SYSTEM = """당신은 인수인계를 받은 후임자의 질문에 답하는 도우미입니다.
반드시 아래 [자료] 안의 내용만 근거로 답하고, 근거 번호를 문장 끝에 [1]처럼 붙입니다.
자료에 답이 없으면 "자료에서 찾지 못했습니다"라고 답하고 누구에게 물어보면 좋을지 연락처 자료가 있으면 알려줍니다.
출력 JSON: {"answer": "답변", "used": [근거 번호들]}"""

NOT_FOUND = "자료에서 찾지 못했습니다. 연락처 탭에서 관련 담당자에게 확인해 보세요."

# 질문에서 뜻을 더하지 않는 말
STOP = {"누구", "누가", "누군", "누구야", "누구예요", "어디", "어디야", "언제", "언제야", "뭐", "뭐야", "뭔가", "무엇", "무슨",
        "어떻게", "어떤", "알려줘", "알려", "알려주세요", "있어", "있나", "있나요", "있어요", "있는", "해", "해요", "해야",
        "하나", "하나요", "하는", "할", "돼", "되나", "되나요", "인가", "인가요", "이야", "이에요", "좀", "거", "것", "날",
        "날짜", "시", "때", "주세요", "해줘", "궁금", "알고", "싶어", "싶어요", "나요", "가요", "요", "님", "그", "이", "저",
        "몇", "며칠", "얼마", "어느", "말해줘", "보여줘", "정리", "내용", "관련", "대해", "대한", "일이", "일은", "일을", "할일"}
# 답의 종류를 정하는 의도어: 이런 말이 있으면 그 답 꼴(기한·위치·종료일·번호)이 들어 있는 근거를 우선한다
STRONG = {"기한", "마감", "언제까지", "끝나", "끝나는", "끝", "종료", "만료", "어디", "위치", "연락처", "전화", "번호", "전화번호"}
# 질문의 의도어 → 원문에서 함께 찾을 말 (핵심 낱말은 아님)
INTENT = {
    "기한": ["이내", "까지", "기한", "마감"], "마감": ["이내", "까지", "마감", "기한"], "언제까지": ["이내", "까지", "기한"],
    "끝나": ["종료", "만료", "계약기간", "까지", "~"], "끝나는": ["종료", "만료", "계약기간", "까지", "~"],
    "끝": ["종료", "만료", "계약기간", "~"], "종료": ["종료", "만료", "계약기간", "~"], "만료": ["만료", "종료", "계약기간", "~"],
    "어디": ["폴더", "위치", "있음", "보관", "공유"], "위치": ["폴더", "위치", "보관"],
    "처리": ["처리", "답변"], "계약": ["계약"], "업무": ["업무"], "일정": ["일정"],
    "연락처": ["연락처", "전화", "내선", "-"], "연락": ["연락처", "전화"], "전화": ["전화", "연락처", "-"], "번호": ["연락처", "-"],
    "전화번호": ["연락처", "전화", "-"], "담당자": ["담당"], "협의": ["협의"],
}
# 핵심 낱말이면서 함께 찾을 말이 있는 것
ANCHOR_SYN = {"대행": ["대행", "부재", "대신"], "대신": ["대행", "대신", "부재"], "부재": ["부재", "대행"]}
TITLES = ("팀장", "과장", "주무관", "주임", "대리", "실장", "부장", "차장", "센터장", "소장", "국장", "이사장", "PM")
ROLE_Q = re.compile(r"누구|누가|누군")
CONTACT_Q = re.compile(r"누구|누가|누군|연락|전화|번호|담당자|협의|물어|대행")
EXTERNAL = re.compile(r"\(주\)|㈜|주식회사|업체|기술|서비스")


def _item_text(sec, it):
    if sec == "schedule":
        return (f"[일정] {it.get('date') or ''} {', '.join(str(m) + '월' for m in it['months'][:3])} {it.get('day') or ''} "
                f"{it['task']} {it.get('related', '')}")
    if sec == "contacts":
        return f"[연락처] {it['name']} {it.get('title', '')} {it.get('org', '')} {' '.join(it.get('phones', []))} {' '.join(it.get('topics', []))}"
    if sec == "issues":
        return f"[현안] {it['title']} {it.get('state', '')} {it.get('next_action', '')} {it.get('due', '')}"
    return f"[담당업무] {it.get('duty', '')} {it.get('detail', '')}"


def _flat(s):
    return re.sub(r"\s+", "", s or "")


def query_terms(q):
    """(핵심 낱말 [(낱말, 찾을 변형들)], 의도어 [(낱말, 찾을 변형들)])"""
    anchors, intents = [], []
    for w in re.findall(r"[가-힣A-Za-z0-9]+", q):
        if re.fullmatch(r"\d+", w):
            continue
        w0 = w
        w = strip_josa(w)
        w = re.sub(r"(님|이야|야|해요|해|예요|이에요|이랑|랑|한테|께)$", "", w) if len(w) > 2 else w
        w = strip_josa(w)
        if w in STOP or w0 in STOP or len(w) < 2:
            continue
        if w in INTENT:
            intents.append((w, INTENT[w]))
        elif w in ANCHOR_SYN:
            anchors.append((w, ANCHOR_SYN[w]))
        else:
            var = [w]
            if re.search(r"용역$|공사$|사업$", w) and len(w) >= 4:
                var.append(re.sub(r"(용역|공사|사업)$", "", w))
            anchors.append((w, var))
    return anchors, intents


def _units(blocks):
    out = []
    for b in blocks:
        text = b.get("text") or ""
        if (b.get("loc") or "").startswith("머리글"):
            continue
        parts = [text] if ("!" in (b.get("loc") or "") or "|" in text) else (split_sentences(text) or [text])
        for p in parts:
            p = re.sub(r"^[\-•·*▪○□◦●※]+\s*", "", p.strip())
            if len(p) >= 4:
                out.append((p, b))
    return out


class Scorer:
    """핵심 낱말 기준 점수: 드문 낱말일수록 무겁게(idf), 의도어는 절반 무게."""

    def __init__(self, texts, anchors, intents):
        self.flats = [_flat(t) for t in texts]
        self.anchors, self.intents = anchors, intents
        n = max(1, len(texts))
        self.w = {}
        for term, var in anchors + intents:
            df = sum(1 for f in self.flats if any(v in f for v in var))
            self.w[term] = math.log(1 + n / (1 + df))

    def score(self, i):
        f = self.flats[i]
        hit = [t for t, var in self.anchors if any(v in f for v in var)]
        if not hit:
            return 0.0, 0
        s = sum(self.w[t] for t in hit)
        s += 0.5 * sum(self.w[t] for t, var in self.intents if any(v in f for v in var))
        return s, len(hit)

    def rank(self, k, rel=0.7):
        need = max(1, math.ceil(len(self.anchors) / 2))
        sc = [(self.score(i), i) for i in range(len(self.flats))]
        sc = [(s, i) for (s, nh), i in sc if s > 0 and nh >= need]
        sc.sort(key=lambda x: -x[0])
        if not sc:
            return []
        top = sc[0][0]
        return [i for s, i in sc if s >= top * rel][:k]


def _role_lookup(q, contacts):
    """'팀장님 누구야?'처럼 직위만 묻는 질문: 같은 직위의 내부 인원을 먼저, 외부 업체는 뒤로."""
    title = next((t for t in TITLES if t in q), None)
    if not title or not ROLE_Q.search(q):
        return None
    cands = [c for c in contacts if c.get("title") == title]
    if not cands:
        return None
    internal = [c for c in cands if not EXTERNAL.search(c.get("org") or "")]
    external = [c for c in cands if c not in internal]
    return internal, external


def _cite(s):
    return f"[{s['file'].split('/')[-1]} {s['loc']}]"


def _item_line(s, it):
    if s == "schedule":
        if it.get("recurring") == "매월":
            when = "매월" + (f" {it['day']}일" if it.get("day") else "")
        elif it.get("date"):
            when = it["date"]
        else:
            when = f"{it['months'][0]}월" + (f" {it['day']}일" if it.get("day") else "")
        extra = f" ({it['timing']})" if it.get("timing") else ""
        return f"• {when}: {it['task']}{extra}"
    if s == "contacts":
        return (f"• {it['name']} {it.get('title', '')} ({it.get('org', '') or '-'}) {', '.join(it.get('phones', [])) or '연락처 없음'}"
                f" — {', '.join(it.get('topics', [])[:2])}")
    if s == "issues":
        return f"• [현안] {it['title']} [{it.get('state', '')}] 다음 할 일: {it.get('next_action') or '-'} / 기한: {it.get('due') or '-'}"
    return f"• [담당업무] {it.get('duty')}" + (f" — {it['detail']}" if it.get("detail") else "")


def retrieve(draft, blocks, q):
    """(정리된 항목 [(구분, 항목)], 원문 문장 [(문장, 블록)], 방식) — 방식: month/role/list/search/none"""
    sections = draft["sections"]
    items = [(sec, it) for sec in ("schedule", "contacts", "issues", "rnr") for it in sections[sec]
             if it.get("status") != "삭제"]
    anchors, intents = query_terms(q)
    month = re.search(r"(\d{1,2})\s*월", q)
    if month and re.search(r"할\s*일|일정|해야|뭐|무엇|업무", q):
        m = int(month.group(1))
        anchors = [a for a in anchors if not re.search(r"\d", a[0])]
        # 이미 끝난 일·지난 기록(반복 업무 제외)은 '할 일'로 내지 않는다
        found = [(s, it) for s, it in items if s == "schedule" and m in it["months"]
                 and not (it.get("timing") in ("완료", "지난 일") and not it.get("recurring"))]
        if anchors:
            sc = Scorer([_item_text(*x) for x in found], anchors, intents)
            found = [found[i] for i in sc.rank(8, rel=0.01)] or found
        return found, [], "month"
    role = _role_lookup(q, [it for s, it in items if s == "contacts"])
    if role:
        internal, external = role
        found = [("contacts", c) for c in internal + external]
        return found, [], "role"
    anchors = [a for a in anchors if a[0] not in TITLES or len(anchors) == 1]
    if not anchors:
        if re.search(r"현안|진행\s*중|남은|급한|밀린|마감|기한", q):
            return [(s, it) for s, it in items if s == "issues"], [], "list"
        return [], [], "none"
    strong = [v for t, var in intents if t in STRONG for v in var]

    def prefer(cands, text_of):
        if not strong:
            return cands
        keep = [c for c in cands if any(v in _flat(text_of(c)) for v in strong)]
        return keep or cands

    # 정리된 항목
    sc = Scorer([_item_text(*x) for x in items], anchors, intents)
    found = prefer([items[i] for i in sc.rank(6)], lambda x: _item_text(*x))
    if CONTACT_Q.search(q):
        found.sort(key=lambda x: x[0] != "contacts")
    # 원문 문장
    units = _units(blocks)
    su = Scorer([u[0] for u in units], anchors, intents)
    sents = prefer([units[i] for i in su.rank(4, rel=0.6)], lambda u: u[0])[:3]
    return found, sents, "search"


def answer(draft, blocks, q, llm=None):
    q = q.strip()
    found_items, sents, how = retrieve(draft, blocks, q)

    sources = []
    for _, it in found_items[:6]:
        sources.extend(it.get("sources", [])[:2])
    for text, b in sents:
        sources.append({"block_id": b["id"], "file": b["file"], "loc": b["loc"], "quote": text[:160]})
    if llm is not None and llm.enabled and how in ("search", "none"):
        # 모델에는 핵심 낱말과 관련된 원문 조각을 조금 더 넓게 준다
        bidx = BM25(blocks, lambda b: b["text"])
        for b, _ in bidx.search(q, k=4):
            sources.append({"block_id": b["id"], "file": b["file"], "loc": b["loc"], "quote": b["text"][:160]})
    uniq, seen = [], set()
    for s in sources:
        k = (s["file"], s["loc"])
        if k not in seen:
            seen.add(k)
            uniq.append(s)
    sources = uniq[:8]

    fallback_note = ""
    if llm is not None and llm.enabled and sources:
        ctx = "\n".join(f"[{i + 1}] ({s['file']} {s['loc']}) {s['quote']}" for i, s in enumerate(sources))
        extra = "\n".join(_item_text(*x) for x in found_items[:8])
        try:
            out = llm.json(QA_SYSTEM, f"[자료]\n{ctx}\n\n[정리된 인수인계 항목]\n{extra}\n\n[질문]\n{q}")
            used = [int(u) for u in out.get("used", []) if str(u).isdigit() and 1 <= int(u) <= len(sources)]
            return {"answer": out.get("answer", "").strip(), "items": [dict(it, section=s) for s, it in found_items],
                    "sources": [sources[u - 1] for u in used] or sources[:3], "mode": llm.label}
        except Exception as e:
            fallback_note = f"(모델 응답 실패로 규칙 기반 답변: {e})"

    # 규칙 기반 답변: 원문 문장(근거 위치 표시) + 정리된 항목
    lines = []
    if how == "role":
        internal = [it for s, it in found_items if not EXTERNAL.search(it.get("org") or "")]
        external = [it for s, it in found_items if it not in internal]
        if internal:
            lines.append("내부 인원:")
            lines += [_item_line("contacts", c) + f" {_cite(c['sources'][0])}" for c in internal if c.get("sources")]
        if external:
            lines.append("참고(외부 업체·기관의 같은 직위):")
            lines += [_item_line("contacts", c) for c in external]
    else:
        quoted = {_flat(t) for t, _ in sents}
        item_lines = []
        for s, it in found_items[:8 if how in ("month", "list") else 4]:
            if how == "search" and s != "contacts" and any(_flat(src.get("quote", "")) in quoted for src in it.get("sources", [])[:1]):
                continue
            item_lines.append(_item_line(s, it) + (f" {_cite(it['sources'][0])}" if it.get("sources") else ""))
        sent_lines = [f"• {t} [{b['file'].split('/')[-1]} {b['loc']}]" for t, b in sents]
        lines = item_lines + sent_lines if CONTACT_Q.search(q) else sent_lines + item_lines
    if not lines:
        text = NOT_FOUND
        sources = []
    else:
        text = "자료에서 찾은 내용입니다.\n" + "\n".join(lines)
    if fallback_note:
        text += "\n" + fallback_note
    return {"answer": text, "items": [dict(it, section=s) for s, it in found_items], "sources": sources[:5],
            "mode": "규칙 기반"}
