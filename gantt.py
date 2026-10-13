"""업무 간트차트와 업무별 체크리스트.

간트차트: 분석한 연간 일정·진행 중인 현안·용역 계약 기간을 착임월부터 12개월 막대로 세운다.
  기간별(날짜순)과 업무별(담당업무 R&R로 묶음) 두 가지로 본다.
체크리스트: 막대 하나(업무 하나)를 누르면 그 업무를 처리할 순서를 만든다.
  자료 근거·빈틈·협업 상대·노하우·법정 기한을 먼저 넣고, 업무 종류(계약·점검·예산·보고·민원·보험)에 맞는 실행 단계를 붙인다.
  모델 없이 규칙으로 만들며, 사람이 체크·추가·삭제한 결과는 인수인계 건에 저장한다.
"""
import hashlib
import re
from datetime import date, timedelta

from radar import _period_end
from report import _base_year, _parse_due, flag_label, live

DONE_RX = re.compile(r"완료|종료|해지")


# ---------------------------------------------------------------- 날짜 도우미

def _add_months(d, n):
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def _month_end(y, m):
    return _add_months(date(y, m, 1), 1) - timedelta(days=1)


def _safe(y, m, d):
    return min(date(y, m, 1) + timedelta(days=(d or 1) - 1), _month_end(y, m))


def _period(p):
    """'2026.01.01~2026.12.31' → (시작, 끝)"""
    m = re.findall(r"(20\d{2})[.\-/]\s*(\d{1,2})[.\-/]\s*(\d{1,2})", p or "")
    if not m:
        return None, None
    try:
        a = date(*map(int, m[0]))
    except ValueError:
        return None, None
    return a, _period_end(p) or a


# ---------------------------------------------------------------- 비슷한 글 찾기(한글 두 글자 단위)

STOP = {"업무", "관리", "결과", "보고", "계획", "정기", "진행", "예정", "필요", "확인", "실시", "제출", "관련", "담당", "매월", "매년",
        "주무", "무관", "대리", "과장", "팀장", "주임", "주식", "회사", "센터", "시설"}


def _grams(text):
    words = re.findall(r"[가-힣A-Za-z0-9]{2,}", text or "")
    out = set()
    for w in words:
        if w in STOP or w.isdigit():
            continue
        out |= {w[i:i + 2] for i in range(len(w) - 1)} - STOP
    return out


def _sim(a, b):
    A, B = _grams(a), _grams(b)
    if not A or not B:
        return 0.0
    return len(A & B) / min(len(A), len(B))


# ---------------------------------------------------------------- 간트차트

def _key(kind, ident):
    return f"{kind}-{ident}"


def _ledger_id(x):
    return hashlib.md5(x["name"].encode("utf-8")).hexdigest()[:8]


