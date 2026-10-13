"""국가법령정보 공동활용 Open API(law.go.kr DRF) 연동: 법령 이름으로 찾고, 조문을 받아 온다.

- 검색: {base}/lawSearch.do?OC=<인증값>&target=law|admrul|ordin&type=JSON&query=<이름>
- 본문: {base}/lawService.do?OC=<인증값>&target=law|admrul|ordin&type=JSON&MST|ID=<일련번호>
OC(인증값)는 open.law.go.kr 에서 'Open API 활용 신청' 후 받은 아이디(보통 신청 이메일의 @ 앞부분)다.

응답 JSON은 대상(법령·행정규칙·자치법규)과 결과 수에 따라 모양이 조금씩 다르므로(결과가 1건이면 목록 대신 객체 등)
정해진 경로만 보지 않고 '이름·일련번호·조문' 키를 찾아 읽는다. 받은 결과는 data/law_cache 에 저장해 두어
같은 법령은 인터넷 없이 다시 쓸 수 있다.
"""
import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://www.law.go.kr/DRF"
TARGETS = {"law": "법령", "admrul": "행정규칙", "ordin": "자치법규"}
NAME_KEYS = ("법령명한글", "법령명_한글", "행정규칙명", "자치법규명", "법령명")
ID_KEYS = {"law": ("법령일련번호", "MST"), "admrul": ("행정규칙일련번호", "행정규칙ID"), "ordin": ("자치법규일련번호", "자치법규ID")}
PARAM = {"law": "MST", "admrul": "ID", "ordin": "MST"}


class LawError(Exception):
    pass


def _walk(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def _text(v):
    """조문·항·호의 글자: 문자열, 문자열 목록, {'content': ...} 모두 받는다."""
    if v is None:
        return ""
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, list):
        return "\n".join(t for t in (_text(x) for x in v) if t)
    if isinstance(v, dict):
        return _text(v.get("content") or v.get("#text") or "")
    return str(v)


