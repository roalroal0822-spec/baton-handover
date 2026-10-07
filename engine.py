"""업무바통 분석 엔진: 업무자료 → 인수인계 초안(담당업무·일정·연락처·현안·확인필요).

규칙 기반으로 항상 동작하고, 모델이 연결되면 모델 추출 결과를 같은 구조로 합친다.
모든 항목은 근거 블록(파일·위치·원문)을 가진다.
"""
import re
import time
import uuid
from collections import Counter, defaultdict

from core.textutil import (HEDGES, extract_dates, find_emails, find_orgs, find_people, find_phones,
                           normalize_phone, similarity, split_sentences, strip_josa)

ACTIONS = ["재계약", "입찰공고", "입찰", "공고", "착공", "준공", "제출", "보고", "통보", "점검", "검사", "재검사",
           "계약", "정산", "갱신", "교육", "회의", "게시", "의뢰", "요구", "확정", "신청", "실시", "협의", "평가",
           "마감", "납부", "집행", "등록", "검토", "만료", "종료", "정비", "교체", "보수", "발주", "검수"]
ISSUE_WORDS = re.compile(r"진행\s*중|협의\s*중|검토\s*중|준비|예정|필요|요망|부탁|재확인|부적합|미정|확인할|해야|할 것|요청|지연|미완료|바랍니다|주십시오|검토하여")
RESOLVED = re.compile(r"적합\s*(판정|받음)|완료$|완료\s*\(|해결")
DONE_WORDS = re.compile(r"^완료$|완료\s*$")
GENERIC = {"용역", "공사", "계약", "관리", "유지", "유지관리", "시설", "업무", "결과", "보고", "제출", "점검", "검사",
           "정기", "관련", "필요", "진행", "예정", "확인", "협의", "담당", "센터", "공단", "주임", "팀장", "이번",
           "년", "월", "일", "올해", "다음", "매월", "매년", "요청", "안내", "실시", "주식회사", "가람", "가람구",
           "가람체육센터", "체육센터", "계약기간", "종료", "후속", "조치", "대비", "중순", "말까지", "까지", "알고",
           "있음", "한번", "결과", "주시기", "바랍니다", "합니다", "하고자", "말씀하신", "일정", "기간", "부탁드립니다"}
VERBISH = ("니다", "다", "요", "음", "함", "고", "서", "며", "면", "는", "던", "할", "될", "된", "해야", "하여")
NAME_KEYS = re.compile(r"이름|성명|담당자|업체\s*담당")
PHONE_KEYS = re.compile(r"연락처|전화|휴대|내선|핸드폰|tel", re.I)
ORG_KEYS = re.compile(r"소속|업체|기관|부서|회사")
TOPIC_KEYS = re.compile(r"업무|협의|계약명|비고|용도|역할")
STATUS_KEYS = re.compile(r"진행|상태|현황")
TITLE_KEYS = re.compile(r"계약명|사업명|업무명|과제명|건명|제목")
PERIOD_KEYS = re.compile(r"기간")
DUTY_KEYS = re.compile(r"담당\s*업무|업무\s*내용|주요\s*업무|업무$")
PERSONAL_PATH = re.compile(r"메모|memo|개인|note|노트|정리용|낙서", re.I)
OFFICIAL_TEXT = re.compile(r"문서번호|시행|결재|계획|보고|현황|분장|공고|지침|규정|기안|-\d+호|\d{4}\.\s*\d{1,2}\.\s*\d{1,2}\.")


def new_id(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:6]}"


# ---------------------------------------------------------------- 문서 분류

def classify(doc):
    path = doc.relpath
    if doc.ext == ".eml":
        return "메일"
    if PERSONAL_PATH.search(path) or doc.ext in (".txt", ".md"):
        return "개인메모"
    head = " ".join(b.text for b in doc.blocks[:15])
    if OFFICIAL_TEXT.search(path + " " + head):
        return "공식문서"
    return "참고자료"


