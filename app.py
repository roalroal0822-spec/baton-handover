"""업무바통(인수인계) 웹 서버."""
import base64
import json
import os
import re
import time
import uuid
from dataclasses import asdict
from datetime import date
from pathlib import Path

from core import lawapi, loaders, ocr
from core.config import Config
from core.export import FORMATS, export
from core.llm import LLM
from core.server import App, HttpError, Response, file_response
from core.sources import DocSystemSource, LocalFolderSource, UploadSource
from core.store import Store
from core import webcommon

import anon
import engine
import network
import photos as photo_mod
import qa
import radar
import report

VERSION = "1.1.0"
HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_data" / "전임자_업무폴더_시설운영"
SAMPLE_MESSY = HERE / "sample_data" / "전임자_뒤죽박죽_최소자료"  # 최소한의 자료를 아무렇게나 모은 경우(사진·스캔본·ZIP·중복 포함)

cfg = Config(HERE, {"app_title": "업무바통", "port": 8101})
llm = LLM(cfg["llm"])
loaders.OCR = ocr.make(llm)  # 사진·스캔본 글자 읽기(Tesseract → Windows OCR → 비전 모델 순)
store = Store(cfg.data_dir)
app = App(HERE / "static")
webcommon.register(app, cfg, llm, "업무바통", VERSION)


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def load_h(hid):
    h = store.read(f"handovers/{hid}/handover.json")
    if not h:
        raise HttpError(404, "인수인계 건을 찾을 수 없습니다")
    return h


def save_h(h):
    h["updated"] = _now()
    store.write(f"handovers/{h['id']}/handover.json", h)


def clean(h, value):
    """사람이 새로 적은 글에서 이름을 역할 이름으로 바꾼다(인수인계 건마다 저장한 이름 해시 표 사용)."""
    return anon.deep(value, lambda s: anon.scrub(s, h.get("privacy")))


def log(hid, action, detail):
    store.append_log(f"handovers/{hid}/history.jsonl", {"action": action, "detail": detail})


# ---------------------------------------------------------------- 인수인계 건 만들기

@app.get("/api/handovers")
def list_handovers(req):
    out = []
    for hid in store.list("handovers"):
        h = store.read(f"handovers/{hid}/handover.json")
        if h:
            out.append({k: h.get(k) for k in ("id", "title", "created", "updated", "successor")}
                       | {"predecessor": h["draft"].get("predecessor"), "confirmed": h.get("review", {}).get("confirmed", False),
                          "generation": len(h["draft"].get("lineage") or []) + 1,
                          "asked": h.get("asked", 0)})
    out.sort(key=lambda x: x["created"], reverse=True)
    return {"handovers": out}


@app.post("/api/handovers")
def create_handover(req):
    body = req.json
    src = body.get("source") or {}
    if not isinstance(src, dict):
        raise HttpError(400, "자료 넣는 방법을 선택해 주세요")
    hid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    try:
        return _create(body, src, hid)
    finally:
        # 올린 원본 사본은 성공·실패와 관계없이 읽은 뒤 지운다(이름이 든 원문을 남기지 않음)
        store.remove_tree(f"handovers/{hid}/inbox")
        if not store.path(f"handovers/{hid}/handover.json").exists():
            store.remove_tree(f"handovers/{hid}")  # 실패한 건은 흔적을 남기지 않는다


UPLOAD_MAX = 500 * 1024 * 1024  # 한 번에 올리는 자료 상한(500MB)