def rows(h, start):
    """간트 막대 목록(창 밖 항목 포함 전체). 각 막대: key, kind, title, segs[{start,end,ms}], status, level, sources"""
    d = h["draft"]
    S = d["sections"]
    base = _base_year(h)
    win0 = date(start.year, start.month, 1)
    win1 = _add_months(win0, 12) - timedelta(days=1)
    out = []

    # 1) 연간 일정: 매월 반복은 달마다 눈금, 날짜가 있으면 그날 표시(◆), 없으면 그 달 전체
    for s in live(S["schedule"]):
        segs = []
        if s.get("recurring") == "매월":
            for i in range(12):
                m0 = _add_months(win0, i)
                if s.get("day"):
                    dd = _safe(m0.year, m0.month, s["day"])
                    segs.append({"start": dd, "end": dd, "ms": True})
                else:
                    segs.append({"start": m0, "end": _month_end(m0.year, m0.month), "ms": False})
        else:
            for m in s.get("months") or []:
                if s.get("year"):
                    y = s["year"]
                elif s.get("timing") in ("완료", "지난 일"):
                    y = base  # 이미 끝난 일은 올해 것으로(내년 칸에 다시 그리지 않게)
                else:
                    y = win0.year if m >= win0.month else win0.year + 1
                if s.get("day"):
                    dd = _safe(y, m, s["day"])
                    segs.append({"start": dd, "end": dd, "ms": True})
                else:
                    segs.append({"start": date(y, m, 1), "end": _month_end(y, m), "ms": False})
        done = s.get("timing") in ("완료", "지난 일")
        late = s.get("timing") == "기한 지남"
        out.append({"key": _key("sc", s["id"]), "kind": "일정", "title": s["task"], "text": " ".join([s["task"], s.get("related") or ""]),
                    "when": s.get("when") or "", "segs": segs, "recurring": s.get("recurring") or "",
                    "status": s.get("timing") or ("매월 반복" if s.get("recurring") == "매월" else "예정"),
                    "done": done, "late": late, "sources": s.get("sources", [])})

    # 2) 진행 중인 현안: 착임일부터 기한까지(기한이 없으면 30일로 표시하고 '기한 없음')
    for it in live(S["issues"]):
        due = _parse_due(it.get("due") or "", base)
        if due and not re.match(r"\d{4}", it.get("due") or "") and due < start - timedelta(days=90):
            due = _parse_due(it.get("due"), base + 1)  # '3월'처럼 연도 없는 기한은 착임 이후 가장 가까운 해로
        no_due = due is None
        end = due or start + timedelta(days=30)
        late = bool(due and due < start)
        segs = [{"start": end, "end": end, "ms": True}] if late else [{"start": start, "end": end, "ms": False}]
        out.append({"key": _key("is", it["id"]), "kind": "현안", "title": it["title"],
                    "text": " ".join([it["title"], it.get("next_action") or "", it.get("state") or ""]),
                    "when": ("기한 없음" if no_due else f"기한 {due:%Y. %m. %d.}"), "segs": segs, "recurring": "",
                    "status": it.get("state") or "진행 중", "next_action": it.get("next_action") or "", "no_due": no_due,
                    "done": bool(DONE_RX.search(it.get("state") or "")), "late": late, "sources": it.get("sources", [])})

    # 3) 계약 대장: 계약 기간 전체
    for x in d.get("ledger") or []:
        if x.get("kind") != "계약":
            continue
        a, b = _period(x.get("period"))
        if not a:
            continue
        src = [x["source"]] if x.get("source") else []
        out.append({"key": _key("ct", _ledger_id(x)), "kind": "계약", "title": x["name"],
                    "text": " ".join([x["name"], x.get("vendor") or "", x.get("status") or ""]),
                    "when": x.get("period") or "", "segs": [{"start": a, "end": b, "ms": False}], "recurring": "",
                    "status": x.get("status") or "", "vendor": x.get("vendor") or "",
                    "done": bool(DONE_RX.search(x.get("status") or "")), "late": False, "sources": src})

    for r in out:
        r["in_window"] = any(sg["end"] >= win0 and sg["start"] <= win1 for sg in r["segs"])
        r["first"] = min((sg["start"] for sg in r["segs"]), default=win1)
    return out, win0, win1


# 글자가 닮지 않아도 업무 종류로 담당업무를 잇는다(예: '냉난방기 교체공사' → 계약 관리 업무)
DUTY_HINTS = [(r"공사|용역|계약|입찰|위탁", r"계약"), (r"점검", r"점검"), (r"예산|요구서|결산", r"예산"), (r"민원", r"민원"),
              (r"수질|수영장|여과", r"수질|수영"), (r"보수|균열|보험|시설물|배관|보일러|방수", r"시설물|유지")]


def _duty_of(r, duties):
    best, score = None, 0.0
    for du in duties:
        sc = _sim(r["title"], du["duty"] + " " + (du.get("detail") or ""))
        if sc > score:
            best, score = du, sc
    if best and score >= 0.34:
        return best["duty"]
    for rx, want in DUTY_HINTS:
        if re.search(rx, r["title"] + (" 계약" if r["kind"] == "계약" else "")):
            du = next((x for x in duties if re.search(want, x["duty"])), None)
            if du:
                return du["duty"]
    return "기타 업무"


