from app.context.contracts import HistoryView, SummaryRow, StoredMessage
from app.context.assembly import validate_summary_reference


def test_earliest_summary_order_is_verified_against_source_range():
    a=SummaryRow(8,1,1,10,14,'订单1001没收到，想查物流。')
    b=SummaryRow(9,1,2,21,30,'订单1002想退货。')
    ctx=HistoryView(1,(a,b),[],'',0)
    rows=[StoredMessage(10,1,'user','订单1001没收到，查物流',10)]
    assert validate_summary_reference('1001','summary:8',question='最开始那个订单后来怎么说',context=ctx,source_rows=rows)
    assert not validate_summary_reference('1001','summary:8',question='它呢',context=ctx,source_rows=rows)
    assert not validate_summary_reference('1001','summary:88',question='最开始那单',context=ctx,source_rows=rows)
    assert not validate_summary_reference('1001','summary:8',question='订单1002呢',context=ctx,source_rows=rows)
    assert not validate_summary_reference('1001','summary:8',question='最开始那单',context=ctx,
        source_rows=[StoredMessage(10,2,'user','订单1001',10)])
