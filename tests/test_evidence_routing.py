from mvp.evidence_routing import route_evidence
from mvp.query import plan_query


def test_generic_text_table_mixed_and_image_routes():
    text = route_evidence('진정간호 절차는?', kind='procedure')
    table = route_evidence('혈액제제별 보관 온도를 비교해줘', kind='comparison')
    mixed = route_evidence('수혈 반응과 제품별 보관 기준을 함께 설명해줘', kind='summary')
    image = route_evidence('수혈 workflow 화면의 순서를 알려줘', kind='procedure')

    assert text.primary == 'text'
    assert text.candidates == ('text',)
    assert text.status == 'ready'
    assert table.primary == 'table'
    assert table.candidates == ('table', 'text')
    assert mixed.candidates == ('table', 'text')
    assert image.primary == 'image'
    assert image.candidates == ('image',)
    assert image.status == 'pending_image_review'


def test_query_plan_records_route_without_changing_out_of_scope_safety():
    plan = plan_query('화성 우주선의 궤도 계산 공식은?')

    assert plan.domain == 'out_of_scope'
    assert plan.evidence_types == ('text',)
    assert plan.evidence_route_status == 'ready'


def test_query_plan_records_table_candidate_without_enabling_table_retrieval():
    plan = plan_query('혈액제제별 보관 온도를 비교해줘')

    assert plan.kind == 'comparison'
    assert plan.evidence_types == ('table', 'text')
    assert plan.evidence_route_status == 'ready'
    assert not hasattr(plan, 'table_results')
