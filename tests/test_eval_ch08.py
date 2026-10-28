import pytest
from scripts.eval_ch08 import load_cases,score_case


def test_frozen_cases_have_expected_sizes_disjoint_questions_and_valid_labels():
    dev,_=load_cases('dev'); acceptance,_=load_cases('acceptance')
    assert len(dev)==24 and len(acceptance)==36
    assert not {c['question'] for c in dev}&{c['question'] for c in acceptance}


def test_mock_or_service_failure_cannot_establish_real_quality():
    case={'expected_operation':'none','expected_candidate':None,'expected_route':'refund'}
    good={'operation':'none','candidate':None,'route':'refund','safe':True}
    assert score_case(case,good,mode='real')['passed']
    assert not score_case(case,good,mode='mock')['quality_verified']
    assert not score_case(case,{**good,'service_error':'unavailable'},mode='real')['passed']
    assert not score_case(case,{**good,'safe':False},mode='real')['passed']
    assert not score_case(case,{**good,'operation':'create_ticket'},mode='real')['passed']
