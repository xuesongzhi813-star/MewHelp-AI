"""Pure synthetic data shared by independent MCP servers. No settings or DB imports."""
from __future__ import annotations

import argparse
import random
import threading
from collections import OrderedDict
from copy import deepcopy
from datetime import datetime, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo

from pydantic import Field
from app.demo_orders import get_demo_order

OrderId = Annotated[str, Field(min_length=1, max_length=64, description="需要查询的订单号")]
STATUSES = ("picked_up", "transit", "transit", "out_for_delivery", "delivered")


def cli(default_port):
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1", choices=["127.0.0.1", "localhost", "::1"])
    parser.add_argument("--port", type=int, default=default_port)
    result = parser.parse_args()
    if not 1 <= result.port <= 65535:
        parser.error("port must be between 1 and 65535")
    return result


class MockBusinessData:
    def __init__(self, seed=None, max_cache=1024):
        if type(max_cache) is not int or max_cache <= 0:
            raise ValueError("缓存上限必须为正整数")
        self._rng = random.Random(seed)
        self._cache = OrderedDict()
        self._lock = threading.RLock()
        self._max_cache = max_cache
        self._now = datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)

    @property
    def cache_size(self):
        with self._lock:
            return len(self._cache)

    def _get(self, kind, order_id, generate):
        if not isinstance(order_id, str) or not order_id.strip() or len(order_id) > 64:
            raise ValueError("订单号必须为1至64字符的非空字符串")
        key = (kind, order_id)
        with self._lock:
            if key not in self._cache:
                demo = get_demo_order(order_id)
                if demo is None and self._rng.random() < 0.1:
                    value = {"order_id": order_id, "found": False, "reason": "演示系统未找到相关记录", "is_demo": True}
                else:
                    value = {"order_id": order_id, "found": True, "is_demo": True, **generate(demo)}
                self._cache[key] = value
                while len(self._cache) > self._max_cache:
                    self._cache.popitem(last=False)
            self._cache.move_to_end(key)
            return deepcopy(self._cache[key])

    def logistics(self, order_id):
        def generate(demo):
            if demo and demo["signed_at"]:
                count = 5
                last = datetime.fromisoformat(demo["signed_at"])
            else:
                count = self._rng.randint(1, 4 if demo else 5)
                last = self._now - timedelta(hours=self._rng.randint(1, 6))
            events = [{"time": (last - timedelta(hours=6 * (count - 1 - index))).isoformat(sep=" ", timespec="seconds"),
                       "status": STATUSES[index], "location": ("演示揽收点", "演示转运中心", "演示目的地中心", "演示派送站", "演示收货点")[index]}
                      for index in range(count)]
            eta = last.date() if count == 5 else (self._now + timedelta(days=self._rng.randint(1, 4))).date()
            return {"carrier": self._rng.choice(("顺丰速运", "中通快递", "圆通速递")),
                    "current_status": STATUSES[count - 1], "eta": eta.isoformat(), "events": events}
        return self._get("logistics", order_id, generate)

    def warranty(self, order_id):
        def generate(demo):
            status = self._rng.choice(("covered", "expired", "no_record"))
            expiry = None
            if status != "no_record":
                expiry = (self._now + timedelta(days=self._rng.randint(1, 60) * (1 if status == "covered" else -1))).date().isoformat()
            return {"warranty_status": status, "expires_at": expiry,
                    "coverage_note": "仅为演示售后保障记录；实际保障范围以店铺政策和人工核实为准。"}
        return self._get("warranty", order_id, generate)

    def return_progress(self, order_id):
        def generate(demo):
            status = self._rng.choice(("requested", "approved", "in_transit", "received", "refunded", "rejected"))
            next_steps = {"requested": "等待审核", "approved": "请按演示指引寄回",
                          "in_transit": "等待退件送达", "received": "等待核验处理",
                          "refunded": "演示退款流程已完成，不代表真实资金到账", "rejected": "请联系人工核实原因"}
            return {"return_no": "DEMO-R" + str(self._rng.randint(100000, 999999)),
                    "status": status, "updated_at": (self._now - timedelta(hours=self._rng.randint(1, 48))).isoformat(sep=" ", timespec="seconds"),
                    "next_step": next_steps[status]}
        return self._get("return_progress", order_id, generate)

