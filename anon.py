"""사람 이름 비식별화: 저장하기 전에 사람 이름을 역할 이름으로 바꾼다.

- 전임자 → '전임자', 후임자 → '후임자', 앞 세대 담당자 → 'n대 담당자'
- 연락처 → 소속·직위로 된 역할 이름(예: '(주)그린공조 팀장', '구청 체육진흥과 주무관'), 같은 역할이 둘이면 'A·B'
- 그 밖에 문장에 나온 사람 → '관계 주무관 1' 처럼 직위만
- 휴대전화는 가운데 자리(010-****-9081), 개인 메일은 앞부분(***@기관.kr)을 가린다(부서 공용 메일은 그대로)
화면·AI 답변·내려받는 문서·바통 파일 어디에도 실제 이름이 남지 않는다. 이후 사람이 직접 적는 글(메모·노하우·서식)도
같은 규칙으로 바꾸기 위해, 이름은 저장하지 않고 '소금 친 해시'만 저장한다(해시로는 이름을 되돌릴 수 없음).
"""
import hashlib
import re
import secrets
from collections import Counter

from engine import is_name, people_in

TITLE_OK = re.compile(r"^(주무관|팀장|과장|대리|주임|부장|차장|사원|국장|계장|실장|소장|본부장|이사장|이사|센터장|선임|책임|연구원|대표|감독관|위원|전문위원|PM|PL|매니저|사무관|주사|주사보|서기|반장|엔지니어|감리원)$")


def _org(o):
    o = re.sub(r"^기관\s+", "", (o or "").strip())
    return o[:20]


def role_label(c):
    title = c.get("title") if TITLE_OK.match(c.get("title") or "") else ""
    org = _org(c.get("org"))
    if org and title:
        return f"{org} {title}"
    if org:
        return f"{org} 담당자"
    # 소속이 없으면 협의 업무로 이름 짓되, 그 문장에 든 이름·번호·'담당:' 같은 머리말은 뺀다
    topic = (c.get("topics") or [""])[0]
    topic = topic.replace(c.get("name") or "\0", " ")
    topic = re.sub(r"\d[\d\-.\s]{5,}\d|\d{2,}-?|담당\s*[:：]?|문의\s*[:：]?|연락처\s*[:：]?", " ", topic)
    if c.get("title"):
        topic = topic.replace(c["title"], " ")
    topic = re.sub(r"\s+", " ", re.sub(r"[^가-힣A-Za-z0-9·()\s]", " ", topic))[:14].strip()
    if len(re.findall(r"[가-힣]", topic)) < 2:
        topic = ""
    if title:
        return f"{topic} 관련 {title}" if topic else f"관계 {title}"
    return f"{topic} 관련 담당자" if topic else "관계자"


def build_aliases(draft, blocks, successor=""):
    """이름 → 역할 이름. blocks: [{'text':..}] (문장에서 사람 이름을 더 찾는다)"""
    names = {}
    pred = draft.get("predecessor") or ""
    if pred:
        names[pred] = "전임자"
    if successor and is_name(successor.strip()):
        names.setdefault(successor.strip(), "후임자")
    for i, x in enumerate(draft.get("lineage") or [], 1):
        if x.get("name") and is_name(x["name"]):
            names.setdefault(x["name"], f"{i}대 담당자")
        if x.get("handed_to") and is_name(x["handed_to"]):
            names.setdefault(x["handed_to"], f"{i + 1}대 담당자")
    # 연락처: 소속·직위 이름. 같은 역할 이름이 여럿이면 A, B …
    contacts = [c for c in draft["sections"]["contacts"] if c.get("name") and c["name"] not in names]
    labels = [role_label(c) for c in contacts]
    dup = Counter(labels)
    seen = Counter()
    for c, lab in zip(contacts, labels):
        if dup[lab] > 1:
            seen[lab] += 1
            lab = f"{lab} {'ABCDEFGHIJ'[min(seen[lab], 10) - 1]}"
        names[c["name"]] = lab
    # 문장·메일 머리글에 나온 나머지 사람
    other = Counter()
    for b in blocks:
        text = b.get("text") or ""
        found = [(n, t) for n, t, _ in people_in(text)]
        found += [(m.group(1), "") for m in re.finditer(r"([가-힣]{2,4})\s*<[^>]+@", text)]
        for n, t in found:
            if n in names or not is_name(n):
                continue
            t = t if TITLE_OK.match(t or "") else ""
            other[t] += 1
            names[n] = f"관계 {t or '담당자'} {other[t]}"
    # 안전장치: 역할 이름 안에 다른 사람(또는 자기) 이름이 들어가면 안 된다
    for n, a in list(names.items()):
        for other_name in names:
            a = a.replace(other_name, "")
        a = re.sub(r"\s+", " ", a).strip()
        names[n] = a if len(a) >= 2 and a not in ("관련", "담당자") else "관계자"
    return names


