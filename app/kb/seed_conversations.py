"""ch03 演示会话种子:3 组真实感历史会话,供对话挖掘 job 演示。

内容刻意覆盖:
- 可抽取的售后 / 运费 / FAQ 知识;
- 含订单号、快递单号的一次性信息;
- 纯寒暄。

幂等约定:``user_id`` 以 ``seed-ch03-`` 开头;同 user_id 已有任意会话时
整组跳过,不追加消息。``seed_demo_conversations`` 只 flush 不 commit,
事务边界归调用方(测试回滚 / CLI 提交)。
"""

from __future__ import annotations

import sys

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.db import get_session_local
from app.models import Conversation, Message

# 每组:user_id + 按时间顺序的 (role, content)。
DEMO_CONVERSATIONS: tuple[dict, ...] = (
    {
        "user_id": "seed-ch03-aftersales",
        "messages": (
            ("user", "你好,我买的鞋子开胶了,还在售后期内吗?"),
            (
                "assistant",
                "有的。自签收之日起 15 天内出现开胶、断底等质量问题,可申请换货:"
                "进入「我的订单」→「申请售后」→ 选择「换货」并上传问题照片,"
                "审核通过后按页面提示寄回,仓库验收合格后 48 小时内为您发出新鞋。",
            ),
            ("user", "换货的运费谁承担?"),
            (
                "assistant",
                "质量问题导致的换货,寄回与重新发货的运费均由本店承担;"
                "请勿使用到付,先垫付后凭运单联系在线客服报销。",
            ),
        ),
    },
    {
        "user_id": "seed-ch03-shipping",
        "messages": (
            ("user", "下单后多久发货?运费怎么算?"),
            (
                "assistant",
                "订单支付成功后 48 小时内安排发货。单笔订单实付满 99 元包邮;"
                "未满 99 元收取 10 元运费。新疆、西藏等偏远地区的运费"
                "以下单页展示为准。",
            ),
            ("user", "可以指定快递公司吗?"),
            (
                "assistant",
                "目前系统默认合作快递为中通/圆通,暂不支持指定;"
                "如有特殊需求可联系在线客服备注,我们会尽量协调但不能保证。",
            ),
        ),
    },
    {
        "user_id": "seed-ch03-oneoff",
        "messages": (
            ("user", "你好"),
            ("assistant", "您好,我是小喵,很高兴为您服务~"),
            ("user", "我订单 202610190001 现在到哪了?"),
            (
                "assistant",
                "您的订单 202610190001 已于 10 月 18 日 14:00 发出,"
                "快递单号 SF1234567890,当前已到达上海转运中心,预计明天送达。",
            ),
            ("user", "好的,谢谢"),
            ("assistant", "不客气,还有其他问题随时找我~"),
        ),
    },
)


def seed_demo_conversations(session: Session) -> int:
    """幂等插入演示会话,返回新插入的组数;只 flush,不 commit。"""
    inserted = 0
    for demo in DEMO_CONVERSATIONS:
        user_id = demo["user_id"]
        existing = session.scalar(
            select(Conversation.id)
            .where(Conversation.user_id == user_id)
            .limit(1)
        )
        if existing is not None:
            continue
        conversation = Conversation(user_id=user_id)
        session.add(conversation)
        session.flush()  # 拿到会话 id 以写入消息
        for role, content in demo["messages"]:
            session.add(
                Message(
                    conversation_id=conversation.id,
                    role=role,
                    content=content,
                )
            )
        session.flush()
        inserted += 1
    return inserted


def main() -> int:
    """CLI 入口:连库灌入演示会话;连接失败给中文报错、退出码 1。"""
    try:
        with get_session_local()() as session:
            with session.begin():
                inserted = seed_demo_conversations(session)
    except OperationalError as exc:
        print(
            f"连接数据库失败,请检查 .env 中的 DB_URL 与本机 MySQL 是否正常。"
            f"原始错误:{exc}",
            file=sys.stderr,
        )
        return 1
    total = len(DEMO_CONVERSATIONS)
    print(
        f"演示会话灌入完成:新插入 {inserted} 组,"
        f"跳过 {total - inserted} 组(共 {total} 组)。"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