def _create(body, src, hid):
    kind = src.get("kind")
    if kind == "upload":
        files = src.get("files")
        if not files:
            raise HttpError(400, "올린 파일이 없습니다")
        if not isinstance(files, list) or not all(isinstance(f, dict) and isinstance(f.get("path"), str) and f["path"].strip()
                                                   and isinstance(f.get("b64"), str) for f in files):
            raise HttpError(400, "올린 파일 형식이 올바르지 않습니다")
        if sum(len(f["b64"]) for f in files) * 3 // 4 > UPLOAD_MAX:
            raise HttpError(400, "한 번에 올릴 수 있는 자료는 500MB까지입니다. 나눠서 올려 주세요")
        try:
            source = UploadSource(files, store.path(f"handovers/{hid}/inbox"))
        except (ValueError, TypeError) as e:
            raise HttpError(400, f"올린 파일을 읽을 수 없습니다({str(e)[:80]})")
    elif kind == "local":
        try:
            source = LocalFolderSource(src.get("path", ""))
        except FileNotFoundError as e:
            raise HttpError(400, str(e))
    elif kind == "sample":
        source = LocalFolderSource(SAMPLE)
    elif kind == "sample_messy":
        source = LocalFolderSource(SAMPLE_MESSY)
    elif kind == "docsystem":
        base = src.get("base_url") or f"http://127.0.0.1:{cfg['port']}/mock-docsystem"
        source = DocSystemSource(str(base), str(src.get("owner") or ""))
    else:
        raise HttpError(400, "자료 넣는 방법을 선택해 주세요")
    try:
        docs, skipped = source.load()
    except HttpError:
        raise
    except Exception as e:
        if kind == "docsystem":
            raise HttpError(400, f"문서시스템에 연결하지 못했습니다. 주소와 소유자 ID를 확인해 주세요({type(e).__name__})")
        raise
    if not [d for d in docs if not d.error]:
        raise HttpError(400, "읽을 수 있는 자료가 없습니다. 한글·PDF·엑셀·워드·메일·텍스트 파일을 넣어 주세요.")
    draft = engine.analyze(docs, str(body.get("predecessor") or "").strip(), llm)
    blocks = [asdict(b) for d in docs for b in d.blocks]
    # 개인정보: 저장하기 전에 사람 이름을 역할 이름(전임자·후임자·소속 직위)으로 바꾼다
    names = anon.build_aliases(draft, blocks, str(body.get("successor") or ""))
    fix = anon.replacer(names)
    draft, blocks, skipped = anon.deep(draft, fix), anon.deep(blocks, fix), anon.deep(skipped, fix)
    team = next((c.get("org") for c in draft["sections"]["contacts"] if re.search(r"(팀|과|실)$", c.get("org") or "")), "")
    privacy = anon.hashed(names)
    title = anon.scrub(fix(str(body.get("title") or "").strip()[:100]), privacy) or f"{team + ' ' if team else ''}업무 인수인계".strip()
    h = {"id": hid, "title": title, "successor": "후임자",
         "created": _now(), "source": anon.deep(source.describe(), fix),
         "skipped": skipped, "draft": draft, "review": {"confirmed": False, "comments": []},
         "privacy": privacy}
    try:  # 후임자 착임(발령)일: D-Day 리스크 레이더의 기준
        h["radar"] = {"start": date.fromisoformat(str(body.get("start"))).isoformat()} if body.get("start") else {}
    except ValueError:
        raise HttpError(400, "착임일 형식이 올바르지 않습니다(예: 2026-10-12)")
    # 포토 아카이브: 사진만 따로 모아 줄인 사본과 찾을 단서(파일·폴더 이름, 사진 속 글자, AI 설명, 찍은 날짜)를 둔다
    def _save_photo(pid, jpeg):
        out = store.path(f"handovers/{hid}/photos/{pid}.jpg")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(jpeg)
    h["photos"] = [anon.deep(p, lambda t: anon.scrub(t, privacy)) for p in photo_mod.build(docs, fix, _save_photo, llm)]
    store.write(f"handovers/{hid}/blocks.json", blocks)
    save_h(h)
    log(hid, "초안 생성", f"{len(docs)}개 자료, {draft['engine']}")
    return h


@app.get("/api/handovers/<hid>")
def get_handover(req):
    return load_h(req.params["hid"])


@app.delete("/api/handovers/<hid>")
def delete_handover(req):
    hid = req.params["hid"]
    load_h(hid)
    store.remove_tree(f"handovers/{hid}")  # 작업 사본만 지움(원본 폴더는 건드리지 않음)
    return {"ok": True}


@app.get("/api/handovers/<hid>/flow")
def handover_flow(req):
    """인수인계 순서도: 단계별 상태와 지금 할 일."""
    return report.flow_steps(load_h(req.params["hid"]))


@app.get("/api/handovers/<hid>/context")
def block_context(req):
    blocks = store.read(f"handovers/{req.params['hid']}/blocks.json", [])
    bid = req.query.get("block", "")
    for i, b in enumerate(blocks):
        if b["id"] == bid:
            same = [x for x in blocks[max(0, i - 3): i + 4] if x["doc_id"] == b["doc_id"]]
            return {"blocks": same}
    raise HttpError(404, "원문 위치를 찾을 수 없습니다")


