from pydantic import BaseModel, Field, ConfigDict, StrictBool, field_validator
from typing import Literal, get_args


class ChatRequest(BaseModel):
    """纯聊天接口的请求体。"""

    session_id: str = Field(min_length=1, description="会话 ID,用于区分不同用户的对话")
    message: str = Field(min_length=1, description="用户发送的消息内容")
    user_id: str | None = Field(default=None,pattern=r'^[0-9a-f]{32}$')
    conversation_id: int | None = Field(default=None,gt=0)


class ConfirmTicketRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    session_id: str = Field(min_length=1, max_length=128)
    action_id: str = Field(min_length=1, max_length=64)
    confirmed: StrictBool

    @field_validator('confirmed')
    @classmethod
    def explicit_confirmation(cls, value):
        if value is not True:
            raise ValueError('必须明确确认后才能创建工单')
        return value


class TicketActionResult(BaseModel):
    action_id: str
    status: str
    result: str


class SelectOrderRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    session_id: str = Field(min_length=1, max_length=128)
    selection_id: str = Field(min_length=1, max_length=64)
    order_id: str = Field(min_length=1, max_length=64)


RefundReason = Literal['不喜欢/不合适', '尺码问题', '商品质量问题', '与描述不符', '其他']
REFUND_REASONS = get_args(RefundReason)


class SubmitRefundRequest(ConfirmTicketRequest):
    reason: RefundReason
    note: str = Field(default='', max_length=500)


class ExtractRequest(BaseModel):
    """售后信息抽取接口的请求体。"""

    message: str = Field(min_length=1, description="待抽取售后信息的用户消息")


class AfterSalesInfo(BaseModel):
    """从用户消息中抽取出的售后意图结构化信息。"""

    is_after_sales: bool = Field(description="用户消息是否与售后相关")
    order_id: str | None = Field(default=None, description="订单号,未提供时为 null")
    request_type: str | None = Field(default=None, description="售后诉求类型,如仅退款、退货退款、换货")
    expected_solution: str | None = Field(default=None, description="用户期望的解决方案,未提供时为 null")
