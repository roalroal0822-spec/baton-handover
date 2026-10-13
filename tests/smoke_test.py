"""구동 점검: 서버를 임시 작업 폴더로 띄워 주요 기능을 차례로 호출한다(모델 없이 규칙 기반).

실행: python tests/smoke_test.py
원본(sample_data)은 읽기만 하며, 작업 데이터는 임시 폴더에 쓰고 끝나면 지운다.
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import shutil
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARK = {"baton": "engine.py", "seomu": "assistant.py", "gyujeong": "amend.py"}


def app_dirs():
    out = []
    for name, mark in MARK.items():
        for p in (ROOT / "apps" / name, ROOT):
            if (p / mark).exists():
                out.append((name, p))
                break
    return out


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Client:
    def __init__(self, port):
        self.base = f"http://127.0.0.1:{port}"

    def status(self, method, path, body=None, headers=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def call(self, method, path, body=None, raw=False):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            b = r.read()
            return b if raw else json.loads(b)


def check_baton(c):
    yield "화면 열기", c.call("GET", "/", raw=True)[:15].lower().startswith(b"<!doctype")
    h = c.call("POST", "/api/handovers", {"source": {"kind": "sample"}, "start": "2026-10-12"})
    s = h["draft"]["sections"]
    yield f"인수인계서 초안(업무 {len(s['rnr'])}, 일정 {len(s['schedule'])}, 연락처 {len(s['contacts'])}, 현안 {len(s['issues'])}, 빈틈 {len(h["draft"]["flags"])})", all(len(s[k]) for k in s)
    a = c.call("POST", f"/api/handovers/{h['id']}/ask", {"q": "11월에 할 일 알려줘"})
    yield "후임자 질문", bool(a["answer"])
    for fmt in ("hwpx", "docx", "md"):
        d = c.call("POST", f"/api/handovers/{h['id']}/export/handover?fmt={fmt}", {}, raw=True)
        yield f"인수인계서 내보내기({fmt})", len(d) > 500
    m = c.call("POST", f"/api/handovers/{h['id']}/export/manual?fmt=hwpx", {}, raw=True)
    yield "업무 매뉴얼 내보내기", len(m) > 500
    hid = h["id"]
    # 개인정보: 자료 속 사람 이름은 역할 이름으로 바뀌어 저장된다
    yield "이름 비식별: 전임자·1대 담당자·연락처를 역할로", h["draft"]["predecessor"] == "전임자" and \
        [x["name"] for x in h["draft"]["lineage"]] == ["1대 담당자"] and any(x["name"] == "계약팀 주임" for x in s["contacts"])
    # AI 전임자 분신: 자료에 없으면 지어내지 않고 물어볼 협업 상대를 알려 준다(전임자에게 답을 받는 창구는 없음)
    a1 = c.call("POST", f"/api/handovers/{hid}/ask", {"q": "체육센터 대관료 감면 기준은?"})
    yield "AI 분신: 자료에 없으면 지어내지 않음", a1["found"] is False and not a1["sources"] and "who" in a1
    a1b = c.call("POST", f"/api/handovers/{hid}/ask", {"q": "시설물 보험 갱신 보험사 계약 조건은?"})
    yield "AI 분신: 모르면 물어볼 협업 상대(역할) 안내", a1b["found"] or any("총무팀" in w["label"] for w in a1b.get("who", []))
    a3 = c.call("POST", f"/api/handovers/{hid}/ask", {"q": "수질검사 부적합 나오면 어떻게 해?"})
    yield "AI 분신: 1대 담당자의 질의응답까지 근거로", a3["found"] and "1대 담당자" in a3["answer"]
    yield "소통 창구(전임자 답변 남기기) 삭제", all(c.status(*x) == 404 for x in [
        ("GET", f"/api/handovers/{hid}/threads"), ("POST", f"/api/handovers/{hid}/questions", {"q": "x"}),
        ("POST", f"/api/handovers/{hid}/gaps/ask", {})])
    # D-Day 리스크 레이더: 착임일 기준 붉은 카드(첫 주·이번 달 계약) + 공공 주기
    rd = c.call("GET", f"/api/handovers/{hid}/radar?start=2026-10-12")
    wk, ct = rd["top"]["week"], rd["top"]["contract"]
    pub = [e["title"] for e in rd["public"]]
    yield (f"리스크 레이더(첫 주 {len(wk)}, 계약 {len(ct)}, 공공 주기 {len(pub)})",
           any("예산요구서" in e["title"] for e in wk) and any("승강기" in e["title"] for e in ct)
           and any("청소" in e["title"] for e in ct) and any("행정사무감사(11월" in t for t in pub)
           and any(cd["id"] == "complaint" for cd in rd["conditional"]) and all(e["level"] == "red" for e in wk + ct))
    rd2 = c.call("POST", f"/api/handovers/{hid}/radar", {"start": "2026-12-20", "audit_month": 6, "gov_level": "시·도"})
    yield "리스크 레이더: 착임일·감사 달 바꾸면 다시 계산", rd2["start"] == "2026-12-20" and rd2["audit_month"] == 6 and \
        any("계약 만료" in e["title"] for e in rd2["top"]["contract"]) and c.status("POST", f"/api/handovers/{hid}/radar", {"start": "2026-99-01"}) == 400
    c.call("POST", f"/api/handovers/{hid}/radar", {"start": "2026-10-12"})
    # 휴먼 네트워크 맵·노하우 카드
    net = c.call("GET", f"/api/handovers/{hid}/network")
    groups = {n["group"] for n in net["nodes"]}
    lab = {n["id"]: n["label"] for n in net["nodes"]}
    pre = [k for k in net["cards"] if k["kind"] == "사전 협의" and "경영지원팀" in lab.get(k["target"], "")]
    yield (f"휴먼 네트워크(상대 {len(net['nodes'])}, 노하우 {len(net['cards'])})",
           {"team", "dept", "gov", "vendor", "civil"} <= groups and len(net["cards"]) >= 5 and pre
           and any("의회" in n["label"] for n in net["nodes"]) and any("그린공조" in lab.get(k["target"], "") for k in net["cards"]))
    target = next(n["id"] for n in net["nodes"] if "총무팀" in n["label"])
    n2 = c.call("POST", f"/api/handovers/{hid}/knowhow", {"target": target, "kind": "사전 협의", "text": "보험 갱신은 문가은 주임에게 11월 첫 주에 미리 연락"})
    mine = [k for k in n2["cards"] if not k["auto"]]
    yield "노하우 카드 직접 쓰기(이름은 역할로 바뀜)", len(mine) == 1 and "문가은" not in mine[0]["text"] and mine[0]["target"] == target
    n3 = c.call("POST", f"/api/handovers/{hid}/knowhow/{pre[0]['id']}/delete", {})
    yield "노하우 카드 숨기기", all(k["id"] != pre[0]["id"] for k in n3["cards"]) and \
        c.status("POST", f"/api/handovers/{hid}/knowhow", {"text": " "}) == 400
    # 지식 릴레이: 1대(모의 바통) → 2대 → 3대 (계보도 사람 이름 대신 세대 이름)
    yield "지식 릴레이: 모의 바통 파일 이어받기", any(it.get("carried") for it in s["schedule"])
    b = c.call("POST", f"/api/handovers/{hid}/baton", {}, raw=True)
    bj = json.loads(b)
    yield (f"바통 파일 저장({len(bj['lineage'])}대, 질의응답 {len(bj['interview'])}건, 노하우 포함)",
           [x["name"] for x in bj["lineage"]] == ["1대 담당자", "2대 담당자"] and len(bj["interview"]) == len(h["draft"]["carried_interview"])
           and any(n["text"].startswith("[노하우") for n in bj["notes"]))
    import base64
    h3 = c.call("POST", "/api/handovers", {"source": {"kind": "upload", "files": [
        {"path": "인계/업무바통_2대.baton", "b64": base64.b64encode(b).decode()},
        {"path": "인계/메모.txt", "b64": base64.b64encode("2027년 3월 16일 상반기 정기 안전점검 착수".encode()).decode()}]}})
    d3 = h3["draft"]
    yield "3대 인수인계: 계보·항목·질의응답 이어받기", [x["name"] for x in d3["lineage"]] == ["1대 담당자", "2대 담당자"] and \
        len(d3["carried_interview"]) == len(h["draft"]["carried_interview"]) and sum(1 for k in d3["sections"] for it in d3["sections"][k] if it.get("carried")) > 10
    # 적대적 검증에서 나온 문제 재발 방지
    nf = c.call("POST", f"/api/handovers/{hid}/ask", {"q": "수영장 비밀번호 뭐야?"})
    yield "AI 분신: 겹치는 낱말만 있는 엉뚱한 질문은 '모름'", nf["found"] is False
    cf = c.call("POST", f"/api/handovers/{hid}/ask", {"q": "착공일 언제야?"})
    yield "AI 분신: 자료끼리 다른 내용은 답에 함께 알림", "⚠" in cf["answer"]
    yield "잘못된 요청은 500 대신 400", all(c.status(*x) == 400 for x in [
        ("POST", f"/api/handovers/{hid}/manual", {"start": "2026-13-01"}), ("POST", f"/api/handovers/{hid}/ask", [1]),
        ("POST", f"/api/handovers/{hid}/knowhow", [1])])
    # 심사위원 관점 점검에서 나온 문제 재발 방지
    sid = next(x["id"] for x in c.call("GET", f"/api/handovers/{hid}")["draft"]["sections"]["schedule"])
    bad_day = [c.status("POST", f"/api/handovers/{hid}/item", {"section": "schedule", "item": {"id": sid, "day": d}}) for d in (-5, 1.5, "x", 40)]
    still = [c.status("GET", f"/api/handovers/{hid}/radar"), c.status("POST", f"/api/handovers/{hid}/export/handover?fmt=md", {}),
             c.status("POST", f"/api/handovers/{hid}/calendar", {"start": "2026-10-12"})]
    yield "잘못된 '일'·'월' 값은 400, 다른 기능은 그대로 동작", bad_day == [400] * 4 and still == [200] * 3 and \
        c.status("POST", f"/api/handovers/{hid}/item", {"section": "rnr", "item": {"detail": "담당 업무 없이"}}) == 400 and \
        c.status("POST", f"/api/handovers/{hid}/item", {"section": "schedule", "item": {"task": "t", "months": ["x"]}}) == 400
    bad_up = [c.status("POST", "/api/handovers", b) for b in (
        {"source": {"kind": "upload", "files": [{"path": "a.txt", "b64": "@@@"}]}}, {"source": {"kind": "upload", "files": "abc"}},
        {"source": {"kind": "upload", "files": [{"b64": "YQ=="}]}}, {"source": "upload"},
        {"source": {"kind": "docsystem", "base_url": "http://127.0.0.1:9/none"}})]
    yield "잘못된 업로드·문서시스템 주소는 400", bad_up == [400] * 5
    c.call("POST", f"/api/handovers/{hid}/ask", {"q": "김도윤 연락처 알려줘"})
    k = c.call("POST", f"/api/handovers/{hid}/knowhow", {"target": "오세영 주무관", "text": "마감 전 재확인"})
    hist = json.dumps(c.call("GET", f"/api/handovers/{hid}/history"), ensure_ascii=False)
    yield "개인정보: 질문 기록·노하우 대상에도 이름 없음", "김도윤" not in hist and \
        all(x["target"] == "" for x in k["cards"] if not x["auto"] and x["text"] == "마감 전 재확인")
    big = ("2026년 11월 10일까지 청소 용역 입찰공고 필요, 계약팀과 협의 중\n" * 8000).encode()
    t0 = time.time()
    bh = c.call("POST", "/api/handovers", {"source": {"kind": "upload", "files": [{"path": "큰메모.txt", "b64": base64.b64encode(big).decode()}]}})
    yield f"아주 큰 텍스트 파일도 멈추지 않음({time.time() - t0:.1f}초)", time.time() - t0 < 20 and any("앞부분" in x["reason"] for x in bh["skipped"])
    yield "DNS 리바인딩 차단(도메인 Host 거부)", c.status("GET", "/api/handovers", headers={"Host": "evil.example"}) == 403
    # 인수인계 순서도: 단계 상태가 실제 진행에 맞게 바뀌는지, 문서에 들어가는지
    f0 = c.call("GET", f"/api/handovers/{hid}/flow")
    c.call("POST", f"/api/handovers/{hid}/review", {"confirmed": True, "comment": "계약팀 강민호 주임에게 먼저 인사하세요"})
    f1 = c.call("GET", f"/api/handovers/{hid}/flow")
    st = lambda f, k: next(x["status"] for x in f["steps"] if x["key"] == k)
    md2 = c.call("POST", f"/api/handovers/{hid}/export/handover?fmt=md", {}, raw=True).decode("utf-8")
    man = c.call("POST", f"/api/handovers/{hid}/export/manual?fmt=md", {"start": "2026-10-12"}, raw=True).decode("utf-8")
    yield (f"인수인계 순서도({f1['done']}/{f1['total']}단계, 지금: {f1['current']})",
           st(f0, "confirm") != "완료" and st(f1, "confirm") == "완료" and st(f1, "baton") == "완료" and st(f1, "ask") == "완료"
           and "인수인계 순서도" in md2 and "착임 후 순서도" in man)
    yield "인수인계서·매뉴얼에 리스크 레이더·네트워크·노하우", all(k in md2 for k in ("D-Day 리스크 레이더", "부임 첫 주에 해야 할 일", "협업 네트워크", "노하우 카드")) \
        and "감사 지적 우려 계약" in man
    # 이름이 어디에도 남지 않는지: 내려받은 문서·바통·답·네트워크·저장 데이터
    names = ["김도윤", "최선우", "정민재", "박서연", "강민호", "문가은", "오세영", "정하은", "최현우", "이정훈", "박정우"]
    outs = md2 + man + b.decode("utf-8") + json.dumps(c.call("GET", f"/api/handovers/{hid}/network"), ensure_ascii=False) + \
        json.dumps(c.call("GET", f"/api/handovers/{hid}"), ensure_ascii=False) + a3["answer"]
    leaked = sorted({n for n in names if n in outs})
    yield f"개인정보: 사람 이름 노출 0건{' (노출: ' + ','.join(leaked) + ')' if leaked else ''}", not leaked and "010-0000-9081" not in outs
    # 뒤죽박죽 최소자료: 사진·스캔본·ZIP·중복·빈 파일·옛 형식이 섞여도 정리해서 읽고, 모자란 영역은 면담 질문지로
    mh = c.call("POST", "/api/handovers", {"source": {"kind": "sample_messy"}})
    reasons = " ".join(x["reason"] for x in mh["skipped"])
    files = [x["relpath"] for x in mh["draft"]["docs"]]
    yield (f"뒤죽박죽 자료 정리(읽음 {len(files)}, 건너뜀 {len(mh['skipped'])})",
           all(k in reasons for k in ("같은 내용", "빈 파일", ".xls", "heic", "임시·시스템"))
           and any(f.startswith("옛날자료.zip/") for f in files) and any(f.endswith(".jpg") for f in files))
    mnames = ["박정우", "이서진", "한서윤", "오민석", "윤가람", "이수민"]
    mdump = json.dumps(c.call("GET", f"/api/handovers/{mh['id']}"), ensure_ascii=False) + \
        c.call("POST", f"/api/handovers/{mh['id']}/export/handover?fmt=md", {}, raw=True).decode("utf-8")
    mleak = [n for n in mnames if n in mdump]
    yield f"개인정보: 스캔본·사진 글자 속 이름도 노출 0건{' (노출: ' + ','.join(mleak) + ')' if mleak else ''}", not mleak
    # 포토 아카이브: 사진만 따로 모으고 키워드로 관련 사진 찾기, AI 분신 답에도 관련 사진
    mid = mh["id"]
    allp = c.call("GET", f"/api/handovers/{mid}/photos")
    edu = c.call("GET", f"/api/handovers/{mid}/photos?q=" + urllib.parse.quote("직무중심교육"))
    sep = c.call("GET", f"/api/handovers/{mid}/photos?q=" + urllib.parse.quote("9월"))
    img = c.call("GET", edu["photos"][0]["url"], raw=True) if edu["photos"] else b""
    ap = c.call("POST", f"/api/handovers/{mid}/ask", {"q": "직무중심교육 결과 알려줘"})
    yield (f"포토 아카이브(사진 {allp['total']}장, '직무중심교육' {len(edu['photos'])}장)",
           allp["total"] == 3 and len(edu["photos"]) == 2 and all("직무중심교육" in p_["name"] for p_ in edu["photos"])
           and any(p_["taken"].startswith("2026-09") for p_ in sep["photos"]) and img[:2] == b"\xff\xd8"
           and len(ap.get("photos", [])) == 2 and c.status("GET", f"/api/handovers/{mid}/photos/zzz/image") == 404)
    gone = edu["photos"][0]["id"]
    c.call("POST", f"/api/handovers/{mid}/photos/{gone}/delete", {})
    yield "포토 아카이브: 사진 지우기", c.call("GET", f"/api/handovers/{mid}/photos")["total"] == 2 and \
        c.status("GET", f"/api/handovers/{mid}/photos/{gone}/image") == 404
    cov = {c_["name"]: c_["level"] for c_ in mh["draft"]["coverage"]}
    yield (f"충실도 점검({sum(v == '충분' for v in cov.values())}/7 충분)",
           len(cov) == 7 and mh["draft"]["predecessor"] == "전임자" and any(v != "충분" for v in cov.values()))
    # 표준 인수인계서(필수 5대 영역): 자료로 채우기 → 저장 → 한글 내보내기, 법령 API는 인증값 없으면 안내
    fm = c.call("GET", f"/api/handovers/{mh['id']}/form")["form"]
    sv = c.call("POST", f"/api/handovers/{mh['id']}/form", {"form": fm})
    hw = c.call("POST", f"/api/handovers/{mh['id']}/export/form?fmt=hwpx", {}, raw=True)
    import io, zipfile
    sec = zipfile.ZipFile(io.BytesIO(hw)).read("Contents/section0.xml").decode("utf-8")
    yield (f"표준 인수인계서(근거 법령 후보 {len(fm['a1']['laws'])}, 예산 {len(fm['a3']['budget'])}, 미결 {len(fm['a4']['issues'])})",
           sv["saved"] and len(fm["a1"]["laws"]) >= 2 and len(fm["a3"]["budget"]) >= 3 and all(f"{n}." in sec for n in range(1, 6)) and "(인)" in sec)
    yield "법령 검색: 인증값(OC) 없으면 신청 안내", c.status("GET", "/api/law/search?q=x") == 400 and \
        c.status("POST", "/api/law/config", {"oc": "a@b.kr"}) == 400
    # 내 일정으로 내보내기(.ics)
    ics = c.call("POST", f"/api/handovers/{hid}/calendar", {"start": "2026-10-12"}, raw=True).decode("utf-8")
    yield f"일정 내보내기(.ics, 일정 {ics.count('BEGIN:VEVENT')}건)", ics.startswith("BEGIN:VCALENDAR") and \
        "RRULE:FREQ=MONTHLY;BYMONTHDAY=10" in ics and "FREQ=YEARLY" in ics and \
        all(len(x.encode("utf-8")) <= 75 for x in ics.split("\r\n"))


def check_seomu(c):
    yield "화면 열기", c.call("GET", "/", raw=True)[:15].lower().startswith(b"<!doctype")
    p = c.call("GET", "/api/procedures")
    yield f"업무별 절차 카드 {len(p['cards'])}개", len(p["cards"]) >= 4
    r = c.call("POST", "/api/chat", {"text": "어제 부산에서 1박 2일 출장 다녀왔어요. 정산 어떻게 해요?"})
    yield f"상황 안내({r.get('card', {}).get('name')})", r.get("type") == "procedure"
    s1 = c.call("POST", "/api/simulate", {"text": "출장비 정산을 10일 뒤에 제출하면 어떻게 돼?"})
    yield f"업무 시뮬레이터({s1['label']})", s1["verdict"] == "bad" and bool(s1["findings"][0]["basis"])
    s2 = c.call("POST", "/api/simulate", {"text": "점심 메뉴 추천해줘"})
    yield f"업무 시뮬레이터 근거 없음({s2['label']})", s2["verdict"] not in ("ok", "bad")
    pc = c.call("POST", "/api/precheck", {"kind": "goods", "fields": {"item": "노트북", "amount": "2500000", "quotes": 1, "purchased": True}})
    yield f"AI 사전점검({pc['label']})", pc["counts"]["bad"] >= 2
    d = c.call("POST", "/api/check/export?fmt=hwpx", {**pc, "title": "AI 사전점검", "question": "노트북"}, raw=True)
    yield "점검 결과 내보내기", len(d) > 500
    rv = c.call("GET", "/api/revision/sample")
    yield "규정 개정 시연본", bool(rv)


def check_gyujeong(c):
    yield "화면 열기", c.call("GET", "/", raw=True)[:15].lower().startswith(b"<!doctype")
    lib = c.call("GET", "/api/library")
    yield f"규정집 {len(lib['entries'])}건", len(lib["entries"]) >= 5
    r = c.call("POST", "/api/agent", {"text": "체육시설 운영 규정에서 대관 사용료 납부 기한을 7일에서 5일로 줄이고, 제8조 다음에 사용허가 취소에 관한 조문을 신설해줘"})
    yield f"개정(작업 {len(r['plan']['ops'])}개, 대비표 {len(r['preview']['compare'])}행)", len(r["plan"]["ops"]) == 2
    cid = r["case"]["id"]
    for d in r["case"]["available_docs"]:
        b = c.call("POST", f"/api/cases/{cid}/doc/{d['kind']}?fmt=hwpx", {}, raw=True)
        yield f"문서: {d['name']}", len(b) > 500
    z = c.call("POST", f"/api/cases/{cid}/bundle", {}, raw=True)
    yield "문서 세트 ZIP", z[:2] == b"PK"
    e = c.call("POST", "/api/agent", {"text": "체육시설 정기대관 운영 지침을 새로 만들려고 해. 조례 제4조제2항 위임이고, 목적은 정기대관 신청, 대상자 선정, 사용료 납부와 취소 기준을 정하기 위해"})
    yield f"제정 초안({len(e['draft']['articles'])}개 조)", len(e["draft"]["articles"]) >= 6
    upper = [x for x in lib["entries"] if x["layer"] == "upper"][0]
    im = c.call("POST", "/api/impact", {"law_id": upper["id"], "sample": True})
    yield f"상위법 영향 분석(후보 {len(im['candidates'])}곳)", len(im["candidates"]) >= 3
    ln = c.call("GET", "/api/lint")
    yield f"전체 점검({ln['summary']})", ln["summary"].get("오류", 0) >= 1


CHECKS = {"baton": check_baton, "seomu": check_seomu, "gyujeong": check_gyujeong}


def main():
    total = failed = 0
    for name, d in app_dirs():
        tmp = tempfile.mkdtemp(prefix=f"smoke-{name}-")
        port = free_port()
        env = dict(os.environ, APP_DATA_DIR=tmp, LLM_PROVIDER="none")
        proc = subprocess.Popen([sys.executable, "run.py", "--no-browser", "--port", str(port)], cwd=d, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            c = Client(port)
            for _ in range(60):
                try:
                    c.call("GET", "/api/info")
                    break
                except Exception:
                    time.sleep(0.3)
            print(f"\n[{name}] http://127.0.0.1:{port}")
            for label, ok in CHECKS[name](c):
                total += 1
                failed += 0 if ok else 1
                print(f"  {'통과' if ok else '실패'}  {label}")
        except Exception as e:
            total += 1
            failed += 1
            print(f"  실패  {type(e).__name__}: {e}")
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
            shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n결과: {total - failed}/{total} 통과")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