def doc_topic(doc):
    """문서 주제(메일 제목, 파일명)."""
    subj = doc.meta.get("Subject") or doc.meta.get("title") or ""
    subj = re.sub(r"^(re|fw|fwd)\s*:\s*|\[[^\]]*\]\s*", "", subj, flags=re.I).strip()
    if subj:
        return subj
    name = re.sub(r"\.[^.]+$", "", doc.name)
    name = re.sub(r"^\d+[_\-. ]*", "", name)
    return name


def topic_words(text):
    return set(topic_list(text))


def topic_list(text):
    """대상어(명사로 보이는 낱말) 목록, 나온 순서대로."""
    out = []
    for w in re.findall(r"[가-힣A-Za-z]{2,}", text):
        w = strip_josa(w)
        if w in GENERIC or len(w) < 2 or w.endswith(VERBISH) or w in out:
            continue
        out.append(w)
    return out


def _clip(s, n=90):
    s = re.sub(r"^[\-•·*▪○□◦●]+\s*", "", s.strip())
    return s if len(s) <= n else s[: n - 1] + "…"


def _action(text):
    for a in ACTIONS:
        if a in text:
            return a
    return ""


# ---------------------------------------------------------------- 규칙 기반 추출

class Extractor:
    def __init__(self, docs, predecessor=""):
        self.docs = [d for d in docs if not d.error]
        self.kind = {d.id: classify(d) for d in self.docs}
        self.blocks = {b.id: b for d in self.docs for b in d.blocks}
        self.predecessor = predecessor or self.guess_predecessor()
        self.items = {"rnr": [], "schedule": [], "contacts": [], "issues": []}

    def guess_predecessor(self):
        cnt = Counter()
        for d in self.docs:
            for b in d.blocks:
                for name, title, _ in find_people(b.text):
                    cnt[name] += 1
            to = d.meta.get("To", "")
            m = re.match(r"\s*([가-힣]{2,4})\s*<", to)
            if m:
                cnt[m.group(1)] += 3
        return cnt.most_common(1)[0][0] if cnt else ""

    def origin(self, doc_id):
        return self.kind.get(doc_id, "참고자료")

    def add(self, section, item, block, quote=None):
        item.setdefault("id", new_id(section[:2]))
        item.setdefault("status", "초안")
        item["sources"] = [block.cite(quote)]
        item["origin"] = [self.origin(block.doc_id)]
        self.items[section].append(item)
        return item

    # 표 단위 처리 (엑셀·한글 표): 열 이름을 보고 연락처/계약 현황/업무분장을 구조적으로 읽는다
    def from_tables(self):
        handled = set()
        for d in self.docs:
            locmap = {b.loc: b for b in d.blocks}
            for t in d.tables:
                header = [h or "" for h in t["header"]]
                hidx = lambda rx: next((i for i, h in enumerate(header) if rx.search(h)), None)
                i_name, i_phone, i_org = hidx(NAME_KEYS), hidx(PHONE_KEYS), hidx(ORG_KEYS)
                i_status, i_title, i_period = hidx(STATUS_KEYS), hidx(TITLE_KEYS), hidx(PERIOD_KEYS)
                i_duty = hidx(DUTY_KEYS)
                i_topic = next((i for i, h in enumerate(header) if TOPIC_KEYS.search(h) and i not in (i_title,)), None)
                i_email = next((i for i, h in enumerate(header) if "메일" in h), None)
                i_note = next((i for i, h in enumerate(header) if "비고" in h), None)
                i_tpos = next((i for i, h in enumerate(header) if h in ("직위", "직급", "직책")), None)
                for row, loc in zip(t["rows"], t.get("row_locs", [])):
                    blk = locmap.get(loc)
                    if blk is None:
                        continue
                    cell = lambda i: (row[i] if i is not None and i < len(row) else "") or ""
                    title = cell(i_title)
                    # 업무분장표
                    if i_duty is not None and i_name is not None and i_phone is None:
                        if self.predecessor and self.predecessor in cell(i_name):
                            for duty in re.split(r"\s*/\s*|\n", cell(i_duty)):
                                if duty.strip():
                                    self.add("rnr", {"duty": duty.strip(), "detail": ""}, blk, duty.strip())
                            handled.add(blk.id)
                        continue
                    # 연락처
                    if i_name is not None and (i_phone is not None or i_email is not None):
                        raw = cell(i_name)
                        name, ttl = raw, cell(i_tpos)
                        m = re.match(r"([가-힣]{2,4})\s*(\S+)?", raw)
                        if m:
                            name = m.group(1)
                            ttl = ttl or (m.group(2) or "")
                        topics = [x for x in [cell(i_topic), title] if x]
                        if name and name != self.predecessor:
                            self.add("contacts", {
                                "name": name, "title": ttl, "org": cell(i_org),
                                "phones": [p for p in find_phones(cell(i_phone))],
                                "emails": find_emails(cell(i_email)) if i_email is not None else [],
                                "topics": topics,
                            }, blk)
                    # 진행 현황(계약·사업 목록)
                    if title and i_status is not None:
                        st = cell(i_status)
                        note = cell(i_note)
                        if st and not DONE_WORDS.search(st):
                            due = extract_dates(note) if note else None
                            self.add("issues", {
                                "title": title, "state": st, "next_action": note,
                                "due": _due_text(due) if due and due["months"] else "",
                                "topic": title,
                            }, blk)
                    # 일정: 비고·진행상황의 시기 표현, 계약기간 종료일
                    if title:
                        for idx in (i_note, i_status):
                            for clause in re.split(r"[,;]\s*", cell(idx)):
                                self.schedule_from_text(clause, blk, related=title)
                        per = cell(i_period)
                        if "~" in per:
                            end = per.split("~")[-1]
                            dd = extract_dates(end)
                            if dd["months"]:
                                self.add("schedule", {**_sched_fields(dd), "task": f"{title} 계약기간 종료 (후속 조치 확인)",
                                                      "related": title, "action": "종료"}, blk, per)
                    handled.add(blk.id)
        return handled

    def schedule_from_text(self, text, blk, related=""):
        dd = extract_dates(text)
        act = _action(text)
        if not dd["months"] or not act:
            return None
        task = _clip(text)
        if related and not _overlap(topic_words(text), topic_words(related)):
            task = f"[{related}] {task}"
        return self.add("schedule", {**_sched_fields(dd), "task": task, "related": related, "action": act},
                        blk, text)

    def from_text(self, handled):
        for d in self.docs:
            topic = doc_topic(d)
            is_mail = d.ext == ".eml"
            for b in d.blocks:
                if b.id in handled or b.loc.startswith("머리글"):
                    continue
                if re.search(r"\|", b.text) and b.loc.startswith("표"):
                    # 구조를 못 읽은 표 행은 문장처럼 다룬다
                    pass
                units = [b.text] if (self.origin(d.id) == "개인메모" or "!" in b.loc) else split_sentences(b.text)
                for sent in units:
                    if len(sent) < 6:
                        continue
                    related = topic if (is_mail or not topic_words(sent) & topic_words(topic)) else ""
                    self.schedule_from_text(sent, b, related=related if is_mail else "")
                    # 사람 + 전화번호가 함께 나오면 연락처
                    people = [p for p in find_people(sent) if p[0] != self.predecessor]
                    phones = find_phones(sent)
                    if people and (phones or re.search(r"내선\s*\d+", sent)):
                        # 전화번호와 가장 가까운 사람을 고른다
                        pm = re.search(r"0\d{1,2}[-) .]?\d{3,4}[- .]\d{4}|내선\s*\d+", sent)
                        ppos = pm.start() if pm else 0
                        name, title, _ = min(people, key=lambda p: abs(ppos - p[2]))
                        if not phones:
                            m = re.search(r"내선\s*(\d+)", sent)
                            phones = [f"내선 {m.group(1)}"]
                        orgs = find_orgs(sent)
                        self.add("contacts", {"name": name, "title": title if title != "님" else "",
                                              "org": orgs[0] if orgs else "", "phones": phones,
                                              "emails": find_emails(sent), "topics": [topic] if is_mail else [_clip(sent, 60)]},
                                 b, sent)
                    elif people and not phones and not is_mail:
                        # 협의 상대만 나오는 문장 → 연락처 후보(번호 없음)
                        name, title, _ = people[0]
                        if title not in ("님",) and re.search(r"협의|문의|담당|통보|보고", sent):
                            orgs = find_orgs(sent)
                            self.add("contacts", {"name": name, "title": title, "org": orgs[0] if orgs else "",
                                                  "phones": [], "emails": [], "topics": [_clip(sent, 60)]}, b, sent)
                    # 현안
                    if (ISSUE_WORDS.search(sent) and not RESOLVED.search(sent)
                            and (_action(sent) or re.search(r"공사|용역|민원|보험|수질|예산", sent))):
                        dd = extract_dates(sent)
                        self.add("issues", {"title": _clip(sent, 70), "state": _state(sent),
                                            "next_action": _next_action(sent),
                                            "due": _due_text(dd) if dd["months"] else "",
                                            "topic": topic if is_mail else ""}, b, sent)
            # 메일 보낸 사람
            frm = d.meta.get("From", "")
            m = re.match(r"\s*\"?([가-힣]{2,4})\"?\s*<([^>]+)>", frm)
            if m and m.group(1) != self.predecessor:
                hb = next((b for b in d.blocks if b.loc == "머리글 From"), d.blocks[0] if d.blocks else None)
                if hb:
                    self.add("contacts", {"name": m.group(1), "title": "", "org": "", "phones": [],
                                          "emails": [m.group(2)], "topics": [topic]}, hb)

    def run(self):
        handled = self.from_tables()
        self.from_text(handled)
        return self.items


