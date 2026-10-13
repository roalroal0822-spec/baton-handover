"use strict";
let H = null;          // 현재 인수인계 건
let pickedFiles = [];
const CTX = () => `/api/handovers/${H.id}/context`;

const SEC = {
  rnr: { title: "담당 업무(R&R)", cols: [{ k: "duty", l: "담당 업무" }, { k: "detail", l: "세부 내용·메모" }] },
  schedule: { title: "연간 업무 일정", cols: [{ k: "months", l: "월", t: "months" }, { k: "day", l: "일", t: "num" }, { k: "task", l: "할 일" }, { k: "related", l: "관련" }] },
  contacts: { title: "협의 연락망(협업 상대)", cols: [{ k: "name", l: "협업 상대(역할)" }, { k: "org", l: "소속" }, { k: "title", l: "직위" }, { k: "phones", l: "연락처", t: "list" }, { k: "emails", l: "메일", t: "list" }, { k: "topics", l: "협의 업무", t: "list" }] },
  issues: { title: "진행 중인 현안", cols: [{ k: "title", l: "현안" }, { k: "state", l: "상태" }, { k: "next_action", l: "다음 할 일" }, { k: "due", l: "기한" }] },
};
const STATUS_TAG = { "초안": "gray", "확인": "ok", "수정": "info", "추가": "info", "삭제": "bad" };

// ------------------------------------------------------------ 개인정보 보호
// 사람 이름은 서버가 자료를 읽을 때 이미 역할 이름(전임자·후임자·소속 직위)으로 바꿔 저장한다(화면·문서·바통 파일 모두 이름 없음).
// 화면에서는 혹시 남은 휴대전화 번호의 가운데 자리를 한 번 더 가린다(010-****-9081).
const MOBILE_RX = /(?<!\d)(01[016789])([-. ]?)(\d{3,4})([-. ]?)(\d{4})(?!\d)/g;
const pv = t => t == null ? t : String(t).replace(MOBILE_RX, (m, a, s1, mid, s2, b) => `${a}${s1}${"*".repeat(mid.length)}${s2}${b}`);
const rawEl = el;
el = function (tag, attrs, ...kids) {
  const editable = (attrs && attrs.contenteditable === "true") || tag === "textarea" || tag === "input";
  if (editable) return rawEl(tag, attrs, ...kids);
  const a = attrs && attrs.title ? { ...attrs, title: pv(attrs.title) } : attrs;
  return rawEl(tag, a, ...kids.flat(Infinity).map(k => (typeof k === "string" || typeof k === "number") ? pv(String(k)) : k));
};
const SVG_NS = "http://www.w3.org/2000/svg";
function svg(tag, attrs, ...kids) {
  const e = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith("on")) e.addEventListener(k.slice(2), v); else e.setAttribute(k, v);
  }
  kids.flat(Infinity).forEach(k => k != null && e.append(k instanceof Node ? k : document.createTextNode(String(k))));
  return e;
}

// ------------------------------------------------------------ 올인원 드롭존
// 확장자 → 사람이 아는 이름(드롭존에 무엇이 들어왔는지 보여 주기)
const KIND_OF = [[/\.(xlsx|xlsm|xls|csv)$/i, "엑셀"], [/\.(hwpx|hwp)$/i, "한글"], [/\.pdf$/i, "PDF"], [/\.(docx|doc)$/i, "워드"],
  [/\.(pptx|ppt)$/i, "파워포인트"], [/\.(eml|msg)$/i, "메일"], [/\.(txt|md|log)$/i, "메모장"],
  [/\.(jpe?g|png|gif|bmp|tiff?|webp|heic)$/i, "사진"], [/\.(zip|7z|rar|egg|alz)$/i, "압축"], [/\.baton$/i, "바통"]];
const kindOf = name => (KIND_OF.find(([rx]) => rx.test(name)) || [0, "기타"])[1];
const fpath = f => f.webkitRelativePath || f._relPath || f.name;
function setupStart() {
  const src = () => ($("input[name=src]:checked") || {}).value || "";
  const label = { sample: "시연용 모의자료로", sample_messy: "뒤죽박죽 모의자료로", local: "PC 폴더 경로로", docsystem: "문서시스템 자료로" };
  const paintGo = () => {
    $("#create").textContent = pickedFiles.length ? `파일 ${pickedFiles.length}개로 인수인계 초안 만들기`
      : src() ? `${label[src()]} 인수인계 초안 만들기` : "인수인계 초안 만들기";
  };
  $$("input[name=src]").forEach(r => r.addEventListener("change", () => {
    $$(".src-box").forEach(b => b.hidden = b.id !== "src-" + r.value);
    paintGo();
  }));
  const paintPicked = () => {
    const box = $("#picked");
    box.innerHTML = "";
    $("#drop").classList.toggle("has", !!pickedFiles.length);
    $(".dz-title").textContent = pickedFiles.length ? `${pickedFiles.length}개 담음 · 더 놓으면 계속 쌓여요` : "여기에 몽땅 끌어다 놓기";
    if (pickedFiles.length) {
      const cnt = {};
      pickedFiles.forEach(f => { const k = kindOf(fpath(f)); cnt[k] = (cnt[k] || 0) + 1; });
      const mb = pickedFiles.reduce((n, f) => n + f.size, 0) / 1048576;
      box.append(el("div", { class: "dz-got" },
        el("b", {}, `${pickedFiles.length}개 담음`), el("span", { class: "muted small" }, ` · ${mb < 0.1 ? "0.1" : mb.toFixed(1)}MB`),
        Object.entries(cnt).map(([k, n]) => el("span", { class: "chip" }, `${k} ${n}`)),
        el("button", { class: "small ghost", onclick: () => { pickedFiles = []; paintPicked(); } }, "모두 비우기")),
        el("details", { class: "small" }, el("summary", {}, "담은 파일 보기"),
          el("ul", { class: "dz-list" }, pickedFiles.map((f, i) => el("li", {}, el("span", { class: "tag gray" }, kindOf(fpath(f))), " ", fpath(f),
            el("button", { class: "small ghost", title: "빼기", onclick: () => { pickedFiles.splice(i, 1); paintPicked(); } }, "✕"))))));
    }
    paintGo();
  };
  // 여러 번 끌어다 놓아도 쌓는다(같은 파일은 한 번만)
  const addFiles = files => {
    const seen = new Set(pickedFiles.map(f => fpath(f) + ":" + f.size));
    for (const f of files) {
      const k = fpath(f) + ":" + f.size;
      if (!seen.has(k)) { seen.add(k); pickedFiles.push(f); }
    }
    paintPicked();
    if (files.length) toast(`${files.length}개 파일을 담았습니다`);
  };
  const drop = $("#drop");
  const toStart = () => { if (goTab) goTab("start"); };
  drop.onclick = e => { if (e.target.id !== "pick-folder") $("#in-files").click(); };
  drop.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); $("#in-files").click(); } };
  $("#pick-folder").onclick = e => { e.preventDefault(); e.stopPropagation(); $("#in-folder").click(); };
  $("#in-folder").onchange = e => { addFiles([...e.target.files]); e.target.value = ""; toStart(); };
  $("#in-files").onchange = e => { addFiles([...e.target.files]); e.target.value = ""; toStart(); };
  // 드롭존뿐 아니라 시작 화면 어디에 놓아도 받는다(브라우저가 파일을 열어 버리지 않게)
  // 화면 어디에 놓아도 올인원 드롭존이 받는다(어느 탭에 있든)
  let depth = 0;
  const isFiles = e => [...((e.dataTransfer && e.dataTransfer.types) || [])].includes("Files");
  window.addEventListener("dragenter", e => { if (isFiles(e)) { depth++; drop.classList.add("over"); document.body.classList.add("dragging"); } });
  window.addEventListener("dragleave", () => { if (--depth <= 0) { depth = 0; drop.classList.remove("over"); document.body.classList.remove("dragging"); } });
  window.addEventListener("dragover", e => e.preventDefault());
  window.addEventListener("drop", async e => {
    e.preventDefault(); depth = 0; drop.classList.remove("over"); document.body.classList.remove("dragging");
    if (!isFiles(e)) return;
    addFiles(await filesFromDrop(e)); toStart();
    window.scrollTo({ top: $("#tab-start").offsetTop - 70, behavior: "smooth" });
  });
  $("#start-date").value = new Date().toISOString().slice(0, 10);
  $("#create").onclick = async () => {
    const kind = pickedFiles.length ? "upload" : src();
    if (!kind) return toast("자료를 드롭존에 끌어다 놓아 주세요", true);
    const source = { kind };
    if (kind === "upload") {
      // 다른 곳에서 온 같은 이름 파일(예: 메모.txt 두 개)이 서로 덮어쓰지 않게 이름을 나눈다
      const used = new Set();
      source.files = (await readFiles(pickedFiles)).map(f => {
        let p = f.path, n = 2;
        while (used.has(p)) p = f.path.replace(/(\.[^./]+)?$/, m => ` (${n})${m}`), n++;
        used.add(p);
        return { ...f, path: p };
      });
    }
    else if (kind === "local") source.path = $("#local-path").value.trim();
    else if (kind === "docsystem") { source.base_url = $("#ds-url").value.trim(); source.owner = $("#ds-owner").value.trim(); }
    const btn = $("#create");
    busy(btn, true, "자료를 읽고 분석하는 중");
    try {
      const h = await api("POST", "/api/handovers", { source, title: $("#title").value, start: $("#start-date").value });
      toast("초안을 만들었습니다");
      pickedFiles = []; paintPicked();
      await refreshList(h.id);
      goTab("draft");
    } finally { busy(btn, false); paintGo(); }
  };
  paintGo();
}

async function refreshList(selectId) {
  const { handovers } = await api("GET", "/api/handovers");
  const sel = $("#cur-h");
  sel.innerHTML = "";
  if (!handovers.length) sel.append(el("option", { value: "" }, "인수인계 건 없음"));
  handovers.forEach(h => sel.append(el("option", { value: h.id }, `${h.title}`)));
  const list = $("#h-list");
  list.innerHTML = "";
  if (!handovers.length) list.append(el("div", { class: "empty" }, "아직 없습니다. 왼쪽 드롭존에 자료를 몽땅 끌어다 놓아 시작하세요. 처음이면 '파일 없이 시작하기 → 시연용 모의자료'를 골라 보세요."));
  else list.append(el("table", { class: "t" },
    el("tr", {}, el("th", {}, "제목"), el("th", {}, "상태"), el("th", {}, "만든 시각"), el("th", {}, "")),
    handovers.map(h => el("tr", {},
      el("td", {}, el("a", { href: "#", onclick: e => { e.preventDefault(); select(h.id); goTab("draft"); } }, h.title)),
      el("td", {}, el("span", { class: "tag " + (h.confirmed ? "ok" : "warn") }, h.confirmed ? "전임자 확인 완료" : "검토 중")),
      el("td", { class: "small muted" }, h.created),
      el("td", {}, el("button", { class: "small danger", onclick: async () => {
        if (!confirm("이 인수인계 건(작업 사본)을 지울까요? 원본 자료는 지워지지 않습니다.")) return;
        await api("DELETE", `/api/handovers/${h.id}`); if (H && H.id === h.id) H = null; refreshList();
      } }, "삭제"))))));
  const id = selectId || (H && H.id) || (handovers[0] && handovers[0].id);
  if (id) await select(id); else { H = null; renderAll(); }
}

async function select(id) {
  H = await api("GET", `/api/handovers/${id}`);
  $("#cur-h").value = id;
  if (H.radar && H.radar.start) $("#m-start").value = H.radar.start;
  renderAll();
}
const predName = () => "전임자";
function renderAvatarHead() {}

function needH(sec) {
  if (H) return false;
  sec.innerHTML = "";
  sec.append(el("div", { class: "empty" }, "먼저 올인원 드롭존에서 인수인계 건을 만들거나 선택하세요."));
  return true;
}

// ------------------------------------------------------------ 초안·검토
function cellValue(it, c) {
  const v = it[c.k];
  if (c.t === "months") return it.recurring === "매월" ? "매월" : (v || []).join(", ");
  if (c.t === "list") return (v || []).join(", ");
  return v ?? "";
}
function parseCell(c, text) {
  text = text.trim();
  if (c.t === "months") return text === "매월" ? [1,2,3,4,5,6,7,8,9,10,11,12] : text.split(/[,\s]+/).map(Number).filter(n => n >= 1 && n <= 12);
  if (c.t === "list") return text ? text.split(/\s*,\s*/) : [];
  if (c.t === "num") return text ? Number(text) || null : null;
  return text;
}

