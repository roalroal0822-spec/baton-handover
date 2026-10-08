"""업무바통 분석 엔진: 업무자료 → 인수인계 초안(담당업무·일정·연락처·현안·확인필요).

규칙 기반으로 항상 동작하고, 모델이 연결되면 모델 추출 결과를 같은 구조로 합친다.
모든 항목은 근거 블록(파일·위치·원문)을 가진다.
"""
import re
import time
import uuid
from collections import Counter, defaultdict
from datetime import date
from email.utils import parsedate_to_datetime

from core.textutil import (HEDGES, NOT_NAMES, extract_dates, find_emails, find_orgs, find_people, find_phones,
                           normalize_phone, similarity, split_sentences, strip_josa)

ACTIONS = ["재계약", "입찰공고", "입찰", "공고", "착공", "준공", "제출", "보고", "통보", "점검", "검사", "재검사",
           "계약", "정산", "갱신", "교육", "회의", "게시", "의뢰", "요구", "확정", "신청", "실시", "협의", "평가",
           "마감", "납부", "집행", "등록", "검토", "만료", "종료", "정비", "교체", "보수", "발주", "검수"]
ISSUE_WORDS = re.compile(r"진행\s*중|협의\s*중|검토\s*중|준비|예정|필요|요망|부탁|재확인|부적합|미정|확인할|해야|할 것|요청|지연|미완료|바랍니다|주십시오|검토하여")
RESOLVED = re.compile(r"적합\s*(판정|받음)|완료$|완료\s*\(|해결")
DONE_WORDS = re.compile(r"^완료$|완료\s*$")
GENERIC = {"용역", "공사", "계약", "관리", "유지", "유지관리", "시설", "업무", "결과", "보고", "제출", "점검", "검사",
           "정기", "관련", "필요", "진행", "예정", "확인", "협의", "담당", "센터", "공단", "주임", "팀장", "이번",
           "년", "월", "일", "올해", "다음", "매월", "매년", "요청", "안내", "실시", "주식회사", 
           "체육센터", "체육센터", "계약기간", "종료", "후속", "조치", "대비", "중순", "말까지", "까지", "알고",
           "있음", "한번", "결과", "주시기", "바랍니다", "합니다", "하고자", "말씀하신", "일정", "기간", "부탁드립니다"}
VERBISH = ("니다", "다", "요", "음", "함", "고", "서", "며", "면", "는", "던", "할", "될", "된", "해야", "하여")
NAME_KEYS = re.compile(r"이름|성명|담당자|업체\s*담당|^\s*담당\s*$")
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


# ---------------------------------------------------------------- 사람 이름 (core.find_people 보강)

SURNAMES = set("김이박최정강조윤장임한오서신권황안송류유전홍고문양손배백허남심노하곽성차주우구민진나지엄채원천방공현함변염여추도소석선설마길연위표명기반왕금옥육인맹제모탁국어은편용예봉경사부")
NAME_STOP = NOT_NAMES | {"인수", "인계", "전산", "안전", "이번", "이후", "이상", "이하", "이용", "정기", "정산", "조치", "최종",
                         "신청", "전체", "공사", "공고", "주차", "방수", "하자", "기간", "서류", "한번", "장비", "결재",
                         "보고서", "예산", "총괄", "실무", "위탁", "관계", "구분", "담당자", "연락처", "메모", "회의"}
EXTRA_TITLES = ("PM", "PL", "매니저", "사무관", "주사보", "주사", "서기", "실무관", "반장", "엔지니어", "감리원")
RE_EXTRA_PERSON = re.compile(r"([가-힣]{2,4})\s?(" + "|".join(EXTRA_TITLES) + r")(?![A-Za-z가-힣])")
# '시설운영팀장 이정훈'처럼 직위가 이름 앞에 붙은 꼴 (부서명이 붙은 경우만: 오탐 방지)
RE_TITLE_FIRST = re.compile(r"([가-힣]{2,12}?)(팀장|과장|센터장|소장|실장|부장|국장|본부장)\s+([가-힣]{2,4})(?![가-힣])")


def is_name(n):
    return (2 <= len(n) <= 4 and n[0] in SURNAMES and n not in NAME_STOP
            and not n.endswith(("팀", "과", "실", "부", "청", "단", "님")))