def _sched_fields(dd):
    return {"months": dd["months"], "day": dd["day"], "recurring": dd["recurring"], "date": dd["date"],
            "when": dd["label"]}


def _due_text(dd):
    if dd.get("date"):
        return dd["date"]
    if dd["months"] and dd.get("day"):
        return f"{dd['months'][0]}월 {dd['day']}일"
    if dd["recurring"]:
        return dd["label"]
    if dd["months"]:
        return f"{dd['months'][0]}월"
    return ""


def _state(s):
    for rx, lab in ((r"진행\s*중", "진행 중"), (r"협의\s*중|협의해야|협의 필요", "협의 필요"), (r"검토", "검토 필요"),
                    (r"준비", "준비 중"), (r"예정", "예정"), (r"부적합", "조치 필요"), (r"미정", "미정"),
                    (r"바랍니다|주십시오|요청", "요청 받음"), (r"필요|해야|할 것|요망|부탁", "조치 필요")):
        if re.search(rx, s):
            return lab
    return "확인 필요"


def _next_action(s):
    parts = re.split(r"[.,]\s*|\s{2,}", s)
    for p in parts:
        if re.search(r"필요|해야|할 것|요망|부탁|협의|확인|요청|게시|제출", p):
            return _clip(p, 70)
    return ""


