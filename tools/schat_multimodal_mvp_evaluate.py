"""Raw-free offline evaluation for the SCHAT text/table MVP preparation."""

from __future__ import annotations

import argparse
import html
import json
import platform
from collections import Counter
from pathlib import Path
from typing import Any, Sequence

from tools.chroma_baseline_evaluate import load_catalog, validate_fixture_document
from tools.structured_table_evidence import (
    TableRecord,
    extract_table_records,
    safe_table_record_manifest,
    search_table_records,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / 'data' / 'library' / 'catalog.sqlite3'
DEFAULT_PDF = ROOT / 'data' / '실무지침서_수혈간호.pdf'
DEFAULT_FIXTURE = ROOT / 'tests' / 'fixtures' / 'transfusion_retrieval_baseline.json'
DEFAULT_MULTIMODAL = ROOT / 'tests' / 'fixtures' / 'transfusion_multimodal_retrieval.json'
DEFAULT_OUTPUT = ROOT / 'artifacts' / '2026-09-17_schat-multimodal-mvp'


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding='utf-8'))


def _row_links(records: Sequence[TableRecord]) -> dict[str, tuple[str, ...]]:
    return {
        row.row_id: row.source_chunk_ids
        for record in records
        for row in record.rows
    }


def _case_result(
    multimodal_case: dict[str, Any],
    retrieval_case: dict[str, Any],
    records: Sequence[TableRecord],
) -> dict[str, Any]:
    hits = search_table_records(retrieval_case['question'], records, limit=10)
    links = _row_links(records)
    gold = set(retrieval_case['reference_context_ids'])
    hit_flags = [bool(gold.intersection(links[hit.row_id])) for hit in hits]
    first = next((rank for rank, matched in enumerate(hit_flags, 1) if matched), None)
    retrieved_gold = set().union(*(
        gold.intersection(links[hit.row_id]) for hit in hits
    )) if hits else set()
    return {
        'case_id': multimodal_case['case_id'],
        'case_type': multimodal_case['case_type'],
        'source_question_id': multimodal_case['source_question_id'],
        'first_gold_rank': first,
        'hit_at_1': any(hit_flags[:1]),
        'hit_at_3': any(hit_flags[:3]),
        'hit_at_5': any(hit_flags[:5]),
        'hit_at_10': any(hit_flags[:10]),
        'recall_at_10': len(retrieved_gold) / max(1, len(gold)),
        'retrieved_table_ids': [hit.table_id for hit in hits],
        'retrieved_row_ids': [hit.row_id for hit in hits],
    }


def _mean(rows: Sequence[dict[str, Any]], key: str) -> float:
    return sum(float(row[key]) for row in rows) / max(1, len(rows))


