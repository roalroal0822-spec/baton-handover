"""인수인계서·후임자 업무매뉴얼 문서 만들기 (한글·워드·마크다운 공통 블록)."""
import re
from datetime import date

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


def handover_blocks(h):
    d = h["draft"]
    S = d["sections"]
    rv = h.get("review", {})
    blocks = [("title", f"인수인계서 ({h.get('title') or '업무 인수인계'})"),
              ("table", ["구분", "내용"], [
                  ["전임자", d.get("predecessor") or "-"],
                  ["후임자", h.get("successor") or "-"],
                  ["작성일", h.get("created", "")[:10]],
                  ["전임자 확인", f"확인 완료 ({rv.get('by', '')}, {rv.get('at', '')})" if rv.get("confirmed") else "검토 중(초안)"],
                  ["작성 방식", d.get("engine", "")],
              ], [1, 3]),
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
              ("h1", "5. 협의 연락망"),
              ("table", ["이름", "소속", "직위", "연락처", "협의 업무", "근거"],
               [[c["name"], c.get("org", ""), c.get("title", ""), "\n".join(c.get("phones", []) + c.get("emails", [])),
                 "\n".join(c.get("topics", [])[:2]), _src(c)] for c in live(S["contacts"])], [1, 2, 1, 2, 3, 2]),
              ("h1", "6. 확인이 필요한 사항"),
              ("table", ["구분", "내용", "처리"], [[f["type"], f["message"], ("해결: " + f.get("note", "")) if f.get("resolved") else "미해결"]
                                               for f in d["flags"]], [1, 5, 2]),
              ("h1", "7. 인계 자료 목록"),
              ("table", ["자료", "종류", "요약"], [[x["relpath"], x.get("kind", ""), x.get("summary", "")] for x in d["docs"]], [3, 1, 4]),
              ("note", "※ 각 항목의 근거는 원문 파일과 위치를 표시한 것입니다. 이 문서는 업무바통 도구로 작성한 초안을 전임자가 검토한 결과입니다.")]
    comments = rv.get("comments") or []
    if comments:
        blocks.insert(-1, ("h1", "8. 전임자 메모"))
        blocks.insert(-1, ("table", ["작성", "내용"], [[c.get("at", ""), c.get("text", "")] for c in comments], [1, 4]))
    return blocks


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
        week1.append(f"{c['name']} {c.get('title', '')}({c.get('org', '')})에게 인사·업무 확인: {', '.join(c.get('topics', [])[:1])}")
    week1.append("확인 필요 사항(인수인계서 6장)을 전임자와 함께 정리")
    unresolved = [f for f in d["flags"] if not f.get("resolved")]
    terms = []
    if career == "신규":
        text = " ".join(s["task"] for s in S["schedule"]) + " ".join(i["title"] for i in S["issues"])
        terms = [(k, v) for k, v in GLOSSARY.items() if k in text]
    return {"successor": successor, "career": career, "start": start.isoformat(), "week1": week1,
            "months": months, "issues": issues, "contacts": contacts, "terms": terms,
            "unresolved": unresolved, "rnr": live(S["rnr"])}


def manual_blocks(h, m):
    title = f"{m['successor'] or '후임자'}님을 위한 업무매뉴얼"
    b = [("title", title),
         ("p", f"착임일 {m['start']} · {'신규 담당자' if m['career'] == '신규' else '경력 담당자'} 기준으로 정리했습니다. "
               f"전임자 {h['draft'].get('predecessor', '')}의 자료와 인수인계 검토 결과를 근거로 합니다."),
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
          ("h1", "5. 꼭 알아둘 연락처"),
          ("table", ["이름", "소속·직위", "연락처", "이럴 때 연락"],
           [[c["name"], f"{c.get('org', '')} {c.get('title', '')}", ", ".join(c.get("phones", [])), ", ".join(c.get("topics", [])[:2])]
            for c in m["contacts"]], [1, 2, 2, 3])]
    if m["unresolved"]:
        b += [("h1", "6. 아직 확인되지 않은 내용 (주의)")] + [("bullet", f"[{f['type']}] {f['message']}") for f in m["unresolved"]]
    if m["terms"]:
        b += [("h1", "7. 처음 보는 용어")] + [("table", ["용어", "뜻"], [[k, v] for k, v in m["terms"]], [1, 4])]
    return b
