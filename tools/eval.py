"""정확도 성적표: 모델·인터넷 없이 같은 결과가 나오는 시험을 돌려 검증결과/에 남긴다.

  python tools/eval.py [--note "무엇을 고친 뒤 쟀는지"]  → 검증결과/scorecard.json, 검증결과/정확도_성적표.md

시험 다섯 가지 (기준일 2026-10-15 고정)
  1. 무작위 업무폴더 20개(시드 고정): 공식문서(워드)·개인메모·연락처 엑셀을 만들어 일정·연락처를 심고,
     자료끼리 다른 날짜·바뀐 번호·'아마' 같은 불확실 표현을 몰래 넣는다. 같은 날짜를 두 번 적은 것,
     같은 번호를 다시 적은 것, 확정된 표현은 '경고하면 안 되는 것'으로 함께 넣는다.
     심은 것을 찾는지, 경고해야 할 것만 경고하는지, 모든 항목의 출처가 실제 원문인지 본다.
  2. 후임자 질문: 시연 자료에 대해 개발자가 적은 질문 30개(A 15개는 고칠 때 써도 되는 묶음, B 15개는 고칠 때 보지 않은 묶음)의
     답에 정답 낱말(번호·날짜·이름)이 들어 있는지 본다.
  3. 모델 감시(가짜 모델): 없는 근거 위치, 원문에 없는 날짜를 낸 항목은 버리는지, 맞는 항목은 버리지 않는지,
     질문 답에 지어낸 번호를 넣으면 규칙 답으로 바꾸는지 본다.
  4. 신원·금융 정보 가림: 자료에 심은 주민번호·계좌번호가 인수인계서와 모델에 보낸 글에 남지 않는지,
     업무 연락망의 전화번호는 가리지 않는지 본다.
문항과 정답은 이 파일에 있고, 틀린 문항은 고치지 않고 성적표에 그대로 적는다.
"""
import json
import random
import re
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import engine  # noqa: E402
import qa  # noqa: E402
from core.pii import SENSITIVE, mask  # noqa: E402
from core.sources import LocalFolderSource  # noqa: E402

TODAY = date(2026, 10, 15)
SAMPLE = ROOT / "tools" / "eval_data" / "전임자_업무폴더_평가용"  # 성적표 전용 모의자료(시연 자료와 따로 둠)

TASKS = [("소방시설 종합정밀점검 결과", "소방서"), ("전기설비 정기검사 결과", "전기안전공사"), ("승강기 자체점검 결과", "승강기안전공단"),
         ("저수조 청소 결과", "구청 위생과"), ("가스시설 안전점검 결과", "가스안전공사"), ("어린이 놀이시설 안전점검 결과", "구청 아동청소년과"),
         ("옥외광고물 정비 결과", "구청 도시디자인과"), ("실내 공기질 측정 결과", "보건환경연구원")]
REPORTS = ["전기 사용량 보고서", "시설 사용 실적", "민원 처리 현황", "소모품 사용 대장"]
HEDGE = [("주차장 도색 공사", "착공"), ("외벽 방수 공사", "착공"), ("CCTV 교체", "설치"), ("화장실 리모델링", "착공"), ("방송설비 교체", "설치")]
PEOPLE = [("한지수", "전기안전공사", "과장"), ("오민지", "기관 경영지원팀", "주임"), ("서도현", "(주)푸른설비", "대리"), ("윤가람", "구청 위생과", "주무관"),
          ("배수아", "기관 계약팀", "주임"), ("임채원", "(주)한빛소방", "팀장"), ("권태오", "가스안전공사", "차장"), ("남윤서", "기관 총무팀", "주임")]


def docx(path, title, lines):
    from docx import Document
    d = Document()
    d.add_paragraph(title)
    for x in lines:
        d.add_paragraph(x)
    path.parent.mkdir(parents=True, exist_ok=True)
    d.save(path)


