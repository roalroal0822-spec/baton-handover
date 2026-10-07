"""후임자 질문 답변: 전임자 자료와 확인된 인수인계 내용만 근거로 답한다."""
import re

from core.search import BM25
from core.textutil import split_sentences

QA_SYSTEM = """당신은 인수인계를 받은 후임자의 질문에 답하는 도우미입니다.
반드시 아래 [자료] 안의 내용만 근거로 답하고, 근거 번호를 문장 끝에 [1]처럼 붙입니다.
자료에 답이 없으면 "자료에서 찾지 못했습니다"라고 답하고 누구에게 물어보면 좋을지 연락처 자료가 있으면 알려줍니다.
출력 JSON: {"answer": "답변", "used": [근거 번호들]}"""


def _item_text(sec, it):
    if sec == "schedule":
        return f"[일정] {', '.join(str(m) + '월' for m in it['months'][:3])} {it.get('day') or ''} {it['task']} {it.get('related', '')}"
    if sec == "contacts":
        return f"[연락처] {it['name']} {it.get('title', '')} {it.get('org', '')} {' '.join(it.get('phones', []))} {' '.join(it.get('topics', []))}"
    if sec == "issues":
        return f"[현안] {it['title']} {it.get('state', '')} {it.get('next_action', '')} {it.get('due', '')}"
    return f"[담당업무] {it.get('duty', '')}"


def answer(draft, blocks, q, llm=None):
    q = q.strip()
    sections = draft["sections"]
    items = [(sec, it) for sec in ("schedule", "contacts", "issues", "rnr") for it in sections[sec]
             if it.get("status") != "삭제"]
    found_items = []
    month = re.search(r"(\d{1,2})\s*월", q)
    if month and re.search(r"할\s*일|일정|해야|뭐|무엇|업무", q):
        m = int(month.group(1))
        found_items = [(s, it) for s, it in items if s == "schedule" and m in it["months"]]
    elif re.search(r"누구|연락|전화|번호|담당자|협의|물어", q):
        idx = BM25([x for x in items if x[0] == "contacts"], lambda x: _item_text(*x))
        res = idx.search(q, k=4)
        found_items = [x for x, sc in res if sc >= res[0][1] * 0.6] if res else []
    elif re.search(r"현안|진행\s*중|남은|급한|밀린|마감|기한", q):
        found_items = [(s, it) for s, it in items if s == "issues"]
    if not found_items:
        idx = BM25(items, lambda x: _item_text(*x))
        found_items = [x for x, _ in idx.search(q, k=5)]
    bidx = BM25(blocks, lambda b: b["text"])
    hits = [b for b, _ in bidx.search(q, k=6)]

    sources = []
    for _, it in found_items:
        sources.extend(it["sources"][:2])
    for b in hits:
        sources.append({"block_id": b["id"], "file": b["file"], "loc": b["loc"], "quote": b["text"][:160]})
    uniq, seen = [], set()
    for s in sources:
        k = (s["file"], s["loc"])
        if k not in seen:
            seen.add(k)
            uniq.append(s)
    sources = uniq[:8]

    if llm is not None and llm.enabled and sources:
        ctx = "\n".join(f"[{i + 1}] ({s['file']} {s['loc']}) {s['quote']}" for i, s in enumerate(sources))
        extra = "\n".join(_item_text(*x) for x in found_items[:8])
        try:
            out = llm.json(QA_SYSTEM, f"[자료]\n{ctx}\n\n[정리된 인수인계 항목]\n{extra}\n\n[질문]\n{q}")
            used = [int(u) for u in out.get("used", []) if str(u).isdigit() and 1 <= int(u) <= len(sources)]
            return {"answer": out.get("answer", "").strip(), "items": [dict(it, section=s) for s, it in found_items],
                    "sources": [sources[u - 1] for u in used] or sources[:3], "mode": llm.label}
        except Exception as e:
            fallback_note = f"(모델 응답 실패로 규칙 기반 답변: {e})"
        else:
            fallback_note = ""
    else:
        fallback_note = ""

    # 규칙 기반 답변: 정리된 항목 + 관련 원문 문장
    lines = []
    if found_items:
        for s, it in found_items[:8]:
            if s == "schedule":
                when = ("매월 " if it.get("recurring") == "매월" else f"{it['months'][0]}월 ") + (f"{it['day']}일 " if it.get("day") else "")
                lines.append(f"• {when.strip()}: {it['task']}")
            elif s == "contacts":
                lines.append(f"• {it['name']} {it.get('title', '')} ({it.get('org', '')}) {', '.join(it.get('phones', []))}"
                             f" — {', '.join(it.get('topics', [])[:2])}")
            elif s == "issues":
                lines.append(f"• {it['title']} [{it.get('state', '')}] 다음 할 일: {it.get('next_action') or '-'} / 기한: {it.get('due') or '-'}")
            else:
                lines.append(f"• {it.get('duty')}")
    if not lines and hits:
        qwords = set(re.findall(r"[가-힣]{2,}", q))
        for b in hits[:3]:
            best = max(split_sentences(b["text"]) or [b["text"]],
                       key=lambda s: len(qwords & set(re.findall(r"[가-힣]{2,}", s))))
            lines.append(f"• {best}")
    if not lines:
        text = "자료에서 찾지 못했습니다. 연락처 탭에서 관련 담당자에게 확인해 보세요."
    else:
        text = "자료에서 찾은 내용입니다.\n" + "\n".join(lines)
    if fallback_note:
        text += "\n" + fallback_note
    return {"answer": text, "items": [dict(it, section=s) for s, it in found_items], "sources": sources[:5],
            "mode": "규칙 기반"}