DUP = re.compile(r"(?<![^\s(\[])(\S{2,}(?:\s\S+){0,3})\s+(?=\1)")
MOBILE = re.compile(r"(01[016789])[-.\s]?(\d{3,4})[-.\s]?(\d{4})")
EMAIL = re.compile(r"\b([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
# 부서·업무용 공용 메일(이름이 아닌 것)은 그대로 둔다
ROLE_MAIL = re.compile(r"^(budget|admin|info|contact|help|support|office|master|webmaster|team|service|cs|civil|minwon|"
                       r"account|accounting|finance|contract|gw|noreply|no-reply|mail|biz|sales|press|hr|safety|facility)\d*$", re.I)


HONOR = re.compile(r"(?<![가-힣])([가-힣]{2,4}?)(?=\s?(?:님|씨|에게|께서|께|선생님))")
ROLE_WORDS = {"전임자", "후임자", "담당자", "관계자", "책임자", "신청자", "이용자", "민원인", "작성자", "결재자", "입회자", "인수자",
              "인계자", "고객", "업체", "주무관", "팀장", "과장", "주임", "대리", "실장", "소장", "부장", "국장", "선생"}


def tidy(s):
    """이름을 역할로 바꾸면 '총무팀 이OO 주임' → '총무팀 총무팀 주임 주임'처럼 겹친다: 바로 이어 반복되는 말을 하나로."""
    prev = None
    while prev != s:
        prev, s = s, DUP.sub("", s)
    return s


def pii(s):
    """이름 말고도 사람을 알아볼 수 있는 것: 휴대전화 가운데 자리, 개인 메일 주소 앞부분을 가린다."""
    s = MOBILE.sub(lambda m: f"{m.group(1)}-****-{m.group(3)}", s)
    return EMAIL.sub(lambda m: m.group(0) if ROLE_MAIL.match(m.group(1)) else f"***@{m.group(2)}", s)


def replacer(names):
    if not names:
        return pii
    titles = "|".join(sorted({t for t in TITLE_OK.pattern.strip("^()$").split("|")}, key=len, reverse=True))
    rx = re.compile("(" + "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True)) + r")(\s?(?:" + titles + r"))?")

    def one(m):
        alias = names[m.group(1)]
        # 역할 이름이 이미 직위로 끝나면(예: '구청 체육진흥과 주무관') 뒤에 붙은 직위는 뺀다
        if m.group(2) and re.search(r"(" + titles + r")(\s[A-Z])?$", alias):
            return alias
        return alias + (m.group(2) or "")
    return lambda s: pii(tidy(rx.sub(one, s)) if rx.search(s) else s)


def deep(obj, fn):
    """JSON 같은 자료 안의 모든 글자(값)에 fn을 적용한다(키는 그대로)."""
    if isinstance(obj, str):
        return fn(obj)
    if isinstance(obj, list):
        return [deep(x, fn) for x in obj]
    if isinstance(obj, dict):
        return {k: deep(v, fn) for k, v in obj.items()}
    return obj


def hashed(names):
    """이후 입력 글을 비식별화하기 위한 소금 친 해시 표(이름 자체는 저장하지 않음)."""
    salt = secrets.token_hex(8)
    return {"salt": salt, "map": {_h(salt, n): a for n, a in names.items()}}


def _h(salt, name):
    return hashlib.sha256((salt + name).encode("utf-8")).hexdigest()[:24]


def scrub(text, table):
    """사람이 새로 적은 글: 2~4글자 한글 낱말이 저장된 이름 해시와 같으면 역할 이름으로 바꾼다(조사가 붙어도)."""
    if not text:
        return text
    salt, mp = (table or {}).get("salt", ""), (table or {}).get("map") or {}

    def one(m):
        w = m.group(0)
        for k in (4, 3, 2):
            if len(w) >= k:
                alias = mp.get(_h(salt, w[:k]))
                if alias:
                    return alias + w[k:]
        return w
    out = re.sub(r"[가-힣]{2,8}", one, str(text))
    # 이 건의 자료에 없던 이름이라도 '이름 + 직위' 꼴이면 직위만 남긴다(예: '홍길동 대리' → '관계 대리')
    for n, t, _ in sorted(people_in(out), key=lambda x: -x[2]):
        if is_name(n) and t and t != "님":
            out = re.sub(re.escape(n) + r"\s?(?=" + re.escape(t) + ")", "관계 ", out)
    # '이수민에게', '김도윤님', '박정우 씨'처럼 높임말·조사가 붙은 이름 → '담당자'
    out = HONOR.sub(lambda m: "담당자" if is_name(m.group(1)) and m.group(1) not in ROLE_WORDS
                    and not TITLE_OK.match(m.group(1)) else m.group(0), out)
    return pii(tidy(out))
