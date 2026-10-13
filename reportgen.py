"""리포트 업그레이더: 전임자가 넣어 둔 보고서를 찾아, 공공기관 보고서 양식으로 '업그레이드한 다음 보고서'를 쓴다.

전임자 보고서를 그대로 베끼지 않는다.
  - 보고 요지(세 줄 요약)를 맨 위에 둔다
  - 정기 보고(월별 결과 보고)는 다음 회차로 넘기고, 지난 회차 값과 나란히 비교하는 표를 만든다(이번 값은 【입력】)
  - 지난 보고서의 '조치 사항·향후 계획'을 이번 보고서의 '후속 조치 점검'으로 이어받는다
  - 다른 자료(일정·현안·계약·예산·빈틈 스캐너·노하우)에서 이 보고서와 관련된 최신 내용을 찾아 넣는다
  - 근거 법령·보고처·제출 기한을 밝히고, 계획서는 다음 연도로 날짜를 옮기며 전년도 결과를 반영한다
  - 무엇을 보완했는지 '전임자 보고서 대비 보완 사항'으로 따로 적는다
모든 문장에는 근거(원문 위치)를 붙이고, 자료에 없는 값은 지어내지 않고 【입력: …】으로 비워 둔다.
"""
import calendar
import re
from datetime import date, timedelta

from core.textutil import similarity

DOC_EXT = {".hwpx", ".hwp", ".pdf", ".docx", ".txt", ".md", ".pptx"}
REPORT_RX = re.compile(r"보고|계획|현황|실적|결과|검토서|추진")
HEAD_RX = re.compile(r"^\s*(?:\d{1,2}|[ⅠⅡⅢⅣⅤⅥⅦ]+|[가-하])\s*[.)]\s*(\S.{0,38})$|^\s*[□■◆◇]\s*(\S.{0,38})$")
MEAS_RX = re.compile(r"^(?P<item>[가-힣A-Za-z][가-힣A-Za-z()\s]{1,18}?)\s*[:：]\s*(?P<val>[\d.]+)\s*(?P<unit>[A-Za-z/%㎎ℓ가-힣]{0,8})\s*"
                     r"\(\s*기준\s*(?P<std>[^)]+)\)\s*[-–]?\s*(?P<judge>부적합|적합|양호|불량|미흡|정상|이상)?")
KV_RX = re.compile(r"([가-힣]{2,6})\s*[:：]\s*([^:：]+?)(?=\s+[가-힣]{2,6}\s*[:：]|$)")
PERIOD_RX = re.compile(r"(20\d{2})\s*년\s*(\d{1,2})\s*월")
YEAR_RX = re.compile(r"(20\d{2})\s*년")
DATE_RX = re.compile(r"(20\d{2})\s*[.\-]\s*(\d{1,2})\s*[.\-]\s*(\d{1,2})\s*\.?")
GENERIC = {"보고", "결과", "계획", "현황", "업무", "초안", "공공기관", "기관", "추진", "관련", "실시", "대한", "위한", "정기", "기준"}
SLOT_RX = [("overview", r"개요|목적|배경"), ("action", r"조치|문제|개선|대책|특이"), ("plan", r"향후|다음"),
           ("admin", r"^결과\s*보고$|보고\s*체계|체계|행정|협조|담당|조직"), ("result", r"결과|현황|실적|일정|내용|계획")]
OFFICIAL = (".hwpx", ".hwp", ".pdf", ".docx")


def josa(word, a="을", b="를"):
    """마지막 한글 글자의 받침으로 조사를 고른다."""
    hs = re.findall(r"[가-힣]", word or "")
    if not hs:
        return a
    return a if (ord(hs[-1]) - 0xAC00) % 28 else b


def subject_of(title):
    t = re.sub(r"\(.*?\)", "", title)
    t = re.sub(r"(결과\s*)?보고(서)?|계획(서)?|현황|\(?초안\)?|^\s*20\d{2}\s*년(도)?", "", t)
    return re.sub(r"\s+", " ", t).strip() or title