def people_in(text):
    """(이름, 직위, 위치) 목록. core.find_people + 'PM' 같은 직함, '부서장 이름' 꼴."""
    out = [p for p in find_people(text) if is_name(p[0])]
    names = {p[0] for p in out}
    for m in RE_EXTRA_PERSON.finditer(text):
        if is_name(m.group(1)) and m.group(1) not in names:
            out.append((m.group(1), m.group(2), m.start()))
            names.add(m.group(1))
    for m in RE_TITLE_FIRST.finditer(text):
        if is_name(m.group(3)) and m.group(3) not in names:
            out.append((m.group(3), m.group(2), m.start(3)))
            names.add(m.group(3))
    return sorted(out, key=lambda p: p[2])


def title_first_org(text, name):
    m = re.search(r"([가-힣]{2,12}?)(팀|과|실|센터|부|국|본부)(장)\s+" + re.escape(name), text)
    return m.group(1) + m.group(2) if m else ""


# ---------------------------------------------------------------- 날짜 (core.extract_dates 보강)

RE_ENUM_MONTHS = re.compile(r"(?<![\d.])(\d{1,2}(?:\s*[·,/、및]\s*\d{1,2})+)\s*월")
RE_YEAR_MONTH = re.compile(r"(20\d{2})\s*년\s*(\d{1,2})\s*월")
RE_REL_MONTH = re.compile(r"(다음\s*달|내달|익월|이번\s*달|이달|금월)\s*(첫째\s*주|둘째\s*주|셋째\s*주|넷째\s*주|마지막\s*주|초|중순|중|말|(\d{1,2})\s*일)?")
RE_NEXT_YEAR = re.compile(r"내년|다음\s*해|명년|익년")


def dates_in(text, ref=None):
    """extract_dates 결과에 열거 월(3·6·9·12월), 상·하반기(대략), 상대 시기(다음 달 첫째 주), 연도를 보탠다."""
    ref = ref or date.today()
    dd = dict(extract_dates(text), approx=False, year=None)
    if dd["date"]:
        dd["year"] = int(dd["date"][:4])
    m = RE_ENUM_MONTHS.search(text)
    if m and not dd["date"]:
        ms = sorted({int(x) for x in re.findall(r"\d{1,2}", m.group(1)) if 1 <= int(x) <= 12})
        if len(ms) > len(dd["months"]) and dd["recurring"] != "매월":
            dd.update(months=ms, label=m.group(0))
            if re.search(r"분기", text):
                dd["recurring"] = "매분기"
    if dd["label"] in ("상반기", "하반기"):
        end = 6 if dd["label"] == "상반기" else 12
        dd.update(months=[end], approx=True, label=f"{dd['label']} 중({end}월까지, 대략)")
    if not dd["months"]:
        m = RE_REL_MONTH.search(text)
        if m:
            add = 0 if re.match(r"이번|이달|금월", m.group(1)) else 1
            y, mo = ref.year + (ref.month + add - 1) // 12, (ref.month + add - 1) % 12 + 1
            dd.update(months=[mo], year=y)
            extra = (m.group(2) or "").replace(" ", "")
            if m.group(3):
                try:
                    dd.update(day=int(m.group(3)), date=date(y, mo, int(m.group(3))).isoformat())
                except ValueError:
                    pass
            elif extra:
                dd["approx"] = True
            dd["label"] = f"{m.group(0).strip()}(={y}년 {mo}월{(' ' + extra) if extra and not m.group(3) else ''}, 기준일 {ref.isoformat()})"
    if dd["year"] is None and dd["months"]:
        m = RE_YEAR_MONTH.search(text)
        if m and int(m.group(2)) == dd["months"][0]:
            dd["year"] = int(m.group(1))
        elif RE_NEXT_YEAR.search(text) and not dd["recurring"]:
            dd["year"] = ref.year + 1
    return dd


def doc_date(doc):
    """문서 기준일: 메일 Date 머리글 → 없으면 None(오늘 기준)."""
    try:
        if doc.meta.get("Date"):
            return parsedate_to_datetime(doc.meta["Date"]).date()
    except (TypeError, ValueError):
        pass
    return None


# ---------------------------------------------------------------- 규칙 기반 추출

