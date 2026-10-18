"""ch02 统一工具执行器:execute_tool(name, args) -> str。

契约:**永不向上抛异常,永远返回 str**——返回值会包成 ToolMessage 回灌给模型,
错误也一律转成模型可读的中文文本(模型据此向用户致歉或转人工),
绝不把堆栈/内部路径泄给模型,更不能打断 SSE 流。

行为:
1. 未注册工具:返回「工具不存在 + 可用工具清单」的中文提示;
2. 正常执行:线程池 submit + future.result(timeout=tool_timeout);
3. pydantic ValidationError(参数校验错):同样 args 重跑必然同样失败,
   不重试,直接返回中文提示(附工具名);
4. 超时 / 其他运行时异常:立即重试 1 次(不 sleep,演示场景);
5. 重试后仍失败:返回统一中文错误文本,含工具名与失败类别(超时/执行出错)。

实现说明:
- 线程池为模块级单例(max_workers=4),避免每次调用建/销毁线程池;
- Python 线程不可强杀,future.result 超时后底层任务仍在池里跑完,
  只是不再等待其结果(演示场景无害,最多占一个 worker 几秒);
- executor 保持同步签名:调用方(如 async 的聊天编排)自行丢进线程/任务,
  本模块不做 async 版本;
- 最外层兜底 except 只接「细粒度分支漏网」的异常(如不可哈希的 name、
  线程池 shutdown 后 submit 抛 RuntimeError),返回同款中文错误文本,
  保证契约在任何输入/任何池状态下都成立。
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError

from pydantic import ValidationError

from app.config import get_settings
from app import tools as tools_mod

logger = logging.getLogger(__name__)

# 模块级线程池单例:所有工具调用共用;线程名前缀便于排查残留任务
_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="tool-exec")

# 首次 + 重试 1 次,共最多 2 次尝试
_MAX_ATTEMPTS = 2


def _unknown_tool_text(name: str) -> str:
    """未注册工具的中文提示:附上可用工具清单,便于模型自行纠正。"""
    available = "、".join(tools_mod.TOOL_REGISTRY)
    return f'工具"{name}"不存在,可用工具:{available}'


def _failure_text(name: str, category: str) -> str:
    """重试后仍失败的统一错误文本(模型读后应致歉并建议转人工)。"""
    return (
        f'工具"{name}"执行失败({category}):已重试仍失败,'
        "请向用户致歉并说明暂时无法完成该操作,建议稍后再试或转人工客服"
    )


def _run_with_retry(tool, name: str, args: dict) -> str:
    """带重试的执行主循环;假定 name/tool 已合法,异常处理只覆盖执行本身。

    submit 也在 try 内:线程池已 shutdown 时 submit 抛 RuntimeError,
    走「执行出错」分支重试/报错,不会向上逃逸。
    """
    # 记录最后一次失败的类别,决定最终错误文本里写「超时」还是「执行出错」
    last_category = "执行出错"
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        # 超时上限运行时读取(而非 import 时固化),测试 monkeypatch
        # app.executor.get_settings 即可改;每次尝试单独 submit,
        # 超时的旧任务留在池里自然跑完,不阻塞新一轮尝试
        future = _EXECUTOR.submit(tool.invoke, args)
        try:
            return str(future.result(timeout=get_settings().tool_timeout))
        except ValidationError:
            # 参数校验错不值得重试:同样的 args 再跑必然同样失败
            logger.warning("工具 %s 第 %d 次调用参数校验失败(ValidationError)", name, attempt)
            return (
                f'工具"{name}"参数校验失败:参数缺失或类型错误,'
                "请检查并补全调用参数后重试"
            )
        except FutureTimeoutError:
            last_category = "超时"
            logger.warning(
                "工具 %s 第 %d 次调用超时(上限 %s 秒)",
                name,
                attempt,
                get_settings().tool_timeout,
            )
        except Exception as exc:
            last_category = "执行出错"
            # exc 本体只进服务端日志(不回灌模型,无泄漏),便于排查
            logger.warning(
                "工具 %s 第 %d 次调用异常:%s: %s",
                name,
                attempt,
                type(exc).__name__,
                exc,
            )
    return _failure_text(name, last_category)


def execute_tool(name: str, args: dict) -> str:
    """按名字执行注册表中的工具,任何失败都转成中文错误文本返回。

    - name 不在 TOOL_REGISTRY → 「工具不存在」提示;
    - 参数校验错(ValidationError)→ 不重试,直接返回校验失败提示;
    - 超时/运行时异常 → 重试 1 次,再失败返回统一错误文本;
    - 成功路径不打日志;失败路径每个失败尝试记一行(工具名 + 类别/异常)。

    最外层兜底 except 只接漏网异常(不可哈希 name、池 shutdown 后
    submit 抛 RuntimeError 等),返回与二次失败同款的中文错误文本。
    """
    try:
        tool = tools_mod.TOOL_REGISTRY.get(name)
        if tool is None:
            # name 不可哈希时 .get 抛 TypeError,由兜底 except 统一接住
            return _unknown_tool_text(name)
        return _run_with_retry(tool, name, args)
    except Exception as exc:
        # 兜底:细粒度分支漏网的任何异常,保证「永不抛、永远返回 str」契约
        logger.warning(
            "工具 %s 执行器兜底捕获异常:%s: %s",
            name,
            type(exc).__name__,
            exc,
        )
        return _failure_text(f"{name}", "执行出错")
