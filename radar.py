"""'시한폭탄 방지' D-Day 리스크 레이더.

인수인계 자료에서 찾은 기한(일정·현안·계약 만료)과 공공기관의 법정·행정 주기(공공 캘린더)를 후임자 착임일 기준으로
한 줄에 세운다. 맨 위에는 붉은 카드 두 묶음을 둔다.
  - [부임 첫 주에 해야 할 일]   : 착임 후 7일 안 기한 + 이미 기한이 지난 일(처리됐는지 확인)
  - [이번 달 미체결 시 감사 지적 우려 계약] : 착임 후 30일 안에 계약 절차(입찰·재계약·착공·갱신)가 걸린 건
공공 캘린더는 '일반적인 주기'다. 기관·지자체마다 날짜가 다르므로 화면과 문서에 '기관 일정 확인'을 함께 적는다.
"""
import calendar
import re
from datetime import date, timedelta

from core.lawapi import public_link
from core.textutil import similarity

WEEK, MONTH, HORIZON = 7, 30, 120
CONTRACT_RX = re.compile(r"계약|용역|입찰|공고|발주|임차|위탁|착공|준공|보험\s*갱신|갱신")
RENEW_RX = re.compile(r"용역|유지관리|임차|위탁|보험|청소|경비")
END_RX = re.compile(r"계약\s*(?:기간\s*)?(?:종료|만료)|재계약|갱신")
DONE_RX = re.compile(r"완료|종료됨|체결 완료|처리 완료")
LEAD_DAYS = 60  # 연간 용역·임차 재계약: 입찰공고(7~40일)·계약심사·적격심사를 고려해 만료 60일 전 착수

GOV_LEVELS = ("시·군·구", "시·도", "공공기관")