# ---------------------------------------------------------------- 병합

def _merge_sources(dst, src):
    seen = {(s["file"], s["loc"]) for s in dst["sources"]}
    for s in src["sources"]:
        if (s["file"], s["loc"]) not in seen:
            dst["sources"].append(s)
            seen.add((s["file"], s["loc"]))
    for o in src.get("origin", []):
        if o not in dst["origin"]:
            dst["origin"].append(o)


def merge_contacts(items):
    by = {}
    order = []
    for it in items:
        k = it["name"]
        if k not in by:
            by[k] = dict(it, phones=list(it["phones"]), emails=list(it["emails"]), topics=list(it["topics"]),
                         sources=list(it["sources"]), origin=list(it["origin"]), phone_sources={})
            for p in it["phones"]:
                by[k]["phone_sources"].setdefault(p, []).extend(it["sources"])
            order.append(k)
            continue
        c = by[k]
        for f in ("title", "org"):
            if not c.get(f) and it.get(f):
                c[f] = it[f]
        for p in it["phones"]:
            digits = normalize_phone(p)
            known = {normalize_phone(x) for x in c["phones"]}
            if digits in known or (p.startswith("내선") and any(k.endswith(digits) for k in known)):
                continue
            if not p.startswith("내선"):
                c["phones"] = [x for x in c["phones"] if not (x.startswith("내선") and digits.endswith(normalize_phone(x)))]
            c["phones"].append(p)
            c["phone_sources"].setdefault(p, []).extend(it["sources"])
        for e in it["emails"]:
            if e not in c["emails"]:
                c["emails"].append(e)
        for t in it["topics"]:
            if t and all(similarity(t, x) < 0.5 for x in c["topics"]):
                c["topics"].append(t)
        _merge_sources(c, it)
    return [by[k] for k in order]