class Extractor:
    def __init__(self, docs, predecessor="", today=None):
        self.docs = [d for d in docs if not d.error]
        self.today = today or date.today()
        self.kind = {d.id: classify(d) for d in self.docs}
        self.blocks = {b.id: b for d in self.docs for b in d.blocks}
        self.ref = {d.id: doc_date(d) or self.today for d in self.docs}
        self.predecessor = predecessor or self.guess_predecessor()
        self.items = {"rnr": [], "schedule": [], "contacts": [], "issues": []}

    def guess_predecessor(self):
        """전임자 추정: 제목·첫 줄의 '○○ 인계/인수인계 (이름)'·'담당 이름'에 큰 가중치,
        메일 받는 사람, 그다음 본문 언급 횟수. 협의 상대(팀장 등)가 많이 나와도 이기지 않게 한다."""
        cnt = Counter()
        for d in self.docs:
            heads = [b.text for b in d.blocks[:2]] + [d.relpath]
            for h in heads:
                for rx in (r"([가-힣]{2,4})\s*(?:[가-힣]{1,4}\s*)?(?:의\s*)?(?:업무\s*)?(?:인수인계|인계)",
                           r"(?:인수인계|인계)[^\n(]{0,12}\(\s*([가-힣]{2,4})\s*[,)]",
                           r"담당\s*(?:자)?\s*[:：]?\s*([가-힣]{2,4})(?![가-힣])",
                           r"전임자?[_\s:：]*([가-힣]{2,4})(?![가-힣])"):
                    for m in re.finditer(rx, h):
                        if is_name(m.group(1)):
                            cnt[m.group(1)] += 6
            for b in d.blocks:
                for name, title, _ in people_in(b.text):
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
                        else:
                            # 같은 부서 동료·결재권자도 연락망에 넣는다(결재·업무 협의 대상)
                            m = re.match(r"([가-힣]{2,4})", cell(i_name).strip())
                            if m and is_name(m.group(1)):
                                duty = " / ".join(x.strip() for x in re.split(r"\s*/\s*|\n", cell(i_duty)) if x.strip())
                                topics = (["결재권자"] if "결재" in duty else []) + ([duty] if duty else [])
                                self.add("contacts", {"name": m.group(1), "title": cell(i_tpos), "org": _team_of(d),
                                                      "phones": [], "emails": [], "topics": topics}, blk)
                        continue
                    # 연락처
                    if i_name is not None and (i_phone is not None or i_email is not None):
                        raw = cell(i_name)
                        name, ttl, org_hint = raw, cell(i_tpos), ""
                        ppl = people_in(raw)
                        m = re.match(r"([가-힣]{2,4})\s*(\S+)?", raw)
                        if ppl and not (m and m.group(1) == ppl[0][0]):
                            # '시설운영팀장 이정훈', '(주)한결안전기술 최현우 과장'처럼 소속·직위가 이름 앞뒤에 붙은 칸
                            name, t2, pos = ppl[0]
                            ttl = ttl or t2
                            org_hint = title_first_org(raw, name) or raw[:pos].strip()
                        elif m:
                            name = m.group(1)
                            ttl = ttl or (m.group(2) or "")
                        topics = [x for x in [cell(i_topic), title] if x]
                        if name and name != self.predecessor:
                            self.add("contacts", {
                                "name": name, "title": ttl, "org": cell(i_org) or org_hint,
                                "phones": [p for p in find_phones(cell(i_phone))],
                                "emails": find_emails(cell(i_email)) if i_email is not None else [],
                                "topics": topics,
                            }, blk)
                    # 진행 현황(계약·사업 목록)
                    if title and i_status is not None:
                        st = cell(i_status)
                        note = cell(i_note)
                        if st and not DONE_WORDS.search(st):
                            due = dates_in(note, self.ref.get(d.id)) if note else None
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
                            dd = dates_in(end, self.ref.get(d.id))
                            if dd["months"]:
                                self.add("schedule", {**_sched_fields(dd), "task": f"{title} 계약기간 종료 (후속 조치 확인)",
                                                      "related": title, "action": "종료"}, blk, per)
                    handled.add(blk.id)
        return handled

    def schedule_from_text(self, text, blk, related=""):
        dd = dates_in(text, self.ref.get(blk.doc_id))
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
                    people = [p for p in people_in(sent) if p[0] != self.predecessor]
                    phones = find_phones(sent)
                    if people and (phones or re.search(r"내선\s*\d+", sent)):
                        # 전화번호와 가장 가까운 사람을 고른다
                        pm = re.search(r"0\d{1,2}[-) .]?\d{3,4}[- .]\d{4}|내선\s*\d+", sent)
                        ppos = pm.start() if pm else 0
                        name, title, _ = min(people, key=lambda p: abs(ppos - p[2]))
                        if not phones:
                            m = re.search(r"내선\s*(\d+)", sent)
                            phones = [f"내선 {m.group(1)}"]
                        orgs = [title_first_org(sent, name)] if title_first_org(sent, name) else find_orgs(sent)
                        self.add("contacts", {"name": name, "title": title if title != "님" else "",
                                              "org": orgs[0] if orgs else "", "phones": phones,
                                              "emails": find_emails(sent), "topics": [topic] if is_mail else [_clip(sent, 60)]},
                                 b, sent)
                    elif people and not phones and not is_mail:
                        # 협의 상대만 나오는 문장 → 연락처 후보(번호 없음)
                        name, title, _ = people[-1] if re.search(r"대행|대리\s*[:：]", sent) else people[0]
                        if title not in ("님",) and re.search(r"협의|문의|담당|통보|보고|대행|결재", sent):
                            orgs = find_orgs(sent)
                            topic_s = (f"{self.predecessor} 부재 시 업무 대행" if re.search(r"부재.*대행", sent) and self.predecessor
                                       else _clip(sent, 60))
                            self.add("contacts", {"name": name, "title": title, "org": orgs[0] if orgs else "",
                                                  "phones": [], "emails": [], "topics": [topic_s]}, b, sent)
                    # 현안
                    if (ISSUE_WORDS.search(sent) and not RESOLVED.search(sent)
                            and (_action(sent) or re.search(r"공사|용역|민원|보험|수질|예산", sent))):
                        dd = dates_in(sent, self.ref.get(d.id))
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
            "when": dd["label"], "year": dd.get("year"), "approx": bool(dd.get("approx"))}


