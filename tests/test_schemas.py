import pytest
from pydantic import ValidationError

from app.schemas import ChatRequest, ExtractRequest, AfterSalesInfo


def test_chat_request_rejects_empty_fields():
    with pytest.raises(ValidationError):
        ChatRequest(session_id="", message="hi")


def test_after_sales_info_defaults():
    info = AfterSalesInfo(is_after_sales=True, order_id="SO123", request_type="仅退款")
    assert info.expected_solution is None