# 공공 캘린더: 법정·행정 주기. keys가 인수인계 자료와 맞으면 '관련 자료'를 붙여 함께 보여 준다.
# month/day: 해마다 돌아오는 날(day가 0이면 그 달 말일), lead: 그날보다 며칠 먼저 준비를 시작해야 하는지
PUBLIC = [
    {"id": "rapid1", "rel": r"집행|발주|선금", "name": "상반기 재정 신속집행 중간 점검", "month": 4, "day": 0, "lead": 14,
     "keys": r"예산|집행|계약|용역|공사|발주",
     "basis": "행정안전부 지방재정 신속집행 추진 지침(해마다 시행)", "law": None,
     "tip": "4월 말 기준 집행률을 점검합니다. 발주·선금 지급이 늦은 사업은 사유를 미리 정리해 두세요."},
    {"id": "rapid2", "rel": r"집행|발주|선금", "name": "상반기 재정 신속집행 마감", "month": 6, "day": 30, "lead": 30,
     "keys": r"예산|집행|계약|용역|공사|발주",
     "basis": "행정안전부 지방재정 신속집행 추진 지침(해마다 시행)", "law": None,
     "tip": "상반기 집행 실적이 기관 평가에 반영됩니다. 미집행 사업의 집행 계획을 확인하세요."},
    {"id": "audit", "rel": r"의회|행정사무감사|전문위원|자료\s*요구", "name": "의회 행정사무감사", "month": None, "day": 15, "lead": 60,
     "keys": r"의회|행정사무감사|전문위원|감사|민원|예산|계약",
     "basis": "지방자치법 제49조(행정사무 감사권 및 조사권) · 해마다 1회, 시기는 의회마다 다름",
     "law": ("지방자치법", "제49조"),
     "tip": "D-60부터 요구자료(사업 추진 현황·계약·민원 처리 실적)를 정리하세요. 전문위원실 자료요구는 감사 2~3주 전에 몰립니다."},
    {"id": "budget", "rel": r"예산\s*요구|예산안|예산요구서", "name": "다음 연도 예산안 의회 제출", "month": 11, "day": 21, "lead": 60,
     "keys": r"예산",
     "basis": "지방자치법 제142조: 시·도는 회계연도 시작 50일 전(11. 11.), 시·군·구는 40일 전(11. 21.)까지",
     "law": ("지방자치법", "제142조"),
     "tip": "부서 예산요구서는 이보다 1~3개월 먼저 예산부서로 갑니다. 소관 사업의 요구 금액·산출 근거를 미리 맞춰 두세요."},
    {"id": "yearend", "rel": r"계약\s*만료|이월|정산|임차", "name": "회계연도 마감(이월·계약 공백 점검)", "month": 12, "day": 31, "lead": 30,
     "keys": r"예산|집행|계약|용역|임차",
     "basis": "지방재정법 제7조(회계연도 독립의 원칙): 그해 세출은 그해 세입으로",
     "law": ("지방재정법", "제7조"),
     "tip": "못 쓴 예산의 이월(명시·사고이월) 여부를 판단하고, 1월 1일부터 끊기면 안 되는 연간 용역의 다음 연도 계약을 확인하세요."},
    {"id": "settle", "rel": r"결산|정산", "name": "결산서 작성·검사", "month": 2, "day": 0, "lead": 30,
     "keys": r"예산|집행|결산|회계",
     "basis": "지방자치법 제150조: 출납 폐쇄 후 80일 이내 결산서 작성 → 검사위원 검사 → 의회 승인",
     "law": ("지방자치법", "제150조"),
     "tip": "소관 사업의 집행 잔액·이월 사유를 결산 자료로 정리합니다."},
    {"id": "thaw", "rel": r"해빙기", "name": "해빙기 시설물 안전점검", "month": 2, "day": 0, "lead": 14,
     "keys": r"시설|안전점검|해빙기|지반|옹벽|공사",
     "basis": "해빙기(2~3월) 안전관리 · 기관별 점검 계획 확인", "law": None,
     "tip": "얼었던 땅이 녹으며 균열·침하가 생기는 시기입니다. 자체점검표와 위탁 점검 일정을 확인하세요."},
    {"id": "summer", "rel": r"우기|배수|방수|침수", "name": "여름철 자연재난대책기간 시작", "month": 5, "day": 15, "lead": 21,
     "keys": r"시설|배수|우기|침수|방수|재난|안전",
     "basis": "여름철 자연재난대책기간(5. 15. ~ 10. 15.) · 기관별 비상근무 계획 확인", "law": None,
     "tip": "배수로·옥상 방수·비상연락망을 기간 시작 전에 점검하세요."},
    {"id": "winter", "rel": r"동절기|동파|보일러|난방|제설", "name": "겨울철 자연재난대책기간 시작", "month": 11, "day": 15, "lead": 21,
     "keys": r"시설|동절기|동파|보일러|제설|난방|재난",
     "basis": "겨울철 자연재난대책기간(11. 15. ~ 3. 15.) · 기관별 비상근무 계획 확인", "law": None,
     "tip": "동파 취약 배관·보일러·제설 자재를 기간 시작 전에 점검하세요."},
]
# 날짜가 아니라 '일이 생기면' 시작되는 법정 기한: 자료에 해당 말이 있으면 조건부 카드로 보여 준다
EVENT_RULES = [
    {"id": "ordinance", "rx": r"조례\s*(?:안|을|를|의)?\s*(?:제정|개정|일부\s*개정|전부\s*개정|폐지)|(?:제정|개정)\s*조례",
     "name": "조례 공포 기한", "when": "의회에서 이송받은 날부터 20일 이내",
     "basis": "지방자치법 제32조: 의장은 의결된 날부터 5일 이내 이송, 단체장은 이송받은 날부터 20일 이내 공포",
     "law": ("지방자치법", "제32조"),
     "tip": "의결 후 이송일을 달력에 적고, 공포 기한(20일)과 시행일을 함께 관리하세요. 재의 요구도 같은 20일 안에 해야 합니다."},
    {"id": "complaint", "rx": r"민원",
     "name": "민원 처리 기한", "when": "접수일부터 법정 처리기간(유형별, 보통 7일 이내 등) 안",
     "basis": "민원 처리에 관한 법률 제17조(법정 처리기간)·시행령 · 기관 처리기간표 확인",
     "law": ("민원 처리에 관한 법률", "제17조"),
     "tip": "인계 시점에 처리 중인 민원은 접수일과 남은 기간을 먼저 확인하세요. 기한 경과는 감사 지적 단골입니다."},
]


def _clamp(y, m, d):
    last = calendar.monthrange(y, m)[1]
    try:
        d = int(d)
    except (TypeError, ValueError):
        d = 1
    return date(y, m, last if d in (0, 99) or d > last else max(1, d))


def dday(d, start):
    n = (d - start).days
    return "D-DAY" if n == 0 else (f"D-{n}" if n > 0 else f"D+{-n}")


