import httpx
import pytest

from app.dependencies import get_extractor
from app.main import app
from app.schemas import AfterSalesInfo


class FakeExtractor:
    """测试替身:异步可调用,记录收到的 message,返回固定 AfterSalesInfo。"""

    def __init__(self, result: AfterSalesInfo) -> None:
        self._result = result
        self.received: list[str] = []

    async def __call__(self, message: str) -> AfterSalesInfo:
        self.received.append(message)
        return self._result


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


async def test_extract_returns_after_sales_info(client):
    fake = FakeExtractor(
        AfterSalesInfo(
            is_after_sales=True,
            order_id="SO202610",
            request_type="退货退款",
            expected_solution="退货退款",
        )
    )
    app.dependency_overrides[get_extractor] = lambda: fake

    response = await client.post(
        "/extract", json={"message": "订单SO202610买的鞋开胶了,我要退货退款"}
    )

    assert response.status_code == 200
    assert response.json() == {
        "is_after_sales": True,
        "order_id": "SO202610",
        "request_type": "退货退款",
        "expected_solution": "退货退款",
    }


async def test_extract_non_after_sales_text(client):
    fake = FakeExtractor(AfterSalesInfo(is_after_sales=False))
    app.dependency_overrides[get_extractor] = lambda: fake

    response = await client.post("/extract", json={"message": "今天天气不错"})

    assert response.status_code == 200
    assert response.json() == {
        "is_after_sales": False,
        "order_id": None,
        "request_type": None,
        "expected_solution": None,
    }


async def test_extract_empty_message_rejected(client):
    response = await client.post("/extract", json={"message": ""})
    assert response.status_code == 422
