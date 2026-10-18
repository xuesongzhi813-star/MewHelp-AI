"""faq 种子数据测试:真实连库,事务回滚不留脏数据。

关键设计:
- seed_faq 接收 session 参数(不开事务、不 commit),测试用
  Session.begin() 包裹后整体 rollback——判重查询与插入共享同一事务,
  新插入的行在事务内即可见;
- 真实 faq 表可能已有其他测试/运行留下的行(不假设空表),断言一律基于
  FAQ_SEED 的 question 集合做相对差值,不耦合全表 count;
- 「邮费/运费/快递费」漏召回演示点用独立测试钉死,改动 FAQ_SEED 的人绕不过去。
"""

import json

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

import app.seed as seed_mod
from app.db import get_engine
from app.models import Faq
from app.seed import FAQ_SEED, seed_faq

SEED_QUESTIONS = [item["question"] for item in FAQ_SEED]

# 五个分类必须全覆盖(与设计文档一致)
REQUIRED_CATEGORIES = {"售后政策", "物流配送", "支付优惠", "账户会员", "商品咨询"}


@pytest.fixture
def tx_session():
    """开启事务的会话:测试内的所有写操作最终整体 rollback。"""
    with Session(get_engine()) as session:
        session.begin()
        try:
            yield session
        finally:
            session.rollback()


# ---------- 纯数据检查(不连库)----------


def test_faq_seed_size_and_categories():
    """种子规模约 12 条,且五个分类全覆盖。"""
    assert 10 <= len(FAQ_SEED) <= 15
    assert REQUIRED_CATEGORIES <= {item["category"] for item in FAQ_SEED}


def test_faq_seed_questions_unique():
    """question 必须唯一(判重按 question 精确匹配,重复会破坏幂等语义)。"""
    assert len(SEED_QUESTIONS) == len(set(SEED_QUESTIONS))


def test_faq_seed_contains_return_policy_entry():
    """必含退货政策条目,答案写实:7 天无理由、商品完好、流程步骤。"""
    items = [i for i in FAQ_SEED if "退货政策" in i["question"]]
    assert items, "FAQ_SEED 缺少退货政策条目"
    answer = items[0]["answer"]
    assert "7 天" in answer
    assert "无理由" in answer
    assert "完好" in answer
    # 流程步骤:售后申请 → 寄回 → 退款
    assert "申请售后" in answer
    assert "寄回" in answer
    assert "退款" in answer


def test_faq_seed_has_no_shipping_cost_entries():
    """漏召回演示点:FAQ_SEED 全部文本(含 question/answer/category)
    严禁出现「邮费」「运费」「快递费」及包邮/免邮类表述——写进去了 ch02 验收 3 就废了。"""
    text = json.dumps(FAQ_SEED, ensure_ascii=False)
    for banned in ("邮费", "运费", "快递费", "包邮", "免邮", "免运费", "运费险"):
        assert banned not in text, f"FAQ_SEED 出现了「{banned}」,漏召回演示点被破坏"


# ---------- 真实连库(事务回滚,不留脏数据)----------


def _clear_seed_rows(session: Session) -> None:
    """事务内清掉与种子同 question 的历史残留行,模拟「空表」口径。

    只删种子同款 question,不动表里其他行;整体处于测试事务中,最终回滚。
    """
    session.execute(delete(Faq).where(Faq.question.in_(SEED_QUESTIONS)))


def test_seed_faq_inserts_all_seed_rows(tx_session):
    """空表(相对口径)灌入:新插入条数 = len(FAQ_SEED),且内容抽查命中退货政策条目。"""
    _clear_seed_rows(tx_session)

    inserted = seed_faq(tx_session)
    assert inserted == len(FAQ_SEED)

    # 种子 question 全部落表(相对计数,不碰全表 count)
    found = tx_session.scalars(
        select(Faq.question).where(Faq.question.in_(SEED_QUESTIONS))
    ).all()
    assert set(found) == set(SEED_QUESTIONS)

    # 内容抽查:退货政策条目真的在库里,且字段完整
    row = tx_session.scalar(
        select(Faq).where(Faq.question == "退货政策是什么?")
    )
    assert row is not None
    assert row.category == "售后政策"
    assert "7 天无理由" in row.answer
    assert row.created_at is not None  # 时间列由数据库默认值填充


def test_seed_faq_is_idempotent(tx_session):
    """幂等:同批再灌一次返回 0 新插入,且不产生重复行。"""
    _clear_seed_rows(tx_session)
    assert seed_faq(tx_session) == len(FAQ_SEED)

    # 第二次:全部命中判重,应 0 新插入
    assert seed_faq(tx_session) == 0

    # 每个 question 在表里仍只有一行
    rows = tx_session.scalars(
        select(Faq).where(Faq.question.in_(SEED_QUESTIONS))
    ).all()
    assert len(rows) == len(FAQ_SEED)


def test_seed_faq_skips_existing_questions(tx_session):
    """判重对表里已有行免疫:已存在的 question 跳过且不覆盖,只补缺的。"""
    _clear_seed_rows(tx_session)

    # 预插一条种子同款 question(模拟外部已存在的行,内容不同)
    first = FAQ_SEED[0]
    tx_session.add(
        Faq(question=first["question"], answer="旧答案", category=first["category"])
    )
    tx_session.flush()

    inserted = seed_faq(tx_session)
    assert inserted == len(FAQ_SEED) - 1  # 只补剩下缺的

    # 已存在的行不被覆盖
    row = tx_session.scalar(select(Faq).where(Faq.question == first["question"]))
    assert row.answer == "旧答案"


# ---------- CLI 入口 ----------


def test_main_returns_1_with_chinese_error_on_connection_failure(
    monkeypatch, capsys
):
    """连接失败:main 捕获 OperationalError,中文报错进 stderr,退出码 1。"""
    def _boom():
        raise OperationalError("stmt", {}, Exception("connect refused"))

    monkeypatch.setattr(seed_mod, "get_session_local", _boom)
    assert seed_mod.main() == 1
    assert "数据库" in capsys.readouterr().err