def _team_of(doc):
    """'시설운영팀 업무분장표' 같은 제목에서 부서명."""
    head = " ".join(b.text for b in doc.blocks[:2]) + " " + doc.name
    m = re.search(r"([가-힣]{2,12}(?:팀|과|실|센터))(?![가-힣])", head)
    return m.group(1) if m else ""


def _due_text(dd):
    if dd.get("approx"):
        return dd["label"]
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
    # 표의 서로 다른 행(다른 계약·사업)은 날짜가 같아도 합치지 않는다
    ra, rb = a.get("related") or "", b.get("related") or ""
    if ra and rb and ra != rb and not _overlap(topic_words(ra), topic_words(rb)):
        return 0
    if a.get("year") and b.get("year") and a["year"] != b["year"]:
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

def build_flags(sections, kind_of_file, today=None):
    today = today or date.today()
    flags = []

    def flag(t, msg, refs, sources, severity="중"):
        flags.append({"id": new_id("fl"), "type": t, "message": msg, "refs": refs, "sources": sources[:4],
                      "severity": severity, "resolved": False})

    # 불확실: 모호한 표현, 개인메모에만 근거 (항목당 1건, 같은 원문 줄은 한 번만)
    seen_quotes = set()
    for sec in ("schedule", "issues"):
        for it in sections[sec]:
            quotes = " ".join(s["quote"] for s in it["sources"])
            label = it.get("task") if sec == "schedule" else it.get("title")
            reasons = []
            m = HEDGES.search(quotes)
            if m:
                reasons.append(f"'{m.group(0)}' 같은 불확실한 표현이 있음")
            memo_only = it["origin"] and all(o == "개인메모" for o in it["origin"])
            if memo_only:
                reasons.append("개인메모에만 있고 공식문서로 확인되지 않음")
            if not reasons:
                continue
            hq = next((s["quote"] for s in it["sources"] if HEDGES.search(s["quote"])), None)
            key = hq or frozenset(s["quote"] for s in it["sources"])
            if key in seen_quotes:
                continue
            seen_quotes.add(key)
            flag("불확실", f"'{label}': " + ", ".join(reasons) + ".", [it["id"]], it["sources"], "중" if m else "하")

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

    # 기한 지남: 해야 할 일인데 오늘 기준으로 날짜가 지났음
    for it in sections["schedule"]:
        if it.get("timing") == "기한 지남":
            flag("기한 지남", f"'{it['task']}': 기한({_when(it)})이 오늘({today.isoformat()}) 기준으로 지났습니다. 처리 여부를 확인해 주세요.",
                 [it["id"]], it["sources"], "상")
    late_quotes = {q["quote"] for it in sections["schedule"] if it.get("timing") == "기한 지남" for q in it["sources"]}
    for it in sections["issues"]:
        m = re.match(r"(20\d{2})-(\d{2})-(\d{2})$", it.get("due") or "")
        if m and date(int(m.group(1)), int(m.group(2)), int(m.group(3))) < today:
            it["past_due"] = True
            if any(q["quote"] in late_quotes for q in it["sources"]):
                continue
            flag("기한 지남", f"현안 '{it['title']}': 기한({it['due']})이 오늘({today.isoformat()}) 기준으로 지났습니다.",
                 [it["id"]], it["sources"], "상")

    # 누락
    for c in sections["contacts"]:
        if not c["phones"] and not c["emails"]:
            flag("누락", f"{c['name']} {c.get('title', '')}의 연락처가 자료에 없습니다.", [c["id"]], c["sources"], "하")
    for it in sections["issues"]:
        if not it.get("next_action"):
            flag("누락", f"현안 '{it['title']}': 다음 할 일이 적혀 있지 않습니다.", [it["id"]], it["sources"], "하")
        if not it.get("due"):
            flag("누락", f"현안 '{it['title']}': 기한이 적혀 있지 않습니다.", [it["id"]], it["sources"], "하")
    for sec, name in (("rnr", "담당업무"), ("schedule", "일정"), ("contacts", "연락처"), ("issues", "현안")):
        if not sections[sec]:
            flag("누락", f"자료에서 {name} 항목을 찾지 못했습니다. 전임자가 직접 보완해 주세요.", [], [], "상")
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