def xlsx(path, rows):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "연락처"
    for r in rows:
        ws.append(r)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def make_world(rnd, root):
    """업무폴더 하나를 만들고 심은 것(정답)을 돌려준다."""
    truth = {"schedule": [], "contacts": [], "flag": [], "noflag": [], "secret": []}
    tasks = rnd.sample(TASKS, 3)
    off = []
    for i, (task, org) in enumerate(tasks, 1):
        m, d = rnd.choice([11, 12]), rnd.randint(2, 27)
        off.append(f"{i}. {task}는 {m}월 {d}일까지 {org}에 제출한다.")
        truth["schedule"].append((task.split()[0], m, d))
    rep, rd = rnd.choice(REPORTS), rnd.randint(3, 20)
    off.append(f"{len(off) + 1}. 매월 {rd}일 {rep}를 경영지원팀에 제출한다.")
    truth["schedule"].append((rep.split()[0], None, rd))
    docx(root / "01_공식문서" / "2026년 시설관리 업무계획.docx", "2026년 시설관리 업무계획 (문서번호: 시설운영팀-101, 2026. 1. 10.)", off)

    pred = rnd.choice(["이서준", "김하린", "박지호", "최유나"])
    memo = [f"인수인계 메모 ({pred}, 개인 정리용)" if rnd.random() < 0.5 else "인수인계 메모 (개인 정리용)"]
    task, m, d = tasks[0][0], *truth["schedule"][0][1:]
    if rnd.random() < 0.5:  # 다른 날짜: 경고해야 함
        d2 = d + rnd.choice([-1, 1]) * rnd.randint(2, 6)
        memo.append(f"- {task} 제출은 {m}월 {d2}일까지로 알고 있음")
        truth["flag"].append(("충돌", f"{m}월 {d2}일", f"{task.split()[0]} 날짜 다름({m}/{d} vs {m}/{d2})"))
        truth["pair"] = {f"{m}월 {d}일", f"{m}월 {d2}일"}
    else:  # 같은 날짜를 다시 적음: 경고하면 안 됨
        memo.append(f"- {task} 제출은 {m}월 {d}일까지")
        truth["noflag"].append(("충돌", f"{m}월 {d}일", f"{task.split()[0]} 같은 날짜를 두 번 적음"))
    x, act = rnd.choice(HEDGE)
    hm = rnd.choice([11, 12])
    if rnd.random() < 0.6:
        memo.append(f"- {x}는 아마 {hm}월 중 {act} 예정")
        truth["flag"].append(("불확실", f"{x}", f"'{x}' 불확실 표현"))
    else:
        memo.append(f"- {x}는 {hm}월 {rnd.randint(2, 27)}일 {act}으로 확정됨")
        truth["noflag"].append(("불확실한 표현", f"{x}", f"'{x}' 확정 표현"))
    people = rnd.sample(PEOPLE, 3)
    rows = [["이름", "소속", "직위", "연락처"]]
    for j, (name, org, title) in enumerate(people):
        ph = f"02-000-{rnd.randint(1000, 9999)}" if j % 2 == 0 else f"010-0000-{rnd.randint(1000, 9999)}"
        rows.append([name, org, title, ph])
        truth["contacts"].append((name, ph))
    name, org, title = people[1]
    if rnd.random() < 0.5:
        new = f"010-0000-{rnd.randint(1000, 9999)}"
        memo.append(f"- {org} {name} {title} 번호 바뀜 {new}")
        truth["flag"].append(("충돌", f"{name}", f"{name} 번호 바뀜"))
    else:
        memo.append(f"- {org} {name} {title} 연락처 {rows[2][3]}")
        truth["noflag"].append(("충돌", f"{name}", f"{name} 같은 번호를 다시 적음"))
    if rnd.random() < 0.6:
        acct = f"{rnd.randint(100, 999)}-{rnd.randint(10, 99)}-{rnd.randint(100000, 999999)}"
        memo.append(f"- 보조금 정산 계좌 {acct} (회계팀 확인)")
        truth["secret"].append(acct)
    if rnd.random() < 0.5:
        rrn = f"{rnd.randint(70, 99)}{rnd.randint(1, 12):02d}{rnd.randint(1, 28):02d}-{rnd.choice([1, 2])}{rnd.randint(100000, 999999)}"
        memo.append(f"- 외부 강사 주민번호 {rrn} 원천징수 서류에 기재해야 함")
        truth["secret"].append(rrn)
    rnd.shuffle(memo[1:])
    (root / "03_개인메모").mkdir(parents=True, exist_ok=True)
    (root / "03_개인메모" / "인수인계 메모.txt").write_text("\n".join(memo) + "\n", encoding="utf-8")
    xlsx(root / "03_개인메모" / "연락처 정리.xlsx", rows)
    return truth


