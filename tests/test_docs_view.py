from __future__ import annotations

import hashlib
import importlib
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
DOCS_VIEW = ROOT / "docs_view"


class _LocalReferenceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.references: list[str] = []

    def handle_starttag(self, _tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if name in {"href", "src"} and value:
                self.references.append(value)


def builder_module():
    try:
        return importlib.import_module("tools.build_docs_view")
    except ModuleNotFoundError:
        pytest.fail("docs_view builder is not implemented")


def _tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.md")):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _load_generated_documents(path: Path) -> dict:
    payload = path.read_text(encoding="utf-8")
    prefix = "window.DOCS_VIEW_DATA = "
    assert payload.startswith(prefix)
    assert payload.endswith(";\n")
    return json.loads(payload[len(prefix) : -2])


def test_build_docs_view_indexes_every_official_document_without_mutating_docs(
    tmp_path: Path,
):
    builder = builder_module()
    output = tmp_path / "docs_view"
    before = _tree_hash(DOCS)

    summary = builder.build_docs_view(DOCS, output)
    data = _load_generated_documents(output / "assets" / "documents.js")
    expected_document_count = len(list(DOCS.rglob("*.md")))
    expected_groups: dict[str, int] = {}
    for path in DOCS.rglob("*.md"):
        relative = path.relative_to(DOCS)
        folder = relative.parts[0] if len(relative.parts) > 1 else "개요"
        if folder in builder.GROUP_ORDER:
            expected_groups[folder] = expected_groups.get(folder, 0) + 1

    assert before == _tree_hash(DOCS)
    assert summary.document_count == expected_document_count
    assert len(data["documents"]) == expected_document_count
    assert data["default_document_id"] == "README"
    assert {
        group["name"]: group["count"] for group in data["groups"]
    } == expected_groups
    assert (
        len(list((output / "rendered" / "docs").rglob("*.html")))
        == expected_document_count
    )
    assert any(
        document["id"] == "08_멘토_검토/멘토_조언_반영_점검"
        and document["title"] == "멘토 조언이 SCHAT에 어떻게 반영되었는지 점검"
        for document in data["documents"]
    )
    document_ids = {document["id"] for document in data["documents"]}
    assert "08_멘토_검토/RAGAS_Live_평가_범위" in document_ids
    assert "08_멘토_검토/서비스_대안_비교" not in document_ids
    assert (output / "index.html").is_file()
    assert (output / "assets" / "style.css").is_file()
    assert (output / "assets" / "app.js").is_file()


def test_generated_viewer_has_no_remote_runtime_dependency(tmp_path: Path):
    builder = builder_module()
    output = tmp_path / "docs_view"
    builder.build_docs_view(DOCS, output)

    index = (output / "index.html").read_text(encoding="utf-8")
    app = (output / "assets" / "app.js").read_text(encoding="utf-8")

    assert "http://" not in index
    assert "https://" not in index
    assert "fetch(" not in app
    assert "XMLHttpRequest" not in app
    assert "WebSocket" not in app
    assert "EventSource" not in app


def test_documents_payload_cannot_close_its_own_script_tag(tmp_path: Path):
    builder = builder_module()
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "README.md").write_text(
        "# Safe\n\n```html\n</script><script>window.injected = true</script>\n```\n",
        encoding="utf-8",
    )
    output = tmp_path / "docs_view"

    builder.build_docs_view(docs, output)
    payload = (output / "assets" / "documents.js").read_text(encoding="utf-8")

    assert "</script" not in payload.casefold()
    assert _load_generated_documents(output / "assets" / "documents.js")["document_count"] == 1


def test_markdown_renderer_supports_tables_code_and_document_links():
    builder = builder_module()
    markdown = """# Sample

| Name | Value |
|---|---:|
| A | 1 |

```python
print("safe")
```

[Internal](../02_요구사항/통합_요구사항서.md)
[External](https://example.com/reference)
"""

    rendered = builder.render_markdown(
        markdown,
        current_path="03_설계/sample.md",
        document_ids={"02_요구사항/통합_요구사항서.md": "02_요구사항/통합_요구사항서"},
    )

    assert "<table>" in rendered
    assert '<pre><code class="language-python">' in rendered
    assert (
        'href="#doc=02_%EC%9A%94%EA%B5%AC%EC%82%AC%ED%95%AD%2F%ED%86%B5%ED%95%A9_%EC%9A%94%EA%B5%AC%EC%82%AC%ED%95%AD%EC%84%9C"'
        in rendered
    )
    assert 'href="https://example.com/reference"' in rendered
    assert 'target="_blank"' in rendered


