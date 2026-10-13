"""인수인계서·후임자 업무매뉴얼 문서 만들기 (한글·워드·마크다운 공통 블록)."""
import calendar
import re
from datetime import date, datetime, timedelta, timezone

from core.loaders import BATON_FORMAT, _BATON_FIELDS

GLOSSARY = {
    "해빙기": "얼었던 땅이 녹는 2~3월. 지반 침하·균열 점검이 필요한 시기",
    "우기": "장마철(6~9월). 배수로·방수·누수 점검 시기",
    "동절기": "겨울철. 동파·난방설비 점검 시기",
    "유리잔류염소": "수영장 물 소독 정도를 나타내는 수질 항목",
    "탁도": "물의 흐린 정도. 기준을 넘으면 여과기 점검 필요",
    "하자보수": "공사·정비 후 일정 기간 내 생긴 결함을 업체가 무상으로 고치는 것",
    "입찰공고": "계약 상대방을 공개 경쟁으로 정하기 위해 알리는 절차",
    "재계약": "계약기간 만료 전 다음 기간 계약을 새로 맺는 것",
    "착공": "공사를 시작하는 것. 이용자 안내·안전조치가 함께 필요",
    "정밀점검": "외부 전문기관이 장비를 써서 하는 상세 점검",
    "전결": "위임전결규정에 따라 정해진 결재권자가 최종 결재하는 것",
    "예산요구서": "다음 연도 사업에 필요한 예산을 요청하는 문서",
    "이사회": "공단의 예산·규정 등 주요 사항을 의결하는 회의",
    "용역": "청소·유지관리처럼 일을 맡기는 계약",
}


# '확인 필요' 구분을 화면·문서에서는 쉬운 말로 보여 준다(저장값은 그대로)
FLAG_LABEL = {"충돌": "자료끼리 다름(충돌)", "불확실": "애매한 내용(불확실)", "누락": "빠진 정보(누락)", "기한 지남": "기한 지남"}


def flag_label(t):
    return FLAG_LABEL.get(t, t)


def _src(it, n=2):
    return "; ".join(f"{s['file'].split('/')[-1]} {s['loc']}" for s in it.get("sources", [])[:n])


def _when(it, base_year=None):
    if it.get("recurring") == "매월":
        return "매월" + (f" {it['day']}일" if it.get("day") else "")
    m = it["months"]
    s = f"{m[0]}월" if len(m) == 1 else ", ".join(f"{x}월" for x in m[:4])
    if it.get("day"):
        s += f" {it['day']}일"
    if it.get("year") and base_year and it["year"] != base_year:
        s = f"{it['year']}년 {s}"
    if it.get("approx"):
        s += " (대략)"
    if it.get("timing"):
        s += f" [{it['timing']}]"
    return s


def _base_year(h):
    try:
        return int((h["draft"].get("today") or h.get("created") or "")[:4])
    except ValueError:
        return date.today().year


def live(items):
    return [i for i in items if i.get("status") != "삭제"]


# ---------------------------------------------------------------- 인수인계 순서도

DONE, NOW, TODO = "완료", "진행 중", "예정"
MARK = {DONE: "✓", NOW: "▶", TODO: "○"}


