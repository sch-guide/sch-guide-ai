from pathlib import Path

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
TABLE_APP = ROOT / "tools" / "table_evidence_app.py"


def test_verified_text_answer_uses_production_streamlit_renderer_without_outer_numbering():
    script = r'''
from src.ai import Answer, Evidence, Statement
from src.answer_ui import render_answer
from src.library import Chunk, Hit
from src.presentation import AnswerPresentation, StatementPresentation

chunk = Chunk(
    id="chunk-1", document_id="doc-1", document_name="synthetic.pdf", page=1,
    title="합성 지침", section="준비", updated_date="2026-09-01",
    text="1) 환자 상태를 확인한다.", index=0,
)
answer = Answer(
    answerable=True, format="bullets",
    statements=[Statement(
        text=chunk.text,
        evidence=[Evidence(chunk_id=chunk.id, quote=chunk.text)],
    )],
)
presentation = AnswerPresentation(
    (StatementPresentation(0, "su001", "common", "before", (0, 0), "1)"),),
    intent="preparation", answer_format="bullets",
)
render_answer(answer, [Hit(chunk, 1.0)], 0, lambda *args, **kwargs: None,
              presentation=presentation)
'''

    app = AppTest.from_string(script, default_timeout=30).run()

    assert not app.exception
    markdown = [element.value for element in app.markdown]
    assert any("환자 상태를 확인한다" in value for value in markdown)
    assert all(not value.startswith("1. 1") for value in markdown)
    assert any("[1] synthetic.pdf" in button.label for button in app.button)


def test_real_table_evaluation_app_searches_local_catalog_without_provider_calls():
    app = AppTest.from_file(str(TABLE_APP), default_timeout=120).run()

    assert not app.exception
    assert app.title[0].value == "수혈 지침 표 근거 로컬 검수"
    assert "evaluation only" in app.info[0].value

    app.text_input[0].set_value("농축적혈구의 적응증과 용량은 어떻게 되나요?").run()
    app.button[0].click().run()

    assert not app.exception
    assert app.subheader
    assert any("p." in heading.value for heading in app.subheader)
    assert not app.warning