class FakeLLM:
    """가짜 모델: 받은 글을 모두 기록하고, make(system, user)가 돌려주는 값을 낸다."""
    enabled = True
    label = "가짜 모델"

    def __init__(self, make=None):
        self.make = make or (lambda s, u: {})
        self.inputs = []

    def json(self, system, user):
        self.inputs.append(user)
        return self.make(system, user)


def analyze(folder, llm=None):
    docs, _ = LocalFolderSource(folder).load()
    return docs, engine.analyze(docs, "", llm, TODAY)


def blocks_of(docs):
    return {b.id: mask(b.text, SENSITIVE)[0] for d in docs for b in d.blocks}


def cite_errors(draft, blocks):
    n, errs = 0, []
    for sec, items in draft["sections"].items():
        for it in items:
            n += 1
            label = it.get("duty") or it.get("task") or it.get("name") or it.get("title")
            if not it.get("sources"):
                errs.append(f"[{sec}] '{label}' 출처 없음")
                continue
            for s in it["sources"]:
                q = s["quote"][:-3] if s["quote"].endswith("...") else s["quote"]
                if s["block_id"] not in blocks or q not in blocks[s["block_id"]]:
                    errs.append(f"[{sec}] '{str(label)[:30]}' 출처 문장이 원문 위치에 없음")
                    break
    return n, errs