def flow_steps(h):
    """인수인계 절차를 7단계 순서도로: 단계마다 상태(완료·진행 중·예정), 근거 숫자, 다음에 할 일, 화면 위치(tab).
    고정된 그림이 아니라 이 인수인계 건이 실제로 어디까지 왔는지를 보여 준다."""
    d = h["draft"]
    S = d["sections"]
    rv = h.get("review") or {}
    items = [it for k in ("rnr", "schedule", "contacts", "issues") for it in live(S[k])]
    reviewed = [it for it in items if it.get("status") in ("확인", "수정", "추가")]
    flags = d.get("flags", [])
    open_flags = [f for f in flags if not f.get("resolved")]
    docs = d.get("docs", [])
    failed = len(h.get("skipped") or [])
    batons = sum(1 for x in docs if x.get("ext") == ".baton")
    asked = h.get("asked", 0)
    confirmed = bool(rv.get("confirmed"))
    steps = [
        {"key": "collect", "name": "자료 모으기", "who": "전임자", "tab": "docs", "status": DONE,
         "detail": f"자료 {len(docs)}건" + (f" · 이전 바통 {batons}개" if batons else "") + (f" · 읽기 실패 {failed}건" if failed else ""),
         "next": "읽지 못한 파일이 있으면 형식을 바꿔 다시 넣기" if failed else ""},
        {"key": "draft", "name": "AI 초안", "who": "AI", "tab": "draft", "status": DONE,
         "detail": f"업무 {len(live(S['rnr']))} · 일정 {len(live(S['schedule']))} · 연락처 {len(live(S['contacts']))} · 현안 {len(live(S['issues']))}",
         "next": ""},
        {"key": "review", "name": "전임자 검토", "who": "전임자", "tab": "draft",
         "status": DONE if confirmed or (items and len(reviewed) == len(items)) else NOW,
         "detail": f"검토한 항목 {len(reviewed)}/{len(items)}",
         "next": "표에서 항목마다 '확인'·수정·삭제"},
        {"key": "flags", "name": "빈틈 스캐너", "who": "전임자", "tab": "flags",
         "status": DONE if not open_flags else NOW,
         "detail": f"처리 {len(flags) - len(open_flags)}/{len(flags)}건" if flags else "빈틈 없음",
         "next": f"충돌·누락·불확실 {len(open_flags)}건 처리" if open_flags else ""},
        {"key": "confirm", "name": "확인 완료", "who": "전임자", "tab": "draft",
         "status": DONE if confirmed else TODO,
         "detail": f"{rv.get('by', '')} · {str(rv.get('at', ''))[:10]}" if confirmed else "전임자 확인 전(초안)",
         "next": "'전임자 확인 완료' 누르기"},
        {"key": "ask", "name": "AI 분신 질의", "who": "후임자", "tab": "ask",
         "status": DONE if asked else TODO,
         "detail": f"AI 분신에게 물은 질문 {asked}건" if asked else "아직 질문 없음",
         "next": "모르는 건 AI 전임자 분신에게 묻기(자료에 없으면 물어볼 협업 상대를 알려 줌)"},
        {"key": "baton", "name": "바통 넘기기", "who": "전임자", "tab": "draft",
         "status": DONE if h.get("baton_saved_at") else TODO,
         "detail": f"바통 파일 저장 {str(h.get('baton_saved_at'))[:10]}" if h.get("baton_saved_at") else "다음 담당자용 .baton 미저장",
         "next": "'바통 파일로 저장(.baton)'으로 다음 담당자에게 넘기기"},
    ]
    # 먼저 끝나야 할 단계가 남았으면 뒤 단계는 '예정'으로 둔다(확인 완료는 검토·정리 뒤)
    if steps[4]["status"] == TODO and (steps[2]["status"] == DONE and steps[3]["status"] == DONE):
        steps[4]["status"] = NOW
    current = next((x for x in steps if x["status"] != DONE), None)
    for i, x in enumerate(steps, 1):
        x["no"] = i
        x["mark"] = MARK[x["status"]]
    return {"steps": steps, "current": current["key"] if current else None,
            "next": (current["next"] if current else "인수인계를 모두 마쳤습니다."),
            "done": sum(1 for x in steps if x["status"] == DONE), "total": len(steps)}


def flow_blocks(flow, title="인수인계 순서도"):
    """문서용 순서도: 가로 한 줄 표(단계 → 단계)로 그려 한글·워드·마크다운 어디서나 같은 모양이 나온다."""
    st = flow["steps"]
    header = [f"{x['no']}. {x['name']}" + (" →" if i < len(st) - 1 else "") for i, x in enumerate(st)]
    rows = [[f"{x['mark']} {x['status']}" for x in st], [x["who"] for x in st], [x["detail"] for x in st]]
    return [("h2", f"{title} ({flow['done']}/{flow['total']} 단계 완료)"),
            ("table", header, rows, [1] * len(st)),
            ("note", f"✓ 완료 · ▶ 진행 중 · ○ 예정 — 지금 할 일: {flow['next']}")]


def onboarding_flow(m):
    """후임자 착임 후 순서도: 첫날 → 첫 주 → 첫 달 → 1년."""
    first_month = m["months"][0] if m["months"] else {"month": "", "items": [], "monthly": []}
    yearly = sum(len(mo["items"]) for mo in m["months"])
    urgent = [i for i in m["issues"] if i.get("days_left") is not None and i["days_left"] <= 30]
    return [
        {"name": "첫날", "what": "연락망 인사·업무 확인", "detail": f"연락처 {len(m['contacts'])}명 중 핵심 {min(4, len(m['contacts']))}명"},
        {"name": "첫 주", "what": "급한 현안·기한 지난 일 처리", "detail": f"할 일 {len(m['week1'])}가지 · 30일 안 기한 현안 {len(urgent)}건"},
        {"name": f"첫 달({first_month['month']}월)", "what": "이번 달 일정·매월 반복 업무",
         "detail": f"이번 달 {len(first_month['items'])}건 · 매월 {len(first_month['monthly'])}건"},
        {"name": "1년", "what": "연간 일정 따라 운영, 다음 바통 준비", "detail": f"12개월 일정 {yearly}건"},
    ]


def coverage_blocks(cov):
    """인수인계 충실도 점검 표: 기본 7개 영역의 자료 충분도와, 모자라면 전임자에게 확인할 내용."""
    if not cov:
        return []
    ok = sum(1 for c in cov if c["level"] == "충분")
    return [("h2", f"인수인계 충실도 점검 ({ok}/{len(cov)} 영역 충분)"),
            ("table", ["영역", "판정", "근거", "부족하면 전임자에게 확인할 내용"],
             [[c["name"], {"충분": "● 충분", "부족": "◐ 부족", "없음": "○ 없음"}[c["level"]], c["note"], c.get("question") or "-"] for c in cov],
             [2, 1, 2, 5])]