def settings(h):
    r = h.get("radar") or {}
    try:
        start = date.fromisoformat(r.get("start") or "")
    except ValueError:
        start = date.today()
    level = r.get("gov_level") if r.get("gov_level") in GOV_LEVELS else "시·군·구"
    try:
        audit = int(r.get("audit_month") or 0)
    except (TypeError, ValueError):
        audit = 0
    return {"start": start, "audit_month": audit if 1 <= audit <= 12 else 0, "gov_level": level}


def _guess_audit_month(text):
    m = re.search(r"행정사무감사[^.\n]{0,12}?\(?\s*(\d{1,2})\s*월", text) or re.search(r"(\d{1,2})\s*월[^.\n]{0,10}행정사무감사", text)
    return int(m.group(1)) if m and 1 <= int(m.group(1)) <= 12 else 11  # 많은 의회가 2차 정례회(11~12월)에 한다


def _src(it):
    return "; ".join(f"{s['file'].split('/')[-1]} {s['loc']}" for s in (it.get("sources") or [])[:1])


def _schedule_dates(s, start):
    """일정 하나 → [(날짜, 대략인지)] (착임일 앞뒤로 의미 있는 것만)"""
    from report import _approx_day
    months = sorted({int(m) for m in s.get("months") or [] if str(m).isdigit() and 1 <= int(m) <= 12})
    if not months or s.get("timing") in ("완료", "지난 일"):
        return []
    rec = s.get("recurring") or ""
    approx = not s.get("day") or bool(s.get("approx"))
    day = s.get("day")
    if not day:
        hint = _approx_day(f"{s.get('when') or ''} {s.get('task') or ''}")
        day = hint if hint in (5, 15, 99) else None  # 1은 '날짜 모름'
    out = []
    if rec == "매월":
        d = _clamp(start.year, start.month, day or 1)
        if d < start:
            nm = start.month % 12 + 1
            d = _clamp(start.year + (start.month == 12), nm, day or 1)
        return [(d, approx)]
    for m in months:
        if s.get("date"):
            try:
                out.append((date.fromisoformat(s["date"]), False))
                break
            except ValueError:
                pass
        # 날짜 없는 '이번 달 중' 일은 이번 달 말일까지로, 다른 달은 그 달 1일부터 챙기도록
        dd = day or (99 if m == start.month else 1)
        y = s.get("year") if not rec and s.get("year") else start.year
        d0 = _clamp(y, m, dd)
        if rec:
            if d0 < start:
                d0 = _clamp(start.year + 1, m, day or 1)
        elif d0 < start and (start - d0).days > 183 and not s.get("year"):
            d0 = _clamp(start.year + 1, m, day or 1)
        out.append((d0, approx))
    return out


def _period_end(p):
    m = re.findall(r"(20\d{2})[.\-/]\s*(\d{1,2})[.\-/]\s*(\d{1,2})", p or "")
    if len(m) >= 2:
        y, mo, d = map(int, m[-1])
        try:
            return date(y, mo, d)
        except ValueError:
            return None
    return None


def _dedupe(evs):
    """같은 날짜·비슷한 제목은 하나로(먼저 넣은 것 = 계약 대장 > 현안 > 일정 순으로 남김)"""
    evs = sorted(evs, key=lambda e: e["date"])
    uniq = []
    for e in evs:
        if not any(_same(e, u) for u in uniq):
            uniq.append(e)
    return uniq


def _short(t):
    """'매년 12월에 시설물 보험 갱신 (총무팀 …)' → '시설물 보험 갱신'"""
    m = re.match(r"^\[([^\]]+)\]", t)
    if m:
        return m.group(1)[:40]
    t = re.sub(r"^(?:매년|매월|올해|내년)?\s*\d{1,2}\s*월\s*(?:중|말|초|중순)?\s*(?:에는|에)?\s*", "", t)
    return re.split(r"[(:,]", t)[0].strip()[:40] or t[:40]


def _same(a, b):
    """같은 일: 날짜가 하루 안이고 제목이 비슷하거나, 둘 중 하나가 대략의 날짜인데 같은 달이고 제목이 비슷하다."""
    sim = similarity(re.sub(r"[\[\]():]", " ", a["title"]), re.sub(r"[\[\]():]", " ", b["title"]))
    if abs((a["date"] - b["date"]).days) <= 1:
        return sim >= 0.3
    if (a["approx"] or b["approx"]) and (a["date"].year, a["date"].month) == (b["date"].year, b["date"].month):
        return sim >= 0.4
    return False