def eval_worlds():
    rnd = random.Random(20261015)
    tmp = Path(tempfile.mkdtemp(prefix="baton-eval-"))
    cite_n, cite_fail = 0, []
    s_ok, s_n, s_fail = 0, 0, []
    c_ok, c_n, c_fail = 0, 0, []
    f_ok, f_n, f_fail = 0, 0, []
    d_ok, d_n, d_fail = 0, 0, []
    p_ok, p_n, p_fail = 0, 0, []
    docs, draft = analyze(SAMPLE)
    n, errs = cite_errors(draft, blocks_of(docs))
    cite_n += n
    cite_fail += ["시연자료 " + e for e in errs]
    for w in range(20):
        root = tmp / f"w{w:02d}"
        truth = make_world(rnd, root)
        spy = FakeLLM()
        docs, draft = analyze(root, spy)
        n, errs = cite_errors(draft, blocks_of(docs))
        cite_n += n
        cite_fail += [f"폴더{w + 1} " + e for e in errs]
        sch = draft["sections"]["schedule"]
        for kw, m, d in truth["schedule"]:
            s_n += 1
            hit = any(kw in it["task"] and it.get("day") == d and (m is None and it.get("recurring") == "매월" or m in it["months"]) for it in sch)
            s_ok += hit
            if not hit:
                s_fail.append(f"폴더{w + 1} '{kw}' {m or '매'}월 {d}일 못 찾음")
        con = draft["sections"]["contacts"]
        for name, ph in truth["contacts"]:
            c_n += 1
            hit = any(c["name"] == name and ph in c["phones"] for c in con)
            c_ok += hit
            if not hit:
                c_fail.append(f"폴더{w + 1} {name} {ph} 못 찾음")
        msgs = [f["type"] + " " + f["message"] for f in draft["flags"]]
        for typ, key, desc in truth["flag"]:
            f_n += 1
            hit = any(m_.startswith(typ) and key in m_ and (typ != "불확실" or "불확실한 표현" in m_) for m_ in msgs)
            f_ok += hit
            if not hit:
                f_fail.append(f"폴더{w + 1} 경고 안 함: {desc}")
        # 서로 다른 일(예: 가스시설 점검 결과 제출 vs 놀이시설 점검 결과 제출)을 시기 충돌로 잘못 보지 않는지
        d_n += 1
        pair = truth.get("pair", set())
        wrong = [m_ for m_ in msgs if m_.startswith("충돌") and "시기가 자료마다" in m_ and not (pair and all(x in m_ for x in pair))]
        d_ok += not wrong
        if wrong:
            d_fail.append(f"폴더{w + 1} 서로 다른 일을 충돌로 봄 → {wrong[0][3:70]}")
        for typ, key, desc in truth["noflag"]:
            d_n += 1
            bad = [m_ for m_ in msgs if (typ in m_) and key in m_]
            d_ok += not bad
            if bad:
                d_fail.append(f"폴더{w + 1} 잘못 경고: {desc} → {bad[0][:60]}")
        if truth["secret"]:
            p_n += 1
            dumped = json.dumps(draft, ensure_ascii=False)
            leak = [s for s in truth["secret"] if s in dumped or any(s in u for u in spy.inputs)]
            kept = all(ph in dumped for _, ph in truth["contacts"])
            if not leak and kept and spy.inputs:
                p_ok += 1
            else:
                p_fail.append(f"폴더{w + 1} " + (f"남음 {leak} " if leak else "") + ("" if kept else "업무 전화번호까지 가림") + ("" if spy.inputs else "모델 입력 없음"))
    return [{"name": "출처가 실제 원문", "passed": cite_n - len(cite_fail), "total": cite_n, "failures": cite_fail,
             "desc": "시연자료와 무작위 폴더 20개에서 뽑은 모든 항목의 출처 문장이 그 파일·위치의 원문에 그대로 있는 비율"},
            {"name": "심은 일정 찾기", "passed": s_ok, "total": s_n, "failures": s_fail,
             "desc": "공식문서에 심은 기한 일정(월·일)과 매월 반복 일정을 찾은 비율"},
            {"name": "심은 연락처 찾기", "passed": c_ok, "total": c_n, "failures": c_fail,
             "desc": "연락처 엑셀에 심은 사람과 번호를 찾은 비율"},
            {"name": "확인 필요 표시", "passed": f_ok, "total": f_n, "failures": f_fail,
             "desc": "자료끼리 다른 날짜, 바뀐 번호, '아마' 같은 불확실 표현을 '확인 필요'로 표시한 비율"},
            {"name": "잘못된 경고 없음", "passed": d_ok, "total": d_n, "failures": d_fail,
             "desc": "같은 날짜·같은 번호를 다시 적은 것, 확정된 표현, 서로 다른 일을 충돌·불확실로 잘못 표시하지 않은 비율"},
            {"name": "신원·금융 정보 가림", "passed": p_ok, "total": p_n, "failures": p_fail,
             "desc": "자료에 심은 주민번호·계좌번호가 인수인계서·모델에 보낸 글에 남지 않고, 업무 전화번호는 그대로인 폴더 비율"}]


# ------------------------------------------------------------ 2. 후임자 질문 (시연 자료)

QA_A = [("정민재 연락처", ["010-0000-9081"]), ("11월에 할 일 알려줘", ["동절기"]), ("수질검사 결과는 어디에 제출해?", ["체육진흥과"]),
        ("냉난방기 공사 누구랑 협의해?", ["오세영"]), ("예산요구서 언제까지 내?", ["10월 16일"]), ("해빙기 점검은 언제야?", ["2월"]),
        ("시설물 보험 갱신 담당자", ["문가은"]), ("계약팀 담당자 누구야?", ["강민호"]), ("승강기 재계약 언제 의뢰해?", ["12월"]),
        ("하반기 정기 안전점검 일정", ["9. 14|9월 14일"]), ("박서연 주무관 전화번호", ["02-000-3345"]), ("공사 안내문 게시 기한", ["10월 24일"]),
        ("민원 답변 기한은?", ["7일"]), ("청소 용역 업체 담당자", ["윤나래"]), ("착공일이 언제야?", ["11월 3일"])]
