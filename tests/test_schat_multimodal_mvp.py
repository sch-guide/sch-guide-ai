import json
from pathlib import Path

from tools.chroma_baseline_evaluate import load_catalog
from tools.schat_multimodal_mvp_evaluate import evaluate_multimodal_mvp

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / 'data' / 'library' / 'catalog.sqlite3'
PDF = ROOT / 'data' / '실무지침서_수혈간호.pdf'
FIXTURE = ROOT / 'tests' / 'fixtures' / 'transfusion_retrieval_baseline.json'
MULTIMODAL = ROOT / 'tests' / 'fixtures' / 'transfusion_multimodal_retrieval.json'


def test_multimodal_mvp_evaluation_excludes_pending_image_and_is_raw_free():
    summary, manifest, results = evaluate_multimodal_mvp(
        catalog_path=CATALOG,
        pdf_path=PDF,
        fixture_path=FIXTURE,
        multimodal_fixture_path=MULTIMODAL,
    )

    assert summary['table']['record_count'] == 5
    assert summary['table']['row_count'] == 38
    assert summary['table']['approved_case_count'] == 5
    assert summary['table']['hit_at_10'] == 1.0
    assert summary['image']['status'] == 'pending_human_review'
    assert summary['image']['pending_case_ids'] == ['TF027']
    assert summary['generation']['actual_api_calls'] == 0
    assert summary['generation']['controlled_paraphrasing'] == 'mock_only_not_connected'
    assert summary['production']['retrieval_changed'] is False
    assert summary['production']['validators_changed'] is False
    assert all(row['source_question_id'] != 'TF027' for row in results)
    assert all(row['hit_at_10'] for row in results)

    fixture = json.loads(FIXTURE.read_text(encoding='utf-8'))
    _, chunks = load_catalog(CATALOG, document_name=fixture['document']['name'])
    encoded = json.dumps(
        {'summary': summary, 'manifest': manifest, 'results': results},
        ensure_ascii=False,
    )
    assert all(chunk.text not in encoded for chunk in chunks)
    assert all('question' not in row for row in results)