def _ev(**kw):
    kw.setdefault("why", "")
    kw.setdefault("basis", "")
    kw.setdefault("link", "")
    kw.setdefault("source", "")
    kw.setdefault("approx", False)
    kw.setdefault("contract", False)
    kw.setdefault("tip", "")
    return kw


def build(h, start=None):
    """레이더: {settings, top:{week:[..], contract:[..]}, timeline:[..], conditional:[..], public:[..]}"""
    from report import _parse_due, flag_label, live
    st = settings(h)
    if start:
        st["start"] = start
    start = st["start"]
    d = h["draft"]
    S = d["sections"]
    text = " ".join([d.get("overview") or ""] + [r["duty"] + " " + (r.get("detail") or "") for r in live(S["rnr"])]
                    + [s["task"] for s in live(S["schedule"])] + [i["title"] + " " + (i.get("next_action") or "") for i in live(S["issues"])]
                    + [" ".join(c.get("topics") or []) for c in live(S["contacts"])] + [x.get("name", "") for x in d.get("ledger") or []]
                    + [n.get("text", "") for n in d.get("carried_notes") or []] + [k.get("text", "") for k in h.get("knowhow") or []])
    if not st["audit_month"]:
        st["audit_month"] = _guess_audit_month(text)
    evs = []

    # 3) 계약 대장: 만료일과 재계약 착수일
    for x in d.get("ledger") or []:
        if x.get("kind") != "계약" or DONE_RX.search(x.get("status") or ""):
            continue
        end = _period_end(x.get("period"))
        if not end or (end - start).days > 365 or end < start - timedelta(days=30):
            continue
        src = f"{x['source']['file'].split('/')[-1]} {x['source']['loc']}" if x.get("source") else ""
        if RENEW_RX.search(x["name"]):
            prep = end - timedelta(days=LEAD_DAYS)
            evs.append(_ev(date=max(prep, start) if prep < start else prep, title=f"{x['name']} 다음 계약 절차 착수(만료 {LEAD_DAYS}일 전)",
                           kind="자료", source=src, contract=True,
                           why=f"계약 만료 {end:%Y. %m. %d.} 입찰공고·계약심사 기간을 고려하면 지금 시작해야 만료 다음 날부터 공백이 없습니다.",
                           late=prep < start))
            evs.append(_ev(date=end, title=f"{x['name']} 계약 만료", kind="자료", source=src, contract=True,
                           why="다음 계약이 체결되지 않으면 용역이 끊기고, 사후 계약·수의계약은 감사 지적 사례가 많습니다."))
        else:
            evs.append(_ev(date=end, title=f"{x['name']} 계약기간 종료(준공·검사·대금 지급)", kind="자료", source=src, contract=True,
                           why="기간 안에 준공검사·대금 지급을 마치지 못하면 지체상금·기간 연장 변경계약이 필요합니다."))
    # 2) 현안 기한
    for i in live(S["issues"]):
        due = _parse_due(i.get("due"), start.year)
        if not due:
            continue
        if due < start and (start - due).days > 183:
            due = date(due.year + 1, due.month, min(due.day, 28))
        title = i["title"] if not i.get("next_action") or i["next_action"] in i["title"] else f"{i['title']}: {i['next_action']}"
        evs.append(_ev(date=due, title=title, kind="자료", source=_src(i), approx=not re.search(r"\d+일|\d{4}-\d{2}-\d{2}", i.get("due") or ""),
                       contract=bool(CONTRACT_RX.search(i["title"] + " " + (i.get("next_action") or ""))), ref=i.get("id")))
    # 1) 인수인계 자료의 일정
    for s in live(S["schedule"]):
        for dt, approx in _schedule_dates(s, start):
            evs.append(_ev(date=dt, title=s["task"], kind="자료", source=_src(s), approx=approx,
                           contract=s.get("recurring") != "매월" and bool(CONTRACT_RX.search(s["task"] + " " + (s.get("related") or ""))),
                           overdue_flag=s.get("timing") == "기한 지남", ref=s.get("id")))
    evs = _dedupe(evs)
    # 자료에 '계약 종료·재계약·갱신'만 적힌 건(계약 대장에 없는 것)도 만료 60일 전 착수일을 세운다
    ledger_names = [x.get("name", "") for x in d.get("ledger") or [] if x.get("kind") == "계약"]
    for e in list(evs):
        t = e["title"]
        if (e["kind"] == "자료" and e["contract"] and END_RX.search(t) and (RENEW_RX.search(t) or "재계약" in t)
                and not any(n and n in t for n in ledger_names) and not e.get("why")):
            prep = e["date"] - timedelta(days=LEAD_DAYS)
            evs.append(_ev(date=max(prep, start), title=f"{_short(t)}: 다음 계약 준비 착수(기한 {LEAD_DAYS}일 전)", kind="자료",
                           source=e["source"], contract=True, late=prep < start,
                           why=(f"기한 {e['date']:%Y. %m. %d.} " + ("착수 권장일이 이미 지났습니다. " if prep < start else "")
                                + "입찰공고·계약심사 기간을 고려하면 지금 시작해야 공백이 없습니다.")))
    evs = _dedupe(evs)

    # 4) 공공 캘린더
    public = []
    for p in PUBLIC:
        if p["id"] == "budget" and st["gov_level"] == "공공기관":
            continue
        rx = re.compile(p["keys"])
        hits = sorted({m.group(0) for m in rx.finditer(text)})
        if not hits:
            continue
        month = p["month"] or st["audit_month"]
        day = p["day"]
        if p["id"] == "budget" and st["gov_level"] == "시·도":
            day = 11
        dt = _clamp(start.year, month, day)
        if dt + timedelta(days=0) < start:
            dt = _clamp(start.year + 1, month, day)
        prep = dt - timedelta(days=p["lead"])
        related = [e["title"] for e in evs if re.search(p.get("rel") or p["keys"], e["title"]) and e["kind"] == "자료"][:2]
        name = p["name"] + (f"({month}월 예정)" if p["id"] == "audit" else "")
        link = public_link(p["law"][0], "law", p["law"][1]) if p.get("law") else ""
        item = _ev(date=dt, title=name, kind="공공 주기", basis=p["basis"], link=link, tip=p["tip"],
                   why=(f"D-{p['lead']}부터 준비" + (f" · 관련 자료: {', '.join(related)}" if related else f" · 자료의 '{'·'.join(hits[:3])}' 업무와 관련")),
                   prep=prep, public_id=p["id"])
        public.append(item)
        evs.append(item)
        if p["lead"] >= 30 and start <= prep:
            evs.append(_ev(date=prep, title=f"{name} 준비 시작(D-{p['lead']})", kind="공공 주기", basis=p["basis"], link=link,
                           tip=p["tip"], why=f"{dt:%Y. %m. %d.} {p['name']}", public_id=p["id"]))
    conditional = []
    for r in EVENT_RULES:
        m = re.search(r["rx"], text)
        if m:
            conditional.append({"id": r["id"], "title": r["name"], "when": r["when"], "basis": r["basis"],
                                "link": public_link(r["law"][0], "law", r["law"][1]), "tip": r["tip"],
                                "why": f"자료에 '{m.group(0)}' 언급이 있습니다"})

    # 빈틈 스캐너에서 아직 풀리지 않은 것(자료끼리 다름·애매한 내용)이 걸린 일정은 카드에 함께 적는다
    flags = {}
    for f in d.get("flags", []):
        if not f.get("resolved"):
            for r in f.get("refs", []):
                flags.setdefault(r, f"[{flag_label(f['type'])}] {f['message']}")
    for e in evs:
        if e.get("ref") in flags:
            e["flag"] = flags[e["ref"]]
    # 5) 위험도·묶음
    week_end, month_end = start + timedelta(days=WEEK), start + timedelta(days=MONTH)
    for e in evs:
        n = (e["date"] - start).days
        e["days"] = n
        e["dday"] = dday(e["date"], start)
        overdue = n < 0 or e.get("late")
        if e["kind"] == "자료" and not e.get("late") and (overdue or n <= WEEK):
            e["group"] = "week"
        elif e["contract"] and e["kind"] == "자료" and n <= MONTH:
            e["group"] = "contract"
        else:
            e["group"] = "timeline"
        e["level"] = ("red" if overdue or n <= WEEK or (e["contract"] and n <= MONTH and e["kind"] == "자료")
                      else "amber" if n <= MONTH or (e["kind"] == "공공 주기" and e.get("prep") and e["prep"] <= start)
                      else "blue")
        if not e["why"]:
            if n < 0:
                e["why"] = f"기한이 착임일보다 {-n}일 지났습니다. 처리됐는지 먼저 확인하세요."
            elif e["contract"] and n <= MONTH:
                e["why"] = _contract_why(e["title"])
            elif n <= WEEK:
                e["why"] = "부임 첫 주 안에 기한이 옵니다."
        if e["approx"] and e["kind"] == "자료":
            e["why"] = (e["why"] + " " if e["why"] else "") + "(자료에 정확한 날짜가 없어 대략의 날짜로 놓았습니다)"
    # 너무 오래 지난 일(45일 넘게)은 이미 처리된 것으로 보고 뺀다
    evs = [e for e in evs if e["days"] >= -45 and e["days"] <= HORIZON or e in public]
    week = [e for e in evs if e["group"] == "week"]
    contract = [e for e in evs if e["group"] == "contract"]
    timeline = sorted([e for e in evs if -45 <= e["days"] <= HORIZON], key=lambda e: e["date"])
    out = {"start": start.isoformat(), "audit_month": st["audit_month"], "gov_level": st["gov_level"],
           "saved": bool(h.get("radar")),
           "top": {"week": [_j(e) for e in week], "contract": [_j(e) for e in contract]},
           "timeline": [_j(e) for e in timeline], "conditional": conditional,
           "public": [_j(e) for e in sorted(public, key=lambda e: e["date"])],
           "counts": {"red": sum(1 for e in timeline if e["level"] == "red"), "amber": sum(1 for e in timeline if e["level"] == "amber")},
           "note": "공공 주기는 일반적인 법정·행정 일정입니다. 기관·의회마다 날짜가 다르니 기관 일정으로 확인하세요."}
    return out