def build(h, start=None):
    """{start, window, months, rows, groups} — 날짜는 'YYYY-MM-DD' 문자열"""
    start = start or date.fromisoformat((h.get("radar") or {}).get("start") or date.today().isoformat())
    rs, win0, win1 = rows(h, start)
    duties = live(h["draft"]["sections"]["rnr"])
    saved = h.get("checklists") or {}
    vis = []
    for r in rs:
        if not r["in_window"] and not r["late"]:
            continue
        r["duty"] = _duty_of(r, duties)
        cl = saved.get(r["key"])
        r["check"] = {"done": sum(1 for i in cl["items"] if i.get("done")), "total": len(cl["items"])} if cl else None
        r["segs"] = [{"start": max(sg["start"], win0).isoformat(), "end": min(sg["end"], win1).isoformat(), "ms": sg["ms"],
                      "clipped": sg["start"] < win0} for sg in r["segs"] if sg["end"] >= win0 and sg["start"] <= win1] \
            or [{"start": win0.isoformat(), "end": win0.isoformat(), "ms": True, "clipped": True}]
        r["first"] = r["first"].isoformat()
        r.pop("in_window")
        vis.append(r)
    order = {"현안": 0, "계약": 1, "일정": 2}
    vis.sort(key=lambda r: (r["done"], r["first"], order[r["kind"]]))
    names = [du["duty"] for du in duties]
    groups = [{"duty": n, "rows": [r["key"] for r in vis if r["duty"] == n]} for n in names + ["기타 업무"]]
    return {"start": start.isoformat(), "today": date.today().isoformat(), "window": {"start": win0.isoformat(), "end": win1.isoformat()},
            "months": [{"y": _add_months(win0, i).year, "m": _add_months(win0, i).month} for i in range(12)],
            "rows": vis, "groups": [g for g in groups if g["rows"]]}


def find(h, key, start=None):
    g = build(h, start)
    return next((r for r in g["rows"] if r["key"] == key), None)


# ---------------------------------------------------------------- 체크리스트

STEPS = [
    (r"공사|교체|착공|준공", "공사", [
        "계약서·설계서로 공사 범위·기간·금액 확인",
        "착공일을 업체와 확정하고 이용자 안내문 게시 일정 잡기",
        "착공계·안전관리계획 등 착공 서류 받기",
        "공정 중간 점검(현장 사진 기록)",
        "준공계 접수 후 준공 검사, 하자보수 보증 확인",
        "대금 지급 서류(검사조서·청구서) 정리"]),
    (r"용역|입찰|계약|위탁|재계약", "계약", [
        "계약 기간·금액·과업 범위 확인(계약서·과업지시서)",
        "월별 이행 실적과 대금 지급 내역 확인",
        "만료 전 재계약·입찰 필요 여부 판단",
        "필요하면 입찰공고·계약심사 일정을 만료일에서 거꾸로 잡기",
        "새 계약 체결 후 업체 담당자·연락처 갱신"]),
    (r"점검|검사|수질|안전", "점검", [
        "점검 대상·일정·점검자 정하기",
        "점검표 양식과 지난 점검 결과 찾아 두기",
        "점검 실시하고 사진·수치 기록",
        "지적 사항 조치 계획 세우기",
        "보고 기한 안에 결과 보고·통보"]),
    (r"예산|요구서|집행|결산", "예산", [
        "전년도 예산·집행 실적과 잔액 확인",
        "예산부서 공문에서 요구 기준·서식·기한 확인",
        "요구서(산출 근거 포함) 작성",
        "내부 결재 후 기한 안에 제출"]),
    (r"민원", "민원", [
        "접수일과 법정 처리기한 확인",
        "현장 확인·사실 조사",
        "처리 결과 회신(기한 안)",
        "반복 민원이면 근본 대책 메모 남기기"]),
    (r"보험", "보험", [
        "현재 보험 만기일·보장 범위 확인",
        "보험사 견적 받아 비교",
        "갱신 계약 결재·체결"]),
    (r"보고|제출|통보|요청", "보고", [
        "제출 대상·서식·기한 확인",
        "자료 취합해 작성",
        "내부 결재",
        "기한 안에 제출하고 접수 확인"]),
]
GENERIC = ["할 일 범위와 기한 확인", "관련 부서·업체와 일정 협의", "실행", "처리 결과 기록"]
GROUPS = ["먼저 확인", "협업", "실행", "기한", "마무리"]


def _item(group, text, src=""):
    return {"id": "ck-" + hashlib.md5((group + text).encode("utf-8")).hexdigest()[:8], "group": group, "text": text,
            "src": src, "done": False, "auto": True}


def _srcname(s):
    return f"{s['file'].split('/')[-1]} {s['loc']}"