def _fill(label):
    return f"【입력: {label}】"


def _words(text):
    ws = set()
    for w in re.findall(r"[가-힣A-Za-z]{2,}", text or ""):
        w = re.sub(r"(은|는|이|가|을|를|의|에|에서|으로|로|과|와|및)$", "", w)
        if len(w) >= 2 and w not in GENERIC and not re.fullmatch(r"\d+년?", w):
            ws.add(w)
    return ws


def kind_of(title):
    if re.search(r"결과\s*보고|결과보고", title):
        return "결과 보고"
    if "계획" in title:
        return "계획"
    return "현황 보고"


def candidates(h, blocks):
    """넣은 자료 가운데 보고서로 보이는 문서(공식 문서형, 제목에 보고·계획·현황 등)."""
    by_file = {}
    for b in blocks:
        by_file.setdefault(b["file"], []).append(b)
    out = []
    for d in h["draft"].get("docs", []):
        if d.get("error") or d.get("ext") not in DOC_EXT:
            continue
        bs = by_file.get(d["relpath"]) or []
        if not bs:
            continue
        title = bs[0]["text"].strip()[:80]
        name = d["relpath"].rsplit("/", 1)[-1]
        if not (REPORT_RX.search(name) or REPORT_RX.search(title)) or len(bs) < 3:
            continue
        if sum(1 for b in bs if re.search(r"스캔|사진", b.get("loc", ""))) > len(bs) / 2:
            continue  # 글자 인식으로만 읽은 스캔본은 원문 구조를 믿기 어려워 뺀다
        parsed = parse(bs)
        out.append({"id": d["id"] if d.get("id") else d["relpath"], "file": d["relpath"], "name": name, "title": parsed["title"],
                    "kind": kind_of(parsed["title"] + " " + name), "doc_kind": d.get("kind", ""),
                    "sections": [s["name"] for s in parsed["sections"]], "lines": len(bs)})
    return out


def parse(bs):
    """보고서 구조: 제목, 작성 부서 줄, 장(제목·줄·표)."""
    title = re.sub(r"\s+", " ", bs[0]["text"]).strip()
    org = ""
    sections, cur = [], {"name": "본문", "lines": [], "rows": []}
    for b in bs[1:]:
        t = b["text"].strip()
        if not org and len(sections) == 0 and not cur["lines"] and re.search(r"(팀|과|실|부|기관|센터)\b|작성", t) and len(t) < 60:
            org = t
            continue
        m = HEAD_RX.match(t)
        if m and len(t) <= 42 and not re.search(r"[:：]", t):
            if cur["lines"] or cur["rows"]:
                sections.append(cur)
            cur = {"name": (m.group(1) or m.group(2)).strip(), "lines": [], "rows": []}
            continue
        if re.match(r"^표\d", b.get("loc", "")):
            cur["rows"].append({"cells": [c.strip() for c in t.split("|")], "src": b})
        else:
            cur["lines"].append({"text": re.sub(r"^[•·\-∙○]\s*", "", t), "src": b})
    if cur["lines"] or cur["rows"]:
        sections.append(cur)
    for s in sections:
        s["slot"] = next((k for k, rx in SLOT_RX if re.search(rx, s["name"])), "other")
    return {"title": title, "org": org, "sections": sections}


def _src(b):
    return f"{b['file'].rsplit('/', 1)[-1]} {b['loc']}" if b else ""


def _shift_month(y, m, k=1):
    m2 = m - 1 + k
    return y + m2 // 12, m2 % 12 + 1


