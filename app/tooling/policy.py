"""Local permission rules. Remote metadata never grants access."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field, replace
from pathlib import Path
from urllib.parse import urlsplit

from app.tooling.validation import NAME, json_text

BUILTIN_EFFECTS = {"query_order": "read", "query_product": "read", "query_faq": "read", "create_ticket": "write"}
ROUTING_INTENTS = frozenset({"物流", "订单", "商品咨询", "退款退货", "售后", "其他"})


@dataclass(frozen=True)
class ToolRule:
    enabled: bool = False
    effect: str = "read"
    alias: str | None = None
    routing_intents: tuple[str, ...] = ()
    timeout: float | None = None
    output_fields: tuple[str, ...] = ()
    enum_map: dict = field(default_factory=dict)


@dataclass(frozen=True)
class PolicySnapshot:
    version: str
    valid: bool
    servers: dict
    rules: dict

    def rule(self, source: str, name: str) -> ToolRule:
        default = ToolRule(enabled=source == "builtin" and name in BUILTIN_EFFECTS,
                           effect=BUILTIN_EFFECTS.get(name, "read"))
        rule = self.rules.get(source + "/" + name, default)
        if source != "builtin":
            server = self.servers.get(source, {})
            return replace(rule, enabled=self.valid and server.get("enabled", False)
                           and rule.enabled and rule.effect == "read")
        if not self.valid and rule.effect == "write":
            return replace(rule, enabled=False)
        return rule


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("配置键重复")
        result[key] = value
    return result


class PolicyLoader:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load(self) -> PolicySnapshot:
        raw = b""
        try:
            raw = self.path.read_bytes()
            if len(raw) > 262144:
                raise ValueError("配置过大")
            data = json.loads(raw, object_pairs_hook=_unique, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if not isinstance(data, dict) or set(data) - {"servers", "rules"}:
                raise ValueError("配置字段无效")
            servers, rules = data.get("servers", {}), data.get("rules", {})
            if not isinstance(servers, dict) or not isinstance(rules, dict):
                raise ValueError("配置必须为对象")
            parsed_servers = {}
            for source, server in servers.items():
                if source == "builtin" or not NAME.fullmatch(source) or not isinstance(server, dict):
                    raise ValueError("Server身份无效")
                if set(server) - {"url", "enabled"} or type(server.get("enabled")) is not bool:
                    raise ValueError("Server配置无效")
                url = urlsplit(server.get("url", ""))
                if url.scheme != "http" or url.hostname not in {"127.0.0.1", "localhost", "::1"} or url.username or url.password or url.fragment or url.query:
                    raise ValueError("本章仅连接本地无凭据HTTP端点")
                if not url.port or not url.path:
                    raise ValueError("Server地址不完整")
                parsed_servers[source] = dict(server)
            parsed_rules = {}
            for identity, value in rules.items():
                source, name = identity.split("/")
                if not NAME.fullmatch(source) or not NAME.fullmatch(name) or not isinstance(value, dict):
                    raise ValueError("规则身份无效")
                if set(value) - {"enabled", "effect", "alias", "routing_intents", "timeout", "output_fields", "enum_map"}:
                    raise ValueError("规则字段无效")
                enabled, effect = value.get("enabled", False), value.get("effect", "read")
                if type(enabled) is not bool or effect not in {"read", "write"}:
                    raise ValueError("规则权限无效")
                if source == "builtin" and name in BUILTIN_EFFECTS and effect != BUILTIN_EFFECTS[name]:
                    raise ValueError("内置效果不能被重定义")
                alias = value.get("alias")
                if alias is not None and (not isinstance(alias, str) or not NAME.fullmatch(alias)):
                    raise ValueError("别名无效")
                intents = value.get("routing_intents", [])
                if not isinstance(intents, list) or any(not isinstance(i, str) or i not in ROUTING_INTENTS for i in intents):
                    raise ValueError("业务路由权限无效")
                timeout = value.get("timeout")
                if timeout is not None and (type(timeout) not in {int, float} or not math.isfinite(timeout) or not 0 < timeout <= 300):
                    raise ValueError("工具时限无效")
                fields = value.get("output_fields", [])
                enum_map = value.get("enum_map", {})
                if not isinstance(fields, list) or any(not isinstance(k, str) for k in fields):
                    raise ValueError("输出投影无效")
                if not isinstance(enum_map, dict) or any(not isinstance(v, dict) or any(not isinstance(t, str) for t in v.values()) for v in enum_map.values()):
                    raise ValueError("枚举映射无效")
                parsed_rules[identity] = ToolRule(enabled, effect, alias, tuple(intents), timeout, tuple(fields), enum_map)
            return PolicySnapshot(hashlib.sha256(raw).hexdigest(), True, parsed_servers, parsed_rules)
        except (OSError, ValueError, TypeError, AttributeError, RecursionError):
            return PolicySnapshot(hashlib.sha256(b"invalid:" + raw).hexdigest(), False, {}, {})

