"""사진·스캔본 글자 읽기(OCR). 쓸 수 있는 방법을 차례로 시도하고, 하나도 없으면 빈 결과와 이유를 돌려준다.

1) Tesseract  : PC에 설치돼 있으면(한국어 kor 언어팩) 사용
2) Windows OCR: Windows 10·11에 들어 있는 글자 인식(한국어 언어팩이 있으면 인터넷 없이 동작, 추가 설치 없음)
3) 비전 모델   : 연결한 모델이 이미지를 볼 수 있으면(gemma3, llava, qwen-vl, llama3.2-vision 등) 글자만 옮겨 적게 함

어떤 방법도 쓸 수 없으면 사진은 '글자를 읽지 못한 사진'으로 남겨 확인 필요에 올린다(조용히 버리지 않음).
"""
import base64
import io
import os
import platform
import re
import shutil
import subprocess
import tempfile

MAX_SIDE = 2400  # 너무 큰 사진은 줄여서 읽는다(속도·메모리)

_WIN_PS = r"""
param([string]$Path)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType = WindowsRuntime]
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
  $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, $type) {
  $t = $asTaskGeneric.MakeGenericMethod($type).Invoke($null, @($op)); $t.Wait(-1) | Out-Null; $t.Result }
$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($Path)) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage((New-Object Windows.Globalization.Language('ko')))
if ($engine -eq $null) { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }
if ($engine -eq $null) { throw 'OCR language pack not installed' }
$result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
foreach ($line in $result.Lines) { $line.Text }
"""

VISION_HINT = re.compile(r"gemma3|llava|vision|-vl|vl:|qwen2\.5vl|qwen-vl|minicpm-v|moondream|bakllava|pixtral|internvl", re.I)
VISION_PROMPT = ("이 이미지에 보이는 글자를 줄 단위로 그대로 옮겨 적으세요. 설명·요약·추측은 쓰지 말고 보이는 글자만 적습니다. "
                 "읽을 수 없는 글자는 건너뜁니다. 글자가 하나도 없으면 '글자 없음'이라고만 답하세요.")


def to_png(data, max_side=MAX_SIDE):
    """어떤 사진이든 PNG로(회전 정보 반영, 큰 사진은 줄임). (png 바이트, 가로, 세로, 찍은 날짜)"""
    from PIL import Image, ImageOps
    im = Image.open(io.BytesIO(data))
    taken = None
    try:
        exif = im.getexif()
        sub = exif.get_ifd(0x8769) if hasattr(exif, "get_ifd") else {}
        raw = sub.get(36867) or exif.get(306)  # DateTimeOriginal, DateTime
        if raw:
            m = re.match(r"(\d{4}):(\d{2}):(\d{2})", str(raw))
            taken = f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None
    except Exception:
        pass
    im = ImageOps.exif_transpose(im)
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    w, h = im.size
    if max(w, h) > max_side:
        im.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue(), w, h, taken


def _tesseract(png):
    exe = shutil.which("tesseract")
    if not exe:
        return None
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "in.png")
        with open(src, "wb") as f:
            f.write(png)
        langs = subprocess.run([exe, "--list-langs"], capture_output=True, text=True, timeout=20).stdout
        lang = "kor+eng" if "kor" in langs else "eng"
        r = subprocess.run([exe, src, "stdout", "-l", lang], capture_output=True, timeout=120)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.decode("utf-8", "replace")[:200])
        return r.stdout.decode("utf-8", "replace")


def _windows(png):
    if platform.system() != "Windows":
        return None
    exe = shutil.which("powershell") or shutil.which("powershell.exe")
    if not exe:
        return None
    with tempfile.TemporaryDirectory() as d:
        src, ps = os.path.join(d, "in.png"), os.path.join(d, "ocr.ps1")
        with open(src, "wb") as f:
            f.write(png)
        with open(ps, "w", encoding="utf-8-sig") as f:
            f.write(_WIN_PS)
        r = subprocess.run([exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", ps, "-Path", src],
                           capture_output=True, timeout=120, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode != 0:
            raise RuntimeError(r.stderr.decode("utf-8", "replace")[:200])
        return r.stdout.decode("utf-8", "replace")


def _vision(png, llm):
    if llm is None or not llm.enabled:
        return None
    mode = str(llm.cfg.get("vision", "auto")).lower()
    if mode in ("false", "off", "no") or (mode == "auto" and not VISION_HINT.search(llm.cfg.get("model") or "")):
        return None
    text = llm.see(base64.b64encode(png).decode(), VISION_PROMPT)
    return "" if re.fullmatch(r"\s*글자\s*없음\.?\s*", text or "") else text


def make(llm=None):
    """loaders.OCR 에 넣을 함수. read(사진 바이트) -> {"text","engine","width","height","taken","error"}"""
    def read(data):
        try:
            png, w, h, taken = to_png(data)
        except Exception as e:
            return {"text": "", "engine": "", "error": f"사진을 열 수 없습니다({type(e).__name__})"}
        tried = []
        for name, fn in (("Tesseract", _tesseract), ("Windows OCR", _windows), ("비전 모델", lambda p: _vision(p, llm))):
            try:
                text = fn(png)
            except Exception as e:  # 한 방법이 실패하면 다음 방법으로
                tried.append(f"{name} 실패({str(e)[:60]})")
                continue
            if text is None:
                continue
            lines = [re.sub(r"\s+", " ", x).strip() for x in text.splitlines()]
            return {"text": "\n".join(x for x in lines if x), "engine": name, "width": w, "height": h, "taken": taken, "error": ""}
        why = "; ".join(tried) or "글자 인식(OCR) 방법이 없습니다(Windows OCR·Tesseract·비전 모델 중 하나 필요)"
        return {"text": "", "engine": "", "width": w, "height": h, "taken": taken, "error": why}
    return read