function renderSection(key) {
  const S = SEC[key];
  const items = H.draft.sections[key];
  const wrap = el("div", { class: "panel" });
  wrap.append(el("div", { class: "row" }, el("h2", { style: { flex: 1, margin: 0 } }, `${S.title} (${items.filter(i => i.status !== "삭제").length})`),
    el("button", { class: "small", onclick: () => addItem(key) }, "+ 직접 추가")));
  const tbl = el("table", { class: "t", style: { marginTop: "8px" } });
  tbl.append(el("tr", {}, S.cols.map(c => el("th", {}, c.l)), el("th", {}, "근거(출처)"), el("th", {}, "검토"), el("th", {}, "")));
  for (const it of items) {
    const tr = el("tr", { style: it.status === "삭제" ? { opacity: .45, textDecoration: "line-through" } : {} });
    for (const c of S.cols) {
      const td = el("td", { contenteditable: H.review.confirmed ? null : "true" }, String(cellValue(it, c)));
      const show = () => { td.textContent = pv(String(cellValue(it, c))); };
      if (!H.review.confirmed) {
        show();
        td.addEventListener("focus", () => { td.textContent = String(cellValue(it, c)); });  // 고칠 때만 원래 글자
      }
      td.addEventListener("blur", async () => {
        const nv = parseCell(c, td.textContent);
        if (JSON.stringify(nv) === JSON.stringify(it[c.k] ?? (c.t === "list" || c.t === "months" ? [] : ""))) return show();
        const upd = { id: it.id, [c.k]: nv };
        if (c.t === "months") upd.recurring = td.textContent.trim() === "매월" ? "매월" : (it.recurring === "매월" ? "" : it.recurring);
        const saved = await api("POST", `/api/handovers/${H.id}/item`, { section: key, item: upd });
        Object.assign(it, saved); tr.querySelector(".st").replaceWith(statusTag(it));
        show();
      });
      tr.append(td);
    }
    tr.append(el("td", {}, citeChips(it.sources, CTX()), (it.origin || []).includes("개인메모") ? el("span", { class: "tag warn", title: "개인메모에서 나온 내용" }, "메모") : null,
      carriedTag(it),
      key === "schedule" ? schedTags(it) : null));
    tr.append(el("td", {}, statusTag(it)));
    tr.append(el("td", { style: { whiteSpace: "nowrap" } },
      H.review.confirmed ? null : el("button", { class: "small", title: "내용이 맞음", onclick: async () => {
        Object.assign(it, await api("POST", `/api/handovers/${H.id}/item`, { section: key, item: { id: it.id }, confirm: true }));
        tr.querySelector(".st").replaceWith(statusTag(it));
      } }, "확인"),
      H.review.confirmed ? null : el("button", { class: "small danger", onclick: async () => {
        Object.assign(it, await api("POST", `/api/handovers/${H.id}/item/delete`, { section: key, id: it.id })); renderDraft();
      } }, it.status === "삭제" ? "되살리기" : "삭제")));
    tbl.append(tr);
  }
  if (!items.length) tbl.append(el("tr", {}, el("td", { colspan: S.cols.length + 3, class: "muted" }, "자료에서 찾지 못했습니다. '직접 추가'로 보완하세요.")));
  wrap.append(tbl);
  return wrap;
}
// 일정 시기 표시: 완료·지난 일·기한 지남, 대략적인 시기, 다른 해
const TIMING_TAG = { "완료": "gray", "지난 일": "gray", "기한 지남": "bad" };
function baseYear() { return Number(((H && H.draft.today) || new Date().toISOString()).slice(0, 4)); }
function schedTags(it) {
  return [it.timing ? el("span", { class: "tag " + (TIMING_TAG[it.timing] || "gray"), title: "오늘 기준" }, it.timing) : null,
    it.approx ? el("span", { class: "tag info", title: it.when || "" }, "시기 대략") : null,
    it.year && it.year !== baseYear() ? el("span", { class: "tag info" }, `${it.year}년`) : null];
}
function carriedTag(it) {
  const c = it.carried;
  if (!c) return null;
  return el("span", { class: "tag " + (c.unverified ? "warn" : "ok"), title: `${c.gen}대 담당자 ${c.name}의 인수인계서(바통 파일)에서 이어받은 항목`
    + (c.unverified ? " · 그 담당자가 확인하지 않고 넘긴 초안" : "") }, `이어받음 · ${c.gen}대 ${c.name}` + (c.unverified ? " · 미확인" : ""));
}
function statusTag(it) { return el("span", { class: "st tag " + (STATUS_TAG[it.status] || "gray") }, it.status || "초안"); }

async function addItem(key) {
  const S = SEC[key];
  const inputs = {};
  const body = el("div", {}, S.cols.map(c => [el("label", { class: "f" }, c.l + (c.t === "months" ? " (예: 3 또는 3, 9 또는 매월)" : c.t === "list" ? " (쉼표로 구분)" : "")),
    inputs[c.k] = el("input", { class: "wide" })]));
  const m = modal(`${S.title} 직접 추가`, body, el("div", { class: "row", style: { marginTop: "10px" } }, el("button", { class: "primary", onclick: async () => {
    const item = {};
    S.cols.forEach(c => item[c.k] = parseCell(c, inputs[c.k].value));
    if (key === "schedule" && inputs.months.value.trim() === "매월") item.recurring = "매월";
    await api("POST", `/api/handovers/${H.id}/item`, { section: key, item });
    m.remove(); H = await api("GET", `/api/handovers/${H.id}`); renderDraft();
  } }, "추가")));
}

function renderDraft() {
  const sec = $("#tab-draft");
  if (needH(sec)) return;
  sec.innerHTML = "";
  const d = H.draft, rv = H.review || {};
  const cnt = k => d.sections[k].filter(i => i.status !== "삭제").length;
  const open = d.flags.filter(f => !f.resolved).length;
  sec.append(el("div", { class: "panel" },
    el("div", { class: "row" },
      el("h2", { style: { flex: 1, margin: 0 } }, H.title),
      rv.confirmed ? el("span", { class: "done-badge", title: rv.at || "" }, `전임자 확인 완료 · ${rv.by || ""} · ${(rv.at || "").slice(0, 10).replace(/-/g, ".")}`)
        : el("span", { class: "tag warn" }, "검토 중(초안)")),
    el("p", { class: "small muted" }, `인계: 전임자 → 후임자 (사람 이름 비식별) · 작성 방식: ${d.engine} · 분석 ${d.seconds}초 · 자료 ${d.docs.length}건`),
    el("div", { class: "stats" },
      ...[["담당업무", cnt("rnr")], ["일정", cnt("schedule")], ["연락처", cnt("contacts")], ["현안", cnt("issues")], ["빈틈", open], ["AI 분신 질문", H.asked || 0]]
        .map(([a, b]) => el("div", { class: "stat" }, el("b", {}, b), a))),
    el("h3", {}, "업무 개요"), el("p", { style: { whiteSpace: "pre-wrap" } }, d.overview),
    d.filtered && d.filtered.length ? el("details", {}, el("summary", { class: "small" }, `모델 결과 중 원문과 맞지 않아 버린 항목 ${d.filtered.reduce((n, f) => n + (f.count || 1), 0)}건`),
      el("ul", { class: "small" }, d.filtered.map(f => el("li", {}, `[${SEC[f.section] ? SEC[f.section].title : f.section}] ${f.label || "(이름 없음)"}${(f.count || 1) > 1 ? ` (${f.count}회)` : ""} — ${f.reason} `, citeChips(f.sources || [], CTX()))))) : null,
    d.llm_errors && d.llm_errors.length ? el("details", {}, el("summary", { class: "small" }, `모델 처리 경고 ${d.llm_errors.length}건 (해당 부분은 규칙 기반 결과 사용)`), el("pre", { class: "doc small" }, d.llm_errors.join("\n"))) : null,
    H.skipped && H.skipped.length ? el("details", {}, el("summary", { class: "small" }, `읽지 못한 파일 ${H.skipped.length}개`), el("ul", { class: "small" }, H.skipped.map(s => el("li", {}, `${s.file}: ${s.reason}`)))) : null,
    el("div", { class: "row", style: { marginTop: "10px" } },
      el("span", { class: "small muted" }, "인수인계서 내려받기:"), exportButtons(`/api/handovers/${H.id}/export/handover`),
      el("span", { class: "spacer", style: { flex: 1 } }),
      rv.confirmed
        ? el("button", { onclick: async () => { H = await api("POST", `/api/handovers/${H.id}/review`, { confirmed: false }); renderAll(); } }, "확인 취소(다시 수정)")
        : el("button", { class: "primary", onclick: async () => {
            const by = "전임자";
            if (open && !confirm(`빈틈 스캐너에 아직 메우지 않은 빈틈이 ${open}건 있습니다. 그래도 확인 완료할까요?`)) return;
            H = await api("POST", `/api/handovers/${H.id}/review`, { confirmed: true, by }); renderAll(); toast("전임자 확인을 완료했습니다. 후임자가 바로 활용할 수 있습니다.");
          } }, "전임자 확인 완료"))));
  // 인수인계 순서도는 요약 카드 바로 아래(서버에서 단계 상태를 받아 채움)
  const flowBox = el("div", { class: "panel flow-panel" }, el("span", { class: "spinner" }));
  sec.children[0].after(flowBox);
  renderFlow(flowBox);
  if ((d.coverage || []).length) flowBox.after(renderCoverage());
  sec.append(renderRelay());
  if (!rv.confirmed) sec.append(el("p", { class: "small muted" }, "표의 내용을 눌러 바로 고칠 수 있습니다. 맞는 항목은 '확인', 틀린 항목은 '삭제'를 누르세요. 근거 칩을 누르면 원문 위치가 보입니다."));
  ["issues", "schedule", "contacts", "rnr"].forEach(k => sec.append(renderSection(k)));
  const memo = el("textarea", { placeholder: "후임자에게 남길 말을 적어 주세요 (협업 상대별 일하는 요령은 휴먼 네트워크의 노하우 카드로 남기면 좋습니다)" });
  sec.append(el("div", { class: "panel" }, el("h2", {}, "전임자 메모"),
    el("ul", {}, (rv.comments || []).map(c => el("li", {}, el("span", { class: "small muted" }, c.at + " "), c.text))),
    memo, el("button", { style: { marginTop: "6px" }, onclick: async () => {
      if (!memo.value.trim()) return;
      H = await api("POST", `/api/handovers/${H.id}/review`, { comment: memo.value.trim() }); renderDraft();
    } }, "메모 저장")));
}

// 인수인계 충실도 점검: 기본 7개 영역마다 자료가 충분한지, 모자란 영역은 전임자에게 한 번에 묻기
function renderCoverage() {
  const cov = H.draft.coverage || [];
  const ok = cov.filter(c => c.level === "충분").length;
  const gaps = cov.filter(c => c.level !== "충분");
  const lv = { "충분": "ok", "부족": "warn", "없음": "bad" };
  return el("div", { class: "panel" },
    el("div", { class: "row" }, el("h2", { style: { flex: 1, margin: 0 } }, "인수인계 충실도 점검"),
      el("span", { class: "tag " + (ok === cov.length ? "ok" : "warn") }, `${ok}/${cov.length} 영역 충분`)),
    el("p", { class: "small muted", style: { margin: "6px 0 12px" } },
      "자료를 대충 모아 넣어도 후임자가 일할 수 있도록, 인수인계서에 꼭 있어야 할 7개 영역을 점검합니다. 모자란 영역은 인계 면담에서 물을 질문지를 만들어 둡니다."),
    el("div", { class: "cov" }, cov.map(c => el("div", { class: "cov-card " + lv[c.level], title: c.question || "자료가 충분합니다" },
      el("div", { class: "row", style: { gap: "6px" } }, el("b", {}, c.name), el("span", { class: "tag " + lv[c.level] }, c.level)),
      el("span", { class: "small muted" }, c.note),
      c.sources && c.sources.length ? el("div", {}, citeChips(c.sources.slice(0, 1), CTX())) : null))),
    gaps.length ? el("div", { class: "interview" },
      el("div", { class: "row" }, el("b", { style: { flex: 1 } }, `인계 면담 질문지 (${gaps.length}개)`),
        el("button", { class: "small", onclick: () => {
          const text = gaps.map((c, i) => `${i + 1}. [${c.name}] ${c.question}`).join("\n");
          (navigator.clipboard ? navigator.clipboard.writeText(text) : Promise.reject()).then(() => toast("질문지를 복사했습니다"), () => modal("인계 면담 질문지", el("pre", { class: "doc" }, text)));
        } }, "질문지 복사")),
      el("ol", { class: "small" }, gaps.map(c => el("li", {}, el("b", {}, `[${c.name}] `), c.question))),
      el("p", { class: "small muted", style: { margin: 0 } }, "면담에서 들은 답은 초안 표에 바로 고쳐 넣거나, 협업 상대별 요령이면 노하우 카드로 남기세요. AI 분신은 그 내용을 근거로 답합니다.")) : null);
}