@app.get("/api/handovers/<hid>/document")
def document_blocks(req):
    blocks = store.read(f"handovers/{req.params['hid']}/blocks.json", [])
    f = req.query.get("file", "")
    return {"blocks": [b for b in blocks if b["file"] == f]}


# ---------------------------------------------------------------- 전임자 검토·수정

SECTIONS = ("rnr", "schedule", "contacts", "issues")
# 구분마다 받을 칸과 형식: 글자(str)·목록(list)·월(months)·일(day)
FIELDS = {
    "rnr": {"duty": "str", "detail": "str"},
    "schedule": {"months": "months", "day": "day", "task": "str", "related": "str", "recurring": "rec"},
    "contacts": {"name": "str", "org": "str", "title": "str", "phones": "list", "emails": "list", "topics": "list"},
    "issues": {"title": "str", "state": "str", "next_action": "str", "due": "str"},
}
MAIN = {"rnr": "duty", "schedule": "task", "contacts": "name", "issues": "title"}


def sanitize_item(sec, raw, new):
    """화면·API에서 온 항목을 정해진 칸과 형식으로만 받는다(잘못된 값이 저장돼 다른 기능까지 망가지지 않게)."""
    if not isinstance(raw, dict):
        raise HttpError(400, "항목 형식이 올바르지 않습니다")
    out = {"id": str(raw.get("id") or "")[:40]} if raw.get("id") else {}
    for k, kind in FIELDS[sec].items():
        if k not in raw:
            continue
        v = raw[k]
        if kind == "str":
            out[k] = "" if v is None else str(v).strip()[:2000]
        elif kind == "list":
            vs = v if isinstance(v, list) else ([v] if v not in (None, "") else [])
            out[k] = [str(x).strip()[:300] for x in vs if str(x).strip()][:20]
        elif kind == "rec":
            out[k] = v if v in ("", "매월", "매년", "매분기") else ""
        elif kind == "day":
            if v in (None, ""):
                out[k] = None
                continue
            try:
                f = float(v)
            except (TypeError, ValueError):
                raise HttpError(400, "'일'은 1~31 사이 숫자로 적어 주세요")
            if f != int(f) or not 1 <= int(f) <= 31:
                raise HttpError(400, "'일'은 1~31 사이 숫자로 적어 주세요")
            out[k] = int(f)
        elif kind == "months":
            vs = v if isinstance(v, list) else []
            ms = []
            for m in vs:
                try:
                    m = int(float(m))
                except (TypeError, ValueError):
                    raise HttpError(400, "'월'은 1~12 사이 숫자로 적어 주세요(예: 3 또는 3, 9 또는 매월)")
                if not 1 <= m <= 12:
                    raise HttpError(400, "'월'은 1~12 사이 숫자로 적어 주세요(예: 3 또는 3, 9 또는 매월)")
                ms.append(m)
            out[k] = sorted(set(ms))
    if new:
        for k, kind in FIELDS[sec].items():
            out.setdefault(k, [] if kind in ("list", "months") else (None if kind == "day" else ""))
        if not out[MAIN[sec]]:
            raise HttpError(400, "내용을 입력해 주세요")
    return out


@app.post("/api/handovers/<hid>/item")
def upsert_item(req):
    h = load_h(req.params["hid"])
    body = req.json
    sec = body.get("section")
    if sec not in SECTIONS:
        raise HttpError(400, "잘못된 구분")
    raw = body.get("item") or {}
    items = h["draft"]["sections"][sec]
    exists = isinstance(raw, dict) and any(it["id"] == raw.get("id") for it in items)
    item = clean(h, sanitize_item(sec, raw, new=not exists))
    for i, it in enumerate(items):
        if it["id"] == item.get("id"):
            before = {k: it.get(k) for k in item if k not in ("status",)}
            it.update(item)
            if body.get("confirm"):
                it["status"] = "확인"
            elif any(before.get(k) != item.get(k) for k in before):
                it["status"] = "수정"
            it["edited_at"] = _now()
            save_h(h)
            log(h["id"], f"{sec} {'확인' if body.get('confirm') else '수정'}", item.get("task") or item.get("title") or item.get("name") or item.get("duty") or "")
            return it
    # 새 항목 (전임자가 직접 추가)
    item.update(id=engine.new_id(sec[:2]), status="추가", sources=[], origin=["전임자 입력"], edited_at=_now())
    items.append(item)
    save_h(h)
    log(h["id"], f"{sec} 추가", item.get("task") or item.get("title") or item.get("name") or item.get("duty") or "")
    return item


