"""The evaluator must expose model failures rather than hide them behind fallback."""
from scripts.eval_ch06 import summarize_rows


def test_fallback_is_not_first_parse_success():
    report = summarize_rows([{'id': 'x', 'intent_correct': True, 'question_correct': True,
                             'first_parse_ok': False, 'repaired_parse_ok': False,
                             'system_fallback': True, 'chain_id': None}])
    assert report['first_parse_rate'] == 0
    assert report['parsed_after_repair_rate'] == 0
    assert report['system_fallback_count'] == 1


def test_chain_requires_every_turn_correct():
    rows = [{'id': str(n), 'intent_correct': n != 1, 'question_correct': True,
             'first_parse_ok': True, 'repaired_parse_ok': False,
             'system_fallback': False, 'chain_id': 'switch'} for n in range(3)]
    report = summarize_rows(rows)
    assert report['intent_accuracy'] == 2 / 3
    assert report['chains'] == {'switch': False}


def test_empty_evaluation_is_not_a_success():
    report = summarize_rows([])
    assert report['total'] == 0
    assert report['passed'] is False


def test_correct_text_but_wrong_order_fails_the_chain():
    report=summarize_rows([{'id':'x','chain_id':'switch','intent_correct':True,
        'question_correct':True,'order_correct':False,'first_parse_ok':True}])
    assert report['chains']['switch'] is False
    assert report['passed'] is False


def test_optional_order_possessive_is_semantically_equivalent():
    from scripts.eval_ch06 import equivalent_resolved_question
    assert equivalent_resolved_question('先不退了，订单1002包裹在哪里？', '先不退了，订单1002的包裹在哪里？')
    assert not equivalent_resolved_question('订单1002包裹在哪里？', '订单1001的包裹在哪里？')
    assert not equivalent_resolved_question('订单1002签收8天能退吗？', '订单1002签收7天能退吗？')
    assert not equivalent_resolved_question('先不退了，订单1002包裹在哪里？', '订单1002的包裹在哪里？')


async def test_resolved_semantic_score_does_not_hide_exact_mismatch_or_allow_passthrough_rewrite():
    from scripts.eval_ch06 import evaluate_router
    class Gateway:
        async def evaluate_case(self, case):
            return {'intent': '物流', 'standalone_question': '订单1002的包裹在哪里？', 'order_id': '1002', 'first_parse_ok': True}
    case = {'id': 'x', 'chain_id': 'switch', 'expected_intent': '物流',
            'expected_question': '订单1002包裹在哪里？', 'expected_order_id': '1002'}
    result = await evaluate_router([case], gateway_factory=Gateway)
    assert result['summary']['question_accuracy'] == 0
    assert result['summary']['semantic_question_accuracy'] == 1
    assert result['summary']['chains'] == {'switch': True}
    assert result['summary']['exact_chains'] == {'switch': False}
    assert result['summary']['passed'] is True
    result = await evaluate_router([{**case, 'expect_passthrough': True}], gateway_factory=Gateway)
    assert result['summary']['passed'] is False