// 인수인계 순서도: 단계마다 완료·진행 중·예정과 근거 숫자, 누르면 해당 화면으로
async function renderFlow(box) {
  const f = await api("GET", `/api/handovers/${H.id}/flow`);
  const cls = { "완료": "done", "진행 중": "now", "예정": "todo" };
  const steps = [];
  f.steps.forEach((x, i) => {
    steps.push(el("button", { class: "fstep " + cls[x.status], title: x.next || x.detail, onclick: () => goTab(x.tab) },
      el("span", { class: "fno" }, x.status === "완료" ? "✓" : String(x.no)),
      el("b", {}, x.name), el("span", { class: "fwho" }, x.who), el("span", { class: "fdetail" }, x.detail)));
    if (i < f.steps.length - 1) steps.push(el("span", { class: "farrow" + (x.status === "완료" ? " done" : ""), "aria-hidden": "true" }));
  });
  box.innerHTML = "";
  box.append(
    el("div", { class: "row" }, el("h2", { style: { flex: 1, margin: 0 } }, "인수인계 순서도"),
      el("span", { class: "tag " + (f.current ? "info" : "ok") }, `${f.done}/${f.total} 단계 완료`)),
    el("div", { class: "flow" }, steps), flowArc(f, cls),
    el("div", { class: "flow-next" }, el("b", {}, f.current ? "지금 할 일 " : "완료 "), f.next));
}

// 순서도를 반원 트랙으로(브랜드 스킨의 넓은 화면에서만 보이고, 나머지는 위의 가로 순서도): 단계는 트랙 위 구간, 지금 단계에 바통
function flowArc(f, cls) {
  const W = 1000, Hh = 340, cx = 500, cy = 300, r = 240, n = f.steps.length;
  const pt = (rr, deg) => [cx + rr * Math.cos(deg * Math.PI / 180), cy + rr * Math.sin(deg * Math.PI / 180)];
  const arc = rr => `M${cx - rr} ${cy}A${rr} ${rr} 0 0 1 ${cx + rr} ${cy}`;
  const cur = f.steps.findIndex(x => x.status === "진행 중");
  const reach = (cur >= 0 ? cur : f.done ? n - 1 : 0) / Math.max(n - 1, 1);
  const g = svg("svg", { viewBox: `0 0 ${W} ${Hh}`, class: "flow-arc", role: "group", "aria-label": `인수인계 순서도, ${f.done}/${f.total} 단계 완료` },
    svg("defs", {}, svg("linearGradient", { id: "fa-g", x1: "0", y1: "0", x2: "1", y2: "0" },
      svg("stop", { offset: "0", class: "fa-s0" }), svg("stop", { offset: "1", class: "fa-s1" }))),
    svg("path", { class: "fa-band", d: arc(r + 22) + `L${cx + r - 22} ${cy}A${r - 22} ${r - 22} 0 0 0 ${cx - r + 22} ${cy}Z` }),
    svg("path", { class: "fa-edge", d: arc(r + 22) }), svg("path", { class: "fa-edge", d: arc(r - 22) }), svg("path", { class: "fa-mid", d: arc(r) }),
    svg("path", { class: "fa-prog", d: arc(r), pathLength: 1, "stroke-dasharray": `${reach} 1` }));
  f.steps.forEach((x, i) => {
    const deg = 180 + (180 * i) / Math.max(n - 1, 1), [px, py] = pt(r, deg), [lx, ly] = pt(r + 56, deg);
    const anchor = Math.abs(lx - cx) < 60 ? "middle" : lx < cx ? "end" : "start";
    const st = cls[x.status], go = () => goTab(x.tab);
    g.append(svg("g", { class: "fa-step " + st, tabindex: 0, role: "button", "aria-label": `${i + 1}단계 ${x.name} · ${x.status}`, onclick: go,
        onkeydown: e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } } },
      svg("title", {}, x.next || x.detail),
      svg("circle", { cx: px, cy: py, r: st === "now" ? 17 : 14 }),
      svg("text", { x: px, y: py + 5, "text-anchor": "middle", class: "fa-no" }, st === "done" ? "✓" : String(x.no)),
      svg("text", { x: lx, y: ly, "text-anchor": anchor, class: "fa-name" }, x.name),
      svg("text", { x: lx, y: ly + 18, "text-anchor": anchor, class: "fa-sub" }, `${x.who} · ${x.detail}`)));
  });
  const [bx, by] = pt(r, 180 + 180 * reach);
  g.append(svg("rect", { class: "fa-baton", x: bx - 17, y: by - 5.5, width: 34, height: 11, rx: 5.5, transform: `rotate(${90 + 180 * reach} ${bx} ${by}) translate(0 -27)` }),
    svg("text", { x: cx, y: cy - 62, "text-anchor": "middle", class: "fa-big" }, `${f.done} / ${f.total}`),
    svg("text", { x: cx, y: cy - 32, "text-anchor": "middle", class: "fa-sub" }, f.current ? `구간 완주 · 지금 ${f.steps[cur] ? f.steps[cur].name : ""}` : "모든 구간 완주"));
  return g;
}

// 지식 릴레이: 업무 계보와 바통 파일(.baton) 저장
function renderRelay() {
  const d = H.draft, lin = d.lineage || [];
  const gen = lin.length + 1;
  const card = (cls, g, name, sub) => el("span", { class: "gen " + cls }, el("b", {}, `${g}대 ${name}`), sub);
  const chain = [];
  lin.forEach((x, i) => { chain.push(card("", i + 1, x.name, x.date || "")); chain.push(el("span", { class: "arrow" }, "→")); });
  chain.push(card("now", gen, "담당자(전임자)", "지금 넘기는 중"));
  chain.push(el("span", { class: "arrow" }, "→"));
  chain.push(card("next", gen + 1, "담당자(후임자)", "이어받을 사람"));
  const carried = ["rnr", "schedule", "contacts", "issues"].reduce((n, k) => n + d.sections[k].filter(i => i.carried && i.status !== "삭제").length, 0);
  return el("div", { class: "panel" },
    el("div", { class: "row" }, el("h2", { style: { flex: 1, margin: 0 } }, "지식 릴레이 · 업무 계보"),
      el("button", { class: "primary", onclick: async () => { await download(`/api/handovers/${H.id}/baton`, {}); renderDraft(); } }, "바통 파일로 저장(.baton)")),
    el("div", { class: "relay" }, chain),
    el("p", { class: "small muted" }, lin.length
      ? `${lin.length}대까지의 인수인계서를 이어받아 ${carried}개 항목, 질의응답 ${(d.carried_interview || []).length}건, 메모 ${(d.carried_notes || []).length}건을 넘겨받았습니다. `
      : "이 업무의 첫 바통입니다. ",
      `검토를 마치고 바통 파일로 저장해 후임자에게 넘기면, 다음 인수인계 때 '올인원 드롭존'에 함께 놓기만 해도 ${gen}대까지의 업무 지식(항목·근거·노하우·메모)이 그대로 이어집니다. 바통 파일에도 사람 이름은 들어가지 않습니다.`,
      rv_confirmed() ? "" : " (아직 전임자 확인 전이라 초안 상태로 저장됩니다)"),
    (d.carried_interview || []).length ? el("details", {}, el("summary", { class: "small" }, `앞 세대가 남긴 질의응답 ${(d.carried_interview || []).length}건`),
      el("ul", { class: "small" }, d.carried_interview.map(x => el("li", {}, el("b", {}, x.q), ` → ${x.a} (${x.by})`)))) : null,
    (d.carried_notes || []).length ? el("details", {}, el("summary", { class: "small" }, `앞 세대가 남긴 메모 ${(d.carried_notes || []).length}건`),
      el("ul", { class: "small" }, d.carried_notes.map(x => el("li", {}, `${x.text} (${x.by})`)))) : null);
}
const rv_confirmed = () => !!(H.review && H.review.confirmed);

// ------------------------------------------------------------ 달력
const monthHead = m => el("h4", {}, `${m}월`);
function renderCalendar() {
  const sec = $("#tab-calendar");
  if (needH(sec)) return;
  sec.innerHTML = "";
  const items = H.draft.sections.schedule.filter(s => s.status !== "삭제");
  const monthly = items.filter(s => s.recurring === "매월");
  const nowM = new Date().getMonth() + 1;
  sec.append(el("div", { class: "panel" }, el("h2", {}, "연간 업무 달력"),
    el("p", { class: "small muted" }, "자료에서 찾은 시기별 할 일입니다. 항목을 누르면 근거를 볼 수 있습니다."),
    monthly.length ? el("div", {}, el("b", {}, "매월 반복: "), monthly.map(s => el("span", { class: "chip", onclick: () => showSource(s.sources[0], CTX()) }, (s.day ? s.day + "일 " : "") + s.task))) : null,
    icsBox()));
  const by = baseYear();
  const later = items.filter(s => s.recurring !== "매월" && !s.recurring && s.year && s.year > by);
  const cal = el("div", { class: "cal" });
  const itemEl = s => el("div", { class: "it", style: { cursor: "pointer", opacity: s.timing === "완료" || s.timing === "지난 일" ? .55 : 1 }, onclick: () => s.sources[0] && showSource(s.sources[0], CTX()) },
    el("b", {}, s.day ? `${s.day}일 ` : ""), s.task, (s.origin || []).every(o => o === "개인메모") ? el("span", { class: "tag warn" }, "메모") : null, schedTags(s));
  for (let m = 1; m <= 12; m++) {
    const its = items.filter(s => s.recurring !== "매월" && !later.includes(s) && (s.months || []).includes(m)).sort((a, b) => (a.day || 0) - (b.day || 0));
    cal.append(el("div", { class: "m" + (m === nowM ? " now" : "") }, monthHead(m),
      its.length ? its.map(itemEl) : el("div", { class: "small muted" }, "일정 없음")));
  }
  sec.append(cal);
  if (later.length) sec.append(el("div", { class: "panel" }, el("h3", {}, `${by + 1}년 이후 일정`),
    later.map(s => el("div", {}, el("b", {}, `${s.year}년 ${s.months[0]}월${s.day ? " " + s.day + "일" : ""} `), itemEl(s)))));
}

// 내 일정으로 내보내기(.ics): Outlook·그룹웨어 일정에 매월·매년 반복으로
function icsBox(startInput) {
  const start = startInput || el("input", { type: "date", value: new Date().toISOString().slice(0, 10) });
  return el("div", { class: "row", style: { marginTop: "10px", alignItems: "center", flexWrap: "wrap" } },
    startInput ? null : [el("label", { class: "small" }, "착임일(이날부터 일정 시작) "), start],
    el("button", { class: "primary", onclick: () => downloadIcs(start.value) }, "내 일정으로 내보내기(.ics)"),
    el("span", { class: "small muted" }, "Outlook: 파일 열기 → 가져오기 / 그룹웨어: 일정 → iCalendar 가져오기. 매월 업무는 매월, 정기 업무는 매년 반복으로 들어가고 알림이 붙습니다."));
}
async function downloadIcs(start) {
  const r = await fetch(`/api/handovers/${H.id}/calendar`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ start }) });
  if (!r.ok) { let m = "내보내기 실패"; try { m = (await r.json()).error || m; } catch (e) {} return toast(m, true); }
  const cd = r.headers.get("Content-Disposition") || "";
  const mm = cd.match(/filename\*=UTF-8''([^;]+)/);
  const name = mm ? decodeURIComponent(mm[1]) : "업무달력.ics";
  const a = el("a", { href: URL.createObjectURL(await r.blob()), download: name });
  document.body.append(a); a.click(); a.remove();
  const sk = Number(r.headers.get("X-Skipped") || 0);
  toast(`${name} 저장됨: 일정 ${r.headers.get("X-Events")}건` + (sk ? ` (이미 지난 일 등 ${sk}건 제외)` : ""));
}