def handover_blocks(h, net=None, rd=None):
    import network
    import radar
    d = h["draft"]
    S = d["sections"]
    rv = h.get("review", {})
    rd = rd or radar.build(h)
    net = net or network.build(h, [])
    blocks = [("title", f"인수인계서 ({h.get('title') or '업무 인수인계'})"),
              ("table", ["구분", "내용"], [
                  ["인계", "전임자 → 후임자 (개인정보 보호를 위해 사람 이름은 적지 않음)"],
                  ["작성일", h.get("created", "")[:10]],
                  ["전임자 확인", f"확인 완료 ({rv.get('by', '')}, {rv.get('at', '')})" if rv.get("confirmed") else "검토 중(초안)"],
                  ["작성 방식", d.get("engine", "")],
              ], [1, 3]),
              *flow_blocks(flow_steps(h)),
              *radar.blocks(rd),
              *coverage_blocks(d.get("coverage") or []),
              ("h1", "1. 업무 개요"), ("p", d.get("overview", "")),
              ("h1", "2. 담당 업무(R&R)"),
              ("table", ["담당 업무", "세부 내용·메모", "상태", "근거"],
               [[r["duty"], r.get("detail", ""), r.get("status", ""), _src(r, 3)] for r in live(S["rnr"])], [3, 3, 1, 2]),
              ("h1", "3. 연간 업무 일정"),
              ("table", ["시기", "할 일", "관련", "근거"],
               [[_when(s, _base_year(h)), s["task"], s.get("related", ""), _src(s)] for s in live(S["schedule"])], [1, 4, 2, 3]),
              ("h1", "4. 진행 중인 현안"),
              ("table", ["현안", "상태", "다음 할 일", "기한", "근거"],
               [[i["title"], i.get("state", ""), i.get("next_action", ""), i.get("due", ""), _src(i)] for i in live(S["issues"])],
               [3, 1, 3, 1, 2]),
              ("h1", "5. 협업 네트워크와 노하우"),
              *network.blocks_for_doc(net),
              ("h1", "6. 빈틈 스캐너"),
              ("table", ["구분", "내용", "처리"], [[flag_label(f["type"]), f["message"], ("해결: " + f.get("note", "")) if f.get("resolved") else "미해결"]
                                               for f in d["flags"]], [1, 5, 2]),
              ("h1", "7. 인계 자료 목록"),
              ("table", ["자료", "종류", "요약"], [[x["relpath"], x.get("kind", ""), x.get("summary", "")] for x in d["docs"]], [3, 1, 4]),
              ("note", "※ 각 항목의 근거는 원문 파일과 위치를 표시한 것입니다. 이 문서는 업무바통 도구로 작성한 초안을 전임자가 검토한 결과입니다.")]
    n = 8
    comments = rv.get("comments") or []
    if comments:
        blocks.insert(-1, ("h1", f"{n}. 전임자 메모"))
        blocks.insert(-1, ("table", ["작성", "내용"], [[c.get("at", ""), c.get("text", "")] for c in comments], [1, 4]))
        n += 1
    lineage = d.get("lineage") or []
    if lineage:
        rows = [[f"{i}대", x.get("name", ""), x.get("handed_to", ""), x.get("date", "")] for i, x in enumerate(lineage, 1)]
        rows.append([f"{len(lineage) + 1}대", f"{len(lineage) + 1}대 담당자(전임자)", f"{len(lineage) + 2}대 담당자(후임자)", "이번 인수인계"])
        blocks.insert(-1, ("h1", f"{n}. 업무 계보(지식 릴레이)"))
        blocks.insert(-1, ("table", ["세대", "담당자", "넘겨받은 사람", "인계일"], rows, [1, 2, 2, 2]))
    return blocks


# ---------------------------------------------------------------- 지식 릴레이: 바통 파일(.baton)

def build_baton(h):
    """다음 담당자가 '자료 넣기'에 그대로 넣으면 이어받는 인수인계서 파일.

    정리된 항목(삭제 제외, 끝난 일회성 일정 제외)·근거·처음 기록된 세대, 앞 세대부터 쌓인 질의응답·메모,
    담당자 계보를 담는다. 1대 → 2대 → 3대로 넘어갈수록 기관의 업무 지식이 쌓인다."""
    d = h["draft"]
    S = d["sections"]
    lineage = [dict(x) for x in d.get("lineage") or []]
    # 사람 이름 대신 세대 이름(1대·2대 담당자)으로 넘긴다
    holder = f"{len(lineage) + 1}대 담당자"
    lineage.append({"name": holder, "handed_to": f"{len(lineage) + 2}대 담당자", "date": date.today().isoformat(),
                    "work": h.get("title") or ""})
    for i, x in enumerate(lineage, 1):
        x["gen"] = i
    me = {"gen": len(lineage), "name": holder}
    sections = {}
    for sec, fields in _BATON_FIELDS.items():
        out = []
        for it in live(S[sec]):
            if sec == "schedule" and not it.get("recurring") and it.get("timing") in ("완료", "지난 일", "기한 지남"):
                continue  # 이미 지난 일회성 일정은 다음 담당자에게 넘길 지식이 아니다
            x = {k: it.get(k) for k in fields}
            x.update(status=it.get("status", ""), since=it.get("carried") or me,
                     sources=[{k: src.get(k, "") for k in ("file", "loc", "quote")} for src in it.get("sources", [])[:2]])
            out.append(x)
        sections[sec] = out
    rv = h.get("review") or {}
    interview = [dict(x) for x in d.get("carried_interview") or []]
    notes = [dict(x) for x in d.get("carried_notes") or []]
    notes += [{"text": c["text"], "by": holder, "at": c.get("at", "")} for c in rv.get("comments") or []]
    notes += [{"text": f"[노하우 · {k.get('kind', '요령')}] {k['text']}", "by": holder, "at": k.get("at", "")} for k in h.get("knowhow") or []]
    notes += [{"text": f"확인 결과: {f['message']} → {f['note']}", "by": holder, "at": ""}
              for f in d.get("flags", []) if f.get("resolved") and f.get("note")]
    return {
        "format": BATON_FORMAT,
        "app": "업무바통",
        "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "work": h.get("title") or "",
        "holder": holder,
        "handed_to": f"{len(lineage) + 1}대 담당자",
        "confirmed": bool(rv.get("confirmed")),
        "lineage": lineage,
        "sections": sections,
        "interview": interview,
        "notes": notes,
        "note": "업무바통 '자료 넣기'에 이 파일을 함께 넣으면 다음 담당자가 이 내용을 근거로 이어받습니다.",
    }