RE_FULL = re.compile(r"(20\d{2})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})")
RE_MD_ANY = re.compile(r"(?<![\d.])(\d{1,2})\s*월\s*(\d{1,2})\s*일")
RE_MD_DOT2 = re.compile(r"(?<![\d.])(\d{1,2})\s*[./]\s*(\d{1,2})(?!\d)")
RE_MON_ANY = re.compile(r"(?<!\d)(\d{1,2})\s*월")
RE_MON_RANGE = re.compile(r"(\d{1,2})\s*[~∼\-]\s*(\d{1,2})\s*월")


def _block_dates(text, ref=None):
    """원문 조각에 나오는 월 집합과 (월, 일) 쌍 집합."""
    months, pairs = set(), set()
    for rx in (RE_FULL,):
        for m in rx.finditer(text):
            pairs.add((int(m.group(2)), int(m.group(3))))
    for rx in (RE_MD_ANY, RE_MD_DOT2):
        for m in rx.finditer(text):
            pairs.add((int(m.group(1)), int(m.group(2))))
    months.update(mo for mo, _ in pairs)
    months.update(int(x) for x in RE_MON_ANY.findall(text))
    for a, b in RE_MON_RANGE.findall(text):
        months.update(range(int(a), int(b) + 1))
    for m in RE_ENUM_MONTHS.finditer(text):
        months.update(int(x) for x in re.findall(r"\d{1,2}", m.group(1)))
    if re.search(r"매월|매달|월\s*1회", text):
        months.update(range(1, 13))
    if re.search(r"분기", text):
        months.update((3, 6, 9, 12))
    if "상반기" in text:
        months.update(range(1, 7))
    if "하반기" in text:
        months.update(range(7, 13))
    months.update(dates_in(text, ref)["months"])
    return {m for m in months if 1 <= m <= 12}, pairs


def _amounts(text, claims=False):
    """금액 → 정수 집합. claims=True 이면 '원·만·억' 단위나 천 단위 쉼표가 있는 것만(모델 주장 쪽)."""
    out = set()
    num = lambda x: int(x.replace(",", "")) if x else 0
    for m in re.finditer(r"(\d[\d,]*)\s*억(?:\s*(\d[\d,]*)\s*만)?(?:\s*(\d[\d,]*))?", text):
        out.add(num(m.group(1)) * 100000000 + num(m.group(2)) * 10000 + num(m.group(3)))
    for m in re.finditer(r"(?<![\d,억])(\d[\d,]*)\s*만\s*원?", text):
        out.add(num(m.group(1)) * 10000)
    for m in re.finditer(r"(?<![\d,])(\d{1,3}(?:,\d{3})+)(?![\d,])", text):
        out.add(num(m.group(1)))
    for m in re.finditer(r"(?<![\d,.])(\d{4,})\s*원", text):
        out.add(num(m.group(1)))
    if not claims:
        out.update(int(x) for x in re.findall(r"(?<![\d,.\-])\d{5,}(?![\d,.\-])", text))
    return {a for a in out if a >= 1000}


def _phone_set(text):
    out = {normalize_phone(p) for p in find_phones(text)}
    out.update(re.findall(r"내선\s*(\d{3,5})", text))
    return out


def _phone_ok(p, known):
    d = normalize_phone(p)
    if not d:
        return True
    return d in known or any(len(x) >= 3 and len(d) >= 3 and (x.endswith(d) or d.endswith(x)) for x in known)