def test_official_markdown_and_generated_html_have_no_broken_local_links():
    broken: list[str] = []

    for markdown_path in DOCS.rglob("*.md"):
        markdown = markdown_path.read_text(encoding="utf-8")
        for raw_target in re.findall(r"!?\[[^\]]*\]\(([^)]+)\)", markdown):
            target = raw_target.strip().split(maxsplit=1)[0].strip("<>")
            parsed = urlsplit(target)
            if parsed.scheme or target.startswith(("#", "mailto:")):
                continue
            resolved = (markdown_path.parent / unquote(parsed.path)).resolve()
            if not resolved.exists():
                broken.append(f"{markdown_path.relative_to(ROOT)} -> {target}")

    for html_path in DOCS_VIEW.rglob("*.html"):
        parser = _LocalReferenceParser()
        parser.feed(html_path.read_text(encoding="utf-8"))
        for target in parser.references:
            parsed = urlsplit(target)
            if parsed.scheme or target.startswith(("#", "mailto:", "data:")):
                continue
            resolved = (html_path.parent / unquote(parsed.path)).resolve()
            if not resolved.exists():
                broken.append(f"{html_path.relative_to(ROOT)} -> {target}")

    assert broken == []


def test_docs_view_includes_plain_language_daily_work_logs(tmp_path: Path):
    builder = builder_module()
    output = tmp_path / "docs_view"

    builder.build_docs_view(DOCS, output)
    data = _load_generated_documents(output / "assets" / "documents.js")
    groups = {group["name"]: group for group in data["groups"]}

    assert "07_쉬운_작업일지" in groups
    assert groups["07_쉬운_작업일지"]["count"] >= 6
    assert {
        document["name"]
        for document in data["documents"]
        if document["folder"] == "07_쉬운_작업일지"
    } >= {
        "README",
        "2026-09-12~17_검색과_근거찾기",
        "2026-09-18_안전검증과_사람검수",
        "2026-09-19_검색방식_3가지_비교",
        "2026-09-20_Gemini_검색과_답변개선",
        "용어를_쉽게_설명",
    }


def test_docs_view_includes_plain_language_program_file_guide(tmp_path: Path):
    builder = builder_module()
    output = tmp_path / "docs_view"

    builder.build_docs_view(DOCS, output)
    data = _load_generated_documents(output / "assets" / "documents.js")
    documents = {document["id"]: document for document in data["documents"]}

    guide = documents["00_기획/프로그램_파일_쉬운_안내"]
    assert guide["title"] == "프로그램 파일을 쉽게 찾아보는 안내서"
    assert "실제 웹앱이 작동하는 핵심 프로그램" in guide["search_text"]
    assert "기능이 고장 나지 않았는지 자동 확인하는 검사" in guide["search_text"]
    assert "평가·검수·문서 생성에 사용하는 보조 도구" in guide["search_text"]

    for path in (ROOT / "src" / "README.md", ROOT / "tests" / "README.md", ROOT / "tools" / "README.md"):
        assert path.is_file()
        assert "개발자가 아닌 사용자를 위한 안내" in path.read_text(encoding="utf-8")


def test_official_documents_describe_the_same_current_project_state():
    project_spec = (DOCS / "00_기획" / "프로젝트_명세서.md").read_text(encoding="utf-8")
    current = (DOCS / "00_기획" / "현재_프로젝트_상태.md").read_text(encoding="utf-8")
    file_guide = (DOCS / "00_기획" / "프로그램_파일_쉬운_안내.md").read_text(encoding="utf-8")
    table_image = (DOCS / "03_설계" / "표_이미지_설계서.md").read_text(encoding="utf-8")
    architecture = (DOCS / "03_설계" / "시스템_아키텍처_설계서.md").read_text(encoding="utf-8")
    prompt_plan = (DOCS / "03_설계" / "프롬프트_구현계획.md").read_text(encoding="utf-8")
    changelog = (DOCS / "06_변경이력" / "변경이력.md").read_text(encoding="utf-8")
    recovery = (DOCS / "06_변경이력" / "복구기준점.md").read_text(encoding="utf-8")
    root_readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "v1.0-rc1` 후보 상태" not in project_spec
    assert "TF027과\npositive Gold 사람 승인은 pending" not in project_spec
    assert "T11` required coverage 보강 완료·Live 재평가 대기" in current
    assert "checklist 10개 항목: 1차 사람 검수 완료" in table_image
    assert "Production image Gold로 사용하지 않음" in table_image
    assert "10/10 1차 검수 완료" in architecture
    assert "현재 평가 결과는 주로 `workspace`" in file_guide
    assert "`artifacts` | 평가 보고서" not in file_guide
    assert "이 계획의 구현은 완료" in prompt_plan
    assert "`src/validator.py`" not in prompt_plan
    assert "Production retrieval·validator 변경: 0건" in changelog
    assert "Production 변경: 0건" not in changelog
    assert "T11 Prompt 로컬 보강 완료·Live 재평가 대기" in recovery
    assert "설정에서 승인한 AI Provider" in root_readme
    assert "검색된 근거만 Groq에 전달" not in root_readme


def test_plain_language_logs_distinguish_the_two_uat_case_sets():
    log = (DOCS / "07_쉬운_작업일지" / "2026-09-21_청킹_자동검사.md").read_text(
        encoding="utf-8"
    )

    assert "진정간호 일반화 회귀 질문 45개" in log