// ------------------------------------------------------------ 빈틈 스캐너
const FLAG_LABEL = { "충돌": "자료끼리 다름(충돌)", "불확실": "애매한 내용(불확실)", "누락": "빠진 정보(누락)", "기한 지남": "기한 지남" };
function renderFlags() {
  const sec = $("#tab-flags");
  if (needH(sec)) return;
  sec.innerHTML = "";
  const flags = H.draft.flags;
  const types = { "충돌": "bad", "기한 지남": "bad", "불확실": "warn", "누락": "info" };
  const panel = el("div", { class: "panel" }, el("h2", {}, `빈틈 스캐너 · 누락·충돌·불확실 표시 (${flags.filter(f => !f.resolved).length}건 남음)`),
    el("p", { class: "small muted" }, "인수인계 자료의 빈틈을 찾아 모았습니다. 그대로 믿으면 위험하니 떠나기 전에 메워 주세요. 자료끼리 다름: 문서마다 날짜·번호가 다름 · 기한 지남: 할 일인데 오늘 기준으로 날짜가 지남 · 애매한 내용: '아마', '미정' 같은 표현이거나 개인메모에만 있음 · 빠진 정보: 연락처·기한·다음 할 일 등이 없음."));
  for (const t of ["충돌", "기한 지남", "불확실", "누락"]) {
    const fs = flags.filter(f => f.type === t);
    if (!fs.length) continue;
    panel.append(el("h3", {}, el("span", { class: "tag " + types[t] }, FLAG_LABEL[t]), ` ${fs.length}건`));
    for (const f of fs) {
      const note = el("input", { placeholder: "처리 내용 (예: 업체 확인 결과 11/3 착공)", value: f.note || "", style: { flex: 1 } });
      panel.append(el("div", { class: "panel", style: { padding: "10px", opacity: f.resolved ? .6 : 1 } },
        el("div", {}, f.message), el("div", { style: { margin: "4px 0" } }, citeChips(f.sources, CTX())),
        el("div", { class: "row" }, note,
          el("button", { class: "small" + (f.resolved ? "" : " primary"), onclick: async () => {
            Object.assign(f, await api("POST", `/api/handovers/${H.id}/flag`, { id: f.id, resolved: !f.resolved, note: note.value }));
            renderFlags();
          } }, f.resolved ? "다시 열기" : "처리 완료"))));
    }
  }
  if (!flags.length) panel.append(el("div", { class: "empty" }, "찾은 빈틈이 없습니다."));
  sec.append(panel);
}

// ------------------------------------------------------------ 자료 목록
function renderDocs() {
  const sec = $("#tab-docs");
  if (needH(sec)) return;
  sec.innerHTML = "";
  const kinds = { "이전 인수인계서": "ok", "공식문서": "ok", "메일": "info", "개인메모": "warn", "사진": "info", "참고자료": "gray", "읽기 실패": "bad" };
  // 뒤죽박죽 자료 정리 결과: 건너뛴 이유별로 묶어서 보여 준다
  const groups = {};
  (H.skipped || []).forEach(x => {
    const k = /같은 내용/.test(x.reason) ? "중복이라 뺌" : /빈 파일/.test(x.reason) ? "빈 파일" : /임시·시스템/.test(x.reason) ? "임시·시스템 파일"
      : /97~2003|heic|압축은 풀지|서식 파일/.test(x.reason) ? "형식 바꿔 다시 넣기" : "읽지 못함";
    (groups[k] = groups[k] || []).push(x);
  });
  const photos = H.draft.docs.filter(x => x.meta && (x.meta.image || x.meta.scanned_pages));
  sec.append(el("div", { class: "panel" }, el("h2", {}, `인계 자료 (${H.draft.docs.length}건)`),
    el("p", { class: "small muted" }, `가져온 곳: ${H.source.kind} ${H.source.path || ""} · 원본은 읽기만 했고 수정하지 않았습니다.`),
    (H.skipped || []).length || photos.length ? el("div", { class: "cov", style: { marginBottom: "14px" } },
      el("div", { class: "cov-card ok" }, el("b", {}, "읽은 자료"), el("span", { class: "small muted" }, `${H.draft.docs.filter(x => !x.error).length}개`)),
      photos.length ? el("div", { class: "cov-card " + (photos.some(x => x.meta.ocr_error) ? "warn" : "ok") }, el("b", {}, "사진·스캔본"),
        el("span", { class: "small muted" }, `${photos.length}개 · 글자 읽음 ${photos.filter(x => !x.meta.ocr_error).length}개`)) : null,
      Object.entries(groups).map(([k, xs]) => el("details", { class: "cov-card " + (k === "읽지 못함" || k === "형식 바꿔 다시 넣기" ? "warn" : "gray") },
        el("summary", {}, el("b", {}, k), ` ${xs.length}개`),
        el("ul", { class: "small", style: { margin: "6px 0 0", paddingLeft: "16px" } }, xs.map(x => el("li", {}, `${x.file}: ${x.reason}`)))))) : null,
    el("table", { class: "t" }, el("tr", {}, el("th", {}, "파일"), el("th", {}, "구분"), el("th", {}, "형식"), el("th", {}, "요약"), el("th", {}, "")),
      H.draft.docs.map(d => el("tr", {}, el("td", {}, d.relpath), el("td", {}, el("span", { class: "tag " + (kinds[d.kind] || "gray") }, d.kind)),
        el("td", {}, d.ext), el("td", { class: "small" }, d.summary,
          d.meta && (d.meta.image || d.meta.scanned_pages) ? el("div", { class: "small muted" },
            d.meta.ocr_error ? `글자 못 읽음: ${d.meta.ocr_error}` : `글자 인식: ${d.meta.ocr_engine}` + (d.meta.taken ? ` · 찍은 날 ${d.meta.taken}` : "")) : null),
        el("td", {}, el("button", { class: "small", onclick: async () => {
          const r = await api("GET", `/api/handovers/${H.id}/document?file=${encodeURIComponent(d.relpath)}`);
          modal(d.relpath, el("pre", { class: "doc" }, r.blocks.map(b => `[${b.loc}] ${b.text}`).join("\n")));
        } }, "내용 보기")))))));
}

// ------------------------------------------------------------ 질문
function setupAsk() {
  const send = async (qtext) => {
    if (!H) return toast("먼저 인수인계 건을 선택하세요", true);
    const q = (qtext || $("#q").value).trim();
    if (!q) return;
    $("#q").value = "";
    const chat = $("#chat");
    chat.append(el("div", { class: "msg me" }, q));
    const bot = (...kids) => el("div", { class: "av-row" }, el("span", { class: "avatar", title: "실제 본인이 아닌, 자료로만 답하는 AI 분신" }, "AI"),
      el("div", { class: "msg bot", style: { width: "auto", flex: 1 } }, ...kids));
    const pending = bot(el("span", { class: "spinner" }), " 자료를 찾는 중");
    chat.append(pending);
    try {
      const r = await api("POST", `/api/handovers/${H.id}/ask`, { q });
      const who = r.who || [];
      const pics = r.photos || [];
      pending.replaceWith(bot(el("div", { style: { whiteSpace: "pre-wrap" } }, r.answer),
        pics.length ? el("div", { class: "ask-photos" }, el("div", { class: "small muted" }, `관련 사진 ${pics.length}장`),
          el("div", { class: "ph-row" }, pics.map(p => photoCard(p, true))),
          el("button", { class: "small", onclick: () => { goTab("photos"); renderPhotos(q); } }, "포토 아카이브에서 더 보기")) : null,
        r.sources && r.sources.length ? el("div", { class: "small muted", style: { marginTop: "6px" } }, `근거 (${r.mode}): `, citeChips(r.sources, CTX())) : null,
        !r.found ? el("div", { class: "who-box" },
          el("b", {}, who.length ? "이 협업 상대에게 물어보세요" : "인계 자료에 없는 내용입니다"),
          who.length ? el("ul", {}, who.map(w => el("li", {}, el("b", {}, w.label), ` · ${w.group}`, w.phone ? ` · ${w.phone}` : "", w.why ? el("span", { class: "muted" }, ` — ${w.why}`) : null)))
            : el("p", { class: "small muted", style: { margin: "4px 0 0" } }, "팀장·업무 대행자에게 확인한 뒤, 답을 초안 표나 노하우 카드에 남겨 두면 다음 질문부터 AI 분신이 답합니다."),
          el("button", { class: "small", style: { marginTop: "6px" }, onclick: () => goTab("network") }, "휴먼 네트워크에서 보기")) : null));
    } catch (e) { pending.remove(); }
  };
  $("#ask").onclick = () => send();
  $("#q").addEventListener("keydown", e => { if (e.key === "Enter") send(); });
  ["3월에 할 일이 뭐야?", "냉난방기 공사 누구랑 협의해?", "수질검사 부적합 나오면 어떻게 해?", "급한 현안 알려줘", "체육센터 대관료 감면 기준은?"].forEach(x =>
    $("#q-examples").append(el("span", { class: "chip", onclick: () => send(x) }, x)));
}

// ------------------------------------------------------------ 매뉴얼
function setupManual() {
  $("#m-start").value = new Date().toISOString().slice(0, 10);
  $("#m-make").onclick = async () => {
    if (!H) return toast("먼저 인수인계 건을 선택하세요", true);
    const body = { start: $("#m-start").value, career: $("#m-career").value };
    const m = await api("POST", `/api/handovers/${H.id}/manual`, body);
    const out = $("#m-out");
    out.innerHTML = "";
    const p = el("div", { class: "panel" }, el("div", { class: "row" }, el("h2", { style: { flex: 1, margin: 0 } }, "후임자 업무매뉴얼"), exportButtons(`/api/handovers/${H.id}/export/manual`, () => body),
        el("button", { onclick: () => downloadIcs(body.start) }, "내 일정(.ics)")),
      H.review.confirmed ? null : el("p", { class: "tag warn" }, "전임자 확인 전 초안 기준입니다"),
      radarCards(m.radar),
      el("h3", {}, "착임 후 순서도"),
      el("div", { class: "flow" }, m.flow.flatMap((x, i) => [
        el("div", { class: "fstep now" }, el("span", { class: "fno" }, String(i + 1)), el("b", {}, x.name), el("span", { class: "fwho" }, x.what), el("span", { class: "fdetail" }, x.detail)),
        i < m.flow.length - 1 ? el("span", { class: "farrow", "aria-hidden": "true" }) : null])),
      el("h3", {}, "1. 첫 주에 할 일"), el("ol", {}, m.week1.map(x => el("li", {}, x))),
      el("h3", {}, "2. 착임월부터 12개월 일정"),
      el("div", { class: "cal" }, m.months.map(mo => el("div", { class: "m" }, monthHead(mo.month),
        mo.items.length ? mo.items.map(s => el("div", { class: "it" }, (s.day ? s.day + "일 " : "") + s.task)) : el("div", { class: "small muted" }, "-")))),
      el("h3", {}, "3. 진행 중인 현안 (기한 순)"),
      el("table", { class: "t" }, el("tr", {}, el("th", {}, "현안"), el("th", {}, "남은 기간"), el("th", {}, "다음 할 일"), el("th", {}, "근거")),
        m.issues.map(i => el("tr", {}, el("td", {}, i.title),
          el("td", {}, i.days_left === null ? "기한 없음" : el("span", { class: "tag " + (i.days_left < 0 ? "bad" : i.days_left <= 30 ? "warn" : "gray") }, i.days_left < 0 ? "기한 지남" : `D-${i.days_left}`)),
          el("td", {}, i.next_action || ""), el("td", {}, citeChips(i.sources, CTX()))))),
      el("h3", {}, "4. 꼭 알아둘 협업 상대"),
      el("table", { class: "t" }, m.contacts.map(c => el("tr", {}, el("td", {}, c.name), el("td", {}, (c.phones || []).join(", ")), el("td", {}, (c.topics || []).slice(0, 2).join(", "))))),
      m.unresolved.length ? [el("h3", {}, "5. 빈틈 스캐너에서 아직 남은 빈틈"), el("ul", {}, m.unresolved.map(f => el("li", {}, `[${FLAG_LABEL[f.type] || f.type}] ${f.message}`)))] : null,
      m.terms.length ? [el("h3", {}, "6. 처음 보는 용어"), el("table", { class: "t" }, m.terms.map(([k, v]) => el("tr", {}, el("td", {}, el("b", {}, k)), el("td", {}, v))))] : null);
    out.append(p);
  };
}