QA_B = [("오세영 팀장 휴대폰 번호", ["010-0000-7788"]), ("2월에 해야 할 일", ["해빙기"]), ("예산 담당 누구?", ["정하은"]),
        ("수영장 여과기 정비 업체 연락처", ["02-000-9087"]), ("정기 안전점검 끝나고 결과 보고는 언제까지?", ["14일"]), ("12월에 할 일", ["보험"]),
        ("우기 대비 점검 내용", ["배수로"]), ("10월 수질검사 때 주의할 점", ["탁도"]), ("총괄 팀장 연락처", ["02-000-7710"]),
        ("부재 시 대행자는 누구야?", ["박하늘"]), ("소방시설 점검 업체 담당자", ["한지훈"]), ("다음 정기 수질검사 날짜", ["10. 6|10월 6일"]),
        ("안전점검 위탁기관 연락처", ["031-000-2201"]), ("3층 다목적실 사용 중지 언제부터?", ["11월 3일"]), ("입찰공고 시기", ["11월"])]


def sample_draft():
    from dataclasses import asdict
    docs, draft = analyze(SAMPLE)
    blocks = [asdict(b) for d in docs for b in d.blocks]
    for b in blocks:
        b["text"] = mask(b["text"], SENSITIVE)[0]
    return draft, blocks


def eval_qa():
    draft, blocks = sample_draft()
    out = []
    for name, cases, desc in (("후임자 질문 A(개선에 쓴 질문)", QA_A, "답에 정답 낱말(번호·날짜·이름)이 들어 있는 비율"),
                              ("후임자 질문 B(개선에 쓰지 않은 질문)", QA_B, "A와 다른 질문. 앱을 고칠 때 보지 않고 한 번만 잼")):
        ok, fails = 0, []
        for q, want in cases:
            a = qa.answer(draft, blocks, q, None)["answer"]
            miss = [w for w in want if not any(x in a for x in w.split("|"))]
            if miss:
                fails.append(f"'{q}' → '{' / '.join(miss)}' 없음: {a[:80].replace(chr(10), ' ')}")
            else:
                ok += 1
        out.append({"name": name, "passed": ok, "total": len(cases), "failures": fails, "desc": desc})
    return out


# ------------------------------------------------------------ 3. 모델 감시