def _related(h, words, rd=None, net=None, everything=False):
    """이 보고서 주제와 관련된 다른 자료 내용(일정·현안·빈틈·계약·예산·노하우·리스크)."""
    S = h["draft"]["sections"]

    def hit(text):
        if everything:
            return True
        tw = _words(text)
        return len(words & tw) >= 1 and any(len(w) >= 3 or w in tw for w in words & tw)
    rel = {"schedule": [s for s in S["schedule"] if s.get("status") != "삭제" and hit(s["task"] + " " + (s.get("related") or ""))],
           "issues": [i for i in S["issues"] if i.get("status") != "삭제" and hit(i["title"] + " " + (i.get("next_action") or ""))],
           "contacts": [c for c in S["contacts"] if c.get("status") != "삭제" and hit(" ".join(c.get("topics") or []) + " " + (c.get("org") or ""))],
           "flags": [f for f in h["draft"].get("flags", []) if not f.get("resolved") and hit(f["message"])],
           "ledger": [x for x in h["draft"].get("ledger", []) if hit(x.get("name", "") + " " + (x.get("status") or ""))],
           "laws": [m for m in h["draft"].get("law_mentions", []) if hit(m.get("source", {}).get("quote", "") + " " + m["name"])],
           "events": [e for e in (rd or {}).get("timeline", []) if e["days"] >= 0 and hit(e["title"])],
           "cards": [k for k in (net or {}).get("cards", []) if hit(k["text"])]}
    return rel


