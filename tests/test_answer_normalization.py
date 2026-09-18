import json
import pytest
from mvp.ai import validate_answer
from mvp.library import Chunk, Hit
from mvp.settings import GuideError

OBS = '(수혈 전, 수혈 15분, 수혈 종료 시까지 30분마다 활력징후와 부작용 유무 관찰)'
ANSWER = '수혈 전, 수혈 15분, 수혈 종료 시까지 30분마다 활력징후와 부작용 유무를 관찰한다.'


def validate(source, answer, section='', quote=None):
    chunk = Chunk('one', 'doc', 'synthetic.pdf', 1, '', section, None, source, 0)
    raw = json.dumps({'answerable': True, 'statements': [{'text': answer,
        'evidence': [{'chunk_id': 'one', 'quote': quote or source}]}]})
    return validate_answer(raw, [Hit(chunk, .9)], trace={})


def test_observation_surface_form():
    result = validate(OBS, ANSWER)
    assert result.answerable
    assert result.statements[0].evidence[0].quote in OBS


@pytest.mark.parametrize('answer', ['방사선 조사는 적혈구와 혈소판 혈액제제에 적용한다.',
                                  '적혈구, 혈소판 혈액제제에 방사선 조사를 시행한다.'])
def test_target_field_with_explicit_subject(answer):
    result = validate('대상혈액: 적혈구, 혈소판 혈액제제', answer, '방사선조사 혈액제제')
    assert result.answerable
    assert result.statements[0].evidence[0].quote in '대상혈액: 적혈구, 혈소판 혈액제제'


@pytest.mark.parametrize('answer', [ANSWER.replace('30분', '15분'),
    ANSWER.replace('30분', '30시간'), ANSWER.replace('수혈 종료 시까지 ', ''),
    ANSWER.replace('수혈 전, ', ''), ANSWER.replace('관찰한다.', '관찰하지 않는다.'),
    ANSWER.replace('관찰한다.', '관찰하고 약물을 투여한다.')])
def test_changed_facts_rejected(answer):
    with pytest.raises(GuideError): validate(OBS, answer)


@pytest.mark.parametrize('source,answer,section', [
    ('대상혈액: 적혈구, 혈소판 혈액제제', '방사선 조사는 적혈구와 혈소판 혈액제제에 적용한다.', ''),
    ('대상혈액: 적혈구, 혈소판 혈액제제', '방사선 조사는 적혈구와 혈장 혈액제제에 적용한다.', '방사선조사 혈액제제'),
    ('혈장제제는 방사선 조사를 하지 않는다.', '혈장제제는 방사선 조사를 한다.', ''),
    ('조건에 해당하면 관찰', '관찰한다.', ''),
    ('대상혈액: 적혈구, 혈소판 혈액제제 (조건 충족 시)', '방사선 조사는 적혈구와 혈소판 혈액제제에 적용한다.', '방사선조사 혈액제제'),
])
def test_no_inference_or_condition_loss(source, answer, section):
    with pytest.raises(GuideError): validate(source, answer, section)


def test_partial_quote_cannot_remove_condition():
    with pytest.raises(GuideError):
        validate('(조건 충족 시 관찰)', '관찰한다.', quote='충족 시 관찰')


def test_outer_parentheses_only():
    assert validate(OBS, OBS[1:-1]).answerable


def test_whitespace_only():
    assert validate('(활력징후와  부작용\n유무 관찰)', '활력징후와 부작용 유무를 관찰한다.').answerable


def test_decimal_not_erased():
    with pytest.raises(GuideError):
        validate('(0.5 mL 사용)', '05 mL 사용.')


def test_negation_inside_parentheses_is_preserved():
    with pytest.raises(GuideError):
        validate('(조건 충족 시에도 사용하지 않는다)', '조건 충족 시에도 사용한다.')


def test_label_validation_still_applies():
    chunk = Chunk('one', 'doc', 'synthetic.pdf', 1, '', '', None, OBS, 0)
    raw = json.dumps({'answerable': True, 'statements': [{'text': ANSWER,
        'label': '활력징후 및 부작용 확인 시점',
        'evidence': [{'chunk_id': 'one', 'quote': OBS}]}]})
    with pytest.raises(GuideError):
        validate_answer(raw, [Hit(chunk, .9)])