@app.post("/api/handovers/<hid>/item/delete")
def delete_item(req):
    h = load_h(req.params["hid"])
    body = req.json
    sec = body.get("section")
    for it in h["draft"]["sections"].get(sec if sec in SECTIONS else "", []):
        if it["id"] == body.get("id"):
            it["status"] = "삭제" if it.get("status") != "삭제" else "초안"
            save_h(h)
            log(h["id"], "항목 삭제 표시" if it["status"] == "삭제" else "삭제 취소", it["id"])
            return it
    raise HttpError(404, "항목이 없습니다")


@app.post("/api/handovers/<hid>/flag")
def resolve_flag(req):
    h = load_h(req.params["hid"])
    body = req.json
    for f in h["draft"]["flags"]:
        if f["id"] == body.get("id"):
            f["resolved"] = bool(body.get("resolved", True))
            f["note"] = clean(h, str(body.get("note") or ""))[:1000]
            save_h(h)
            log(h["id"], "확인필요 처리", f"{f['message']} → {f['note']}")
            return f
    raise HttpError(404, "항목이 없습니다")


@app.post("/api/handovers/<hid>/review")
def review(req):
    h = load_h(req.params["hid"])
    body = req.json
    rv = h.setdefault("review", {"confirmed": False, "comments": []})
    if body.get("comment"):
        text = clean(h, str(body["comment"]))[:4000]
        rv.setdefault("comments", []).append({"at": _now(), "text": text})
        log(h["id"], "전임자 메모", text)
    if "confirmed" in body:
        rv["confirmed"] = bool(body["confirmed"])
        rv["by"] = "전임자"  # 확인자도 이름 대신 역할로
        rv["at"] = _now()
        if rv["confirmed"]:
            for sec in SECTIONS:
                for it in h["draft"]["sections"][sec]:
                    if it.get("status") == "초안":
                        it["status"] = "확인"
        log(h["id"], "전임자 확인 완료" if rv["confirmed"] else "확인 취소", rv.get("by", ""))
    if "successor" in body:
        h["successor"] = "후임자" if str(body["successor"] or "").strip() else ""
    save_h(h)
    return h


@app.get("/api/handovers/<hid>/history")
def history(req):
    return {"history": store.read_log(f"handovers/{req.params['hid']}/history.jsonl")}


# ---------------------------------------------------------------- 후임자 질문·매뉴얼

@app.post("/api/handovers/<hid>/ask")
def ask(req):
    """AI 전임자 분신에게 묻기: 전임자 자료로만 1인칭으로 답하고, 자료에 없으면 found=False와 함께 물어볼 곳(역할)을 알려 준다."""
    h = load_h(req.params["hid"])
    q = str(req.json.get("q") or "").strip()[:500]
    if not q:
        raise HttpError(400, "질문을 입력해 주세요")
    log(h["id"], "AI 분신에게 질문", clean(h, q))
    h.setdefault("asked", 0)
    h["asked"] += 1
    save_h(h)
    blocks = store.read(f"handovers/{h['id']}/blocks.json", [])
    res = qa.answer(h["draft"], blocks, q, llm, persona="전임자")
    res = qa.persona_wrap(res, "전임자")
    res["photos"] = [p for p in photo_mod.search(h, q) if p["score"] >= 2][:4]  # 질문과 관련된 사진
    if not res.get("found"):
        res["who"] = network.who_knows(h, q, blocks)
        if res["photos"]:
            res["answer"] = ("글로 된 제 자료에서는 답을 찾지 못했어요. 대신 관련 사진이 있어요. 사진을 눌러 내용을 확인해 주세요. "
                             "확인한 내용을 표나 노하우 카드에 남겨 두면 다음부터는 제가 답할게요.")  # 자료에 없으면: 누구에게 물어보면 되는지(역할)
    return res


# ---------------------------------------------------------------- 포토 아카이브

@app.get("/api/handovers/<hid>/photos")
def list_photos(req):
    h = load_h(req.params["hid"])
    q = str(req.query.get("q") or "").strip()[:100]
    found = photo_mod.search(h, q)
    return {"photos": found, "total": len(h.get("photos") or []), "q": q}