def generate(h, blocks, file, mode="next", base=None, rd=None, net=None, llm=None):
    """file: 전임자 보고서(상대경로). mode: next(다음 회차·다음 연도) | refresh(같은 회차 보완). → 보고서 dict"""
    base = base or date.today()
    bs = [b for b in blocks if b["file"] == file]
    if not bs:
        raise ValueError("보고서를 찾을 수 없습니다")
    p = parse(bs)
    title0, kind = p["title"], kind_of(p["title"] + " " + file)
    words = _words(title0)
    rel = _related(h, words, rd, net)
    if kind == "현황 보고":  # 업무 전반 현황: 주제를 좁히지 않고 업무 전체의 현안·일정을 담는다
        everything = _related(h, set(), rd, net, everything=True)
        for k in ("issues", "events", "contacts", "flags"):
            rel[k] = everything[k][:6]
    up = []                     # 보완 사항
    fills = 0
    secs = []                   # [{"title", "bullets":[{"text","src"}], "table":{...}}]
    # 작성 부서: 전임자 보고서의 문서번호·작성일·작성자는 새 보고서에 쓰지 않는다
    org = re.sub(r"\(?\s*문서번호[^)]*\)?|\(\s*20\d{2}\.[^)]*\)|작성\s*[:：].*$", "", p["org"]).strip(" ,·") or "【입력: 작성 부서】"

    # 제목·기간
    pm = PERIOD_RX.search(title0)
    py = YEAR_RX.search(title0)
    if kind == "결과 보고" and pm and mode == "next":
        y0, m0 = int(pm.group(1)), int(pm.group(2))
        y1, m1 = _shift_month(y0, m0)
        title = title0.replace(pm.group(0), f"{y1}년 {m1}월")
        prev_label, cur_label = f"{m0}월", f"{m1}월"
        up.append(f"다음 회차({y1}년 {m1}월) 보고서로 넘기고, 지난 회차({m0}월) 값과 나란히 비교하도록 구성")
    elif kind == "계획" and py and mode == "next":
        y0 = int(py.group(1))
        y1 = y0 + 1
        title = YEAR_RX.sub(f"{y1}년", title0, count=1)
        prev_label, cur_label = f"{y0}년", f"{y1}년"
        up.append(f"{y1}년 계획으로 날짜를 옮기고, {y0}년 추진 결과(완료·지연·확인 필요)를 반영")
    else:
        title = re.sub(r"\s*\(?초안\)?\s*", " ", title0).strip()
        title = f"{title} ({base.year}년 {base.month}월 {base.day}일 기준)"
        prev_label, cur_label, y1, m1 = "이전", "현재", base.year, base.month
        up.append(f"{base.year}. {base.month}. {base.day}. 기준으로 다른 자료의 최신 내용을 모아 다시 작성")

    # 원문 장 모으기
    slot = {}
    for s in p["sections"]:
        slot.setdefault(s["slot"], []).append(s)
    lines = lambda k: [ln for s in slot.get(k, []) for ln in s["lines"]]

    # 1. 개요
    ov = []
    purpose = lines("overview")
    if purpose and kind != "결과 보고" and not any(KV_RX.search(x["text"]) for x in purpose):
        ov += [{"text": f"목적: {x['text']}", "src": _src(x["src"])} for x in purpose[:2]]
    else:
        subj = subject_of(title0)
        what = "결과" if kind == "결과 보고" else ("추진 현황과 향후 일정" if kind == "현황 보고" else "추진 계획")
        ov.append({"text": f"목적: {subj} {what}{josa(what)} 보고하고 후속 조치를 관리하고자 함", "src": ""})
        up.append("원문에 없던 '목적'을 넣어 보고 이유를 분명히 함")
    # 근거: 법령 이름 + 이 보고를 정한 다른 문서(계획서 등)
    basis = [f"{m['name']}" + (f" {m['article']}" if m.get("article") else "") for m in rel["laws"][:3]]
    plan_ref = []
    for b in blocks:
        if (b["file"] != file and b["file"].lower().endswith(OFFICIAL) and _words(b["text"]) & words
                and re.search(r"제출|보고|통보|매월|매년", b["text"]) and len(b["text"]) < 160):
            plan_ref.append(b)
    if plan_ref:
        f0 = plan_ref[0]
        doc_title = next((x["text"] for x in blocks if x["file"] == f0["file"]), f0["file"])[:40]
        basis.append(f"「{doc_title}」" + ("" if "|" in f0["text"] else f": {f0['text'].lstrip('•·- ')}"))
    if basis:
        ov.append({"text": "근거: " + " / ".join(basis), "src": _src(plan_ref[0]) if plan_ref else ""})
        up.append("보고 근거(법령·상위 계획 문서)를 찾아 명시")
    # 결과 보고: 검사 개요(일자·기관·대상) — 다음 회차 일자는 지난 보고서의 '다음 예정일'로 미리 채움
    kv_lines = [x for x in lines("overview") if KV_RX.search(x["text"])]
    next_due = None
    for x in lines("plan") + lines("other"):
        dm = DATE_RX.search(x["text"])
        if dm and re.search(r"다음|예정", x["text"]):
            next_due = (date(int(dm.group(1)), int(dm.group(2)), int(dm.group(3))), x)
            break
    for x in kv_lines:
        parts = []
        for k, v in KV_RX.findall(x["text"]):
            v = v.strip()
            if kind == "결과 보고" and mode == "next" and re.search(r"일|일자|일시", k):
                if next_due:
                    parts.append(f"{k}: {next_due[0]:%Y. %-m. %-d.}(예정, 실제 일자 확인)")
                else:
                    parts.append(f"{k}: {_fill(k)}")
                    fills += 1
            else:
                parts.append(f"{k}: {v}")
        ov.append({"text": " / ".join(parts), "src": _src(x["src"])})
    if next_due and kind == "결과 보고":
        up.append(f"지난 보고서의 '다음 예정일'({next_due[0]:%-m. %-d.})을 이번 보고서 개요에 미리 반영")
    secs.append({"title": "개요", "bullets": ov})

    # 2. 결과·현황
    meas = []
    for x in lines("result"):
        m = MEAS_RX.match(x["text"])
        if m:
            meas.append((m, x))
    if kind == "결과 보고" and meas:
        rows = []
        for m, x in meas:
            prev = f"{m.group('val')}{m.group('unit')}" + (f" ({m.group('judge')})" if m.group("judge") else "")
            if mode == "next":
                rows.append([m.group("item").strip(), _fill(f"{cur_label} 측정값"), prev, m.group("std").strip(), _fill("적합/부적합")])
                fills += 2
            else:
                rows.append([m.group("item").strip(), prev, "-", m.group("std").strip(), m.group("judge") or "-"])
        hdr = ["항목", f"이번({cur_label})", f"지난({prev_label})", "기준", "판정"] if mode == "next" else ["항목", "측정값", "비고", "기준", "판정"]
        bad = [m.group("item").strip() for m, _ in meas if m.group("judge") in ("부적합", "불량", "미흡", "이상")]
        bl = []
        if bad:
            bl.append({"text": f"지난 회차 기준 미달 항목({', '.join(bad)})은 이번 회차에 중점 확인", "src": _src(meas[0][1]["src"])})
        secs.append({"title": "검사(추진) 결과", "table": {"header": hdr, "rows": rows}, "bullets": bl})
        up.append("결과를 '이번·지난 회차·기준·판정' 비교표로 정리(원문은 줄글)")
    elif kind == "계획":
        items = []
        for x in lines("result") + lines("other"):
            t = x["text"]
            t2 = DATE_RX.sub(lambda d: f"{int(d.group(1)) + (1 if mode == 'next' else 0)}. {int(d.group(2))}. {int(d.group(3))}.", t)
            t2 = re.sub(r"\(\s*\)", "", t2)
            items.append({"text": t2 + (" (요일·기간 재확인)" if t2 != t else ""), "src": _src(x["src"])})
        for s in rel["schedule"]:
            if not any(similarity(s["task"], i["text"]) >= 0.35 for i in items):
                items.append({"text": f"{'·'.join(str(m) + '월' for m in s['months'][:3])}: {s['task']} (다른 자료에서 찾아 추가)",
                              "src": _src(s["sources"][0]) if s.get("sources") else ""})
                up.append(f"계획서에 빠져 있던 관련 일정 반영: {s['task'][:30]}")
        secs.append({"title": "추진 일정", "bullets": items})
        done = [s for s in rel["schedule"] if s.get("timing") in ("완료", "지난 일")]
        lessons = [{"text": f"[{y1 - 1}년 결과] {s['task']} — {s.get('timing')}", "src": _src(s["sources"][0]) if s.get("sources") else ""} for s in done[:6]]
        if lessons:
            secs.append({"title": f"{y1 - 1}년 추진 결과 반영", "bullets": lessons})
            up.append("전년도 추진 결과(완료·확인 필요)를 별도 장으로 정리")
    else:
        # 현황 보고: 예산·계약 표를 자료에서 계산해 넣는다
        bud = [x for x in h["draft"].get("ledger", []) if x["kind"] == "예산"]
        con = [x for x in h["draft"].get("ledger", []) if x["kind"] == "계약"]
        bl, extra_plan, extra_admin = [], [], []
        for x in lines("result") + lines("other") + lines("overview"):
            t = x["text"]
            if len(re.findall(r"[가-힣]", t)) < 4:
                continue
            item = {"text": t, "src": _src(x["src"])}
            if re.search(r"규정|법령|법\b|훈령|지침|조례", t):
                ov.append({"text": "근거: " + re.sub(r"^관련\s*규정\s*[:：]\s*", "", t), "src": item["src"]})
            elif re.search(r"시스템|보관|공유폴더|서버", t):
                extra_admin.append({"text": "자료·시스템: " + re.sub(r"^시스템\s*[:：]\s*", "", t), "src": item["src"]})
            elif re.search(r"할 일|예정|까지", t):
                extra_plan.append(item)
            else:
                bl.append(item)
        sec = {"title": "추진 현황", "bullets": bl[:8]}
        if bud:
            rows, ta, ts = [], 0, 0
            for x in bud:
                try:
                    a, s_ = float(x["amount"]), float(x.get("spent") or 0)
                except ValueError:
                    continue
                ta, ts = ta + a, ts + s_
                rows.append([x["name"], f"{a:,.0f}", f"{s_:,.0f}", f"{(s_ / a * 100) if a else 0:.1f}%", x.get("status", "")])
            if rows:
                rows.append(["합계", f"{ta:,.0f}", f"{ts:,.0f}", f"{(ts / ta * 100) if ta else 0:.1f}%", ""])
                unit = next((x.get("unit") for x in bud if x.get("unit")), "")
                sec["table"] = {"header": ["사업명", f"예산액{unit}", "집행액", "집행률", "비고"], "rows": rows}
                up.append(f"예산 집행현황 표를 자료(엑셀)에서 계산해 추가(집행률 합계 {(ts / ta * 100) if ta else 0:.1f}%)")
        secs.append(sec)
        if con:
            secs.append({"title": "계약 현황", "table": {"header": ["계약명", "업체", "계약기간", "진행"],
                                                       "rows": [[x["name"], x.get("vendor", ""), x.get("period", ""), x.get("status", "")] for x in con]},
                         "bullets": []})
            up.append("계약 현황 표 추가")

    # 3. 후속 조치·문제점
    acts = []
    for x in lines("action") + (lines("plan") if kind == "결과 보고" else []):
        t = x["text"]
        if next_due and x is next_due[1]:
            continue
        if re.search(r"완료|적합 판정|해결", t):
            acts.append({"text": f"[지난 회차 조치 완료] {t}", "src": _src(x["src"])})
        elif re.search(r"제출|보고", t) and re.search(r"매월|매년|까지", t):
            continue  # 제출 의무는 행정사항으로
        else:
            acts.append({"text": f"[이번 회차 확인] {t} → 결과: {_fill('확인 결과')}", "src": _src(x["src"])})
            fills += 1
    for i in rel["issues"][:6]:
        if set(i.get("origin") or []) <= {"사진"} and i.get("origin"):
            continue  # 사진 글자에서만 나온 현안 조각은 보고서 본문에 넣지 않는다
        if not any(similarity(i["title"], a["text"]) >= 0.4 for a in acts):
            acts.append({"text": f"[진행 중 현안] {i['title']}" + (f" — 다음 할 일: {i['next_action']}" if i.get("next_action") and i["next_action"] not in i["title"] else "")
                         + (f" (기한 {i['due']})" if i.get("due") else ""), "src": _src(i["sources"][0]) if i.get("sources") else ""})
    for f in rel["flags"][:3]:
        acts.append({"text": f"[확인 필요] {f['message']}", "src": ""})
    if acts:
        secs.append({"title": "후속 조치 및 문제점", "bullets": acts})
        if any(a["text"].startswith("[이번 회차 확인]") for a in acts):
            up.append("지난 보고서의 조치·향후 계획을 이번 회차 '후속 조치 점검' 항목으로 이어받음")
        if rel["flags"]:
            up.append(f"빈틈 스캐너에서 이 보고서와 관련된 확인 필요 {len(rel['flags'])}건을 함께 표시")

    # 4. 향후 계획(앞으로의 관련 일정 + 다음 제출 기한)
    plan = []
    due_line = next((x for s in p["sections"] for x in s["lines"] if re.search(r"매월\s*(\d{1,2})\s*일까지|매년", x["text"]) and re.search(r"제출|보고", x["text"])), None)
    if not due_line:
        due_line = next((b for b in plan_ref if re.search(r"매월\s*\d{1,2}\s*일까지", b["text"])), None)
        due_line = {"text": due_line["text"], "src": due_line} if due_line else None
    if due_line:
        dm = re.search(r"매월\s*(\d{1,2})\s*일까지", due_line["text"])
        if dm and kind == "결과 보고" and mode == "next":
            ny, nm = _shift_month(y1, m1)
            d = min(int(dm.group(1)), calendar.monthrange(ny, nm)[1])
            plan.append({"text": f"이번 보고서 제출 기한: {ny}. {nm}. {d}.까지 ({due_line['text']})", "src": _src(due_line["src"])})
            up.append(f"제출 기한을 날짜로 계산해 명시({nm}. {d}.)")
    plan += extra_plan if kind == "현황 보고" else []
    for e in rel["events"][:5]:
        if any(similarity(e["title"], x["text"]) >= 0.5 for x in plan):
            continue
        plan.append({"text": f"{e['date'][5:].replace('-', '. ')}. {e['title']}" + (f" ({e['dday']})" if e.get("dday") else ""), "src": e.get("source") or ""})
    if rel["events"]:
        up.append("D-Day 리스크 레이더에서 관련 일정을 찾아 향후 계획에 반영")
    if kind == "결과 보고" and mode == "next":
        plan.append({"text": f"다음 정기검사: {_fill('다음 회차 예정일')}", "src": ""})
        fills += 1
    if plan:
        secs.append({"title": "향후 계획", "bullets": plan})

    # 5. 행정사항: 보고처·협조, 관련 노하우
    adm = []
    for x in lines("admin") + [ln for s in p["sections"] for ln in s["lines"] if re.search(r"제출|통보|보고", ln["text"]) and re.search(r"과|팀|기관|이사장|청", ln["text"])]:
        if not any(a["text"].endswith(x["text"]) for a in adm):
            adm.append({"text": f"보고·통보: {x['text']}", "src": _src(x["src"])})
    adm += extra_admin if kind == "현황 보고" else []
    rows = [r for s in slot.get("admin", []) for r in s["rows"]]
    for c in rel["contacts"][:3]:
        adm.append({"text": f"협의: {c['name']}" + (f" ({', '.join(c.get('phones', [])[:1])})" if c.get("phones") else "") + f" — {', '.join((c.get('topics') or [])[:1])}",
                    "src": _src(c["sources"][0]) if c.get("sources") else ""})
    for k in rel["cards"][:2]:
        adm.append({"text": f"실무 요령: {k['text']}", "src": k.get("source", "")})
    if adm or rows:
        sec = {"title": "행정사항", "bullets": adm[:8]}
        if rows:
            sec["table"] = {"header": rows[0]["cells"], "rows": [r["cells"] for r in rows[1:]]}
        secs.append(sec)
        if rel["contacts"] or rel["cards"]:
            up.append("보고처·협의 상대와 실무 요령(노하우 카드)을 행정사항에 정리")

    # 보고 요지(세 줄)
    summary = []
    head = subject_of(title0)
    if kind == "결과 보고":
        summary.append(f"{cur_label} {head} 결과를 보고드림" if mode == "next" else f"{head} 결과를 보완해 보고드림")
    elif kind == "계획":
        n_new = sum(1 for s in secs for x in s.get("bullets", []) if "다른 자료에서 찾아 추가" in x["text"])
        n_all = next((len(s["bullets"]) for s in secs if s["title"] == "추진 일정"), 0)
        summary.append(f"{cur_label} {head} 계획(일정 {n_all}건" + (f", 다른 자료에서 찾아 보탠 일정 {n_new}건" if n_new else "") + ")을 보고드림")
    else:
        summary.append(f"{head} 현황({base.month}. {base.day}. 기준)을 보고드림")
    key = next((a["text"] for a in acts if a["text"].startswith("[이번 회차 확인]")), None) if acts else None
    if key:
        summary.append("중점 확인: " + re.sub(r"^\[[^\]]+\]\s*| → 결과:.*$", "", key))
    elif meas:
        summary.append(f"검사 항목 {len(meas)}종, 지난 회차 대비 비교")
    elif kind == "현황 보고" and any(s.get("table") for s in secs):
        t = next(s["table"] for s in secs if s.get("table"))
        if t["rows"] and t["rows"][-1][0] == "합계":
            summary.append(f"예산 집행률 {t['rows'][-1][3]}(합계 기준)")
    elif kind == "계획":
        lessons = next((s for s in secs if s["title"].endswith("추진 결과 반영")), None)
        if lessons:
            summary.append(f"전년도 추진 결과 {len(lessons['bullets'])}건 반영")
    if plan:
        first = re.sub(r"\s*\([^()]*\)$", "", plan[0]["text"])
        summary.append(first if kind == "결과 보고" or first.startswith(("이번", "다음")) else f"가장 가까운 일정: {first}")
    elif rel["events"]:
        summary.append(f"다가오는 일정: {rel['events'][0]['title']}")
    summary = [s for s in summary if s][:3]
    up.insert(0, "맨 위에 '보고 요지'(세 줄 요약)를 둠")
    if llm is not None and getattr(llm, "enabled", False):
        summary = _polish(llm, summary) or summary

    # 근거 자료
    srcs = sorted({b["file"] for b in bs} | {x["file"] for x in plan_ref[:1]} |
                  {s["sources"][0]["file"] for k in ("schedule", "issues") for s in rel[k] if s.get("sources")})
    up.append("모든 문장에 근거(원문 위치)를 달고, 자료에 없는 값은 【입력】으로 비워 둠")
    return {"title": title, "org": org, "date": f"{base.year}. {base.month}. {base.day}.", "kind": kind, "mode": mode,
            "original": {"file": file, "title": title0, "sections": [s["name"] for s in p["sections"]], "lines": len(bs)},
            "summary": summary, "sections": secs, "upgrades": _uniq(up), "fills": fills, "attachments": srcs}