def merge_similar(items, keyfn, threshold):
    out = []
    for it in items:
        for o in out:
            if keyfn(o, it) >= threshold:
                _merge_sources(o, it)
                for f in ("next_action", "due", "state", "related", "topic"):
                    if not o.get(f) and it.get(f):
                        o[f] = it[f]
                o.setdefault("variants", [])
                if it.get("title") and it.get("title") != o.get("title"):
                    o["variants"].append(it.get("title"))
                break
        else:
            out.append(it)
    return out


def _sched_key(a, b):
    if a["months"] != b["months"]:
        return 0
    if a.get("day") != b.get("day") and a.get("day") and b.get("day"):
        return 0
    if similarity(a["task"], b["task"]) >= 0.3:
        return 1
    ta, tb = topic_words(a["task"]), topic_words(b["task"])
    return 1 if _overlap(ta, tb) and a.get("action") == b.get("action") else 0


def _overlap(ta, tb):
    """대상어가 겹치는지 (한쪽이 다른 쪽에 포함되는 경우 포함: 청소 ⊂ 청소용역)."""
    for x in ta:
        for y in tb:
            if len(x) >= 2 and len(y) >= 2 and (x == y or (min(len(x), len(y)) >= 2 and (x in y or y in x))):
                return True
    return False


def _issue_key(a, b):
    ca, cb = _core_topic(a), _core_topic(b)
    if ca and cb and _overlap(ca, cb):
        return 1.0
    ta = topic_words(a.get("topic") or "") | topic_words(a["title"])
    tb = topic_words(b.get("topic") or "") | topic_words(b["title"])
    if not ta or not tb:
        return 0
    return len(ta & tb) / max(1, min(len(ta), len(tb))) * 0.6


def _core_topic(it):
    """현안의 핵심 대상어(예: 냉난방기, 청소, 승강기)."""
    src = it.get("topic") or it.get("title") or ""
    words = [w for w in topic_list(src) if not re.search(r"\d", w)]
    return {words[0]} if words else set()


# ---------------------------------------------------------------- 확인 필요 표시

