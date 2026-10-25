from scripts.eval_ch07 import score_summary, score_dialogue


def test_summary_missing_entity_or_request_and_invented_result_fail():
    case=dict(expected_facts=['ORD-1001','13800138000','查物流'],forbidden_claims=['已退款'])
    assert score_summary(case,'ORD-1001 手机13800138000要求查物流',grounded=True,complete=True)['passed']
    for text in ('ORD-1001要求查物流','ORD-1001 手机13800138000','ORD-1001 手机13800138000 查物流 已退款'):
        assert not score_summary(case,text,grounded=True,complete=True)['passed']
    assert not score_summary(case,'ORD-1001 手机13800138000 查物流',grounded=True,complete=True,service_error='timeout')['passed']


def test_dialogue_requires_full_logs_and_default_no_compression():
    case=dict(expected_facts=['1001','未收到'],forbidden_claims=['已经解决'])
    rows=[dict(answer='1001未收到',done=True,history_logged=True,service_error=None) for _ in range(21)]
    assert score_dialogue(case,rows,profile='default',downgrades=0,segments=0)['passed']
    assert not score_dialogue(case,rows,profile='default',downgrades=1,segments=0)['passed']
    rows[0]['history_logged']=False
    assert not score_dialogue(case,rows,profile='default',downgrades=0,segments=0)['passed']
    rows[0]['history_logged']=True
    assert not score_dialogue(case,rows,profile='demo',downgrades=1,segments=0)['passed']
