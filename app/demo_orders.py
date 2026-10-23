"""Fixed synthetic orders shared by cards and read-only tools."""
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class DemoOrder:
    order_id: str
    product: str
    sku: str
    status: str
    amount: float
    placed_at: str
    signed_at: str | None
    condition: str | None
    is_demo: bool = True


_ORDERS = (
    DemoOrder('1001', '复古水洗牛仔夹克', 'MH-DJ101', '已签收', 199.0,
              '2026-10-18 10:00:00', '2026-10-21 14:00:00', None),
    DemoOrder('1002', '美式印花夏威夷衬衫', 'MH-SH201', '已发货', 129.0,
              '2026-10-22 10:00:00', None, None),
    DemoOrder('1003', '灯芯绒高腰阔腿裤', 'MH-PT301', '退款中', 159.0,
              '2026-10-10 10:00:00', '2026-10-15 14:00:00', None),
)


def list_demo_orders():
    return [asdict(order) for order in _ORDERS]


def get_demo_order(order_id):
    return next((asdict(order) for order in _ORDERS if order.order_id == order_id), None)


def get_demo_logistics(order_id):
    order = get_demo_order(order_id)
    if order is None:
        return None
    return {'order_id': order_id, 'carrier': '顺丰速运', 'is_demo': True,
            'current_node': '已签收' if order['signed_at'] else '运输中',
            'eta': order['signed_at'][:10] if order['signed_at'] else '2026-10-25'}
