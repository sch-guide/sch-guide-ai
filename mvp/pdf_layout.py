"""PDF 표·책갈피와 선택적 로컬 OCR. 병원 원문을 외부에 보내지 않습니다."""

import os
import shutil
import subprocess
import time
from dataclasses import replace
from io import BytesIO
from pathlib import Path

from mvp.documents import PdfInputError
from mvp.settings import ROOT

EXTRACTION_VERSION = 2


def ocr_executable():
    executable = shutil.which("tesseract")
    bundled = ROOT / 'data/tools/tesseract/tesseract.exe'
    if bundled.is_file():
        executable = str(bundled)
    if not executable:
        candidate = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Tesseract-OCR/tesseract.exe"
        executable = str(candidate) if candidate.is_file() else ""
    return executable


def ocr_status():
    executable = ocr_executable()
    if not executable:
        return False, "Tesseract와 한국어(kor)·영어(eng) 언어 파일 설치가 필요합니다."
    try:
        args = [executable, '--list-langs'] + ocr_arguments()
        result = subprocess.run(args, capture_output=True, timeout=10, check=True,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        # Windows는 한글 경로를 시스템 인코딩으로 출력할 수 있습니다. 언어 코드는 ASCII입니다.
        languages = set(result.stdout.decode('utf-8', errors='replace').split())
        if not {"kor", "eng"}.issubset(languages):
            return False, "Tesseract의 한국어(kor)·영어(eng) 언어 파일을 확인하세요."
    except Exception:
        return False, "로컬 OCR 실행 환경을 확인하세요."
    return True, "한국어·영어 로컬 OCR 사용 가능"


def ocr_arguments():
    directory = ROOT / 'data/ocr'
    return ['--tessdata-dir', str(directory)] if (directory / 'kor.traineddata').is_file() else []


def ocr_page(content, index):
    import pypdfium2 as pdfium

    ready, reason = ocr_status()
    if not ready:
        raise PdfInputError(reason + " (OCR_SETUP)")
    with pdfium.PdfDocument(content) as pdf:
        page = pdf[index]
        try:
            width, height = page.get_size()
            scale = min(3, (12_000_000 / max(width * height, 1)) ** 0.5)
            bitmap = page.render(scale=scale)
            try:
                image = bitmap.to_pil()
                try:
                    buffer = BytesIO()
                    image.save(buffer, format='PNG')
                    # 이미지와 텍스트는 파이프로 전달해 임시 원문 파일도 만들지 않습니다.
                    for segmentation in ('3', '6'):
                        result = subprocess.run([ocr_executable(), 'stdin', 'stdout', '-l', 'kor+eng',
                            '--psm', segmentation] + ocr_arguments(), input=buffer.getvalue(),
                            capture_output=True, timeout=15, check=True,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                        text = result.stdout.decode('utf-8').strip()
                        if text:
                            return text
                    return ''
                finally:
                    image.close()
            finally:
                bitmap.close()
        finally:
            page.close()


def bookmark_sections(content):
    from pypdf import PdfReader

    reader, sections = PdfReader(BytesIO(content)), {}
    def walk(entries, parents=()):
        last = parents
        for entry in entries:
            if isinstance(entry, list):
                walk(entry, last)
            else:
                page = reader.get_destination_page_number(entry)
                title = str(entry.get("/Title", "")).strip()
                last = parents + ((title,) if title else ())
                if page is not None and title:
                    sections[page + 1] = " > ".join(last)
    walk(reader.outline)
    return sections


def enhance_pdf(document, content, *, ocr=False):
    import pdfplumber

    warnings, pages = list(document.warnings), []
    if ocr:
        ready, reason = ocr_status()
        if not ready:
            raise PdfInputError(reason + " (OCR_SETUP)")
    try:
        sections = bookmark_sections(content)
    except Exception:
        sections = {}
    current_section, ocr_count, ocr_seconds = "", 0, 0
    try:
        with pdfplumber.open(BytesIO(content)) as pdf:
            for original, page in zip(document.pages, pdf.pages, strict=True):
                current_section = sections.get(original.number, current_section)
                text, error = original.text, original.error
                try:
                    tables = page.find_tables()
                    if tables:
                        # 표 내부를 별도로 추출하되, 행 순서와 같은 행의 셀 관계를 유지합니다.
                        outside = page.filter(lambda obj: not any(
                            obj.get("x0", -1) >= t.bbox[0] and obj.get("x1", -1) <= t.bbox[2]
                            and obj.get("top", -1) >= t.bbox[1] and obj.get("bottom", -1) <= t.bbox[3]
                            for t in tables))
                        blocks = [(line["top"], line["text"], False) for line in outside.extract_text_lines()]
                        for table in tables:
                            rows = table.extract()
                            blocks.append((table.bbox[1], "\n".join(" | ".join((cell or "").replace("\n", " ")
                                                                 for cell in row) for row in rows), True))
                        paragraphs, lines = [], []
                        for _, block, is_table in sorted(blocks):
                            if is_table:
                                if lines:
                                    paragraphs.append('\n'.join(lines))
                                    lines = []
                                paragraphs.append(block)
                            else:
                                lines.append(block)
                        if lines:
                            paragraphs.append('\n'.join(lines))
                        text = "\n\n".join(paragraphs)
                        warnings.append("PDF 표를 행·열 텍스트로 추출했습니다. 병합 셀과 열 제목을 확인하세요.")
                    if page.images and len(text.strip()) < 80:
                        if ocr and ocr_count < 50 and ocr_seconds < 120:
                            ocr_count += 1
                            started = time.perf_counter()
                            try:
                                text = ocr_page(content, original.number - 1)
                            finally:
                                ocr_seconds += time.perf_counter() - started
                            error = None if text else "OCR에서 읽을 수 있는 글을 찾지 못했습니다."
                            warnings.append("OCR로 읽은 내용이 있습니다. 숫자·단위와 표의 순서를 원본과 대조하세요.")
                        else:
                            warnings.append(f"PDF {original.number}페이지: 이미지 중심 페이지입니다. OCR 또는 원본 확인이 필요합니다.")
                    elif page.images:
                        warnings.append("이미지가 있는 페이지가 있습니다. 그림 속 글과 도식은 검색에서 빠질 수 있습니다.")
                except Exception:
                    warnings.append(f"PDF {original.number}페이지: 표/OCR 보완에 실패하여 기존 추출 결과를 유지합니다.")
                    text, error = original.text, original.error
                pages.append(replace(original, text=text, error=error, section=current_section))
                page.close()
    except Exception:
        return replace(document, warnings=tuple(dict.fromkeys(warnings + ["표 보완 추출에 실패했습니다. 기본 텍스트 결과를 확인하세요."])))
    if ocr_count >= 50:
        warnings.append("OCR은 파일당 최대 50페이지입니다. 남은 스캔 페이지는 나누어 등록하세요.")
    return replace(document, pages=tuple(pages), warnings=tuple(dict.fromkeys(warnings)))