def build_flags(sections, kind_of_file):
    flags = []

    def flag(t, msg, refs, sources, severity="중"):
        flags.append({"id": new_id("fl"), "type": t, "message": msg, "refs": refs, "sources": sources[:4],
                      "severity": severity, "resolved": False})

    # 불확실: 모호한 표현, 개인메모에만 근거
    for sec in ("schedule", "issues"):
        for it in sections[sec]:
            quotes = " ".join(s["quote"] for s in it["sources"])
            label = it.get("task") if sec == "schedule" else it.get("title")
            if HEDGES.search(quotes):
                m = HEDGES.search(quotes)
                flag("불확실", f"'{label}' 은(는) 자료에 '{m.group(0)}' 같은 불확실한 표현이 있습니다.",
                     [it["id"]], it["sources"])
            if sec in ("schedule", "issues") and it["origin"] and all(o == "개인메모" for o in it["origin"]):
                flag("불확실", f"'{label}' 은(는) 개인메모에만 있고 공식문서로 확인되지 않습니다.",
                     [it["id"]], it["sources"], "하")

    # 충돌: 같은 일(대상어+행위)인데 시기가 다름
    sch = sections["schedule"]
    for i, a in enumerate(sch):
        for b in sch[i + 1:]:
            if not a.get("action") or a.get("action") != b.get("action"):
                continue
            ta = topic_words(a["task"] + " " + (a.get("related") or "")) - set(ACTIONS)
            tb = topic_words(b["task"] + " " + (b.get("related") or "")) - set(ACTIONS)
            inter = {x for x in ta for y in tb if x == y or (len(x) >= 3 and len(y) >= 3 and (x in y or y in x))}
            if not inter or len(inter) / max(1, min(len(ta), len(tb))) < 0.3:
                continue
            if a["months"] == b["months"] and (a.get("day") == b.get("day") or not a.get("day") or not b.get("day")):
                continue
            if a.get("recurring") or b.get("recurring"):
                continue
            flag("충돌", f"'{a['action']}' 시기가 자료마다 다릅니다: {_when(a)} vs {_when(b)}",
                 [a["id"], b["id"]], a["sources"] + b["sources"], "상")

    # 충돌: 같은 사람 번호가 서로 다름
    for c in sections["contacts"]:
        mobiles = [p for p in c["phones"] if p.startswith("010")]
        lands = [p for p in c["phones"] if not p.startswith("010") and not p.startswith("내선")]
        changed = any(re.search(r"바뀜|변경|새\s*번호", s["quote"]) for s in c["sources"])
        if len(mobiles) > 1 or len(lands) > 1 or (changed and len(c["phones"]) > 1):
            flag("충돌", f"{c['name']} {c.get('title', '')}의 연락처가 자료마다 다릅니다: {', '.join(c['phones'])}"
                 + (" (번호 변경 메모 있음)" if changed else ""), [c["id"]], c["sources"], "상")

    # 누락
    for c in sections["contacts"]:
        if not c["phones"] and not c["emails"]:
            flag("누락", f"{c['name']} {c.get('title', '')}의 연락처가 자료에 없습니다.", [c["id"]], c["sources"], "하")
    for it in sections["issues"]:
        if not it.get("next_action"):
            flag("누락", f"현안 '{it['title']}' 의 다음 할 일이 적혀 있지 않습니다.", [it["id"]], it["sources"], "하")
        if not it.get("due"):
            flag("누락", f"현안 '{it['title']}' 의 기한이 없습니다.", [it["id"]], it["sources"], "하")
    for sec, name in (("rnr", "담당업무"), ("schedule", "일정"), ("contacts", "연락처"), ("issues", "현안")):
        if not sections[sec]:
            flag("누락", f"자료에서 {name}을(를) 찾지 못했습니다. 전임자가 직접 보완해 주세요.", [], [], "상")
    months = set()
    for it in sch:
        months.update(it["months"])
    empty = [m for m in range(1, 13) if m not in months]
    if sch and empty:
        flag("누락", f"일정이 하나도 없는 달: {', '.join(f'{m}월' for m in empty)}. 빠진 정기업무가 없는지 확인해 주세요.",
             [], [], "하")
    uniq, seen = [], set()
    for f in flags:
        if f["message"] not in seen:
            seen.add(f["message"])
            uniq.append(f)
    return uniq


def _when(it):
    if it.get("date"):
        return it["date"]
    if it.get("day"):
        return f"{it['months'][0]}월 {it['day']}일"
    return ", ".join(f"{m}월" for m in it["months"][:3])


# ---------------------------------------------------------------- 모델 추출

