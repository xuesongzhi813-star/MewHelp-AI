"""统一工具执行器 execute_tool 测试。

关键设计:
- 全部用 fake 工具(不碰真实五工具与真实库):
  - 行为类 fake 用 @tool 装普通闭包函数(计数器用 list.append,避免闭包内
    不可变绑定;pool 线程里追加,主线程在 execute_tool 返回后断言,时序安全);
  - 校验错计数的 fake 直接鸭子类型实现 .invoke(真实 StructuredTool 的
    ValidationError 发生在 func 执行之前,func 内计数器数不到 invoke 次数);
- 注册表注入:executor 运行时读 app.tools.TOOL_REGISTRY 模块变量,
  monkeypatch 该变量即可换成纯 fake 注册表;
- 超时注入:executor 运行时读 app.executor.get_settings 模块变量,
  monkeypatch 成 SimpleNamespace(tool_timeout=...) 即可,不动真实 .env
  与 lru_cache(与 test_tools.py 的注入手法同一意图)。
"""

import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from langchain_core.tools import tool
from pydantic import BaseModel, Field

import app.tools as tools_mod
from app.executor import execute_tool
import app.executor as executor_mod


# ---------------------------------------------------------------------------
# fake 工具构造器
# ---------------------------------------------------------------------------


def _make_echo_tool() -> tuple:
    """最简单的成功工具:原样返回固定文本。返回 (工具, 调用记录 list)。"""
    calls: list = []

    @tool
    def fake_echo() -> str:
        """测试用:返回固定文本。"""
        calls.append(1)
        return "echo-ok"

    return fake_echo, calls


def _make_flaky_tool(fail_first: int) -> tuple:
    """前 fail_first 次抛 RuntimeError,之后返回成功文本。"""
    calls: list = []

    @tool
    def fake_flaky() -> str:
        """测试用:先失败若干次再成功。"""
        calls.append(1)
        if len(calls) <= fail_first:
            raise RuntimeError("fake 内部故障")
        return "flaky-recovered"

    return fake_flaky, calls


def _make_always_raise_tool() -> tuple:
    """始终抛 RuntimeError。"""
    calls: list = []

    @tool
    def fake_broken() -> str:
        """测试用:始终抛异常。"""
        calls.append(1)
        raise RuntimeError("fake 永久故障")

    return fake_broken, calls


def _make_slow_tool(sleep_seconds: float) -> tuple:
    """先 sleep 再返回:配合极小 tool_timeout 制造超时。"""
    calls: list = []

    @tool
    def fake_slow() -> str:
        """测试用:故意 sleep。"""
        calls.append(1)
        time.sleep(sleep_seconds)
        return "slow-done"

    return fake_slow, calls


class _ValidationBoomTool:
    """鸭子类型假工具:invoke 必抛 pydantic ValidationError,并精确计数。

    真实 StructuredTool 的参数校验发生在 func 执行之前,func 内的计数器
    数不到 invoke 次数,所以这里直接实现 .invoke 自己抛,验证「不重试」。
    """

    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, args: dict) -> str:
        self.calls += 1
        # 必抛 ValidationError:缺必填字段 x
        _StrictArgs(**args)
        return "never-reached"


class _StrictArgs(BaseModel):
    """只有一个必填字段 x 的严格 schema。"""

    x: int = Field(description="测试必填字段")


class _EchoArgs(BaseModel):
    """带必填字段的 schema:用于真实 StructuredTool 的校验错路径。"""

    order_id: str = Field(description="订单号,必填")


@pytest.fixture
def fake_registry(monkeypatch):
    """把 app.tools.TOOL_REGISTRY 换成可注入的空 dict,测例自行装 fake 工具。

    executor 运行时通过 app.tools 模块属性取注册表,patch 后立即可见。
    """
    registry: dict = {}
    monkeypatch.setattr(tools_mod, "TOOL_REGISTRY", registry)
    return registry


# ---------------------------------------------------------------------------
# 正常路径
# ---------------------------------------------------------------------------


def test_execute_tool_returns_result_as_is(fake_registry):
    """正常路径:返回工具结果原样(不额外包装)。"""
    echo, _calls = _make_echo_tool()
    fake_registry["fake_echo"] = echo
    result = execute_tool("fake_echo", {})
    assert isinstance(result, str)
    assert result == "echo-ok"


# ---------------------------------------------------------------------------
# 未注册工具
# ---------------------------------------------------------------------------


def test_execute_tool_unknown_name(fake_registry):
    """未注册工具:中文错误文本含工具名与可用工具清单,不抛异常。"""
    echo, _calls = _make_echo_tool()
    fake_registry["fake_echo"] = echo
    result = execute_tool("no_such_tool", {})
    assert isinstance(result, str)
    assert "no_such_tool" in result
    assert "不存在" in result
    # 附可用工具清单,便于模型纠正
    assert "fake_echo" in result
    # fake 工具未被误执行
    assert _calls == []


# ---------------------------------------------------------------------------
# 参数校验错:不重试
# ---------------------------------------------------------------------------


def test_execute_tool_validation_error_runs_once(fake_registry):
    """参数校验错:只执行 1 次(不重试),返回中文提示。"""
    boom = _ValidationBoomTool()
    fake_registry["fake_strict"] = boom
    result = execute_tool("fake_strict", {})  # 缺必填字段 x
    assert isinstance(result, str)
    assert "fake_strict" in result
    assert "参数校验失败" in result
    assert "参数缺失或类型错误" in result
    # 关键断言:不重试,只 invoke 了 1 次
    assert boom.calls == 1