def evaluate_multimodal_mvp(
    *,
    catalog_path: Path,
    pdf_path: Path,
    fixture_path: Path,
    multimodal_fixture_path: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    fixture = _load_json(fixture_path)
    multimodal = _load_json(multimodal_fixture_path)
    metadata, chunks = load_catalog(
        catalog_path, document_name=fixture['document']['name']
    )
    validate_fixture_document(fixture['document'], metadata, chunks)
    if metadata['chunk_count'] != 105:
        raise ValueError('catalog/chunk integrity failure')
    cases_by_id = {case['question_id']: case for case in fixture['cases']}
    approved = [
        case for case in multimodal['cases']
        if case['evaluation_status'] == 'approved'
    ]
    if not approved:
        raise ValueError('approved table gold is required')
    records = extract_table_records(
        pdf_path, metadata['id'], metadata['document_name'], chunks
    )
    if not records:
        raise ValueError('structured table extraction failed')
    results = []
    for case in approved:
        source_id = case['source_question_id']
        source = cases_by_id.get(source_id)
        if source is None or source['needs_human_review']:
            raise ValueError('approved table case has invalid source gold')
        results.append(_case_result(case, source, records))

    pending_image = [
        case['source_question_id']
        for case in multimodal['cases']
        if case['evaluation_status'] == 'pending_image_sequence_interpretation'
        and case.get('source_question_id')
    ]
    modes = Counter(record.extraction_mode for record in records)
    summary = {
        'schema_version': 1,
        'dataset_version': fixture['dataset_version'],
        'document_version': fixture['document_version'],
        'catalog': {
            'document_id': metadata['id'],
            'chunk_count': metadata['chunk_count'],
            'chunk_version': metadata['chunk_version'],
        },
        'text_presentation': {
            'status': 'verified_exact_source_sidecar',
            'clinical_text_rewritten': False,
            'citation_contract_changed': False,
        },
        'table': {
            'status': 'evaluation_only',
            'record_count': len(records),
            'row_count': sum(len(record.rows) for record in records),
            'extraction_modes': dict(modes),
            'approved_case_count': len(results),
            'hit_at_1': _mean(results, 'hit_at_1'),
            'hit_at_3': _mean(results, 'hit_at_3'),
            'hit_at_5': _mean(results, 'hit_at_5'),
            'hit_at_10': _mean(results, 'hit_at_10'),
            'recall_at_10': _mean(results, 'recall_at_10'),
        },
        'image': {
            'status': 'pending_human_review',
            'pending_case_ids': sorted(set(pending_image)),
            'approved_image_dependent_gold': 0,
            'vision_descriptions_generated': 0,
        },
        'generation': {
            'provider_status': 'disabled_unapproved',
            'controlled_paraphrasing': 'mock_only_not_connected',
            'actual_api_calls': 0,
        },
        'production': {
            'retrieval_changed': False,
            'validators_changed': False,
            'facet_slot_changed': False,
            'selection_limit_changed': False,
        },
    }
    manifest = safe_table_record_manifest(records)
    encoded = json.dumps(
        {'summary': summary, 'manifest': manifest, 'results': results},
        ensure_ascii=False,
    )
    if any(chunk.text in encoded for chunk in chunks):
        raise ValueError('raw source text leak detected')
    return summary, manifest, results


def _review_html(summary: dict[str, Any], results: Sequence[dict[str, Any]]) -> str:
    table = summary['table']
    rows = ''.join(
        '<tr>'
        f'<td>{html.escape(row["case_id"])}</td>'
        f'<td>{html.escape(row["case_type"])}</td>'
        f'<td>{row["first_gold_rank"] or "-"}</td>'
        f'<td>{"PASS" if row["hit_at_10"] else "MISS"}</td>'
        '</tr>'
        for row in results
    )
    return f'''<!doctype html><html lang="ko"><meta charset="utf-8">
<title>SCHAT Multimodal MVP Offline Review</title>
<style>body{{font-family:system-ui;margin:40px;max-width:1000px}}table{{border-collapse:collapse;width:100%}}
th,td{{border:1px solid #ccd;padding:8px;text-align:left}}.pending{{color:#8a5a00}}</style>
<h1>SCHAT Multimodal MVP Offline Review</h1>
<p>Generation API calls: <strong>0</strong> · Production retrieval changes: <strong>0</strong></p>
<h2>Text presentation</h2><p>Exact SourceUnit sidecar verified; clinical text rewrite 없음.</p>
<h2>Structured table</h2>
<p>Records {table['record_count']} · Rows {table['row_count']} · Approved cases {table['approved_case_count']}
· Hit@10 {table['hit_at_10']:.4f}</p>
<table><thead><tr><th>Case</th><th>Type</th><th>First gold rank</th><th>Top-10</th></tr></thead>
<tbody>{rows}</tbody></table>
<h2 class="pending">Pending</h2><p>TF027 image sequence, live provider quality evaluation.</p>
</html>'''


def write_artifacts(
    output_dir: Path,
    summary: dict[str, Any],
    manifest: list[dict[str, Any]],
    results: list[dict[str, Any]],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=False)
    payloads = {
        'summary.json': summary,
        'table_manifest.json': manifest,
        'table_results.json': results,
        'environment.json': {
            'python': platform.python_version(),
            'platform': platform.platform(),
            'generation_api_calls': 0,
        },
    }
    for name, payload in payloads.items():
        (output_dir / name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8'
        )
    (output_dir / 'review.html').write_text(
        _review_html(summary, results), encoding='utf-8'
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, default=DEFAULT_CATALOG)
    parser.add_argument('--pdf', type=Path, default=DEFAULT_PDF)
    parser.add_argument('--fixture', type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument('--multimodal-fixture', type=Path, default=DEFAULT_MULTIMODAL)
    parser.add_argument('--output-dir', type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary, manifest, results = evaluate_multimodal_mvp(
        catalog_path=args.catalog,
        pdf_path=args.pdf,
        fixture_path=args.fixture,
        multimodal_fixture_path=args.multimodal_fixture,
    )
    write_artifacts(args.output_dir, summary, manifest, results)
    print(json.dumps({
        'table_record_count': summary['table']['record_count'],
        'approved_case_count': summary['table']['approved_case_count'],
        'table_hit_at_10': summary['table']['hit_at_10'],
        'generation_api_calls': 0,
    }, ensure_ascii=False))


if __name__ == '__main__':
    main()