// ------------------------------------------------------------ 이력
async function renderHistory() {
  const sec = $("#tab-history");
  if (needH(sec)) return;
  const { history } = await api("GET", `/api/handovers/${H.id}/history`);
  sec.innerHTML = "";
  sec.append(el("div", { class: "panel" }, el("h2", {}, "처리 이력"),
    el("table", { class: "t" }, el("tr", {}, el("th", {}, "시각"), el("th", {}, "작업"), el("th", {}, "내용")),
      history.slice().reverse().map(x => el("tr", {}, el("td", { class: "small" }, x.at), el("td", {}, x.action), el("td", { class: "small" }, x.detail))))));
}

// ------------------------------------------------------------ 표준 인수인계서(필수 5대 영역)
let FORM = null, FORM_SAVED = false, FORM_TIMER = null, FORM_HID = null;
const AREAS = [
  ["a1", "1. 담당 사무 개요 및 근거 법령"], ["a2", "2. 주요 추진 업무 및 연간 일정"], ["a3", "3. 예산 및 계약 현황"],
  ["a4", "4. 미결 과제 및 유의 사항"], ["a5", "5. 보관 문서 및 주요 연락처"]];
function formChanged() {
  $("#form-state").textContent = "고치는 중…";
  clearTimeout(FORM_TIMER);
  FORM_TIMER = setTimeout(saveForm, 1200);  // 잠시 손을 멈추면 저장
}
async function saveForm() {
  clearTimeout(FORM_TIMER);
  const r = await api("POST", `/api/handovers/${H.id}/form`, { form: FORM });
  FORM_SAVED = true;
  const st = $("#form-state"); if (st) st.textContent = `저장됨 · ${r.updated}`;
}
function input(obj, key, attrs = {}) {
  const tag = attrs.multiline ? "textarea" : "input";
  const e = el(tag, { class: "wide", placeholder: attrs.placeholder || "", style: attrs.multiline ? { minHeight: attrs.h || "70px" } : {} });
  e.value = obj[key] || "";
  e.addEventListener("input", () => { obj[key] = e.value; formChanged(); });
  return e;
}
function linesInput(obj, key, placeholder) {
  const e = el("textarea", { class: "wide", placeholder, style: { minHeight: "90px" } });
  e.value = (obj[key] || []).join("\n");
  e.addEventListener("input", () => { obj[key] = e.value.split("\n").map(x => x.trim()).filter(Boolean); formChanged(); });
  return e;
}
// 표 편집기: 칸마다 입력, 줄 추가·삭제
function editTable(rows, cols, onAdd) {
  const t = el("table", { class: "t form-t" });
  const draw = () => {
    t.innerHTML = "";
    t.append(el("tr", {}, cols.map(c => el("th", { style: c.w ? { width: c.w } : {} }, c.l)), el("th", { style: { width: "44px" } }, "")));
    rows.forEach((r, i) => t.append(el("tr", {}, cols.map(c => el("td", {}, input(r, c.k, { multiline: c.multi, h: "60px" }))),
      el("td", {}, el("button", { class: "small danger", title: "이 줄 삭제", onclick: () => { rows.splice(i, 1); formChanged(); draw(); } }, "✕")))));
  };
  draw();
  return el("div", { class: "form-t-wrap" }, t, el("button", { class: "small", style: { marginTop: "6px" }, onclick: () => {
    const r = {}; cols.forEach(c => r[c.k] = ""); (onAdd || (x => x))(r); rows.push(r); formChanged(); draw(); } }, "+ 줄 추가"));
}
async function renderForm(force) {
  const sec = $("#tab-form");
  if (needH(sec)) return;
  if (!FORM || FORM_HID !== H.id || force) {
    const r = await api("GET", `/api/handovers/${H.id}/form`);
    FORM = r.form; FORM_SAVED = r.saved; FORM_HID = H.id;
    FORM.__updated = r.updated;
  }
  const f = FORM;
  sec.innerHTML = "";
  sec.append(el("div", { class: "panel" },
    el("div", { class: "row" }, el("h2", { style: { flex: 1, margin: 0 } }, "표준 인수인계서"),
      el("span", { id: "form-state", class: "small muted" }, FORM_SAVED ? `저장됨 · ${f.__updated || ""}` : "자료로 채운 초안 · 아직 저장 전"),
      el("button", { class: "small", onclick: async () => {
        if (!confirm("자료에서 다시 채울까요? 지금까지 고친 내용은 사라집니다.")) return;
        const r = await api("POST", `/api/handovers/${H.id}/form/reset`, {}); FORM = r.form; FORM_SAVED = false; renderForm(); } }, "자료로 다시 채우기"),
      el("button", { class: "primary small", onclick: () => saveForm().then(() => toast("저장했습니다")) }, "저장")),
    el("p", { class: "small muted", style: { margin: "8px 0 12px" } },
      "공공기관 인수인계서 필수 5대 영역입니다. 넣은 자료로 먼저 채워 두었으니 확인·수정만 하면 됩니다. 고치면 자동으로 저장되고, 한글(HWPX)·워드 서식으로 내려받습니다."),
    el("div", { class: "row" }, el("span", { class: "small muted" }, "내려받기:"),
      ["hwpx", "docx", "md"].map(fmt => el("button", { onclick: async () => { await saveForm(); download(`/api/handovers/${H.id}/export/form?fmt=${fmt}`, {}); } },
        { hwpx: "한글(HWPX)", docx: "워드(DOCX)", md: "마크다운" }[fmt])))));
  // 기본 정보
  const m = f.meta;
  sec.append(el("div", { class: "panel" }, el("h3", { style: { marginTop: 0 } }, "기본 정보"),
    el("div", { class: "form-grid" }, [["org", "기관"], ["dept", "부서"], ["title", "업무명"], ["from", "인계자"], ["position", "인계자 직위"],
      ["to", "인수자"], ["date", "인계일"], ["witness", "입회자"]].map(([k, l]) => el("label", { class: "fld" }, el("span", {}, l), input(m, k))))));
  const area = (key, title, ...body) => el("details", { class: "panel form-area", open: true }, el("summary", {}, el("h2", { style: { display: "inline", margin: 0 } }, title)), ...body);
  // 1. 담당 사무 개요 및 근거 법령
  sec.append(area("a1", AREAS[0][1],
    el("h3", {}, "가. 담당 사무 개요"), input(f.a1, "summary", { multiline: true, h: "100px" }),
    el("h3", {}, "나. 담당 사무 (한 줄에 하나)"), linesInput(f.a1, "duties", "예) 예산 편성·집행 관리"),
    el("h3", {}, "다. 근거 법령"),
    el("p", { class: "small muted" }, "자료에 나온 법령 이름은 '자료 기재(확인 전)'로 넣어 두었습니다. '조문 찾기'로 국가법령정보센터의 현행 조문을 받아 오세요."),
    lawRows(f.a1.laws), lawSearchBox()));
  sec.append(area("a2", AREAS[1][1], editTable(f.a2.tasks, [{ k: "when", l: "시기", w: "18%" }, { k: "task", l: "업무" }, { k: "note", l: "비고", w: "22%" }])));
  sec.append(area("a3", AREAS[2][1],
    el("h3", {}, "가. 예산"), editTable(f.a3.budget, [{ k: "name", l: "사업명" }, { k: "amount", l: "예산액", w: "14%" }, { k: "spent", l: "집행액", w: "14%" }, { k: "note", l: "비고" }]),
    el("h3", {}, "나. 계약(조달)"), editTable(f.a3.contracts, [{ k: "name", l: "계약명" }, { k: "vendor", l: "업체", w: "16%" }, { k: "period", l: "계약기간", w: "18%" }, { k: "amount", l: "금액", w: "13%" }, { k: "status", l: "진행 상황", w: "15%" }])));
  sec.append(area("a4", AREAS[3][1],
    el("h3", {}, "가. 미결 과제"), editTable(f.a4.issues, [{ k: "title", l: "과제" }, { k: "state", l: "현재 상태", w: "13%" }, { k: "next", l: "다음 조치" }, { k: "due", l: "기한", w: "12%" }]),
    el("h3", {}, "나. 유의 사항 (한 줄에 하나)"), linesInput(f.a4, "cautions", "예) 착공일이 자료마다 다름: 업체 확인 필요")));
  sec.append(area("a5", AREAS[4][1],
    el("h3", {}, "가. 보관 문서"), editTable(f.a5.docs, [{ k: "name", l: "문서명" }, { k: "kind", l: "구분", w: "14%" }, { k: "where", l: "보관 위치" }]),
    el("h3", {}, "나. 주요 연락처"), editTable(f.a5.contacts, [{ k: "name", l: "이름", w: "11%" }, { k: "org", l: "소속" }, { k: "title", l: "직위", w: "10%" }, { k: "phone", l: "연락처" }, { k: "topic", l: "관련 업무" }])));
}
// 근거 법령 목록: 조문을 받아 온 것은 '법령정보 확인', 자료에서 이름만 찾은 것은 '조문 찾기'
function lawRows(laws) {
  const box = el("div", { class: "law-rows" });
  const draw = () => {
    box.innerHTML = "";
    if (!laws.length) box.append(el("div", { class: "empty" }, "아직 근거 법령이 없습니다. 아래에서 법령 이름으로 찾아 넣으세요."));
    laws.forEach((x, i) => box.append(el("div", { class: "law-row" },
      el("div", { class: "row", style: { gap: "6px" } },
        el("b", {}, x.law), x.article ? el("span", {}, x.article + (x.title ? `(${x.title})` : "")) : null,
        el("span", { class: "tag " + (x.verified ? "ok" : "warn") }, x.verified ? `법령정보 확인${x.enforce ? " · 시행 " + x.enforce : ""}` : "자료 기재(확인 전)"),
        x.found_in ? el("span", { class: "small muted" }, `자료: ${x.found_in}`) : null,
        el("span", { style: { flex: 1 } }),
        x.link ? el("a", { href: x.link, target: "_blank", rel: "noopener", class: "small" }, "law.go.kr에서 보기") : null,
        x.verified ? null : el("button", { class: "small", onclick: () => { const q = $("#law-q"); q.value = x.law; $("#law-art").value = x.article || ""; $("#law-go").click(); q.scrollIntoView({ block: "center" }); } }, "조문 찾기"),
        el("button", { class: "small danger", onclick: () => { laws.splice(i, 1); formChanged(); draw(); } }, "✕")),
      x.text ? el("div", { class: "law-text" }, x.text) : null)));
  };
  draw();
  box._redraw = draw;
  LAW_ROWS = box;
  return box;
}
let LAW_ROWS = null;
function lawSearchBox() {
  const wrap = el("div", { class: "law-box" });
  const status = el("div", { class: "small" });
  const results = el("div", {});
  const q = el("input", { id: "law-q", class: "wide", placeholder: "법령 이름 (예: 지방재정법, 공유재산 및 물품 관리법)" });
  const art = el("input", { id: "law-art", placeholder: "조문(선택) 예: 제20조", style: { width: "160px" } });
  const target = el("select", {}, [["law", "법령"], ["admrul", "행정규칙(훈령·예규·고시)"], ["ordin", "자치법규(조례·규칙)"]].map(([v, t]) => el("option", { value: v }, t)));
  const go = el("button", { id: "law-go", class: "primary small", onclick: async () => {
    results.innerHTML = ""; results.append(el("span", { class: "spinner" }));
    try {
      const r = await api("GET", `/api/law/search?q=${encodeURIComponent(q.value)}&target=${target.value}`);
      results.innerHTML = "";
      if (!r.items.length) return results.append(el("div", { class: "empty" }, "찾은 법령이 없습니다. 이름을 줄여서 찾아보세요."));
      results.append(el("div", { class: "law-list" }, r.items.map(it => el("button", { class: "law-item", onclick: () => loadArticles(it, results, art.value) },
        el("b", {}, it.name), el("span", { class: "small muted" }, [it.kind, it.dept, it.enforce && `시행 ${it.enforce}`].filter(Boolean).join(" · "))))));
    } catch (e) { results.innerHTML = ""; }
  } }, "찾기");
  q.addEventListener("keydown", e => { if (e.key === "Enter") go.click(); });
  wrap.append(el("h3", { style: { marginTop: "18px" } }, "법령 찾아 넣기 (국가법령정보센터)"), status,
    el("div", { class: "row", style: { marginTop: "6px" } }, target, q, art, go), results);
  api("GET", "/api/law/status").then(st => {
    if (st.configured) return status.append(el("span", { class: "tag ok" }, "법령 API 연결 설정됨"));
    const oc = el("input", { placeholder: "인증값(OC)", style: { width: "180px" } });
    status.append(el("div", { class: "law-setup" },
      el("span", {}, "법령 API 인증값(OC)이 필요합니다. "), el("a", { href: "https://open.law.go.kr", target: "_blank", rel: "noopener" }, "open.law.go.kr"),
      el("span", {}, "에서 'Open API 활용 신청' 후 받은 아이디(보통 신청 이메일의 @ 앞부분)를 넣으세요. "), oc,
      el("button", { class: "small", onclick: async () => { await api("POST", "/api/law/config", { oc: oc.value }); status.innerHTML = ""; status.append(el("span", { class: "tag ok" }, "저장했습니다")); } }, "저장")));
  });
  return wrap;
}
async function loadArticles(it, box, want) {
  box.innerHTML = ""; box.append(el("span", { class: "spinner" }));
  let r;
  try { r = await api("GET", `/api/law/articles?id=${encodeURIComponent(it.id)}&target=${it.target}`); } catch (e) { box.innerHTML = ""; return; }
  const norm = s => (s || "").replace(/\s+/g, "");
  const picks = new Set(r.articles.filter(a => want && norm(a.no) === norm(want)).map(a => a.no));
  box.innerHTML = "";
  const list = el("div", { class: "art-list" }, r.articles.map(a => {
    const cb = el("input", { type: "checkbox" }); cb.checked = picks.has(a.no);
    cb.addEventListener("change", () => cb.checked ? picks.add(a.no) : picks.delete(a.no));
    return el("label", { class: "art" + (picks.has(a.no) ? " on" : "") }, cb, el("div", {}, el("b", {}, `${a.no}${a.title ? `(${a.title})` : ""}`), el("div", { class: "small muted art-text" }, a.text)));
  }));
  box.append(el("div", { class: "row" }, el("b", { style: { flex: 1 } }, `${r.name} · 조문 ${r.articles.length}개${r.enforce ? " · 시행 " + r.enforce : ""}`),
    el("button", { class: "primary small", onclick: () => {
      const chosen = r.articles.filter(a => picks.has(a.no));
      if (!chosen.length) return toast("넣을 조문을 고르세요", true);
      const laws = FORM.a1.laws;
      // 자료에서 이름만 찾아 둔 같은 법령 줄은 확인된 조문으로 바꾼다
      for (let i = laws.length - 1; i >= 0; i--) if (!laws[i].verified && norm(laws[i].law) === norm(r.name)) laws.splice(i, 1);
      chosen.forEach(a => laws.push({ law: r.name, article: a.no, title: a.title, text: a.text, enforce: r.enforce, link: r.link + "/" + encodeURIComponent(a.no), verified: true, target: r.target, found_in: "" }));
      formChanged(); LAW_ROWS && LAW_ROWS._redraw(); box.innerHTML = ""; toast(`${chosen.length}개 조문을 넣었습니다`);
    } }, "고른 조문 넣기")), list);
}