class LawClient:
    def __init__(self, oc, store=None, base=BASE, timeout=15):
        self.oc = (oc or "").strip()
        self.store = store
        self.base = base.rstrip("/")
        self.timeout = timeout

    @property
    def configured(self):
        return bool(self.oc)

    def _get(self, path, params):
        if not self.oc:
            raise LawError("법령 API 인증값(OC)이 없습니다. open.law.go.kr 에서 Open API 활용 신청 후 받은 아이디를 넣어 주세요.")
        q = urllib.parse.urlencode({"OC": self.oc, "type": "JSON", **params})
        key = hashlib.sha1(f"{path}?{q}".encode()).hexdigest()[:20]
        cache = f"law_cache/{key}.json"
        if self.store:
            hit = self.store.read(cache)
            if hit and time.time() - hit.get("_at", 0) < 7 * 86400:  # 1주일 안에 받은 결과는 다시 쓴다(법령 개정 반영 위해 기한)
                return hit["data"]
        try:
            with urllib.request.urlopen(f"{self.base}/{path}?{q}", timeout=self.timeout) as r:
                raw = r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            raise LawError(f"법령 API 오류({e.code})")
        except (urllib.error.URLError, TimeoutError) as e:
            stale = self.store.read(cache) if self.store else None
            if stale:  # 인터넷이 끊겼으면 오래된 저장본이라도 쓴다
                return stale["data"]
            raise LawError(f"법령 API에 연결할 수 없습니다({getattr(e, 'reason', e)}). 인터넷 연결이나 기관 방화벽(www.law.go.kr 허용)을 확인해 주세요.")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # 인증 실패 등은 JSON 대신 안내 문구(HTML)를 돌려준다
            msg = re.sub(r"<[^>]+>", " ", raw)
            msg = re.sub(r"\s+", " ", msg).strip()[:150]
            raise LawError(f"법령 API가 결과 대신 안내를 보냈습니다: {msg or '빈 응답'} (인증값(OC)과 신청한 서비스 범위를 확인해 주세요)")
        if self.store:
            self.store.write(cache, {"_at": time.time(), "data": data})
        return data

    def search(self, query, target="law", display=20):
        """이름으로 찾기 → [{id, name, kind, dept, enforce, promulgated, link}]"""
        if target not in TARGETS:
            raise LawError("검색 대상은 법령·행정규칙·자치법규 중 하나여야 합니다")
        query = str(query or "").strip()[:100]
        if not query:
            raise LawError("찾을 법령 이름을 입력해 주세요")
        data = self._get("lawSearch.do", {"target": target, "query": query, "display": display})
        out, seen = [], set()
        for d in _walk(data):
            name = next((_text(d[k]) for k in NAME_KEYS if d.get(k)), "")
            ident = next((_text(d[k]) for k in ID_KEYS[target] if d.get(k)), "")
            if not name or not ident or ident in seen:
                continue
            seen.add(ident)
            out.append({"id": ident, "name": name, "target": target, "kind": _text(d.get("법령구분명") or d.get("행정규칙종류") or d.get("자치법규종류")) or TARGETS[target],
                        "dept": _text(d.get("소관부처명") or d.get("소관부처") or d.get("지자체기관명")),
                        "enforce": _date(_text(d.get("시행일자"))), "promulgated": _date(_text(d.get("공포일자") or d.get("발령일자"))),
                        "link": public_link(name, target)})
        return out

    def articles(self, ident, target="law"):
        """조문 목록 → {name, enforce, articles:[{no, title, text}]}"""
        if target not in TARGETS:
            raise LawError("대상이 올바르지 않습니다")
        ident = re.sub(r"[^0-9A-Za-z]", "", str(ident or ""))[:20]
        if not ident:
            raise LawError("법령 일련번호가 없습니다")
        data = self._get("lawService.do", {"target": target, PARAM[target]: ident})
        name, enforce = "", ""
        for d in _walk(data):
            name = name or next((_text(d[k]) for k in NAME_KEYS if d.get(k)), "")
            enforce = enforce or _date(_text(d.get("시행일자")))
        arts = []
        for d in _walk(data):
            body = d.get("조문내용")
            if body is None or (d.get("조문여부") and _text(d.get("조문여부")) != "조문"):
                continue  # 장·절 제목(전문)은 건너뜀
            no = _text(d.get("조문번호"))
            sub = _text(d.get("조문가지번호"))
            if not no and isinstance(body, list) and sum(1 for x in body if re.match(r"\s*제\s*\d+\s*조", _text(x))) > 1:
                # 행정규칙 등: 조문 전체가 글자 목록으로 온다 → 항목마다 한 조문
                for x in body:
                    t = _text(x)
                    m = re.match(r"\s*(제\s*\d+\s*조(?:의\s*\d+)?)\s*(?:\(([^)]*)\))?", t)
                    if m:
                        arts.append({"no": re.sub(r"\s+", "", m.group(1)), "title": m.group(2) or "", "text": t[:6000]})
                    elif arts and t:
                        arts[-1]["text"] = (arts[-1]["text"] + "\n" + t)[:6000]
                continue
            parts = [_text(body)]
            for hang in _as_list(d.get("항")):
                if isinstance(hang, dict):
                    parts.append(_text(hang.get("항내용")))
                    for ho in _as_list(hang.get("호")):
                        if isinstance(ho, dict):
                            parts.append(_text(ho.get("호내용")))
                            for mok in _as_list(ho.get("목")):
                                if isinstance(mok, dict):
                                    parts.append(_text(mok.get("목내용")))
            text = "\n".join(p for p in parts if p)
            if not text:
                continue
            label = f"제{no}조" + (f"의{sub}" if sub and sub != "0" else "") if no else ""
            if not label:
                m = re.match(r"\s*(제\s*\d+\s*조(?:의\s*\d+)?)", text)
                label = re.sub(r"\s+", "", m.group(1)) if m else f"{len(arts) + 1}번째 조문"
            arts.append({"no": label, "title": _text(d.get("조문제목")), "text": text[:6000]})
        if not arts:
            # 행정규칙·자치법규 중에는 조문이 나뉘지 않은 긴 글로 오는 경우가 있다: '제n조'로 나눈다
            whole = "\n".join(_text(d.get(k)) for d in _walk(data) for k in ("조문내용", "본문", "내용") if isinstance(d.get(k), (str, list)))
            for m in re.finditer(r"(제\s*\d+\s*조(?:의\s*\d+)?)\s*(?:\(([^)]*)\))?(.*?)(?=제\s*\d+\s*조(?:의\s*\d+)?\s*(?:\(|$)|\Z)", whole, re.S):
                arts.append({"no": re.sub(r"\s+", "", m.group(1)), "title": m.group(2) or "", "text": (m.group(0).strip())[:6000]})
        return {"name": name, "enforce": enforce, "target": target, "id": ident, "articles": arts[:800]}


def _as_list(v):
    return v if isinstance(v, list) else ([v] if v else [])


def _date(s):
    m = re.match(r"(\d{4})(\d{2})(\d{2})$", s or "")
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else (s or "")


def public_link(name, target="law", article=""):
    """누구나 열 수 있는 국가법령정보센터 주소(인증값 없이). 예: https://www.law.go.kr/법령/지방재정법/제20조"""
    if not name:
        return ""
    url = f"https://www.law.go.kr/{TARGETS.get(target, '법령')}/{urllib.parse.quote(name)}"
    return url + (f"/{urllib.parse.quote(article)}" if article else "")
