"""구동안내서.md → [양식 2] 표 서식의 워드(.docx) (+ LibreOffice가 있으면 PDF).

실행: python tools/make_guide_docx.py [기관명] [팀명]
만든 파일: 제출/[구동안내서]_기관명_팀명.docx (.pdf)
"""
import re
import shutil
import subprocess
import sys
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

ROOT = Path(__file__).resolve().parents[1]
FONT = "맑은 고딕"
ROWS = ["실행 환경", "설치·실행 절차", "LLM 연결 설정", "모의데이터 안내", "기능 사용 순서", "알려진 제약 사항"]


def sections(md):
    out, cur = {}, None
    for line in md.splitlines():
        m = re.match(r"^##\s+(.+)$", line)
        if m:
            cur = m.group(1).strip()
            out[cur] = []
        elif cur and line.strip():
            out[cur].append(line.rstrip())
    return out


def _font(run, size=9.5, bold=False, color=None):
    run.font.name = FONT
    run.font.size = Pt(size)
    run.font.bold = bold
    run._element.rPr.rFonts.set(qn("w:eastAsia"), FONT)
    if color:
        run.font.color.rgb = RGBColor(*color)


def _rich(par, text, size=9.5):
    """**굵게** 와 `코드` 표시를 살려 넣는다."""
    for part in re.split(r"(\*\*[^*]+\*\*)", text):
        if not part:
            continue
        bold = part.startswith("**")
        _font(par.add_run(part.strip("*").replace("`", "")), size, bold)


def _shade(cell, hexcolor):
    tcPr = cell._element.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hexcolor)
    tcPr.append(shd)


def _fill(cell, lines):
    cell.paragraphs[0].text = ""
    first = True
    for line in lines:
        p = cell.paragraphs[0] if first else cell.add_paragraph()
        first = False
        p.paragraph_format.space_after = Pt(1.5)
        p.paragraph_format.line_spacing = 1.12
        m = re.match(r"^(\s*)([-*]|\d+\.)\s+(.*)$", line)
        if m:
            mark = "•" if m.group(2) in "-*" else m.group(2)
            p.paragraph_format.left_indent = Cm(0.35 + 0.3 * (len(m.group(1)) // 2))
            p.paragraph_format.first_line_indent = Cm(-0.3)
            _rich(p, f"{mark} {m.group(3)}")
        else:
            _rich(p, line.strip())


def _widths(tbl, ws):
    """LibreOffice·한글은 표 격자(tblGrid) 너비를 따른다: 격자와 칸 너비를 모두 정한다."""
    tbl.autofit = False
    grid = tbl._tbl.tblGrid
    for gc, w in zip(grid.findall(qn("w:gridCol")), ws):
        gc.set(qn("w:w"), str(int(w.twips)))
    for r in tbl.rows:
        for c, w in zip(r.cells, ws):
            c.width = w


def build(org="기관명", team="팀명"):
    md = (ROOT / "구동안내서.md").read_text(encoding="utf-8")
    sec = sections(md)
    doc = Document()
    s = doc.sections[0]
    s.page_height, s.page_width = Cm(29.7), Cm(21.0)
    s.left_margin = s.right_margin = Cm(1.8)
    s.top_margin = s.bottom_margin = Cm(1.6)
    t = doc.add_paragraph()
    _font(t.add_run("[양식 2] 구동 안내서"), 15, True)
    sub = doc.add_paragraph()
    _font(sub.add_run(f"업무바통(인수인계) · 2026 공공 AI 대전환 챌린지 세션2 지정과제① · {org} {team}"), 9.5, False, (90, 90, 90))
    tbl = doc.add_table(rows=1, cols=2)
    tbl.style = "Table Grid"
    tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
    hdr = tbl.rows[0].cells
    for c, txt in zip(hdr, ("구분", "내용")):
        _shade(c, "D9D9D9")
        c.paragraphs[0].text = ""
        _font(c.paragraphs[0].add_run(txt), 10, True)
    for name in ROWS:
        row = tbl.add_row().cells
        _shade(row[0], "EEF2FA")
        row[0].paragraphs[0].text = ""
        _font(row[0].paragraphs[0].add_run(name), 10, True)
        _fill(row[1], sec.get(name, ["-"]))
    _widths(tbl, (Cm(3.0), Cm(14.4)))
    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
    t2 = doc.add_table(rows=2, cols=1)
    t2.style = "Table Grid"
    _widths(t2, (Cm(17.4),))
    _shade(t2.rows[0].cells[0], "EEF2FA")
    t2.rows[0].cells[0].paragraphs[0].text = ""
    _font(t2.rows[0].cells[0].paragraphs[0].add_run("개발 경위 요약(1쪽 이내)"), 10.5, True)
    _fill(t2.rows[1].cells[0], [x.replace("【기관명·부서·팀명】", f"{org} · {team}") for x in sec.get("개발 경위 요약", ["-"])])
    out_dir = ROOT / "제출"
    out_dir.mkdir(exist_ok=True)
    name = f"[구동안내서]_{org}_{team}"
    docx = out_dir / f"{name}.docx"
    doc.save(docx)
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if soffice:
        subprocess.run([soffice, "--headless", "--convert-to", "pdf", "--outdir", str(out_dir), str(docx)],
                       capture_output=True, timeout=180)
    return docx


if __name__ == "__main__":
    p = build(*(sys.argv[1:3] if len(sys.argv) >= 3 else []))
    print("만든 파일:", p, "(+ PDF)" if p.with_suffix(".pdf").exists() else "")