// ------------------------------------------------------------ 시한폭탄 방지 · D-Day 리스크 레이더
const LV = { red: "위험", amber: "주의", blue: "예정" };
const fmtD = s => s.slice(5).replace("-", ".");
function rcard(e) {
  return el("div", { class: "rcard lv-" + e.level },
    el("div", { class: "rc-head" }, el("span", { class: "dd" }, e.days < 0 ? `기한 ${-e.days}일 지남` : e.dday), el("span", { class: "rc-date" }, fmtD(e.date)),
      e.kind === "공공 주기" ? el("span", { class: "tag info" }, "공공 주기") : null),
    el("b", { class: "rc-title" }, e.title),
    e.why ? el("p", { class: "rc-why" }, e.why) : null,
    e.flag ? el("p", { class: "rc-flag" }, "⚠ 빈틈 " + e.flag) : null,
    e.source ? el("span", { class: "rc-src" }, "근거: " + e.source) : null);
}
function radarCards(rd) {
  if (!rd) return null;
  const col = (title, sub, list, empty) => el("div", { class: "rcol" },
    el("div", { class: "rcol-head" }, el("b", {}, title), el("span", {}, sub)),
    list.length ? list.map(rcard) : el("div", { class: "rc-empty" }, empty));
  return el("div", { class: "rcols" },
    col("부임 첫 주에 해야 할 일", "착임 후 7일 안 · 이미 지난 기한", rd.top.week, "첫 주 안에 기한이 오는 일은 없습니다"),
    col("이번 달 미체결 시 감사 지적 우려 계약", "착임 후 30일 안 계약·공사·갱신", rd.top.contract, "30일 안에 걸린 계약 건은 없습니다"));
}
async function renderRadar(start) {
  const sec = $("#tab-radar");
  if (needH(sec)) return;
  const q = start ? `?start=${start}` : "";
  const rd = await api("GET", `/api/handovers/${H.id}/radar${q}`);
  const dt = el("input", { type: "date", value: rd.start });
  const audit = el("select", {}, [...Array(12)].map((_, i) => el("option", { value: i + 1, selected: rd.audit_month === i + 1 || null }, `${i + 1}월`)));
  const lvl = el("select", {}, ["시·군·구", "시·도", "공공기관"].map(x => el("option", { value: x, selected: rd.gov_level === x || null }, x)));
  // 달별로 묶은 세로 타임라인
  const byMonth = {};
  rd.timeline.forEach(e => (byMonth[e.date.slice(0, 7)] = byMonth[e.date.slice(0, 7)] || []).push(e));
  sec.innerHTML = "";
  sec.append(
    el("div", { class: "panel radar-top" },
      el("div", { class: "row" }, el("span", { class: "bomb", "aria-hidden": "true" }, "⏰"),
        el("div", { style: { flex: 1 } }, el("h2", { style: { margin: 0 } }, "D-Day 리스크 레이더"),
          el("span", { class: "small" }, "인수인계 자료의 기한 + 공공기관 법정·행정 주기를 후임자 착임(발령)일 기준으로 줄 세웁니다")),
        el("span", { class: "tag bad" }, `위험 ${rd.counts.red}`), el("span", { class: "tag warn" }, `주의 ${rd.counts.amber}`)),
      el("div", { class: "row radar-set" },
        el("label", {}, "후임자 착임일 ", dt), el("label", {}, "의회 행정사무감사 ", audit), el("label", {}, "기관 유형 ", lvl),
        el("button", { class: "primary small", onclick: async () => {
          await api("POST", `/api/handovers/${H.id}/radar`, { start: dt.value, audit_month: Number(audit.value), gov_level: lvl.value });
          H = await api("GET", `/api/handovers/${H.id}`); renderRadar(); renderDraft(); toast("착임일 기준으로 다시 계산했습니다");
        } }, "기준 저장")),
      radarCards(rd)),
    el("div", { class: "panel" },
      el("h2", {}, "앞으로 120일 타임라인"),
      el("div", { class: "legend small" }, el("span", { class: "lg red" }, "위험"), el("span", { class: "lg amber" }, "주의(30일 안)"), el("span", { class: "lg blue" }, "예정"),
        el("span", { class: "tag info" }, "공공 주기"), " = 법정·행정 일반 주기(기관 일정 확인)"),
      el("div", { class: "tl" }, Object.entries(byMonth).map(([ym, list]) => el("div", { class: "tl-month" },
        el("div", { class: "tl-m" }, `${Number(ym.slice(5))}월`),
        el("div", { class: "tl-items" }, list.map(e => el("div", { class: "tl-item lv-" + e.level + (e.days < 0 ? " past" : "") },
          el("span", { class: "tl-dot" }),
          el("span", { class: "tl-dd" }, e.days < 0 ? `+${-e.days}일` : e.dday),
          el("span", { class: "tl-date" }, fmtD(e.date)),
          el("div", { class: "tl-body" }, el("b", {}, e.title), e.kind === "공공 주기" ? el("span", { class: "tag info" }, "공공 주기") : null,
            e.why ? el("div", { class: "small muted" }, e.why) : null,
            e.tip ? el("div", { class: "small" }, "💡 " + e.tip) : null,
            e.basis ? el("div", { class: "small muted" }, "근거: " + e.basis, " ", e.link ? el("a", { href: e.link, target: "_blank", rel: "noopener" }, "법령 보기") : null) : null,
            e.source ? el("div", { class: "small muted" }, "자료: " + e.source) : null)))))))),
    rd.conditional.length ? el("div", { class: "panel" }, el("h2", {}, "일이 생기면 시작되는 법정 기한"),
      el("div", { class: "cond" }, rd.conditional.map(c => el("div", { class: "cond-card" },
        el("b", {}, c.title), el("div", { class: "dd-text" }, c.when), el("div", { class: "small muted" }, c.basis),
        el("div", { class: "small" }, "💡 " + c.tip), el("div", { class: "small muted" }, c.why, " · ", el("a", { href: c.link, target: "_blank", rel: "noopener" }, "법령 보기")))))) : null,
    el("p", { class: "small muted" }, "※ " + rd.note));
}