def verify_model_item(sec, it, raw, block_texts, refs):
    """모델 항목의 핵심 사실(사람·전화·메일·날짜·금액·대상어)이 근거로 든 원문 조각에 실제로 있는지 본다.
    맞지 않으면 버릴 이유(문자열)를, 맞으면 None을 돌려준다."""
    text = "\n".join(block_texts)
    flat = re.sub(r"\s+", "", text)
    known_phones = _phone_set(text)
    months, pairs = set(), set()
    for bt, ref in zip(block_texts, refs):
        m, p = _block_dates(bt, ref)
        months |= m
        pairs |= p
    fields = {"rnr": ["duty"], "schedule": ["task"], "contacts": ["topics"], "issues": ["title", "next_action", "due", "state"]}[sec]
    strs = []
    for f in fields:
        v = it.get(f)
        strs.extend(v if isinstance(v, list) else [v or ""])
    claim = " ".join(str(x) for x in strs if x)
    # 사람 이름
    names = [it["name"]] if sec == "contacts" else []
    names += [n for n, _, _ in people_in(claim)]
    for n in names:
        if n and re.sub(r"\s+", "", n) not in flat:
            return f"이름 '{n}'이 근거 원문에 없음"
    # 전화번호·메일
    phones = list(it.get("phones") or []) + find_phones(claim)
    for p in phones:
        if not _phone_ok(p, known_phones):
            return f"전화번호 '{p}'가 근거 원문에 없음"
    for e in list(it.get("emails") or []) + find_emails(claim):
        if e.lower() not in text.lower():
            return f"메일 '{e}'이 근거 원문에 없음"
    # 날짜
    claims = []
    if sec == "schedule":
        claims += [(m, it.get("day")) for m in it["months"][:1]] + [(m, None) for m in it["months"][1:]]
    for x in strs:
        if not x:
            continue
        iso = re.match(r"(20\d{2})-(\d{2})-(\d{2})$", str(x).strip())
        if iso:
            claims.append((int(iso.group(2)), int(iso.group(3))))
            continue
        for m in RE_FULL.finditer(str(x)):
            claims.append((int(m.group(2)), int(m.group(3))))
        for m in RE_MD_ANY.finditer(str(x)):
            claims.append((int(m.group(1)), int(m.group(2))))
        if not claims or sec != "schedule":
            for mo in RE_MON_ANY.findall(str(x)):
                claims.append((int(mo), None))
    for mo, dy in claims:
        if not 1 <= mo <= 12:
            continue
        if mo not in months:
            return f"시기 '{mo}월'이 근거 원문에 없음"
        if dy and (mo, dy) not in pairs:
            return f"날짜 '{mo}월 {dy}일'이 근거 원문에 없음"
    # 금액
    have = _amounts(text)
    for a in _amounts(claim, claims=True):
        if a not in have:
            return f"금액 '{a:,}원'이 근거 원문에 없음"
    # 대상어: 할 일·현안·업무 문구의 낱말이 하나라도 원문에 있어야 한다
    if sec != "contacts":
        main = str(it.get({"rnr": "duty", "schedule": "task", "issues": "title"}[sec]) or "")
        words = [w for w in topic_list(main) if not re.search(r"\d", w)]
        if words and not any(w in flat or (len(w) >= 3 and w[:-1] in flat) for w in words):
            return f"'{_clip(main, 30)}'의 내용이 근거 원문에 없음"
    return None


def _as_list(v):
    if v is None or v == "":
        return []
    return v if isinstance(v, list) else [v]