def _uniq(xs):
    out = []
    for x in xs:
        if x not in out:
            out.append(x)
    return out


def _polish(llm, summary):
    """모델이 있으면 보고 요지를 개조식으로 다듬는다(새 사실·숫자 추가 금지, 숫자가 바뀌면 버림)."""
    try:
        out = llm.json("공공기관 보고서의 '보고 요지'를 개조식 문장으로 다듬는다. 새 사실이나 숫자를 더하지 말고 같은 뜻으로만 고친다. "
                       "JSON {\"summary\": [문장, ...]}로만 답한다.", "\n".join(summary))
        new = [str(x).strip() for x in out.get("summary", []) if str(x).strip()][:3]
        nums = lambda xs: sorted(re.findall(r"\d+", " ".join(xs)))
        return new if new and nums(new) == nums(summary) else None
    except Exception:
        return None


def to_blocks(r):
    """한글·워드·마크다운 문서 블록(공공기관 보고서 양식)."""
    b = [("title", r["title"]),
         ("p", f"{r['org']} · {r['date']}"),
         ("h2", "보고 요지")] + [("bullet", s) for s in r["summary"]]
    for i, s in enumerate(r["sections"], 1):
        b.append(("h1", f"{i}. {s['title']}"))
        if s.get("table"):
            t = s["table"]
            n = len(t["header"])
            b.append(("table", t["header"], [(row + [""] * n)[:n] for row in t["rows"]], [2] + [1] * (n - 1)))
        for x in s.get("bullets", []):
            b.append(("bullet", x["text"] + (f"  〔근거: {x['src']}〕" if x.get("src") else "")))
    if r["attachments"]:
        b.append(("h2", "붙임"))
        b += [("bullet", f"{i}. {a.rsplit('/', 1)[-1]}") for i, a in enumerate(r["attachments"], 1)]
    b.append(("note", f"※ 【입력】 {r['fills']}곳은 실제 값을 확인해 채워 주세요. 이 보고서는 업무바통 리포트 업그레이더가 "
                      f"전임자 보고서({r['original']['file'].rsplit('/', 1)[-1]})와 인수인계 자료를 근거로 쓴 초안입니다."))
    b.append(("h2", "전임자 보고서 대비 보완 사항"))
    b += [("bullet", u) for u in r["upgrades"]]
    return b