@app.get("/api/handovers/<hid>/photos/<pid>/image")
def photo_image(req):
    h = load_h(req.params["hid"])
    pid = req.params["pid"]
    if not any(p["id"] == pid for p in h.get("photos") or []):
        raise HttpError(404, "사진을 찾을 수 없습니다")
    path = store.path(f"handovers/{h['id']}/photos/{pid}.jpg")
    if not path.exists():
        raise HttpError(404, "사진을 찾을 수 없습니다")
    return Response(path.read_bytes(), 200, "image/jpeg", {"Cache-Control": "private, max-age=3600"})


@app.post("/api/handovers/<hid>/photos/<pid>/delete")
def delete_photo(req):
    """사진 속 얼굴·이름표 등 남기면 안 되는 사진은 지운다(줄인 사본과 기록 모두)."""
    h = load_h(req.params["hid"])
    pid = req.params["pid"]
    before = len(h.get("photos") or [])
    h["photos"] = [p for p in h.get("photos") or [] if p["id"] != pid]
    if len(h["photos"]) == before:
        raise HttpError(404, "사진을 찾을 수 없습니다")
    p = store.path(f"handovers/{h['id']}/photos/{pid}.jpg")
    if p.exists():
        p.unlink()
    save_h(h)
    log(h["id"], "사진 삭제", pid)
    return {"ok": True, "total": len(h["photos"])}


# ---------------------------------------------------------------- D-Day 리스크 레이더

@app.get("/api/handovers/<hid>/radar")
def get_radar(req):
    h = load_h(req.params["hid"])
    start = None
    if req.query.get("start"):
        try:
            start = date.fromisoformat(req.query["start"])
        except ValueError:
            raise HttpError(400, "착임일 형식이 올바르지 않습니다(예: 2026-10-12)")
    return radar.build(h, start)


@app.post("/api/handovers/<hid>/radar")
def save_radar(req):
    """후임자 착임(발령)일·의회 행정사무감사 달·기관 유형을 저장한다."""
    h = load_h(req.params["hid"])
    b = req.json
    try:
        start = date.fromisoformat(str(b.get("start") or date.today().isoformat()))
    except ValueError:
        raise HttpError(400, "착임일 형식이 올바르지 않습니다(예: 2026-10-12)")
    try:
        audit = int(b.get("audit_month") or 0)
    except (TypeError, ValueError):
        audit = 0
    h["radar"] = {"start": start.isoformat(), "audit_month": audit if 1 <= audit <= 12 else 0,
                  "gov_level": b.get("gov_level") if b.get("gov_level") in radar.GOV_LEVELS else "시·군·구"}
    save_h(h)
    log(h["id"], "리스크 레이더 기준 저장", f"착임일 {start.isoformat()}")
    return radar.build(h)


# ---------------------------------------------------------------- 휴먼 네트워크 맵 · 노하우 카드

def _net(h):
    return network.build(h, store.read(f"handovers/{h['id']}/blocks.json", []))


@app.get("/api/handovers/<hid>/network")
def get_network(req):
    return _net(load_h(req.params["hid"]))


@app.post("/api/handovers/<hid>/knowhow")
def add_knowhow(req):
    """노하우 카드 직접 쓰기: 대상(협업 상대 노드)·유형·요령. 사람 이름을 적어도 역할 이름으로 바뀌어 저장된다."""
    h = load_h(req.params["hid"])
    b = req.json
    text = clean(h, str(b.get("text") or "").strip()[:500])
    if not text:
        raise HttpError(400, "노하우를 입력해 주세요")
    kind = b.get("kind") if b.get("kind") in network.KINDS else "요령"
    target = str(b.get("target") or "")[:40]
    if target and not any(n["id"] == target for n in _net(h)["nodes"]):
        target = ""  # 지도에 있는 협업 상대만(이름 같은 임의 글자를 저장하지 않음)
    card = {"id": f"m-{uuid.uuid4().hex[:8]}", "target": target, "kind": kind, "text": text,
            "source": "전임자 작성", "at": _now()}
    h.setdefault("knowhow", []).append(card)
    save_h(h)
    log(h["id"], "노하우 카드 추가", text[:60])
    return _net(h)


