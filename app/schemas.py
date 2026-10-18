from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """纯聊天接口的请求体。"""

    session_id: str = Field(min_length=1, description="会话 ID,用于区分不同用户的对话")
    message: str = Field(min_length=1, description="用户发送的消息内容")


class ExtractRequest(BaseModel):
    """售后信息抽取接口的请求体。"""

    message: str = Field(min_length=1, description="待抽取售后信息的用户消息")


class AfterSalesInfo(BaseModel):
    """从用户消息中抽取出的售后意图结构化信息。"""

    is_after_sales: bool = Field(description="用户消息是否与售后相关")
    order_id: str | None = Field(default=None, description="订单号,未提供时为 null")
    request_type: str | None = Field(default=None, description="售后诉求类型,如仅退款、退货退款、换货")
    expected_solution: str | None = Field(default=None, description="用户期望的解决方案,未提供时为 null")