def _contract_why(t):
    if re.search(r"착공|준공|공사", t):
        return "착공·준공 일정이 계약서와 달라지면 변경계약·지체상금 처리가 필요하고, 누락 시 감사 지적 대상입니다."
    if re.search(r"입찰|공고|발주", t):
        return "공고가 늦으면 기존 계약 만료일까지 새 계약을 못 맺어 공백·수의계약이 생기고 감사 지적 우려가 있습니다."
    if re.search(r"갱신|보험", t):
        return "갱신이 늦으면 보장·계약 공백이 생깁니다. 공백 기간 사고는 기관 책임이 됩니다."
    return "이 기한 안에 계약 절차를 마치지 못하면 계약 공백·사후 처리로 감사 지적 우려가 있습니다."


def _j(e):
    x = {k: v for k, v in e.items() if k not in ("prep", "late", "overdue_flag")}
    x["date"] = e["date"].isoformat()
    return x


def blocks(rd):
    """인수인계서·매뉴얼용 문서 블록."""
    def rows(es):
        return [[e["dday"], e["date"], e["title"], e["why"] or e.get("basis", ""), e.get("source") or e.get("basis") or ""] for e in es]
    b = [("h2", f"D-Day 리스크 레이더 (착임일 {rd['start']} 기준)")]
    if rd["top"]["week"]:
        b += [("p", "■ 부임 첫 주에 해야 할 일"),
              ("table", ["D-Day", "날짜", "할 일", "왜 급한가", "근거"], rows(rd["top"]["week"]), [1, 1, 4, 4, 2])]
    if rd["top"]["contract"]:
        b += [("p", "■ 이번 달(착임 후 30일) 미체결 시 감사 지적 우려 계약"),
              ("table", ["D-Day", "날짜", "계약 건", "위험", "근거"], rows(rd["top"]["contract"]), [1, 1, 4, 4, 2])]
    later = [e for e in rd["timeline"] if e["group"] == "timeline" and e["days"] >= 0][:12]
    if later:
        b += [("p", "■ 앞으로 120일 타임라인 (자료 + 공공 주기)"),
              ("table", ["D-Day", "날짜", "할 일", "구분", "근거·참고"],
               [[e["dday"], e["date"], e["title"], e["kind"], e.get("basis") or e.get("source") or ""] for e in later], [1, 1, 4, 1, 4])]
    if rd["conditional"]:
        b += [("p", "■ 일이 생기면 시작되는 법정 기한"),
              ("table", ["기한", "언제까지", "근거"], [[c["title"], c["when"], c["basis"]] for c in rd["conditional"]], [2, 3, 4])]
    b.append(("note", "※ " + rd["note"]))
    return b
