"""포토 아카이브: 넣은 자료 속 사진을 따로 모아 보고, 키워드로 관련 사진을 찾는다.

사진 한 장마다 찾을 단서를 모아 둔다.
  - 파일 이름·폴더 이름(예: '2026 직무중심교육/현장.jpg')
  - 사진 속 글자(OCR: 현수막·안내문·화이트보드)
  - AI 사진 설명(이미지를 볼 수 있는 모델을 연결했을 때만, 사람 이름·얼굴 특징은 쓰지 않게 함)
  - 찍은 날짜(EXIF)
  - 같은 폴더에 있는 다른 자료 이름, 이 사진을 근거로 한 인수인계 항목
원본 사진은 저장하지 않고 긴 변 1,024px로 줄인 사본만 보관한다(올린 원본 사본은 읽은 뒤 지움).
사진 속 얼굴·이름표는 자동으로 가리지 않으므로 화면에서 지울 수 있게 한다.
"""
import io
import re

THUMB_SIDE = 1024
CAPTION_PROMPT = ("이 사진이 무엇을 찍은 것인지 한국어 한 문장으로 설명하세요(행사·교육·회의·시설·장소·물건·상태). "
                  "사진 속 글자(현수막·안내문)가 있으면 그대로 넣으세요. 사람의 이름이나 얼굴 생김새는 쓰지 마세요.")
STOP = {"사진", "관련", "보여줘", "보여", "찾아줘", "찾아", "있어", "어디", "뭐야", "자료", "파일", "이미지"}
JOSA = re.compile(r"(은|는|이|가|을|를|의|에|에서|으로|로|과|와|도|만|이랑|랑)$")


def thumbnail(data):
    """(JPEG 바이트, 가로, 세로, 찍은 날짜) — 회전 정보 반영, 긴 변 1,024px."""
    from PIL import Image, ImageOps
    im = Image.open(io.BytesIO(data))
    taken = None
    try:
        exif = im.getexif()
        raw = (exif.get_ifd(0x8769) if hasattr(exif, "get_ifd") else {}).get(36867) or exif.get(306)
        m = re.match(r"(\d{4}):(\d{2}):(\d{2})", str(raw or ""))
        taken = f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None
    except Exception:
        pass
    im = ImageOps.exif_transpose(im)
    if im.mode != "RGB":
        im = im.convert("RGB")
    im.thumbnail((THUMB_SIDE, THUMB_SIDE))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=82)  # EXIF(위치 정보 등)는 담지 않는다
    return buf.getvalue(), im.size[0], im.size[1], taken


def caption(jpeg, llm):
    """이미지를 볼 수 있는 모델이 연결돼 있으면 한 문장 설명. 아니면 빈 글자."""
    import base64
    from core import ocr
    if llm is None or not llm.enabled:
        return ""
    mode = str(llm.cfg.get("vision", "auto")).lower()
    if mode in ("false", "off", "no") or (mode == "auto" and not ocr.VISION_HINT.search(llm.cfg.get("model") or "")):
        return ""
    try:
        png, *_ = ocr.to_png(jpeg, 1024)
        return re.sub(r"\s+", " ", llm.see(base64.b64encode(png).decode(), CAPTION_PROMPT, max_tokens=200)).strip()[:200]
    except Exception:
        return ""


def build(docs, fix, save, llm=None):
    """사진 문서 → 사진 기록 목록. save(id, jpeg바이트)로 줄인 사본을 저장한다. fix: 이름 비식별 함수."""
    out = []
    by_folder = {}
    for d in docs:
        by_folder.setdefault(d.relpath.rsplit("/", 1)[0] if "/" in d.relpath else "", []).append(d)
    for d in docs:
        data = getattr(d, "image_bytes", None)
        if not data:
            continue
        try:
            jpeg, w, h, taken = thumbnail(data)
        except Exception:
            continue
        save(d.id, jpeg)
        folder = d.relpath.rsplit("/", 1)[0] if "/" in d.relpath else ""
        siblings = [x.name for x in by_folder.get(folder, []) if x is not d and not getattr(x, "image_bytes", None)] if folder else []
        out.append({"id": d.id, "file": fix(d.relpath), "name": fix(d.name), "folder": fix(folder),
                    "taken": taken or d.meta.get("taken") or "", "size": f"{w}x{h}",
                    "ocr": fix(" / ".join(t for t in (b.text for b in d.blocks) if _readable(t)))[:1500], "ocr_engine": d.meta.get("ocr_engine", ""),
                    "caption": fix(caption(jpeg, llm)), "siblings": [fix(x) for x in siblings[:20]]})
    return out


def _readable(line):
    """글자 인식이 잘못 읽은 부스러기(기호·영문 조각)는 빼고, 한글 낱말이 있는 줄만 남긴다."""
    t = line.strip()
    hangul = len(re.findall(r"[가-힣]", t))
    good = len(re.findall(r"[가-힣0-9A-Za-z\s.,:/()~·%-]", t))
    return hangul >= 2 and good / max(len(t), 1) >= 0.8


def _words(q):
    ws = []
    for w in re.findall(r"[가-힣A-Za-z0-9]+", q or ""):
        w = JOSA.sub("", w) if len(w) > 2 else w
        if len(w) >= 2 and w not in STOP:
            ws.append(w)
    return ws


def _flat(s):
    return re.sub(r"[\s_\-·.()\[\]]+", "", s or "").lower()


def search(h, q=""):
    """키워드로 사진 찾기 → [사진 + reasons(찾은 까닭) + score]. q가 비면 전부(찍은 날짜순)."""
    photos = h.get("photos") or []
    linked = {}
    for sec in ("rnr", "schedule", "contacts", "issues"):
        for it in h["draft"]["sections"].get(sec, []):
            for s in it.get("sources", []):
                linked.setdefault(s.get("file"), []).append(it.get("title") or it.get("task") or it.get("duty") or it.get("name") or "")
    words = _words(q)
    out = []
    for p in photos:
        fields = [("파일 이름", re.sub(r"\.[^.]+$", "", p["name"]), 3), ("폴더 이름", p["folder"], 3),
                  ("사진 속 글자", p["ocr"], 2), ("AI 사진 설명", p["caption"], 2),
                  ("이 사진을 근거로 한 항목", " ".join(linked.get(p["file"], [])), 2),
                  ("같은 폴더 자료", " ".join(p["siblings"]), 1), ("찍은 날짜", _date_words(p["taken"]), 1)]
        score, reasons = 0, []
        for w in words:
            fw = _flat(w)
            for label, text, weight in fields:
                if fw and fw in _flat(text):
                    score += weight
                    if label not in reasons:
                        reasons.append(label)
                    break
        if words and not score:
            continue
        out.append({**p, "url": f"/api/handovers/{h['id']}/photos/{p['id']}/image", "score": score, "reasons": reasons,
                    "linked": linked.get(p["file"], [])[:3]})
    out.sort(key=lambda x: (-x["score"], x["taken"] or "9999", x["file"]))
    return out


def _date_words(taken):
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", taken or "")
    if not m:
        return ""
    y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
    return f"{y}년 {mo}월 {d}일 {mo}월 {y}"