@app.post("/api/handovers/<hid>/knowhow/<kid>/delete")
def delete_knowhow(req):
    """직접 쓴 카드는 지우고, 자료에서 뽑은 카드는 숨긴다(자료를 다시 읽어도 나오지 않게)."""
    h = load_h(req.params["hid"])
    kid = req.params["kid"]
    before = len(h.get("knowhow") or [])
    h["knowhow"] = [k for k in h.get("knowhow") or [] if k["id"] != kid]
    if len(h["knowhow"]) == before:
        if not re.fullmatch(r"k-[0-9a-f]{8}", kid):
            raise HttpError(404, "카드를 찾을 수 없습니다")
        h.setdefault("knowhow_hidden", []).append(kid)
    save_h(h)
    log(h["id"], "노하우 카드 삭제", kid)
    return _net(h)


# ---------------------------------------------------------------- 표준 인수인계서(필수 5대 영역) 입력 폼

@app.get("/api/handovers/<hid>/form")
def get_form(req):
    """저장한 폼이 없으면 자료 분석 결과로 채운 초안을 돌려준다(저장 전)."""
    h = load_h(req.params["hid"])
    if h.get("form"):
        return {"form": h["form"], "saved": True, "updated": h.get("form_updated", "")}
    return {"form": report.prefill_form(h), "saved": False, "updated": ""}


@app.post("/api/handovers/<hid>/form")
def save_form(req):
    h = load_h(req.params["hid"])
    h["form"] = clean(h, report.clean_form(req.json.get("form")))
    h["form_updated"] = _now()
    save_h(h)
    log(h["id"], "표준 인수인계서 저장", "")
    return {"form": h["form"], "saved": True, "updated": h["form_updated"]}


@app.post("/api/handovers/<hid>/form/reset")
def reset_form(req):
    """자료에서 다시 채우기(저장한 내용은 지워짐)."""
    h = load_h(req.params["hid"])
    h.pop("form", None)
    h.pop("form_updated", None)
    save_h(h)
    log(h["id"], "표준 인수인계서 다시 채우기", "")
    return {"form": report.prefill_form(h), "saved": False, "updated": ""}


# ---------------------------------------------------------------- 법령 검색(국가법령정보 공동활용 Open API)

def _law():
    oc = os.environ.get("LAW_OC") or (cfg.get("law") or {}).get("oc", "")
    # 주소는 화면에서 바꿀 수 없고(다른 곳으로 요청이 새지 않게), 시험·내부 중계 서버용으로 환경변수로만 바꾼다
    return lawapi.LawClient(oc, store, base=os.environ.get("LAW_API_BASE") or lawapi.BASE)


@app.get("/api/law/status")
def law_status(req):
    c = _law()
    return {"configured": c.configured, "from_env": bool(os.environ.get("LAW_OC")),
            "guide": "open.law.go.kr → Open API → 활용 신청 → 승인 후 받은 OC(보통 신청 이메일의 @ 앞부분)를 넣으세요."}


@app.post("/api/law/config")
def law_config(req):
    oc = str(req.json.get("oc") or "").strip()
    if oc and not re.fullmatch(r"[A-Za-z0-9._-]{2,64}", oc):
        raise HttpError(400, "인증값(OC)은 영문·숫자로 된 아이디여야 합니다(이메일 전체가 아니라 @ 앞부분)")
    cfg.update_section("law", {"oc": oc}, {"oc"})
    return law_status(req)


@app.get("/api/law/search")
def law_search(req):
    try:
        return {"items": _law().search(req.query.get("q", ""), req.query.get("target", "law"))}
    except lawapi.LawError as e:
        raise HttpError(400, str(e))


@app.get("/api/law/articles")
def law_articles(req):
    try:
        res = _law().articles(req.query.get("id", ""), req.query.get("target", "law"))
    except lawapi.LawError as e:
        raise HttpError(400, str(e))
    res["link"] = lawapi.public_link(res["name"], res["target"])
    return res


def _manual(h, body):
    try:
        start = date.fromisoformat(str(body["start"])) if body.get("start") else date.today()
    except ValueError:
        raise HttpError(400, "착임일 형식이 올바르지 않습니다(예: 2026-10-12)")
    m = report.build_manual(h, "후임자", body.get("career", "신규"), start)
    m["radar"] = radar.build(h, start)
    return m


@app.post("/api/handovers/<hid>/manual")
def manual(req):
    h = load_h(req.params["hid"])
    return _manual(h, req.json)