// ------------------------------------------------------------ 휴먼 네트워크 맵 · 노하우 카드
let NET = null, NET_SEL = null;
const GROUP_COLOR = { team: "#1d4ed8", dept: "#0891b2", gov: "#7c3aed", vendor: "#ea580c", civil: "#dc2626" };
const short = (t, n) => (t.length > n ? t.slice(0, n - 1) + "…" : t);
function netGraph(net) {
  const W = 820, Hh = 560, cx = W / 2, cy = Hh / 2;
  const duties = net.duties, nodes = net.nodes;
  const pos = { center: [cx, cy] };
  const dAng = {};
  duties.forEach((d, i) => { const a = -Math.PI / 2 + (i * 2 * Math.PI) / Math.max(duties.length, 1); dAng[d.id] = a; pos[d.id] = [cx + 175 * Math.cos(a), cy + 125 * Math.sin(a)]; });
  // 바깥 고리: 연결된 업무 방향 순서대로 고르게 놓아 겹치지 않게
  const anchor = n => (n.link in dAng ? dAng[n.link] : -Math.PI / 2 - 0.3);
  const order = [...nodes].sort((a, b) => ((anchor(a) + 2 * Math.PI) % (2 * Math.PI)) - ((anchor(b) + 2 * Math.PI) % (2 * Math.PI)));
  const off = order.length ? ((anchor(order[0]) + 2 * Math.PI) % (2 * Math.PI)) : 0;
  order.forEach((n, i) => { const a = off + (i * 2 * Math.PI) / order.length; pos[n.id] = [cx + 345 * Math.cos(a), cy + 228 * Math.sin(a)]; });
  const g = svg("svg", { viewBox: `0 0 ${W} ${Hh}`, class: "net-svg", role: "img", "aria-label": "업무 협업 네트워크 그림" });
  net.edges.forEach(e => { const [x1, y1] = pos[e.from] || pos.center, [x2, y2] = pos[e.to] || pos.center;
    g.append(svg("line", { x1, y1, x2, y2, class: "net-edge" + (e.to === NET_SEL ? " on" : "") })); });
  duties.forEach(d => { const [x, y] = pos[d.id];
    g.append(svg("g", { class: "net-duty" }, svg("rect", { x: x - 62, y: y - 15, width: 124, height: 30, rx: 15 }),
      svg("text", { x, y: y + 4, "text-anchor": "middle" }, short(d.label, 10)), svg("title", {}, d.label))); });
  g.append(svg("g", { class: "net-center" }, svg("circle", { cx, cy, r: 50 }),
    svg("text", { x: cx, y: cy - 2, "text-anchor": "middle" }, short(net.center.label.replace(/\s*업무 인수인계$/, ""), 9)),
    svg("text", { x: cx, y: cy + 15, "text-anchor": "middle", class: "sub" }, "담당 업무")));
  nodes.forEach(n => { const [x, y] = pos[n.id];
    const r = 15 + Math.min(n.tips.length, 4) * 2;
    g.append(svg("g", { class: "net-node" + (NET_SEL === n.id ? " on" : ""), tabindex: 0, onclick: () => { NET_SEL = n.id; drawNetwork(); },
        onkeydown: ev => { if (ev.key === "Enter") { NET_SEL = n.id; drawNetwork(); } } },
      svg("circle", { cx: x, cy: y, r, fill: GROUP_COLOR[n.group] }),
      n.tips.length ? svg("text", { x, y: y + 4, "text-anchor": "middle", class: "cnt" }, String(n.tips.length)) : null,
      svg("text", { x, y: y + r + 14, "text-anchor": "middle", class: "lbl" }, short(n.label, 13)), svg("title", {}, n.label))); });
  return g;
}
function cardEl(c, net) {
  const target = net.nodes.find(n => n.id === c.target);
  return el("div", { class: "kcard k-" + ({ "사전 협의": "pre", "재확인": "re", "주의": "warn", "요령": "tip" }[c.kind] || "tip") },
    el("div", { class: "row", style: { gap: "6px" } }, el("span", { class: "ktag" }, c.kind), el("b", { class: "small", style: { flex: 1 } }, target ? target.label : "공통"),
      el("button", { class: "small ghost", title: c.auto ? "자료에서 뽑은 카드 숨기기" : "카드 지우기", onclick: async () => {
        NET = await api("POST", `/api/handovers/${H.id}/knowhow/${c.id}/delete`, {}); drawNetwork(); } }, "✕")),
    el("p", {}, c.text),
    el("span", { class: "small muted" }, c.auto ? `자료에서 찾음 · ${c.source}` : "전임자가 직접 적음"));
}
function drawNetwork() {
  const sec = $("#tab-network"), net = NET;
  const sel = net.nodes.find(n => n.id === NET_SEL);
  const txt = el("textarea", { placeholder: "예: 이 예산 항목은 결재 전에 경영지원팀 주무관과 사전 협의 필수 / 이 업체는 마감 3일 전 재확인" });
  const tsel = el("select", {}, el("option", { value: "" }, "공통(특정 상대 없음)"), net.nodes.map(n => el("option", { value: n.id, selected: NET_SEL === n.id || null }, n.label)));
  const ksel = el("select", {}, ["사전 협의", "재확인", "주의", "요령"].map(k => el("option", { value: k }, k)));
  sec.innerHTML = "";
  sec.append(
    el("div", { class: "panel" },
      el("div", { class: "row" }, el("div", { style: { flex: 1 } }, el("h2", { style: { margin: 0 } }, "휴먼 네트워크 맵"),
        el("span", { class: "small muted" }, "문서에 없는 '누구와 어떻게'를 보여 줍니다. 원은 협업 상대(역할), 숫자는 노하우 카드 수입니다. 사람 이름은 쓰지 않습니다.")),
        el("span", { class: "tag info" }, `협업 상대 ${net.counts.nodes}`), el("span", { class: "tag ok" }, `노하우 ${net.counts.cards}`)),
      el("div", { class: "legend small" }, Object.entries(net.groups).map(([k, v]) => el("span", { class: "lg", style: `--c:${GROUP_COLOR[k]}` }, v.name))),
      el("div", { class: "net-wrap" }, el("div", { class: "net-graph" }, netGraph(net)),
        el("div", { class: "net-side" }, sel ? [
          el("span", { class: "tag", style: { background: GROUP_COLOR[sel.group], color: "#fff" } }, net.groups[sel.group].name),
          el("h3", { style: { margin: "6px 0" } }, sel.label),
          el("div", { class: "small muted" }, [sel.org, sel.title].filter(Boolean).join(" · ")),
          sel.phones.length || sel.emails.length ? el("div", { class: "small" }, "☎ ", [...sel.phones, ...sel.emails].join(", ")) : null,
          el("div", { class: "small", style: { marginTop: "6px" } }, el("b", {}, "이럴 때 연락 "), sel.topics.slice(0, 3).join(" / ") || "-"),
          el("div", { class: "small muted" }, "연결 업무: ", (net.duties.find(d => d.id === sel.link) || { label: "업무 전반" }).label),
          el("h4", {}, `노하우 카드 ${sel.tips.length}장`),
          net.cards.filter(c => c.target === sel.id).map(c => cardEl(c, net)),
          el("button", { class: "small", onclick: () => { txt.focus(); } }, "+ 이 상대에 대한 노하우 쓰기")]
          : el("div", { class: "empty small" }, "원을 누르면 협업 상대의 역할·연락처·노하우가 여기에 나옵니다.")))),
    el("div", { class: "panel" },
      el("div", { class: "row" }, el("h2", { style: { flex: 1, margin: 0 } }, "노하우 카드"),
        el("span", { class: "small muted" }, "자료 속 '사전 협의 필수', '3일 전 재확인' 같은 문장을 찾아 카드로 만들었습니다. 인수인계서·바통 파일에도 들어갑니다.")),
      el("div", { class: "kgrid" }, net.cards.map(c => cardEl(c, net))),
      el("div", { class: "kadd" }, el("b", {}, "노하우 직접 쓰기"),
        el("div", { class: "row" }, tsel, ksel), txt,
        el("button", { class: "primary small", onclick: async () => {
          if (!txt.value.trim()) return toast("노하우를 입력해 주세요", true);
          NET = await api("POST", `/api/handovers/${H.id}/knowhow`, { target: tsel.value, kind: ksel.value, text: txt.value.trim() });
          NET_SEL = tsel.value || NET_SEL; drawNetwork(); toast("노하우 카드를 남겼습니다(사람 이름은 역할로 바뀌어 저장)");
        } }, "카드 남기기"))));
}
async function renderNetwork() {
  const sec = $("#tab-network");
  if (needH(sec)) return;
  NET = await api("GET", `/api/handovers/${H.id}/network`);
  if (!NET.nodes.some(n => n.id === NET_SEL)) NET_SEL = (NET.nodes.find(n => n.tips.length) || NET.nodes[0] || {}).id || null;
  drawNetwork();
}

// ------------------------------------------------------------ 포토 아카이브
let PHOTO_Q = "";
const photoCard = (p, compact) => el("div", { class: "ph-card" + (compact ? " compact" : "") },
  el("button", { class: "ph-img", title: "크게 보기", onclick: () => photoModal(p) }, rawEl("img", { src: p.url, alt: p.name, loading: "lazy" })),
  el("div", { class: "ph-body" },
    el("b", { class: "ph-name" }, p.name),
    el("span", { class: "small muted" }, [p.taken ? `찍은 날 ${p.taken.replace(/-/g, ".")}` : "찍은 날 모름", p.folder ? ` · ${p.folder}` : ""].join("")),
    (p.reasons || []).length ? el("div", { class: "ph-why" }, p.reasons.map(r => el("span", { class: "tag info" }, r))) : null,
    !compact && p.caption ? el("div", { class: "small" }, "🤖 " + p.caption) : null,
    !compact && p.ocr ? el("div", { class: "small muted ph-ocr" }, "사진 속 글자: " + p.ocr.slice(0, 120)) : null));
function photoModal(p) {
  const m = modal(p.name,
    rawEl("img", { src: p.url, alt: p.name, style: "width:100%;border-radius:12px;display:block" }),
    el("div", { class: "small", style: { marginTop: "10px" } }, `파일: ${p.file} · 찍은 날: ${p.taken || "모름"} · 크기: ${p.size}`),
    p.caption ? el("p", {}, "🤖 AI 사진 설명: " + p.caption) : null,
    p.ocr ? el("p", { class: "small" }, `사진 속 글자(${p.ocr_engine || "글자 인식"}): ${p.ocr}`) : el("p", { class: "small muted" }, "사진 속 글자를 읽지 못했거나 글자가 없습니다."),
    (p.linked || []).length ? el("p", { class: "small" }, "이 사진을 근거로 한 항목: " + p.linked.join(" / ")) : null,
    el("div", { class: "row", style: { marginTop: "8px" } },
      el("button", { class: "small danger", onclick: async () => {
        if (!confirm("이 사진을 지울까요? (줄인 사본과 기록이 지워지고, 원본 파일은 그대로입니다)")) return;
        await api("POST", `/api/handovers/${H.id}/photos/${p.id}/delete`, {});
        H.photos = (H.photos || []).filter(x => x.id !== p.id); m.remove ? m.remove() : null; renderPhotos(); toast("사진을 지웠습니다");
      } }, "이 사진 지우기")));
  return m;
}
async function renderPhotos(q) {
  const sec = $("#tab-photos");
  if (needH(sec)) return;
  if (q !== undefined) PHOTO_Q = q;
  const r = await api("GET", `/api/handovers/${H.id}/photos?q=${encodeURIComponent(PHOTO_Q)}`);
  const box = el("input", { class: "wide", value: PHOTO_Q, placeholder: "키워드로 사진 찾기 (예: 직무중심교육, 주차장 균열, 9월)" });
  box.addEventListener("keydown", e => { if (e.key === "Enter") renderPhotos(box.value.trim()); });
  // 추천 키워드: 사진 파일·폴더 이름에서
  const words = [...new Set((H.photos || []).flatMap(p => (p.name.replace(/\.[^.]+$/, "") + " " + p.folder).split(/[\s_()\[\]\/·,.-]+/))
    .filter(w => w.length >= 3 && !/^\d+$|^IMG|^DSC|현장사진/i.test(w)))].slice(0, 8);
  sec.innerHTML = "";
  sec.append(el("div", { class: "panel" },
    el("div", { class: "row" }, el("div", { style: { flex: 1 } }, el("h2", { style: { margin: 0 } }, "포토 아카이브"),
      el("span", { class: "small muted" }, "넣은 자료 속 사진만 따로 모았습니다. 파일·폴더 이름, 사진 속 글자, AI 사진 설명, 찍은 날짜, 같은 폴더 자료로 찾습니다.")),
      el("span", { class: "tag info" }, `사진 ${r.total}장`)),
    el("div", { class: "row", style: { marginTop: "12px" } }, box, el("button", { class: "primary", onclick: () => renderPhotos(box.value.trim()) }, "찾기"),
      PHOTO_Q ? el("button", { onclick: () => renderPhotos("") }, "전체 보기") : null),
    words.length ? el("div", { class: "row small", style: { marginTop: "8px" } }, words.map(w => el("span", { class: "chip", onclick: () => renderPhotos(w) }, w))) : null,
    el("p", { class: "small muted", style: { margin: "10px 0 0" } }, "※ 사진은 긴 변 1,024px로 줄인 사본만 보관하고 위치 정보(EXIF)는 지웁니다. 사진 속 얼굴·이름표는 자동으로 가리지 않으니, 남기면 안 되는 사진은 크게 보기에서 지워 주세요.")),
    el("div", { class: "panel" },
      el("h3", { style: { marginTop: 0 } }, PHOTO_Q ? `'${PHOTO_Q}' 관련 사진 ${r.photos.length}장` : `전체 사진 ${r.photos.length}장`),
      r.photos.length ? el("div", { class: "ph-grid" }, r.photos.map(p => photoCard(p)))
        : el("div", { class: "empty" }, r.total ? "찾는 사진이 없습니다. 다른 낱말로 찾아 보세요." : "넣은 자료에 사진이 없습니다. 드롭존에 사진(JPG·PNG 등)을 함께 놓으면 여기에 모입니다.")));
}

// 화면 스킨 바꾸기: 정부표준(KRDS풍) ↔ 브랜드형. 고른 값은 이 브라우저에 기억한다
function setupSkin() {
  const paint = () => $$(".skin-sw button").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.skin === document.documentElement.dataset.skin)));
  $$(".skin-sw button").forEach(b => b.addEventListener("click", () => {
    const s = b.dataset.skin;
    document.documentElement.dataset.skin = s;
    $("#skin-css").href = `/skin-${s}.css`;
    try { localStorage.setItem("baton:skin", s); } catch (e) {}
    paint();
    $("#skin-css").onload = () => dispatchEvent(new Event("resize"));
  }));
  paint();
}

