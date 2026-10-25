from langchain_core.messages import AIMessage,HumanMessage,ToolMessage
from app.context.tokens import count_model_tokens, estimate_text, model_payload


def test_unicode_mixed_estimate_and_fractional_cjk_rounding():
    assert estimate_text('你好abc') == 3
    assert estimate_text('你好abc',cjk_ratio=1.5) == 4
    assert estimate_text('') == 0


def test_tool_args_count_but_artifact_not_sent():
    short=AIMessage(content='',tool_calls=[{'name':'query_order','args':{'order_id':'1'},'id':'c'}])
    long=AIMessage(content='',tool_calls=[{'name':'query_order','args':{'order_id':'长'*100},'id':'c'}])
    assert count_model_tokens([long]) > count_model_tokens([short])+90
    plain=ToolMessage(content='已发货',tool_call_id='c')
    archive=plain.model_copy(update={'artifact':{'raw':'秘密原始结果'*10000}})
    assert count_model_tokens([plain]) == count_model_tokens([archive])
    assert 'artifact' not in str(model_payload([archive]))
    assert '秘密' not in str(model_payload([archive]))


def test_internal_ids_and_flags_are_not_provider_payload():
    m=HumanMessage(content='订单1001',id='db:9',additional_kwargs={'turn_from_msg_id':9})
    assert model_payload([m]) == [{'role':'user','content':'订单1001'}]
    assert count_model_tokens([m],schemas=[{'name':'新工具'}]) > count_model_tokens([m])