LLM_SYSTEM = """당신은 공공기관 인수인계서 작성을 돕는 도우미입니다.
주어진 업무자료 조각에서 인수인계에 필요한 정보만 뽑습니다. 자료에 없는 내용은 절대 만들지 않습니다.
각 항목에는 근거가 된 조각 번호(id)를 block_ids 에 반드시 적습니다.
출력 JSON 형식:
{"duties":[{"duty":"담당업무","block_ids":["..."]}],
 "schedule":[{"months":[3],"day":16,"recurring":"","task":"할 일","block_ids":["..."]}],
 "contacts":[{"name":"","title":"","org":"","phones":[""],"emails":[""],"topics":["협의 업무"],"block_ids":["..."]}],
 "issues":[{"title":"현안","state":"진행 중","next_action":"다음 할 일","due":"기한","block_ids":["..."]}]}
recurring 은 "매월", "매분기", "매년" 중 하나이거나 빈 문자열입니다."""


def llm_extract(llm, docs, predecessor, kind_of):
    """모델로 문서별 추출. 근거 id 가 실제 블록이 아니면 버린다."""
    items = {"rnr": [], "schedule": [], "contacts": [], "issues": []}
    errors = []
    blocks = {b.id: b for d in docs for b in d.blocks}
    for d in docs:
        if d.error or not d.blocks:
            continue
        windows, cur, size = [], [], 0
        for b in d.blocks:
            line = f"[{b.id}] {b.text}"
            if size + len(line) > 3500 and cur:
                windows.append(cur)
                cur, size = [], 0
            cur.append(line)
            size += len(line)
        if cur:
            windows.append(cur)
        for win in windows:
            user = (f"전임자: {predecessor}\n문서: {d.relpath} ({kind_of.get(d.id)})\n"
                    f"메일 정보: {d.meta.get('Subject', '')} {d.meta.get('From', '')}\n\n" + "\n".join(win))
            try:
                out = llm.json(LLM_SYSTEM, user)
            except Exception as e:
                errors.append(f"{d.relpath}: {e}")
                continue
            for sec_in, sec in (("duties", "rnr"), ("schedule", "schedule"), ("contacts", "contacts"), ("issues", "issues")):
                for raw in out.get(sec_in, []) or []:
                    if not isinstance(raw, dict):
                        continue
                    ids = [i for i in raw.get("block_ids", []) if i in blocks]
                    if not ids:
                        continue
                    srcs = [blocks[i].cite() for i in ids]
                    it = {"id": new_id(sec[:2]), "status": "초안", "sources": srcs,
                          "origin": list({kind_of.get(blocks[i].doc_id, "참고자료") for i in ids}), "by": "model"}
                    if sec == "rnr":
                        it.update(duty=str(raw.get("duty", "")).strip(), detail="")
                        if not it["duty"]:
                            continue
                    elif sec == "schedule":
                        months = [int(m) for m in raw.get("months", []) if str(m).isdigit() and 1 <= int(m) <= 12]
                        if not months or not raw.get("task"):
                            continue
                        day = raw.get("day")
                        it.update(months=months, day=int(day) if str(day or "").isdigit() else None,
                                  recurring=raw.get("recurring") or "", date=None, when="",
                                  task=str(raw["task"]), related="", action=_action(str(raw["task"])))
                    elif sec == "contacts":
                        if not raw.get("name") or raw.get("name") == predecessor:
                            continue
                        it.update(name=str(raw["name"]), title=raw.get("title") or "", org=raw.get("org") or "",
                                  phones=[p for p in raw.get("phones", []) if p], emails=[e for e in raw.get("emails", []) if e],
                                  topics=[t for t in raw.get("topics", []) if t])
                    else:
                        if not raw.get("title"):
                            continue
                        it.update(title=str(raw["title"]), state=raw.get("state") or "확인 필요",
                                  next_action=raw.get("next_action") or "", due=raw.get("due") or "", topic="")
                    items[sec].append(it)
    return items, errors


# ---------------------------------------------------------------- 전체 실행