# ---------------------------------------------------------------- 내 일정으로 내보내기(.ics)

def _ics_escape(s):
    return (str(s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n"))


def _ics_fold(line):
    """RFC 5545: 한 줄은 75바이트 이하. 한글(UTF-8 3바이트)이 잘리지 않게 글자 단위로 접는다."""
    out, cur, size = [], "", 0
    for ch in line:
        n = len(ch.encode("utf-8"))
        if size + n > (75 if not out else 74):
            out.append(cur)
            cur, size = "", 0
        cur += ch
        size += n
    out.append(cur)
    return "\r\n ".join(out)


LAST = 99  # '말'(말일) 표시


def _approx_day(text):
    """날짜(일)가 없을 때 시기 표현으로 대략의 날: 초 5일, 중순 15일, 말 말일, 그 밖에는 1일."""
    if re.search(r"월\s*말|말까지|말일|하순", text):
        return LAST
    if re.search(r"중순", text):
        return 15
    if re.search(r"월\s*초|초순", text):
        return 5
    return 1


def _clamp(y, m, d):
    try:
        d = int(d)
    except (TypeError, ValueError):
        d = 1
    return date(y, m, max(1, min(d, calendar.monthrange(y, m)[1])))


def _next_on(months, day, start):
    """start 이후(당일 포함) 처음 오는 (months 중 한 달, day일). day가 그 달보다 크면 말일."""
    for k in range(0, 25):
        y, m = start.year + (start.month - 1 + k) // 12, (start.month - 1 + k) % 12 + 1
        if m in months:
            d = _clamp(y, m, day)
            if d >= start:
                return d
    return None


def build_ics(h, start=None):
    """연간 업무 달력 → iCalendar(.ics). Outlook·그룹웨어 일정에 '가져오기'로 바로 넣는다.

    - 매월 업무: 매월 반복(RRULE FREQ=MONTHLY), 말일 넘는 날짜는 말일(BYMONTHDAY=-1)
    - 매년·매분기 업무: 해당 달에 매년 반복(RRULE FREQ=YEARLY;BYMONTH=…)
    - 일회성 일정: 그 날짜 하루(이미 지난 일은 넣지 않음)
    - 날짜(일)가 자료에 없으면 그 달 1일에 넣고 설명에 '날짜 확인 필요'를 적는다
    - 알림: 매월 업무는 하루 전, 나머지는 일주일 전
    반환: (ics 바이트, {"events": n, "skipped": [(할 일, 이유)]})"""
    start = start or date.today()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    pred = h["draft"].get("predecessor") or "전임자"
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//업무바통//인수인계 업무 달력//KO", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", f"X-WR-CALNAME:{_ics_escape('업무바통 - ' + (h.get('title') or pred + ' 업무'))}",
             "X-WR-TIMEZONE:Asia/Seoul"]
    events, skipped = 0, []
    # 아직 확인되지 않은(충돌·불확실 등) 일정은 제목에 표시해 그대로 믿지 않게 한다
    open_flags = {}
    for f in h["draft"].get("flags", []):
        if not f.get("resolved"):
            for r in f.get("refs", []):
                open_flags.setdefault(r, f)

    def event(uid, day0, s, rrule="", alarm_days=7):
        nonlocal events
        approx = not s.get("day") or s.get("approx")
        src = "; ".join(f"{x['file'].split('/')[-1]} {x['loc']}" for x in s.get("sources", [])[:2])
        desc = [f"시기: {s.get('when') or _when(s)}"]
        if s.get("related"):
            desc.append(f"관련: {s['related']}")
        if src:
            desc.append(f"근거: {src}")
        if approx:
            desc.append("자료에 정확한 날짜(일)가 없어 대략의 날짜에 넣었습니다(초 5일·중순 15일·말 말일, 그 밖에는 1일 또는 착임일). 실제 날짜를 확인해 고쳐 주세요.")
        flag = open_flags.get(s["id"])
        if flag:
            desc.append(f"빈틈 스캐너({flag_label(flag['type'])}): {flag['message']}")
        desc.append(f"업무바통 인수인계({pred} → {h.get('successor') or '후임자'})에서 내보냄")
        summary = re.sub(r"\s+", " ", s["task"]).strip()
        if approx:
            summary += " (날짜 확인)"
        if flag:
            summary = f"[빈틈] {summary}"
        ev = ["BEGIN:VEVENT", f"UID:{uid}@baton-handover", f"DTSTAMP:{stamp}",
              f"DTSTART;VALUE=DATE:{day0:%Y%m%d}", f"DTEND;VALUE=DATE:{day0 + timedelta(days=1):%Y%m%d}",
              f"SUMMARY:{_ics_escape(summary[:150])}", f"DESCRIPTION:{_ics_escape(chr(10).join(desc))}",
              "CATEGORIES:업무바통,인수인계", "TRANSP:TRANSPARENT"]
        if rrule:
            ev.append(f"RRULE:{rrule}")
        ev += ["BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{_ics_escape(summary[:60])}",
               f"TRIGGER:-P{alarm_days}D", "END:VALARM", "END:VEVENT"]
        lines.extend(ev)
        events += 1

    for s in live(h["draft"]["sections"]["schedule"]):
        months = sorted({int(m) for m in s.get("months") or [] if str(m).isdigit() and 1 <= int(m) <= 12})
        if not months or not (s.get("task") or "").strip():
            skipped.append((s.get("task") or "(할 일 없음)", "시기(월)가 없음"))
            continue
        day = s.get("day") or _approx_day(f"{s.get('when') or ''} {s['task']}")
        rec = s.get("recurring") or ""
        uid = f"{h['id']}-{s['id']}"
        if rec == "매월":
            first = _next_on(range(1, 13), 31 if day == LAST else day, start)
            byday = -1 if day in (LAST, 31) else day
            event(uid, first, s, f"FREQ=MONTHLY;BYMONTHDAY={byday}", alarm_days=1)
        elif rec in ("매년", "매분기"):
            # 달마다 따로 매년 반복(여러 달을 한 규칙에 넣으면 날짜를 가장 짧은 달에 맞춰야 하고 Outlook 호환도 떨어짐)
            for m in months:
                last = calendar.monthrange(2027, m)[1]  # 2월은 28일 기준
                dd = last if day in (LAST,) or day > last else day
                byday = -1 if dd == last and (day == LAST or day > 28) else dd
                event(f"{uid}-{m}", _next_on([m], dd, start), s, f"FREQ=YEARLY;BYMONTH={m};BYMONTHDAY={byday}")
        else:
            if s.get("timing") in ("완료", "지난 일"):
                skipped.append((s["task"], f"이미 지난 일({s['timing']})"))
                continue
            for m in months:
                d0 = None
                if s.get("date"):
                    try:
                        d0 = date.fromisoformat(s["date"])
                    except ValueError:
                        d0 = None
                elif s.get("year"):
                    d0 = _clamp(s["year"], m, 31 if day == LAST else day)
                else:
                    # 연도 없는 일회성 일정: 올해 그 날짜로 보되, 착임일 전 반년 안이면 '기한 지남'(내년 일정으로 넘기지 않음)
                    d0 = _clamp(start.year, m, 31 if day == LAST else day)
                    if d0 < start and not s.get("day") and m == start.month:
                        d0 = start  # 날짜 없이 '이번 달 중'인 일은 착임일에 넣는다
                    elif d0 < start and (start - d0).days > 183:
                        d0 = _clamp(start.year + 1, m, 31 if day == LAST else day)
                if not d0 or d0 < start:
                    skipped.append((s["task"], f"기한 지남: {d0.isoformat() if d0 else str(m) + '월'}(착임일 {start.isoformat()} 전)"))
                    continue
                event(f"{uid}-{m}", d0, s)
                if s.get("date"):
                    break
    lines.append("END:VCALENDAR")
    data = "\r\n".join(_ics_fold(x) for x in lines) + "\r\n"
    return data.encode("utf-8"), {"events": events, "skipped": skipped}


def _parse_due(due, year):
    if not due:
        return None
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", due)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.match(r"(\d{1,2})월(?:\s*(\d{1,2})일)?", due)
    if m:
        try:
            return date(year, int(m.group(1)), int(m.group(2) or 28))
        except ValueError:
            return None
    return None


def build_manual(h, successor="", career="신규", start=None):
    """후임자 맞춤 업무매뉴얼: 착임월부터 12개월 일정, 기한순 현안, 첫 주 할 일, (신규자) 용어 설명."""
    d = h["draft"]
    S = d["sections"]
    start = start or date.today()
    order = [((start.month - 1 + i) % 12) + 1 for i in range(12)]
    months = []

    def in_slot(s, m):
        if m not in s["months"] or s.get("recurring") == "매월":
            return False
        if s.get("recurring"):  # 매년·매분기: 해마다 돌아온다
            return True
        if s.get("timing") in ("완료", "지난 일", "기한 지남"):
            return False  # 이미 지난 일은 앞으로의 할 일이 아니다(기한 지남은 첫 주 할 일로)
        slot_year = start.year + (1 if m < start.month else 0)
        return not s.get("year") or s["year"] == slot_year

    for m in order:
        items = [s for s in live(S["schedule"]) if in_slot(s, m)]
        monthly = [s for s in live(S["schedule"]) if s.get("recurring") == "매월"]
        months.append({"month": m, "items": items, "monthly": monthly})
    issues = []
    for i in live(S["issues"]):
        due = _parse_due(i.get("due"), start.year)
        if due and due < start and start.month - due.month > 6:
            due = date(due.year + 1, due.month, due.day)
        days = (due - start).days if due else None
        issues.append({**i, "days_left": days})
    issues.sort(key=lambda x: (x["days_left"] is None, x["days_left"] if x["days_left"] is not None else 0))
    contacts = live(S["contacts"])
    week1 = []
    urgent = [i for i in issues if i["days_left"] is not None and i["days_left"] <= 30]
    for i in urgent[:3]:
        state = "기한 지남" if i["days_left"] < 0 else f"D-{i['days_left']}"
        nxt = i.get("next_action") or "진행상황 확인"
        same = re.sub(r"\W", "", nxt) in re.sub(r"\W", "", i["title"])
        week1.append(f"[{state}] {i['title']}" + ("" if same else f": {nxt}"))
    for s in live(S["schedule"]):
        if s.get("timing") == "기한 지남" and len(week1) < 5 and not any(s["task"] in w for w in week1):
            week1.append(f"[기한 지남] {s['task']}: 처리됐는지 확인")
    for c in contacts[:4]:
        week1.append(f"{c['name']}에게 인사·업무 확인: {', '.join(c.get('topics', [])[:1])}")
    week1.append("빈틈 스캐너 항목(인수인계서 6장)을 전임자와 함께 정리")
    unresolved = [f for f in d["flags"] if not f.get("resolved")]
    terms = []
    if career == "신규":
        text = " ".join(s["task"] for s in S["schedule"]) + " ".join(i["title"] for i in S["issues"])
        terms = [(k, v) for k, v in GLOSSARY.items() if k in text]
    m = {"successor": successor, "career": career, "start": start.isoformat(), "week1": week1,
         "months": months, "issues": issues, "contacts": contacts, "terms": terms,
         "unresolved": unresolved, "rnr": live(S["rnr"])}
    m["flow"] = onboarding_flow(m)
    return m


def manual_blocks(h, m):
    import radar
    title = "후임자 업무매뉴얼"
    b = [("title", title),
         ("p", f"착임일 {m['start']} · {'신규 담당자' if m['career'] == '신규' else '경력 담당자'} 기준으로 정리했습니다. "
               "전임자의 자료와 인수인계 검토 결과를 근거로 합니다."),
         *radar.blocks(m.get("radar") or radar.build(h, date.fromisoformat(m["start"]))),
         ("h2", "착임 후 순서도"),
         ("table", [f"{x['name']}" + (" →" if i < 3 else "") for i, x in enumerate(m["flow"])],
          [[x["what"] for x in m["flow"]], [x["detail"] for x in m["flow"]]], [1, 1, 1, 1]),
         ("h1", "1. 첫 주에 할 일")] + [("bullet", x) for x in m["week1"]]
    b += [("h1", "2. 맡게 될 업무")] + [("bullet", r["duty"]) for r in m["rnr"]]
    rows = []
    for mo in m["months"]:
        for s in mo["items"]:
            rows.append([f"{mo['month']}월", (f"{s['day']}일 " if s.get("day") else "") + s["task"], _src(s, 1)])
    monthly = m["months"][0]["monthly"] if m["months"] else []
    b += [("h1", "3. 착임월부터 12개월 일정"),
          ("table", ["월", "할 일", "근거"], rows, [1, 5, 2])]
    if monthly:
        b += [("p", "매월 반복 업무:")] + [("bullet", f"{_when(s)}: {s['task']}") for s in monthly]
    b += [("h1", "4. 진행 중인 현안 (기한 순)"),
          ("table", ["현안", "남은 기간", "다음 할 일", "근거"],
           [[i["title"], ("기한 지남" if (i["days_left"] or 0) < 0 else f"D-{i['days_left']}") if i["days_left"] is not None else "기한 없음",
             i.get("next_action", ""), _src(i, 1)] for i in m["issues"]], [3, 1, 3, 2]),
          ("h1", "5. 꼭 알아둘 협업 상대"),
          ("table", ["협업 상대(역할)", "연락처", "이럴 때 연락"],
           [[c["name"], ", ".join(c.get("phones", [])), ", ".join(c.get("topics", [])[:2])]
            for c in m["contacts"]], [2, 2, 3])]
    if m["unresolved"]:
        b += [("h1", "6. 빈틈 스캐너에서 아직 남은 빈틈 (주의)")] + [("bullet", f"[{flag_label(f['type'])}] {f['message']}") for f in m["unresolved"]]
    if m["terms"]:
        b += [("h1", "7. 처음 보는 용어")] + [("table", ["용어", "뜻"], [[k, v] for k, v in m["terms"]], [1, 4])]
    return b


# ---------------------------------------------------------------- 표준 인수인계서(필수 5대 영역)
# 입력 폼을 표준화한다. 자료에서 찾은 내용으로 먼저 채우고(자동 작성), 사람이 고친 뒤 한글(HWPX) 서식으로 내려받는다.

FORM_AREAS = [
    ("a1", "1. 담당 사무 개요 및 근거 법령"),
    ("a2", "2. 주요 추진 업무 및 연간 일정"),
    ("a3", "3. 예산 및 계약 현황"),
    ("a4", "4. 미결 과제 및 유의 사항"),
    ("a5", "5. 보관 문서 및 주요 연락처"),
]


def prefill_form(h):
    """분석 결과로 5대 영역을 채운다. 근거 법령은 자료에 나온 이름을 '확인 전' 후보로 넣고, 법령 검색으로 조문을 붙이게 한다."""
    d = h["draft"]
    S = d["sections"]
    base_year = _base_year(h)
    rv = h.get("review") or {}
    team = next((c.get("org") for c in S["contacts"] if c.get("org") and re.search(r"(팀|과|실)$", c.get("org", ""))), "")
    flags = [f for f in d.get("flags", []) if not f.get("resolved") and f["type"] in ("충돌", "불확실", "기한 지남")]
    notes = [r.get("detail") for r in live(S["rnr"]) if r.get("detail")]
    return {
        "meta": {"org": "", "dept": team, "position": "", "from": d.get("predecessor") or "", "to": h.get("successor") or "",
                 "date": date.today().isoformat(), "witness": rv.get("by") if rv.get("by") and rv.get("by") != d.get("predecessor") else "",
                 "title": h.get("title") or ""},
        "a1": {"summary": re.sub(r"\s+", " ", d.get("overview", "")).strip()[:1500],
               "duties": [r["duty"] for r in live(S["rnr"])],
               "laws": [{"law": m["name"], "article": m.get("article", ""), "title": "", "text": "", "enforce": "",
                         "link": "", "verified": False, "found_in": f"{m['source']['file'].split('/')[-1]} {m['source']['loc']}"}
                        for m in d.get("law_mentions", [])[:15]]},
        "a2": {"tasks": [{"when": _when(s, base_year).replace(" [완료]", "").replace(" [지난 일]", ""), "task": s["task"],
                          "note": "; ".join(x for x in (s.get("related", ""), s.get("timing", "")) if x)}
                         for s in live(S["schedule"]) if s.get("timing") not in ("완료", "지난 일") or s.get("recurring")]},
        "a3": {"budget": [{"name": x["name"], "amount": _won(x["amount"], x.get("unit")), "spent": _won(x.get("spent"), x.get("unit")), "note": x.get("status", "")}
                          for x in d.get("ledger", []) if x["kind"] == "예산"],
               "contracts": [{"name": x["name"], "vendor": x.get("vendor", ""), "period": x.get("period", ""),
                              "amount": _won(x["amount"], x.get("unit")), "status": x.get("status", "")}
                             for x in d.get("ledger", []) if x["kind"] == "계약"]},
        "a4": {"issues": [{"title": i["title"], "state": i.get("state", ""), "next": i.get("next_action", ""), "due": i.get("due", "")}
                          for i in live(S["issues"])],
               "cautions": [f"[{flag_label(f['type'])}] {f['message']}" for f in flags[:10]] + notes[:5]},
        "a5": {"docs": [{"name": x["relpath"].split("/")[-1], "kind": x.get("kind", ""), "where": x["relpath"]}
                        for x in d.get("docs", []) if not x.get("error")],
               "contacts": [{"name": c["name"], "org": c.get("org", ""), "title": c.get("title", ""),
                             "phone": ", ".join(c.get("phones", []) + c.get("emails", [])), "topic": ", ".join(c.get("topics", [])[:2])}
                            for c in live(S["contacts"])]},
    }


def _won(v, unit=""):
    """금액 칸: 숫자면 천 단위 쉼표와 단위(원·천원)를 붙인다."""
    s = str(v or "").strip()
    if re.fullmatch(r"\d+(\.0+)?", s):
        n = int(float(s))
        u = "천원" if "천원" in (unit or "") else "백만원" if "백만원" in (unit or "") else "원"
        return f"{n:,}{u}"
    return s


FORM_FIELDS = {
    "a2.tasks": ["when", "task", "note"],
    "a3.budget": ["name", "amount", "spent", "note"],
    "a3.contracts": ["name", "vendor", "period", "amount", "status"],
    "a4.issues": ["title", "state", "next", "due"],
    "a5.docs": ["name", "kind", "where"],
    "a5.contacts": ["name", "org", "title", "phone", "topic"],
    "a1.laws": ["law", "article", "title", "text", "enforce", "link", "found_in"],
}


def clean_form(f):
    """화면에서 보낸 폼을 정해진 칸만 남겨 저장(글자 수 상한)."""
    t = lambda v, n=2000: str(v or "").strip()[:n]
    f = f if isinstance(f, dict) else {}
    meta = f.get("meta") if isinstance(f.get("meta"), dict) else {}
    out = {"meta": {k: t(meta.get(k), 100) for k in ("org", "dept", "position", "from", "to", "date", "witness", "title")}}
    for area in ("a1", "a2", "a3", "a4", "a5"):
        out[area] = {}
    a1 = f.get("a1") if isinstance(f.get("a1"), dict) else {}
    out["a1"]["summary"] = t(a1.get("summary"), 4000)
    out["a1"]["duties"] = [t(x, 300) for x in (a1.get("duties") or [])[:100] if t(x)]
    a4 = f.get("a4") if isinstance(f.get("a4"), dict) else {}
    out["a4"]["cautions"] = [t(x, 500) for x in (a4.get("cautions") or [])[:100] if t(x)]
    for path, cols in FORM_FIELDS.items():
        area, key = path.split(".")
        src = f.get(area) if isinstance(f.get(area), dict) else {}
        rows = src.get(key) if isinstance(src.get(key), list) else []
        clean = []
        for r in rows[:300]:
            if not isinstance(r, dict):
                continue
            row = {c: t(r.get(c), 6000 if c == "text" else 500) for c in cols}
            if path == "a1.laws":
                row["verified"] = bool(r.get("verified"))
                row["target"] = r.get("target") if r.get("target") in ("law", "admrul", "ordin") else "law"
            if any(row.get(c) for c in cols):
                clean.append(row)
        out[area][key] = clean
    return out


def form_blocks(h, f):
    """표준 인수인계서(필수 5대 영역) 문서 블록: 한글(HWPX)·워드·마크다운 공통."""
    m = f["meta"]
    title = "업무 인수인계서"
    b = [("title", title),
         ("table", ["구분", "내용", "구분", "내용"], [
             ["기관", m.get("org") or "-", "부서", m.get("dept") or "-"],
             ["인계자", m.get("from") or "-", "인수자", m.get("to") or "-"],
             ["직위", m.get("position") or "-", "인계일", m.get("date") or "-"],
             ["업무명", m.get("title") or "-", "입회자", m.get("witness") or "-"]], [1, 2, 1, 2])]
    a1 = f["a1"]
    b += [("h1", FORM_AREAS[0][1]), ("h2", "가. 담당 사무 개요"), ("p", a1.get("summary") or "-")]
    if a1.get("duties"):
        b += [("h2", "나. 담당 사무")] + [("bullet", x) for x in a1["duties"]]
    laws = a1.get("laws") or []
    b += [("h2", "다. 근거 법령")]
    if laws:
        b.append(("table", ["법령", "조문", "내용", "시행일·확인"],
                  [[x["law"], " ".join(y for y in (x.get("article"), f"({x['title']})" if x.get("title") else "") if y),
                    (x.get("text") or "(조문 미확인)")[:600], (x.get("enforce") or "") + (" · 법령정보 확인" if x.get("verified") else " · 자료 기재(확인 전)")]
                   for x in laws], [2, 1, 6, 2]))
        b.append(("note", "※ '법령정보 확인'은 국가법령정보센터(law.go.kr) Open API로 받아 온 현행 조문입니다."))
    else:
        b.append(("p", "-"))
    rows = [[x["when"], x["task"], x["note"]] for x in f["a2"].get("tasks", [])]
    b += [("h1", FORM_AREAS[1][1]), ("table", ["시기", "업무", "비고"], rows or [["-", "-", "-"]], [2, 5, 2])]
    b += [("h1", FORM_AREAS[2][1]), ("h2", "가. 예산"),
          ("table", ["사업명", "예산액", "집행액", "비고"], [[x["name"], x["amount"], x["spent"], x["note"]] for x in f["a3"].get("budget", [])] or [["-"] * 4], [3, 2, 2, 3]),
          ("h2", "나. 계약(조달)"),
          ("table", ["계약명", "업체", "계약기간", "금액", "진행 상황"],
           [[x["name"], x["vendor"], x["period"], x["amount"], x["status"]] for x in f["a3"].get("contracts", [])] or [["-"] * 5], [3, 2, 2, 2, 2])]
    b += [("h1", FORM_AREAS[3][1]), ("h2", "가. 미결 과제"),
          ("table", ["과제", "현재 상태", "다음 조치", "기한"], [[x["title"], x["state"], x["next"], x["due"]] for x in f["a4"].get("issues", [])] or [["-"] * 4], [4, 1, 4, 1]),
          ("h2", "나. 유의 사항")] + ([("bullet", x) for x in f["a4"].get("cautions", [])] or [("p", "-")])
    b += [("h1", FORM_AREAS[4][1]), ("h2", "가. 보관 문서"),
          ("table", ["문서명", "구분", "보관 위치"], [[x["name"], x["kind"], x["where"]] for x in f["a5"].get("docs", [])] or [["-"] * 3], [3, 1, 4]),
          ("h2", "나. 주요 연락처"),
          ("table", ["협업 상대(역할)", "소속", "직위", "연락처", "관련 업무"],
           [[x["name"], x["org"], x["title"], x["phone"], x["topic"]] for x in f["a5"].get("contacts", [])] or [["-"] * 5], [2, 2, 1, 2, 3])]
    b += [("p", "위와 같이 업무를 인계·인수합니다."),
          ("p", f"{m.get('date') or ''}"),
          ("table", ["구분", "직위", "성명", "서명"], [
              ["인계자", m.get("position") or "", m.get("from") or "", "(인)"],
              ["인수자", "", m.get("to") or "", "(인)"],
              ["입회자", "", m.get("witness") or "", "(인)"]], [1, 2, 2, 1])]
    return b