// 트랙 연출(브랜드 스킨에서만 보임): 스크롤 진행도를 CSS 변수로 넘기고, 옆 메뉴의 바통이 고른 화면으로 넘어간다
function setupTrack() {
  const root = document.documentElement, nav = $("nav.tabs");
  const gauge = $(".track-gauge"), gProg = gauge && $(".tg-prog", gauge), gBaton = gauge && $(".tg-baton", gauge), gPct = gauge && $(".tg-pct", gauge);
  const baton = el("span", { class: "nav-baton", "aria-hidden": "true" });
  nav.prepend(baton);
  let ticking = false;
  const onScroll = () => {
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(() => {
      ticking = false;
      const max = root.scrollHeight - innerHeight;
      const sp = max > 0 ? Math.min(scrollY / max, 1) : 0;
      root.style.setProperty("--sp", sp.toFixed(4));
      root.style.setProperty("--sy", scrollY + "px");
      if (gauge) {
        // 반원(왼쪽 출발선 180° → 오른쪽 결승선 360°) 위를 바통이 달린다
        const a = Math.PI * (1 + sp);
        gProg.style.strokeDashoffset = String(1 - sp);
        gBaton.setAttribute("transform", `translate(${80 + 62 * Math.cos(a)} ${80 + 62 * Math.sin(a)})`);
        gPct.textContent = Math.round(sp * 100) + "%";
        gauge.classList.toggle("idle", max < 200);
      }
    });
  };
  const place = () => {
    const b = $("nav.tabs button.on");
    if (!b) return;
    const row = getComputedStyle(nav).flexDirection === "row";
    baton.style.transform = row ? `translateX(${b.offsetLeft}px)` : `translateY(${b.offsetTop}px)`;
    baton.style.width = row ? b.offsetWidth + "px" : "";
    baton.style.height = row ? "" : b.offsetHeight + "px";
    if (gauge) $(".tg-lbl", gauge).textContent = b.textContent.replace(/^\d/, "");
    onScroll();
  };
  addEventListener("scroll", onScroll, { passive: true });
  addEventListener("resize", place);
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) $$(".hero-track").forEach(x => x.pauseAnimations && x.pauseAnimations());
  return place;
}

// ------------------------------------------------------------ 드롭존 허브: 드롭 한 번으로 만들어지는 것
const HUB = [
  ["draft", "인수인계 초안", "출처가 달린 업무·일정·연락처·현안, 전임자 검토", "M4 5h16M4 10h16M4 15h10"],
  ["flags", "빈틈 스캐너", "누락·충돌·불확실·기한 지남", "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM20 20l-4-4"],
  ["radar", "D-Day 리스크 레이더", "부임 첫 주 할 일·감사 지적 우려 계약", "M12 3a9 9 0 1 0 9 9M12 7a5 5 0 1 0 5 5M12 12l6-6"],
  ["network", "휴먼 네트워크", "협업 상대 지도와 노하우 카드", "M6 6a2 2 0 1 0 0 .1M18 6a2 2 0 1 0 0 .1M12 18a2 2 0 1 0 0 .1M7 7l4 9M17 7l-4 9M8 6h8"],
  ["ask", "AI 전임자 분신", "자료로만 답하고 모르면 물어볼 곳 안내", "M4 5h16v10H9l-5 4z"],
  ["reports", "리포트 업그레이더", "전임자 보고서로 다음 보고서 작성", "M7 3h7l5 5v13H7zM14 3v5h5M10 13h6M10 17h6"],
  ["form", "표준 인수인계서", "필수 5대 영역 서식·법령 검색", "M5 4h14v16H5zM8 8h8M8 12h8M8 16h5"],
  ["calendar", "업무 달력", "월별 할 일, 내 일정(.ics)으로", "M4 6h16v14H4zM4 10h16M8 3v4M16 3v4"],
  ["manual", "업무매뉴얼", "착임일·경력 맞춤 후임자 매뉴얼", "M5 4h11a3 3 0 0 1 3 3v13H8a3 3 0 0 1-3-3zM5 17a3 3 0 0 1 3-3h11"],
  ["photos", "포토 아카이브", "사진만 모아 키워드로 찾기", "M4 7h4l2-3h4l2 3h4v12H4zM12 10a3 3 0 1 0 0 6 3 3 0 0 0 0-6"],
];
async function renderHub() {
  const box = $("#hub");
  if (!box) return;
  const metric = {};
  if (H) {
    const d = H.draft, live = k => d.sections[k].filter(i => i.status !== "삭제").length;
    metric.draft = `항목 ${live("rnr") + live("schedule") + live("contacts") + live("issues")}`;
    metric.flags = `남은 빈틈 ${d.flags.filter(f => !f.resolved).length}`;
    metric.ask = `질문 ${H.asked || 0}`;
    metric.calendar = `일정 ${live("schedule")}`;
    metric.photos = `사진 ${(H.photos || []).length}`;
    metric.form = H.form ? "저장됨" : "자동 작성됨";
    metric.manual = "착임일 맞춤";
    $("#hub-sub").textContent = `지금: ${H.title}`;
  } else $("#hub-sub").textContent = "자료를 놓으면 아래가 모두 자동으로 채워집니다";
  const paint = () => {
    box.innerHTML = "";
    HUB.forEach(([tab, name, desc, path]) => box.append(el("button", { class: "hub-card" + (H ? "" : " idle"), onclick: () => H ? goTab(tab) : toast("먼저 올인원 드롭존에 자료를 놓아 주세요", true) },
      el("span", { class: "hub-ico", html: `<svg viewBox="0 0 24 24"><path d="${path}"/></svg>` }),
      el("b", {}, name), el("span", { class: "hub-desc" }, desc),
      metric[tab] ? el("span", { class: "hub-metric" + (/^(위험|남은 빈틈) [1-9]/.test(metric[tab]) ? " hot" : "") }, metric[tab]) : null)));
  };
  paint();
  if (!H) return;
  const hid = H.id;
  const [rd, net, rp] = await Promise.all([api("GET", `/api/handovers/${hid}/radar`).catch(() => null),
    api("GET", `/api/handovers/${hid}/network`).catch(() => null), api("GET", `/api/handovers/${hid}/reports`).catch(() => null)]);
  if (!H || H.id !== hid) return;
  if (rd) metric.radar = `위험 ${rd.counts.red} · 주의 ${rd.counts.amber}`;
  if (net) metric.network = `상대 ${net.counts.nodes} · 노하우 ${net.counts.cards}`;
  if (rp) metric.reports = rp.reports.length ? `보고서 ${rp.reports.length}건 발견` : "보고서 없음";
  paint();
}

// ------------------------------------------------------------ 리포트 업그레이더
let REPORT = null, REPORT_SEL = null;
async function renderReports() {
  const sec = $("#tab-reports");
  if (needH(sec)) return;
  const { reports } = await api("GET", `/api/handovers/${H.id}/reports`);
  if (!reports.some(r => r.file === REPORT_SEL)) { REPORT_SEL = reports[0] ? reports[0].file : null; REPORT = null; }
  const dt = el("input", { type: "date", value: (H.radar && H.radar.start) || new Date().toISOString().slice(0, 10) });
  const mode = el("select", {}, el("option", { value: "next" }, "다음 회차(다음 달·다음 연도)로 쓰기"), el("option", { value: "refresh" }, "같은 회차를 보완해 다시 쓰기"));
  const body = () => ({ file: REPORT_SEL, mode: mode.value, date: dt.value });
  const make = async () => { REPORT = await api("POST", `/api/handovers/${H.id}/reports/build`, body()); draw(); };
  const out = el("div");
  const draw = () => {
    out.innerHTML = "";
    if (!REPORT) return;
    const r = REPORT;
    out.append(el("div", { class: "rp-wrap" },
      el("div", { class: "panel rp-doc" },
        el("div", { class: "row" }, el("span", { class: "tag info" }, r.kind), el("span", { class: "small muted", style: { flex: 1 } }, `${r.org} · ${r.date}`),
          exportButtons(`/api/handovers/${H.id}/reports/export`, body)),
        el("h2", { class: "rp-title" }, r.title),
        el("div", { class: "rp-sum" }, el("b", {}, "보고 요지"), el("ul", {}, r.summary.map(x => el("li", {}, x)))),
        r.sections.map((s, i) => el("div", { class: "rp-sec" }, el("h3", {}, `${i + 1}. ${s.title}`),
          s.table ? el("table", { class: "t" }, el("tr", {}, s.table.header.map(x => el("th", {}, x))),
            s.table.rows.map(row => el("tr", {}, row.map(c => el("td", { class: /【입력/.test(c) ? "fill" : "" }, c))))) : null,
          el("ul", {}, (s.bullets || []).map(x => el("li", { class: /【입력/.test(x.text) ? "fill-li" : "" }, x.text, x.src ? el("span", { class: "rp-src" }, x.src) : null))))),
        r.attachments.length ? el("div", { class: "small muted" }, "붙임: ", r.attachments.map(a => a.split("/").pop()).join(" · ")) : null,
        el("p", { class: "small", style: { marginTop: "10px" } }, `【입력】 ${r.fills}곳은 실제 값을 확인해 채워 주세요(내려받은 한글 파일에서 바로 고칠 수 있습니다).`)),
      el("div", { class: "panel rp-side" },
        el("h3", { style: { marginTop: 0 } }, "전임자 보고서 대비 업그레이드"),
        el("ol", { class: "rp-up" }, r.upgrades.map(u => el("li", {}, u))),
        el("h4", {}, "전임자 원본"),
        el("div", { class: "small" }, el("b", {}, r.original.title)),
        el("div", { class: "small muted" }, `${r.original.file.split("/").pop()} · ${r.original.lines}줄 · 장: ${r.original.sections.join(" / ") || "구분 없음"}`))));
  };
  sec.innerHTML = "";
  sec.append(el("div", { class: "panel" },
    el("div", { class: "row" }, el("div", { style: { flex: 1 } }, el("h2", { style: { margin: 0 } }, "리포트 업그레이더"),
      el("span", { class: "small muted" }, "드롭한 자료에서 전임자 보고서를 찾아, 공공기관 보고서 양식으로 더 나은 다음 보고서를 씁니다. 모든 문장에 근거가 붙고, 자료에 없는 값은 【입력】으로 비워 둡니다.")),
      el("span", { class: "tag info" }, `찾은 보고서 ${reports.length}건`)),
    reports.length ? el("div", { class: "rp-list" }, reports.map(r => el("button", { class: "rp-pick" + (r.file === REPORT_SEL ? " on" : ""), onclick: () => { REPORT_SEL = r.file; REPORT = null; renderReports(); } },
      el("span", { class: "tag " + (r.kind === "결과 보고" ? "ok" : r.kind === "계획" ? "info" : "gray") }, r.kind), el("b", {}, r.title), el("span", { class: "small muted" }, `${r.name} · ${r.lines}줄`))))
      : el("div", { class: "empty" }, "드롭한 자료에서 보고서(결과 보고·계획·현황 보고 등)를 찾지 못했습니다. 한글·PDF·워드 보고서를 함께 놓아 주세요."),
    reports.length ? el("div", { class: "row", style: { marginTop: "12px" } }, mode, el("label", { class: "small" }, "작성 기준일 ", dt),
      el("button", { class: "primary", onclick: make }, "업그레이드 보고서 만들기")) : null), out);
  if (REPORT && REPORT.original.file === REPORT_SEL) draw(); else if (REPORT_SEL) make();
}

let goTab;
function renderAll() { renderDraft(); renderCalendar(); renderFlags(); renderDocs(); renderRadar(); renderNetwork(); PHOTO_Q = ""; renderPhotos(); renderHub(); REPORT = null; }
document.addEventListener("DOMContentLoaded", async () => {
  setupStart(); setupAsk(); setupManual(); setupSkin();
  const placeBaton = setupTrack();
  goTab = setupTabs(id => {
    document.body.dataset.tab = id; requestAnimationFrame(placeBaton);
    if (id === "history") renderHistory(); if (id === "form") renderForm(); if (id === "reports") renderReports(); if (id === "start") renderHub();
  });
  $("#cur-h").onchange = e => e.target.value && select(e.target.value);
  await refreshList();
});