def eval_model_guard():
    rnd = random.Random(11)
    docs, _ = analyze(SAMPLE)
    real = [(b.id, b.text) for d in docs for b in d.blocks if re.search(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일", b.text)]

    def make(system, user):
        if "인수인계서 작성" not in system:
            return {}
        ids = re.findall(r"^\[([^\]]+)\]", user, re.M)
        cand = [(i, t) for i, t in real if i in ids]
        out = {"duties": [{"duty": "가짜 업무(근거 없음)", "block_ids": ["없는-위치-1"]}], "schedule": [], "contacts": [], "issues": []}
        for i, t in cand[:2]:
            m = re.search(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일", t)
            mo, dy = int(m.group(1)), int(m.group(2))
            fake = next(x for x in range(3, 28) if f"{x}일" not in t and x != dy)
            out["schedule"].append({"months": [mo], "day": fake, "task": t[:40] + " (지어낸 날짜)", "block_ids": [i]})
            out["schedule"].append({"months": [mo], "day": dy, "task": t[:40], "block_ids": [i]})
        return out

    llm = FakeLLM(make)
    draft = engine.analyze(docs, "", llm, TODAY)
    k = [min(2, len([i for i, _ in real if i in re.findall(r"^\[([^\]]+)\]", u, re.M)])) for u in llm.inputs]
    bad_total = len(llm.inputs) + sum(k)  # 호출마다 근거 없는 업무 1건 + 지어낸 날짜 일정 k건
    good_total = sum(k)  # 호출마다 원문과 맞는 일정 k건
    kept_model = [it for sec in draft["sections"].values() for it in sec if it.get("by") == "model"]
    fake_left = [it for it in kept_model if "지어낸 날짜" in (it.get("task") or "") or "가짜 업무" in (it.get("duty") or "")]
    bad_ok = bad_total - len(fake_left)
    good_fail = [f["label"] for f in draft["filtered"] if "지어낸 날짜" not in f["label"] and "가짜 업무" not in f["label"]]
    # 질문 답: 지어낸 번호는 규칙 답으로, 자료 안 숫자만 쓴 답은 그대로
    d2, blocks = sample_draft()
    q_ok, q_fail = 0, []
    qs = [q for q, _ in QA_A + QA_B]
    for q in qs:
        fake = f"010-0000-{rnd.randint(1000, 9999)}"
        ans = f"담당자 번호는 {fake}입니다 [1]"
        r = qa.answer(d2, blocks, q, FakeLLM(lambda s, u, ans=ans: {"answer": ans, "used": [1]}))
        if fake not in r["answer"]:
            q_ok += 1
        else:
            q_fail.append(f"'{q}' 지어낸 번호 {fake}가 답에 남음")
    g_ok, g_fail = 0, []
    for q in qs:
        holder = {}

        def mk(s, u, holder=holder):
            nums = re.findall(r"\d{2,3}-\d{3}-\d{4}", u)
            holder["a"] = (f"자료에 있는 번호는 {nums[0]}입니다 [1]" if nums else "자료에서 찾은 내용을 정리했습니다 [1]")
            return {"answer": holder["a"], "used": [1]}
        r = qa.answer(d2, blocks, q, FakeLLM(mk))
        if holder.get("a") is None or r["answer"] == holder["a"]:
            g_ok += 1
        else:
            g_fail.append(f"'{q}' 바른 모델 답을 버림")
    return [{"name": "모델 항목 걸러내기", "passed": bad_ok, "total": bad_total,
             "failures": [f"남은 항목: {it.get('task') or it.get('duty')}" for it in fake_left],
             "desc": "가짜 모델이 없는 근거 위치를 대거나 원문에 없는 날짜를 넣은 항목을 인수인계서에서 버린 비율"},
            {"name": "맞는 모델 항목은 살림", "passed": good_total - len(good_fail), "total": good_total, "failures": [f"잘못 버림: {x}" for x in good_fail],
             "desc": "근거 위치와 날짜가 원문과 맞는 모델 항목을 버리지 않은 비율"},
            {"name": "질문 답의 지어낸 번호 차단", "passed": q_ok, "total": len(qs), "failures": q_fail,
             "desc": "가짜 모델이 질문 답에 자료에 없는 전화번호를 넣었을 때 규칙 기반 답으로 바꾼 비율"},
            {"name": "바른 질문 답은 살림", "passed": g_ok, "total": len(qs), "failures": g_fail,
             "desc": "자료 안의 번호만 쓴 모델 답을 잘못 버리지 않은 비율"}]


# ------------------------------------------------------------ 4. 가림 규칙 자체

SENS_POS = ["주민번호 850315-2345678 확인", "계좌 110-234-567890 입금", "카드 4321-8765-1234-5678 결제", "외국인등록번호 900101-5123456",
            "여권번호 M12345678 사본", "국민은행 123456-12-345678", "신한 110-12-345678", "주민 9001011234567", "농협 301-1234-5678-91",
            "주민등록 760505 1234567", "카드번호 1111 2222 3333 4444", "우리 1002-345-678901", "여권 S98765432", "계좌 3333-01-1234567",
            "강사료 입금 계좌 352-0123-4567-83"]


def eval_pii():
    ok, fails = 0, []
    for t in SENS_POS:
        out, kinds = mask(t, SENSITIVE)
        if kinds and not re.search(r"\d{4,}", out.replace(" ", "")):
            ok += 1
        else:
            fails.append(f"'{t}' → '{out}'")
    docs, draft = analyze(SAMPLE)
    neg = sorted({b.text for d in docs for b in d.blocks} | {c for it in draft["sections"]["contacts"] for c in it["phones"]})
    n_ok, n_fail = 0, []
    for t in neg:
        _, kinds = mask(t, SENSITIVE)
        if kinds:
            n_fail.append(f"잘못 가림({', '.join(kinds)}): {t[:60]}")
        else:
            n_ok += 1
    return [{"name": "신원·금융 번호 가림", "passed": ok, "total": len(SENS_POS), "failures": fails,
             "desc": "주민번호·외국인등록번호·계좌·카드·여권 번호가 든 문장 15개를 가린 비율"},
            {"name": "업무 정보 오탐 없음", "passed": n_ok, "total": len(neg), "failures": n_fail,
             "desc": "시연 자료의 모든 문장과 연락처 번호(전화번호·날짜·금액·문서번호 포함)를 잘못 가리지 않은 비율"}]


def main():
    import argparse
    ap = argparse.ArgumentParser(description="정확도 성적표 만들기")
    ap.add_argument("--note", default="", help="측정 이력에 남길 한 줄(무엇을 고친 뒤 쟀는지)")
    note = ap.parse_args().note
    t0 = time.time()
    areas = eval_worlds() + eval_qa() + eval_model_guard() + eval_pii()
    out = ROOT / "검증결과"
    out.mkdir(exist_ok=True)
    prev = None
    try:
        prev = json.loads((out / "scorecard.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    summary = ", ".join(f"{a['name']} {a['passed']}/{a['total']}" for a in areas)
    hist = (prev or {}).get("history", [])
    if not hist or hist[-1]["summary"] != summary or note:
        hist.append({"date": date.today().isoformat(), "summary": summary, "note": note})
    sc = {"app": "업무바통", "date": date.today().isoformat(), "command": "python tools/eval.py",
          "author": "개발자 작성(블라인드 아님) · 무작위 폴더(시드 고정)와 A 질문은 앱을 고칠 때 봤고, B 질문만 보지 않음", "seconds": round(time.time() - t0, 1),
          "areas": areas, "history": hist[-10:]}
    (out / "scorecard.json").write_text(json.dumps(sc, ensure_ascii=False, indent=1), encoding="utf-8")
    md = ["# 정확도 성적표 (업무바통)", "", f"- 측정일: {sc['date']} · 재현: `{sc['command']}` (모델·인터넷 불필요, {sc['seconds']}초, 기준일 {TODAY} 고정)",
          f"- 문항: {sc['author']}", "", "| 시험 | 결과 | 무엇을 쟀나 |", "|---|---|---|"]
    for a in areas:
        pct = f" ({a['passed'] / a['total'] * 100:.1f}%)" if a["total"] else ""
        md.append(f"| {a['name']} | {a['passed']}/{a['total']}{pct} | {a['desc']} |")
    md += ["", "## 틀린 문항 (고치지 않고 공개)", ""]
    fails = [(a["name"], f) for a in areas for f in a["failures"]]
    md += [f"- [{n}] {f}" for n, f in fails] or ["- 없음"]
    md += ["", "## 측정 이력", ""] + [f"- {i}차 {h['date']}" + (f" ({h['note']})" if h.get("note") else "") + f": {h['summary']}" for i, h in enumerate(hist[-10:], 1)]
    (out / "정확도_성적표.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md[:8 + len(areas)]))
    print(f"\n틀린 문항 {len(fails)}건 · 결과: {out / '정확도_성적표.md'}")


if __name__ == "__main__":
    main()