def test_execute_tool_validation_error_via_real_tool(fake_registry):
    """真实 StructuredTool 路径:args_schema 必填缺失时 invoke 抛
    ValidationError,executor 捕获后转中文文本(同样只执行 1 次)。"""
    calls: list = []

    @tool(args_schema=_EchoArgs)
    def fake_picky(order_id: str) -> str:
        """测试用:必填 order_id。"""
        calls.append(1)
        return order_id

    fake_registry["fake_picky"] = fake_picky
    result = execute_tool("fake_picky", {})  # 缺 order_id
    assert "fake_picky" in result
    assert "参数校验失败" in result
    # 校验发生在 func 之前,func 从未执行;executor 也只尝试了 1 次
    assert calls == []


# ---------------------------------------------------------------------------
# 超时:重试 1 次后报错
# ---------------------------------------------------------------------------


def test_execute_tool_timeout_retries_once_then_fails(fake_registry, monkeypatch):
    """超时:注入极小 tool_timeout,工具必超时 → 执行 2 次(重试 1 次)
    后返回含「超时」类别的中文错误文本。

    超时上限 0.1s / 工具 sleep 0.5s:留足余量(Windows 定时器粒度约 15ms,
    慢机也不至于工具先跑完导致漏断言),总耗时约 0.2s(两次等待都早退)。
    """
    slow, calls = _make_slow_tool(sleep_seconds=0.5)
    fake_registry["fake_slow"] = slow
    # 不动 lru_cache,直接替换 executor 模块内的 get_settings 引用
    monkeypatch.setattr(
        executor_mod, "get_settings", lambda: SimpleNamespace(tool_timeout=0.1)
    )

    result = execute_tool("fake_slow", {})

    assert isinstance(result, str)
    assert "fake_slow" in result
    assert "超时" in result
    assert "重试" in result
    # 首次超时 + 重试 1 次 = 2 次执行
    assert len(calls) == 2
    # 超时后底层线程仍在跑(python 线程不可强杀),但结果里不体现


# ---------------------------------------------------------------------------
# 首次异常重试成功
# ---------------------------------------------------------------------------


def test_execute_tool_retries_once_and_succeeds(fake_registry):
    """首次 RuntimeError、重试成功:返回成功结果,共执行 2 次。"""
    flaky, calls = _make_flaky_tool(fail_first=1)
    fake_registry["fake_flaky"] = flaky

    result = execute_tool("fake_flaky", {})

    assert result == "flaky-recovered"
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# 二次全败
# ---------------------------------------------------------------------------


def test_execute_tool_all_attempts_fail(fake_registry):
    """始终抛异常:执行 2 次后返回统一错误文本,不向上抛异常。"""
    broken, calls = _make_always_raise_tool()
    fake_registry["fake_broken"] = broken

    result = execute_tool("fake_broken", {})

    assert isinstance(result, str)
    assert "fake_broken" in result
    assert "执行失败" in result
    assert "执行出错" in result
    assert len(calls) == 2  # 首次 + 重试 1 次,不再多


# ---------------------------------------------------------------------------
# 错误文本质量:不泄漏内部痕迹
# ---------------------------------------------------------------------------


def test_error_text_has_no_internal_traces(fake_registry, monkeypatch):
    """错误文本不暴露堆栈/文件路径/异常类名等内部痕迹——覆盖全部四类
    错误文本:超时、执行出错、参数校验错、未注册工具。"""
    # 超时类:注入极小超时上限(0.1s 上限 / 0.5s sleep,余量见超时用例注释)
    slow, _calls = _make_slow_tool(sleep_seconds=0.5)
    fake_registry["fake_slow"] = slow
    monkeypatch.setattr(
        executor_mod, "get_settings", lambda: SimpleNamespace(tool_timeout=0.1)
    )
    timeout_result = execute_tool("fake_slow", {})

    # 执行出错类
    broken, _calls2 = _make_always_raise_tool()
    fake_registry["fake_broken"] = broken
    error_result = execute_tool("fake_broken", {})

    # 参数校验错类
    boom = _ValidationBoomTool()
    fake_registry["fake_strict"] = boom
    validation_result = execute_tool("fake_strict", {})

    # 未注册工具类
    unknown_result = execute_tool("no_such_tool", {})

    for text in (timeout_result, error_result, validation_result, unknown_result):
        assert "Traceback" not in text
        assert ".py" not in text  # 文件路径痕迹
        assert "RuntimeError" not in text  # 异常类名不给模型
        assert "sleep" not in text  # 内部实现细节
        assert "ValidationError" not in text  # 异常类名不给模型
        assert "TypeError" not in text  # 异常类名不给模型


# ---------------------------------------------------------------------------
# 兜底分支:契约在极端输入/池状态下也不破
# ---------------------------------------------------------------------------


def test_execute_tool_unhashable_name_falls_back(fake_registry):
    """不可哈希的 name(dict.get 抛 TypeError):兜底接住,返回中文文本。"""
    result = execute_tool(["not", "hashable"], {})  # type: ignore[arg-type]
    assert isinstance(result, str)
    assert "执行失败" in result
    assert "执行出错" in result


def test_execute_tool_pool_shutdown_falls_back(fake_registry, monkeypatch):
    """线程池 shutdown 后 submit 抛 RuntimeError:兜底接住,不向上抛。

    用一个已 shutdown 的池替换模块级 _EXECUTOR,复现评审探针场景。
    """
    echo, _calls = _make_echo_tool()
    fake_registry["fake_echo"] = echo

    dead_pool = ThreadPoolExecutor(max_workers=1)
    dead_pool.shutdown(wait=True)
    monkeypatch.setattr(executor_mod, "_EXECUTOR", dead_pool)

    result = execute_tool("fake_echo", {})
    assert isinstance(result, str)
    assert "fake_echo" in result
    assert "执行失败" in result
    # submit 抛 RuntimeError 走重试循环的「执行出错」分支:两次尝试都被接住
    assert "执行出错" in result
