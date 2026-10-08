"""업무바통(인수인계) 웹 서버."""
import base64
import time
import uuid
from dataclasses import asdict
from datetime import date
from pathlib import Path

from core.config import Config
from core.export import FORMATS, export
from core.llm import LLM
from core.server import App, HttpError, file_response
from core.sources import DocSystemSource, LocalFolderSource, UploadSource
from core.store import Store
from core import webcommon

import engine
import qa
import report

VERSION = "1.0.0"
HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_data" / "전임자_김도윤_업무폴더"

cfg = Config(HERE, {"app_title": "업무바통", "port": 8101})
llm = LLM(cfg["llm"])
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
                       | {"predecessor": h["draft"].get("predecessor"), "confirmed": h.get("review", {}).get("confirmed", False)})
    out.sort(key=lambda x: x["created"], reverse=True)
    return {"handovers": out}


@app.post("/api/handovers")
def create_handover(req):
    body = req.json
    src = body.get("source") or {}
    hid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    kind = src.get("kind")
    if kind == "upload":
        if not src.get("files"):
            raise HttpError(400, "올린 파일이 없습니다")
        source = UploadSource(src["files"], store.path(f"handovers/{hid}/inbox"))
    elif kind == "local":
        try:
            source = LocalFolderSource(src.get("path", ""))
        except FileNotFoundError as e:
            raise HttpError(400, str(e))
    elif kind == "sample":
        source = LocalFolderSource(SAMPLE)
    elif kind == "docsystem":
        base = src.get("base_url") or f"http://127.0.0.1:{cfg['port']}/mock-docsystem"
        source = DocSystemSource(base, src.get("owner", ""))
    else:
        raise HttpError(400, "자료 넣는 방법을 선택해 주세요")
    docs, skipped = source.load()
    if not [d for d in docs if not d.error]:
        raise HttpError(400, "읽을 수 있는 자료가 없습니다. 한글·PDF·엑셀·워드·메일·텍스트 파일을 넣어 주세요.")
    draft = engine.analyze(docs, body.get("predecessor", "").strip(), llm)
    h = {"id": hid, "title": body.get("title") or f"{draft['predecessor'] or '전임자'} 업무 인수인계",
         "successor": body.get("successor", ""), "created": _now(), "source": source.describe(),
         "skipped": skipped, "draft": draft, "review": {"confirmed": False, "comments": []}}
    store.write(f"handovers/{hid}/blocks.json", [asdict(b) for d in docs for b in d.blocks])
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


@app.post("/api/handovers/<hid>/item")
def upsert_item(req):
    h = load_h(req.params["hid"])
    body = req.json
    sec = body.get("section")
    if sec not in SECTIONS:
        raise HttpError(400, "잘못된 구분")
    item = body.get("item") or {}
    items = h["draft"]["sections"][sec]
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
    if sec == "schedule":
        item.setdefault("months", [])
        item["months"] = [int(m) for m in item["months"] if 1 <= int(m) <= 12]
        item.setdefault("task", "")
    items.append(item)
    save_h(h)
    log(h["id"], f"{sec} 추가", item.get("task") or item.get("title") or item.get("name") or item.get("duty") or "")
    return item


@app.post("/api/handovers/<hid>/item/delete")
def delete_item(req):
    h = load_h(req.params["hid"])
    body = req.json
    for it in h["draft"]["sections"].get(body.get("section"), []):
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
            f["note"] = body.get("note", "")
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
        rv.setdefault("comments", []).append({"at": _now(), "text": body["comment"]})
        log(h["id"], "전임자 메모", body["comment"])
    if "confirmed" in body:
        rv["confirmed"] = bool(body["confirmed"])
        rv["by"] = body.get("by", "")
        rv["at"] = _now()
        if rv["confirmed"]:
            for sec in SECTIONS:
                for it in h["draft"]["sections"][sec]:
                    if it.get("status") == "초안":
                        it["status"] = "확인"
        log(h["id"], "전임자 확인 완료" if rv["confirmed"] else "확인 취소", rv.get("by", ""))
    if "successor" in body:
        h["successor"] = body["successor"]
    save_h(h)
    return h


@app.get("/api/handovers/<hid>/history")
def history(req):
    return {"history": store.read_log(f"handovers/{req.params['hid']}/history.jsonl")}


# ---------------------------------------------------------------- 후임자 질문·매뉴얼

@app.post("/api/handovers/<hid>/ask")
def ask(req):
    h = load_h(req.params["hid"])
    q = (req.json.get("q") or "").strip()
    if not q:
        raise HttpError(400, "질문을 입력해 주세요")
    blocks = store.read(f"handovers/{h['id']}/blocks.json", [])
    res = qa.answer(h["draft"], blocks, q, llm)
    log(h["id"], "후임자 질문", q)
    return res


def _manual(h, body):
    start = date.fromisoformat(body["start"]) if body.get("start") else date.today()
    return report.build_manual(h, body.get("successor") or h.get("successor", ""), body.get("career", "신규"), start)


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
        blocks = report.handover_blocks(h)
        name = f"인수인계서_{h['draft'].get('predecessor', '')}"
    elif req.params["what"] == "manual":
        m = _manual(h, req.json)
        blocks = report.manual_blocks(h, m)
        name = f"업무매뉴얼_{m['successor'] or '후임자'}"
    else:
        raise HttpError(404, "없는 문서")
    data, ctype = export(blocks, fmt)
    # 산출물은 원본과 분리된 data/ 아래에도 보관
    out = store.path(f"handovers/{h['id']}/outputs/{name}.{fmt}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    return file_response(data, f"{name}.{fmt}", ctype)


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