@app.post("/api/handovers/<hid>/export/<what>")
def export_doc(req):
    h = load_h(req.params["hid"])
    fmt = req.query.get("fmt", "hwpx")
    if fmt not in FORMATS:
        raise HttpError(400, f"지원하지 않는 파일 형식입니다: '{fmt}'. 한글(hwpx)·워드(docx)·마크다운(md) 중에서 골라 주세요.")
    if req.params["what"] == "handover":
        blocks = report.handover_blocks(h, _net(h), radar.build(h))
        name = f"인수인계서_{_fname(h.get('title'))}"
    elif req.params["what"] == "form":
        blocks = report.form_blocks(h, h.get("form") or report.prefill_form(h))
        name = f"업무인수인계서_{_fname(h.get('title'))}"
    elif req.params["what"] == "manual":
        m = _manual(h, req.json)
        blocks = report.manual_blocks(h, m)
        name = f"업무매뉴얼_{_fname(h.get('title'))}"
    else:
        raise HttpError(404, "없는 문서")
    data, ctype = export(blocks, fmt)
    # 산출물은 원본과 분리된 data/ 아래에도 보관
    out = store.path(f"handovers/{h['id']}/outputs/{name}.{fmt}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    return file_response(data, f"{name}.{fmt}", ctype)


# ---------------------------------------------------------------- 지식 릴레이(.baton)·내 일정(.ics)

def _fname(s):
    return re.sub(r'[\\/:*?"<>|\s]+', "_", s or "").strip("_") or "업무"


@app.post("/api/handovers/<hid>/baton")
def export_baton(req):
    """다음 담당자에게 넘길 바통 파일. 다음 인수인계 때 '자료 넣기'에 넣으면 이어받는다."""
    h = load_h(req.params["hid"])
    b = report.build_baton(h)
    data = json.dumps(b, ensure_ascii=False, indent=1).encode("utf-8")
    name = f"업무바통_{_fname(h.get('title'))}_{len(b['lineage'])}대.baton"
    out = store.path(f"handovers/{h['id']}/outputs/{name}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    h["baton_saved_at"] = _now()
    save_h(h)
    log(h["id"], "바통 파일 저장", f"{len(b['lineage'])}대 {b['holder']} → {b['handed_to'] or '다음 담당자'}")
    return file_response(data, name, "application/json")


@app.post("/api/handovers/<hid>/calendar")
def export_calendar(req):
    """연간 업무 달력을 .ics로: Outlook·그룹웨어 일정에 매월·매년 반복 일정으로 들어간다."""
    h = load_h(req.params["hid"])
    body = req.json or {}
    try:
        start = date.fromisoformat(body["start"]) if body.get("start") else date.today()
    except ValueError:
        raise HttpError(400, "착임일 형식이 올바르지 않습니다(예: 2026-10-12)")
    data, info = report.build_ics(h, start)
    if not info["events"]:
        raise HttpError(400, "내보낼 일정이 없습니다. 연간 업무 달력에 일정이 있는지 확인해 주세요.")
    name = f"업무달력_{_fname(h.get('title'))}.ics"
    log(h["id"], "일정 내보내기(.ics)", f"{info['events']}건, 제외 {len(info['skipped'])}건")
    res = file_response(data, name, "text/calendar; charset=utf-8")
    res.headers["X-Events"] = str(info["events"])
    res.headers["X-Skipped"] = str(len(info["skipped"]))
    return res


# ---------------------------------------------------------------- 문서시스템 API 연계(모의)

def _mock_docs():
    files = sorted(p for p in SAMPLE.rglob("*") if p.is_file())
    return [{"id": f"DOC-{i + 1:04d}", "title": p.stem, "filename": p.name, "category": p.parent.name,
             "created": "2026-09-30", "path": p} for i, p in enumerate(files)]


@app.get("/mock-docsystem/documents")
def mock_list(req):
    return {"documents": [{k: v for k, v in d.items() if k != "path"} for d in _mock_docs()],
            "note": "문서관리시스템 연계를 가정한 모의 API입니다."}


@app.get("/mock-docsystem/documents/<did>/content")
def mock_content(req):
    for d in _mock_docs():
        if d["id"] == req.params["did"]:
            return {"filename": d["filename"], "b64": base64.b64encode(d["path"].read_bytes()).decode()}
    raise HttpError(404, "문서 없음")


def main():
    import argparse
    ap = argparse.ArgumentParser(description="업무바통(인수인계) 실행")
    ap.add_argument("--port", type=int, default=cfg["port"])
    ap.add_argument("--host", default=cfg["host"])
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    cfg.data["port"] = a.port
    app.run(a.host, a.port, not a.no_browser, "업무바통(인수인계)")


if __name__ == "__main__":
    main()
