import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TF027 = ROOT / 'tests' / 'fixtures' / 'tf027_image_human_review_checklist.json'
UAT = ROOT / 'tests' / 'fixtures' / 'schat_v1_operational_uat.json'


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def test_tf027_stays_pending_until_every_clinical_visual_check_is_human_approved():
    checklist = load(TF027)

    assert checklist['case_id'] == 'TF027'
    assert checklist['needs_human_review'] is True
    assert checklist['review_status'] == 'needs_human_review'
    assert checklist['production_gold_approved'] is False
    assert checklist['included_in_aggregate'] is False
    assert checklist['vision_api_calls'] == 0
    assert checklist['raw_image_stored'] is False
    assert checklist['source_reference']['page'] == 11
    required = {
        'figure_boundary',
        'start_and_end_nodes',
        'node_labels',
        'arrow_direction_and_connections',
        'decision_branch_conditions',
        'workflow_order',
        'numbers_units_and_times',
        'caption_nearby_text_relationship',
        'illegible_or_occluded_elements',
        'production_gold_approval',
    }
    rows = checklist['checks']
    assert {row['check_id'] for row in rows} == required
    assert all(row['status'] == 'unreviewed' for row in rows)
    assert all(row['reviewed_value'] is None for row in rows)
    assert checklist['approval']['reviewer'] is None
    assert checklist['approval']['reviewed_at'] is None


def test_operational_uat_has_36_unique_cases_and_required_behavior_coverage():
    fixture = load(UAT)
    cases = fixture['cases']

    assert fixture['dataset_version'] == 'schat-v1-operational-uat-v1'
    assert len(cases) == 36
    assert len({case['case_id'] for case in cases}) == 36
    assert len({case['question'] for case in cases}) == 36
    assert {'sedation', 'transfusion', 'out_of_scope'} <= {
        case['document_scope'] for case in cases
    }
    assert {
        'fact_specific',
        'procedure',
        'preparation',
        'cautions',
        'temporal',
        'product_specific',
        'table_lookup',
        'comparison',
        'summary',
        'follow_up',
        'image_workflow',
        'negative',
    } <= {case['question_type'] for case in cases}


def test_operational_uat_keeps_provider_and_image_execution_behind_approval():
    fixture = load(UAT)

    assert fixture['actual_provider_calls'] == 0
    assert fixture['actual_vision_calls'] == 0
    assert fixture['live_execution_status'] == 'separate_approval_required'
    for case in fixture['cases']:
        assert case['actual_provider_calls'] == 0
        assert case['max_provider_calls_after_approval'] in {0, 1}
        if case['expected_behavior'] == 'abstain':
            assert case['max_provider_calls_after_approval'] == 0
        if case['question_type'] == 'image_workflow':
            assert case['expected_behavior'] == 'pending_human_review'
            assert case['max_provider_calls_after_approval'] == 0
            assert case['needs_human_review'] is True


def test_uat_expected_routes_are_bounded_and_out_of_scope_is_zero_call():
    fixture = load(UAT)
    allowed_routes = {'text', 'table', 'mixed', 'image_pending', 'pre_llm_block'}

    assert all(case['expected_evidence_type'] in allowed_routes for case in fixture['cases'])
    negatives = [
        case for case in fixture['cases'] if case['document_scope'] == 'out_of_scope'
    ]
    assert len(negatives) == 4
    assert all(case['expected_behavior'] == 'abstain' for case in negatives)
    assert all(case['expected_provider_calls'] == 0 for case in negatives)
    assert all(case['expected_fixed_abstention'] for case in negatives)
