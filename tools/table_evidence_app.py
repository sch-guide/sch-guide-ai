"""Local evaluation UI for exact structured table retrieval.

Run with:
    streamlit run tools/table_evidence_app.py

The app reads the local PDF/catalog at runtime. It never writes source text to
artifacts and does not call a generation, embedding, or vision API.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

import streamlit as st

from tools.chroma_baseline_evaluate import load_catalog
from tools.structured_table_evidence import (
    TableRecord,
    TableSearchHit,
    extract_table_records,
    render_table_markdown,
    search_table_records,
)

ROOT = Path(__file__).resolve().parents[1]
PDF = ROOT / 'data' / '실무지침서_수혈간호.pdf'
CATALOG = ROOT / 'data' / 'library' / 'catalog.sqlite3'
DOCUMENT_NAME = '실무지침서_수혈간호.pdf'


def local_safety_contract() -> dict[str, Any]:
    return {
        'scope': 'local_evaluation_only',
        'generation_api_calls': 0,
        'artifact_source_text_writes': False,
        'production_retrieval_changed': False,
        'image_interpretation_enabled': False,
    }


def build_hit_view(
    hit: TableSearchHit,
    records: Sequence[TableRecord],
) -> dict[str, Any]:
    record = next((item for item in records if item.table_id == hit.table_id), None)
    if record is None:
        raise ValueError('table record not found')
    row = next((item for item in record.rows if item.row_id == hit.row_id), None)
    if row is None:
        raise ValueError('table row not found')
    return {
        'document': record.document_name,
        'page': record.page,
        'table_id': record.table_id,
        'row_id': row.row_id,
        'row_index': row.row_index,
        'score': hit.score,
        'headers': tuple(cell.text for cell in record.header),
        'cells': tuple(cell.text for cell in row.cells),
        'source_chunk_ids': row.source_chunk_ids,
        'extraction_mode': record.extraction_mode,
        'markdown': render_table_markdown(record, row_indexes=(row.row_index,)),
    }


@st.cache_resource(show_spinner=False)
def load_local_records() -> tuple[TableRecord, ...]:
    metadata, chunks = load_catalog(CATALOG, document_name=DOCUMENT_NAME)
    if metadata['chunk_count'] != 105:
        raise ValueError('transfusion catalog must contain exactly 105 chunks')
    return extract_table_records(PDF, metadata['id'], DOCUMENT_NAME, chunks)


def main() -> None:
    st.set_page_config(page_title='SCHAT 표 근거 검수', layout='wide')
    st.title('수혈 지침 표 근거 로컬 검수')
    st.caption('PDF exact cell/row만 표시합니다. 생성 API·vision·production retrieval은 사용하지 않습니다.')
    records = load_local_records()
    st.info(f'구조화 표 {len(records)}개 · 로컬 evaluation only')
    question = st.text_input('질문을 입력하세요', placeholder='예: 제품별 보관 조건을 비교해줘')
    if not st.button('표 근거 검색', type='primary', disabled=not question.strip()):
        return
    hits = search_table_records(question, records, limit=10)
    if not hits:
        st.warning('일치하는 구조화 표 행을 찾지 못했습니다.')
        return
    for rank, hit in enumerate(hits, 1):
        view = build_hit_view(hit, records)
        st.subheader(f'{rank}위 · p.{view["page"]} · row {view["row_index"]}')
        st.caption(
            f'{view["document"]} · {view["table_id"]} · '
            f'score {view["score"]:.4f} · {view["extraction_mode"]}'
        )
        st.markdown(view['markdown'])
        with st.expander('로컬 citation metadata'):
            st.code('\n'.join(view['source_chunk_ids']) or '(linked chunk 없음)')


if __name__ == '__main__':
    main()