def generate(h, row, net, rd):
    """막대 하나로 체크리스트를 만든다. net: network.build, rd: radar.build 결과"""
    d = h["draft"]
    text = row["text"]
    items = []

    # 먼저 확인: 근거 원문, 이 업무와 관련된 빈틈
    for s in row.get("sources", [])[:3]:
        items.append(_item("먼저 확인", f"근거 원문 확인: {_srcname(s)}", _srcname(s)))
    blocks = {s.get("block_id") for s in row.get("sources", [])}
    raw_id = row["key"].split("-", 1)[1]
    for f in d.get("flags", []):
        if f.get("resolved"):
            continue
        hit = raw_id in (f.get("refs") or []) or blocks & {s.get("block_id") for s in f.get("sources", [])} or _sim(text, f["message"]) >= .5
        if hit:
            items.append(_item("먼저 확인", f"빈틈 메우기 [{flag_label(f['type'])}] {f['message']}",
                               _srcname(f["sources"][0]) if f.get("sources") else ""))

    # 협업: 이 업무와 이어진 협업 상대, 그 상대에 대한 노하우
    nodes = sorted(((max(_sim(text, " ".join([n["label"], n.get("org", "")] + n.get("topics", []))),
                         .6 if row.get("vendor") and row["vendor"] in n["label"] else 0), n) for n in net.get("nodes", [])),
                   key=lambda x: -x[0])
    who = [n for sc, n in nodes if sc >= .4][:2]
    for n in who:
        tel = ", ".join(n.get("phones", [])[:1])
        items.append(_item("협업", f"{n['label']}와(과) 일정·내용 사전 협의" + (f" ({tel})" if tel else "")))
    ids = {n["id"] for n in who}
    for c in net.get("cards", []):
        if c.get("target") in ids or _sim(text, c["text"]) >= .5:
            items.append(_item("협업", f"노하우 [{c['kind']}] {c['text']}", c.get("source") or ""))

    # 실행: 자료에 적힌 다음 할 일 → 업무 종류별 단계
    if row.get("next_action") and row["next_action"] != row["title"]:
        items.append(_item("실행", row["next_action"]))
    kind_text = text + " " + row["kind"]
    steps = next((st for rx, _, st in STEPS if re.search(rx, kind_text)), GENERIC)
    items += [_item("실행", s) for s in steps]

    # 기한: 막대의 날짜와 레이더의 법정·행정 기한 중 이 업무와 닮은 것
    if row["kind"] == "현안":
        items.append(_item("기한", f"{row['when']}" + (" — 기한을 자료에서 찾지 못했으니 직접 정하기" if row.get("no_due") else " 까지 처리")))
    elif row["kind"] == "계약":
        items.append(_item("기한", f"계약 기간 {row['when']}"))
    elif row.get("when"):
        items.append(_item("기한", f"{row['when']}에 실시"))
    for e in rd.get("timeline", []):
        if _sim(text, e["title"]) >= .5 and e["title"] != row["title"]:
            items.append(_item("기한", f"{e['date']} {e['title']}" + (f" (근거: {e['basis']})" if e.get("basis") else ""),
                               e.get("source") or ""))

    items.append(_item("마무리", "처리 결과를 인수인계 초안 표와 처리 이력에 남기기"))
    seen, out = set(), []
    for it in items:
        if it["id"] not in seen:
            seen.add(it["id"])
            out.append(it)
    return {"key": row["key"], "title": row["title"], "kind": row["kind"], "when": row["when"], "duty": row.get("duty", ""),
            "status": row.get("status", ""), "items": out}


def clean_items(raw):
    """화면에서 보낸 체크리스트 항목을 검사해 저장 형식으로"""
    out = []
    for it in raw or []:
        t = re.sub(r"\s+", " ", str(it.get("text") or "")).strip()[:300]
        if not t:
            continue
        g = it.get("group") if it.get("group") in GROUPS else "실행"
        out.append({"id": str(it.get("id") or "")[:20] or "ck-" + hashlib.md5(t.encode("utf-8")).hexdigest()[:8],
                    "group": g, "text": t, "src": str(it.get("src") or "")[:200], "done": bool(it.get("done")), "auto": bool(it.get("auto"))})
    return out[:80]


def to_blocks(cl):
    done = sum(1 for i in cl["items"] if i["done"])
    b = [("title", f"업무 체크리스트: {cl['title']}"),
         ("table", ["구분", "기간", "담당업무", "진행"], [[cl["kind"], cl["when"], cl.get("duty", ""), f"{done}/{len(cl['items'])} 완료"]], [1, 2, 2, 1])]
    for g in GROUPS:
        xs = [i for i in cl["items"] if i["group"] == g]
        if not xs:
            continue
        b.append(("h2", g))
        b += [("bullet", ("☑ " if i["done"] else "☐ ") + i["text"] + (f"  〔근거: {i['src']}〕" if i.get("src") else "")) for i in xs]
    b.append(("note", "※ 업무바통이 인수인계 자료(근거·빈틈·협업 상대·노하우·법정 기한)로 만든 체크리스트입니다. 기관 규정에 맞게 고쳐 쓰세요."))
    return b