def llm_extract(llm, docs, predecessor, kind_of, refs=None):
    """모델로 문서별 추출. 근거 id 가 실제 블록이 아니거나, 핵심 사실이 그 블록 원문에 없으면 버리고 기록한다."""
    items = {"rnr": [], "schedule": [], "contacts": [], "issues": []}
    errors = []
    filtered = []
    refs = refs or {}
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
                    cited = raw.get("block_ids", []) or []
                    cited = cited if isinstance(cited, list) else [cited]
                    ids = [i for i in cited if isinstance(i, str) and i in blocks]
                    label = str(raw.get("duty") or raw.get("task") or raw.get("name") or raw.get("title") or "")
                    if not ids:
                        filtered.append({"section": sec, "label": _clip(label, 60), "reason": "근거 위치가 실제 자료에 없음",
                                         "sources": []})
                        continue
                    srcs = [blocks[i].cite() for i in ids]
                    it = {"id": new_id(sec[:2]), "status": "초안", "sources": srcs,
                          "origin": list({kind_of.get(blocks[i].doc_id, "참고자료") for i in ids}), "by": "model"}
                    if sec == "rnr":
                        it.update(duty=str(raw.get("duty", "")).strip(), detail="")
                        if not it["duty"]:
                            continue
                    elif sec == "schedule":
                        months = [int(m) for m in _as_list(raw.get("months")) if str(m).isdigit() and 1 <= int(m) <= 12]
                        if not months or not raw.get("task"):
                            continue
                        day = raw.get("day")
                        it.update(months=months, day=int(day) if str(day or "").isdigit() else None,
                                  recurring=raw.get("recurring") or "", date=None, when="",
                                  task=str(raw["task"]), related="", action=_action(str(raw["task"])))
                    elif sec == "contacts":
                        if not raw.get("name") or raw.get("name") == predecessor:
                            continue
                        it.update(name=str(raw["name"]), title=str(raw.get("title") or ""), org=str(raw.get("org") or ""),
                                  phones=[str(p) for p in _as_list(raw.get("phones")) if p],
                                  emails=[str(e) for e in _as_list(raw.get("emails")) if e],
                                  topics=[str(t) for t in _as_list(raw.get("topics")) if t])
                    else:
                        if not raw.get("title"):
                            continue
                        it.update(title=str(raw["title"]), state=raw.get("state") or "확인 필요",
                                  next_action=raw.get("next_action") or "", due=raw.get("due") or "", topic="")
                    reason = verify_model_item(sec, it, raw, [blocks[i].text for i in ids],
                                               [refs.get(blocks[i].doc_id) for i in ids])
                    if reason:
                        filtered.append({"section": sec, "label": _clip(label, 60), "reason": reason, "sources": srcs[:2]})
                        continue
                    if sec == "schedule":
                        it.update(year=None, approx=False)
                    items[sec].append(it)
    uniq = {}
    for f in filtered:
        k = (f["section"], f["label"], f["reason"])
        if k in uniq:
            uniq[k]["count"] += 1
        else:
            uniq[k] = dict(f, count=1)
    return items, errors, list(uniq.values())


# ---------------------------------------------------------------- 일정 다듬기 (반복 주기·지난 일·메모 노하우)

EXPLICIT_YEARLY = re.compile(r"매년|연\s*1회|해마다|매해")
DONE_RX = re.compile(r"완료(?!\s*(?:해야|할|예정|필요|하여|하도록|목표|까지|요망))|적합\s*판정|적합\s*받음|실시\s*결과|검사일\s*[:：]|시행\s*완료|제출\s*함")
INTENT_RX = re.compile(r"예정|까지|필요|해야|할 것|바랍니다|부탁|주십시오|요청|의뢰|게시|확정하고자|협의")


def apply_cadence(schedule, ex):
    """'정기'만 보고 매년으로 잡힌 일정: 같은 업무가 다른 곳에서 '매월'로 나오면 매월로 고친다.
    (예: 수질검사 보고서의 '다음 정기검사 10.6.' ↔ 계획서 '수영장 수질검사: 매월 10일까지')"""
    monthly = [b for b in ex.blocks.values() if re.search(r"매월|매달", b.text)]
    for it in schedule:
        if it.get("recurring") != "매년" or it.get("by") == "model":
            continue
        quotes = " ".join(src["quote"] for src in it["sources"])
        if EXPLICIT_YEARLY.search(quotes):
            continue
        docs = {ex.blocks[src["block_id"]].doc_id for src in it["sources"] if src.get("block_id") in ex.blocks}
        topics = set()
        for d in ex.docs:
            if d.id in docs:
                topics |= {w for w in topic_words(doc_topic(d)) if not re.search(r"\d", w)}
        topics |= {w for w in topic_words(it["task"]) if not re.search(r"\d|정기", w)}
        for b in monthly:
            words = {w for w in topic_words(b.text) if not re.search(r"\d", w)}
            if topics and _overlap(topics, words):
                it["recurring"] = "매월"
                it["months"] = list(range(1, 13))
                it["when"] = (it.get("when") or "") + " (같은 업무가 매월 반복됨)"
                if all(src.get("block_id") != b.id for src in it["sources"]):
                    it["sources"].append(b.cite())
                break