def analyze(docs, predecessor="", llm=None):
    t0 = time.time()
    ex = Extractor(docs, predecessor)
    raw = ex.run()
    engine = "규칙 기반"
    llm_errors = []
    if llm is not None and llm.enabled:
        m_items, llm_errors = llm_extract(llm, ex.docs, ex.predecessor, ex.kind)
        for k in raw:
            raw[k] = raw[k] + m_items[k]
        engine = f"규칙 기반 + 모델({llm.label})"
    sections = {
        "rnr": merge_similar(raw["rnr"], lambda a, b: similarity(a["duty"], b["duty"]), 0.55),
        "schedule": merge_similar(raw["schedule"], _sched_key, 0.45),
        "contacts": merge_contacts(raw["contacts"]),
        "issues": merge_similar(raw["issues"], _issue_key, 0.5),
    }
    # 업무분장 자료가 없으면 현안·일정의 대상어로 담당업무를 추정
    if not sections["rnr"]:
        seen = set()
        for it in sections["issues"] + sections["schedule"]:
            t = it.get("topic") or it.get("related") or ""
            if t and t not in seen:
                seen.add(t)
                sections["rnr"].append({"id": new_id("rn"), "duty": f"{t} 관련 업무(자료에서 추정)", "detail": "",
                                        "status": "초안", "sources": it["sources"][:2], "origin": it["origin"]})
    for c in sections["contacts"]:
        c.pop("phone_sources", None)
    sections["schedule"].sort(key=lambda s: (0 if s.get("recurring") == "매월" else 1, s["months"][0], s.get("day") or 0))
    sections["issues"].sort(key=lambda s: (_due_sort(s.get("due")), s["title"]))
    flags = build_flags(sections, ex.kind)
    docs_info = []
    for d in docs:
        info = d.to_dict()
        info["kind"] = ex.kind.get(d.id, "읽기 실패" if d.error else "참고자료")
        info["topic"] = doc_topic(d)
        info["summary"] = _doc_summary(d)
        docs_info.append(info)
    kinds = Counter(i["kind"] for i in docs_info)
    overview = (f"{ex.predecessor or '전임자'}의 업무자료 {len(docs_info)}건("
                + ", ".join(f"{k} {v}" for k, v in kinds.items())
                + f")에서 담당업무 {len(sections['rnr'])}건, 일정 {len(sections['schedule'])}건, "
                f"연락처 {len(sections['contacts'])}명, 현안 {len(sections['issues'])}건을 찾았습니다. "
                f"확인이 필요한 사항은 {len(flags)}건입니다.")
    if llm is not None and llm.enabled:
        try:
            o = llm.json("공공기관 인수인계서의 '업무 개요'를 3문장 이내로 씁니다. 주어진 항목에 없는 내용은 쓰지 않습니다. 출력: {\"overview\":\"...\"}",
                         _overview_input(ex.predecessor, sections))
            if o.get("overview"):
                overview = o["overview"].strip() + "\n\n" + overview
        except Exception as e:
            llm_errors.append(f"개요 작성: {e}")
    return {
        "predecessor": ex.predecessor,
        "engine": engine,
        "seconds": round(time.time() - t0, 2),
        "overview": overview,
        "sections": sections,
        "flags": flags,
        "docs": docs_info,
        "llm_errors": llm_errors,
    }


def _overview_input(pred, sections):
    lines = [f"전임자: {pred}", "담당업무:"] + [f"- {r['duty']}" for r in sections["rnr"][:10]]
    lines += ["현안:"] + [f"- {i['title']} ({i.get('state', '')}, 기한 {i.get('due', '')})" for i in sections["issues"][:10]]
    return "\n".join(lines)


def _due_sort(due):
    if not due:
        return (99, 99)
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", due)
    if m:
        return (int(m.group(2)), int(m.group(3)))
    m = re.match(r"(\d{1,2})월(?:\s*(\d{1,2})일)?", due)
    if m:
        return (int(m.group(1)), int(m.group(2) or 0))
    return (98, 0)


def _doc_summary(d):
    if d.error:
        return d.error
    if d.ext == ".eml":
        return f"{d.meta.get('From', '')} → {d.meta.get('Subject', '')}"
    texts = [b.text for b in d.blocks if len(b.text) > 8][:3]
    return _clip(" / ".join(texts), 120)