def schedule_timing(it, today):
    """'완료'(이미 끝난 일) / '지난 일'(날짜가 지난 기록) / '기한 지남'(해야 할 일인데 날짜가 지남) / ''."""
    quotes = it["task"] + " " + " ".join(src["quote"] for src in it.get("sources", [])[:1])
    if it.get("action") != "종료" and DONE_RX.search(it["task"]):
        return "완료"
    when = None
    if it.get("date"):
        try:
            when = date.fromisoformat(it["date"])
        except ValueError:
            when = None
    elif it.get("year") and it.get("months") and not it.get("recurring"):
        mo = it["months"][-1]
        when = date(it["year"], mo, 28)
    if when is None or when >= today:
        return ""
    if it.get("recurring") in ("매년", "매분기") and not INTENT_RX.search(quotes):
        return "지난 일"  # 올해 몫은 지났고, 반복 업무라 내년에도 한다
    if INTENT_RX.search(quotes) and not DONE_RX.search(quotes):
        return "기한 지남"
    return "지난 일"


NOTE_RX = re.compile(r"이내|많은\s*것|양식|공유\s*폴더|폴더에|보관\s*위치|주의|노하우|요령|비밀번호|자주")


def attach_notes(sections, ex):
    """메모의 노하우·처리 기준(예: '민원 답변은 접수 후 7일 이내', '민원 많은 것: …', '양식은 공유폴더 …')을
    가장 가까운 담당업무의 '세부 내용·메모'에 근거와 함께 붙인다. 맞는 업무가 없으면 '업무 참고사항'으로 모은다."""
    rnr = sections["rnr"]
    leftovers = []
    for d in ex.docs:
        memo = ex.kind.get(d.id) == "개인메모"
        for b in d.blocks:
            if b.loc.startswith("머리글"):
                continue
            for piece in split_sentences(re.sub(r"^[\-•·*▪○□◦●]+\s*", "", b.text)):
                piece = piece.strip().rstrip(".")
                if len(piece) < 6 or not NOTE_RX.search(piece):
                    continue
                if not memo and "이내" not in piece:
                    continue
                words = {w for w in topic_words(piece) if not re.search(r"\d", w)}
                best, score = None, 0
                for r in rnr:
                    dw = {w for w in topic_words(r["duty"]) if not re.search(r"\d", w)}
                    sc = sum(1 for w in words if any(w == x or (len(w) >= 3 and len(x) >= 3 and (w in x or x in w)) for x in dw))
                    if sc > score:
                        best, score = r, sc
                target = best
                if target is None:
                    leftovers.append((piece, b))
                    continue
                if piece in (target.get("detail") or ""):
                    continue
                target["detail"] = (target["detail"] + " / " if target.get("detail") else "") + piece
                if all((s_["file"], s_["loc"]) != (b.file, b.loc) for s_ in target["sources"]):
                    target["sources"].append(b.cite(piece))
                o = ex.origin(b.doc_id)
                if o not in target["origin"]:
                    target["origin"].append(o)
    if leftovers:
        rnr.append({"id": new_id("rn"), "duty": "업무 참고사항(메모·노하우)", "status": "초안",
                    "detail": " / ".join(p for p, _ in leftovers),
                    "sources": [b.cite(p) for p, b in leftovers],
                    "origin": sorted({ex.origin(b.doc_id) for _, b in leftovers})})


# ---------------------------------------------------------------- 전체 실행

def analyze(docs, predecessor="", llm=None, today=None):
    t0 = time.time()
    ex = Extractor(docs, predecessor, today)
    raw = ex.run()
    engine = "규칙 기반"
    llm_errors = []
    filtered = []
    if llm is not None and llm.enabled:
        m_items, llm_errors, filtered = llm_extract(llm, ex.docs, ex.predecessor, ex.kind, ex.ref)
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
    apply_cadence(sections["schedule"], ex)
    for it in sections["schedule"]:
        it["timing"] = schedule_timing(it, ex.today)
    attach_notes(sections, ex)
    sections["schedule"].sort(key=lambda s: (0 if s.get("recurring") == "매월" else 1, s.get("year") or 0,
                                             s["months"][0], s.get("day") or 0))
    sections["issues"].sort(key=lambda s: (_due_sort(s.get("due")), s["title"]))
    flags = build_flags(sections, ex.kind, ex.today)
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
                f"확인이 필요한 사항은 {len(flags)}건입니다."
                + (f" 모델이 낸 항목 중 원문과 맞지 않는 {sum(f['count'] for f in filtered)}건은 버렸습니다." if filtered else ""))
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
        "filtered": filtered,
        "today": ex.today.isoformat(),
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
