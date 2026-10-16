"""faq 表种子数据:静态常量 + 幂等灌入 + CLI 入口。

设计要点:
- FAQ_SEED 严禁出现「邮费/运费/快递费」相关内容——这是 ch02 验收刻意
  保留的漏召回演示点(用户问运费问题时 FAQ 检索工具应召回不到),
  tests/test_seed.py 有断言把这一点钉死,别在这里“顺手补上”。
- seed_faq 接收外部 session 且不在函数内 commit:判重查询与插入共享
  同一事务,测试用 begin()/rollback() 包裹即可不留脏数据;
  CLI 场景则由 main() 自己开事务并提交。
- 连接串一律走 get_settings()(真实密码只在 gitignored .env)。
"""

import sys

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.db import get_session_local
from app.models import Faq

# 约 12 条,覆盖五个分类;同一政策刻意用不同口语问法(如「退货政策是什么」
# 与「怎么退货」),利于后续 LIKE 检索的多问法命中。
FAQ_SEED: list[dict] = [
    # ---------- 售后政策 ----------
    {
        "question": "退货政策是什么?",
        "answer": (
            "本店支持 7 天无理由退货:自商品签收之日起 7 天内,商品未经穿着使用、"
            "吊牌完整且包装完好,均可申请退货。退货流程:1) 进入「我的订单」选择对应订单,"
            "点击「申请售后」;2) 选择退货原因并提交申请;3) 审核通过后按页面提示将商品寄回;"
            "4) 仓库验收合格后,退款将在 1-3 个工作日内原路退回您的付款账户。"
        ),
        "category": "售后政策",
    },
    {
        "question": "怎么退货?",
        "answer": (
            "退货操作步骤:打开「我的订单」→ 找到要退货的订单 → 点击「申请售后」→ 选择"
            "「退货退款」并填写原因 → 提交后等待审核(一般 24 小时内)→ 审核通过后按提示"
            "寄回商品即可。注意需在签收后 7 天内、商品完好(未穿着、吊牌齐全)的前提下申请。"
        ),
        "category": "售后政策",
    },
    {
        "question": "换货政策是怎样的?",
        "answer": (
            "自签收之日起 15 天内,商品存在质量问题(如破损、色差、错发)可申请换货:"
            "在「我的订单」中提交售后并选择「换货」,审核通过后按页面提示寄回商品,"
            "仓库收到并验收后 48 小时内为您发出换新商品。非质量问题的尺码不合适,"
            "建议直接走 7 天无理由退货后重新下单。"
        ),
        "category": "售后政策",
    },
    # ---------- 物流配送 ----------
    {
        "question": "下单后多久发货?",
        "answer": (
            "正常情况下,订单支付成功后 48 小时内安排出库发货;预售商品以商品详情页"
            "标注的发货时间为准;大促期间订单量较大,发货可能延迟 1-2 天,敬请谅解。"
            "发货后可在订单详情页查看物流轨迹。"
        ),
        "category": "物流配送",
    },
    {
        "question": "怎么修改收货地址?",
        "answer": (
            "订单尚未发货时:进入「我的订单」→ 订单详情 → 点击「修改收货地址」即可更新;"
            "订单已发货则无法再修改地址,建议留意物流来电,或联系配送员协商送达位置。"
            "如有特殊情况也可联系在线客服协助处理。"
        ),
        "category": "物流配送",
    },
    {
        "question": "怎么查看物流信息?",
        "answer": (
            "进入「我的订单」→ 点击对应订单的「查看物流」,即可实时跟踪包裹的配送进度,"
            "包括揽收、中转、派送等节点。若物流长时间(超过 5 天)无更新,"
            "可联系在线客服帮您向物流方催查。"
        ),
        "category": "物流配送",
    },
    # ---------- 支付优惠 ----------
    {
        "question": "支持哪些支付方式?",
        "answer": (
            "目前支持微信支付、支付宝、云闪付以及主流银行卡在线支付。支付过程中如遇失败,"
            "请确认账户余额或支付限额;扣款成功但订单未生成时,款项一般会在 1-2 个工作日内"
            "自动退回,无需重复支付。"
        ),
        "category": "支付优惠",
    },
    {
        "question": "优惠券怎么使用?",
        "answer": (
            "下单结算时,在订单确认页的「优惠券」栏点击选择可用券即可自动抵扣对应金额。"
            "注意:每笔订单限使用一张优惠券;优惠券有使用门槛和有效期,以下单页面展示为准;"
            "使用优惠券的订单若发生退货,退款会在优惠券有效期内一并返还。"
        ),
        "category": "支付优惠",
    },
    {
        "question": "可以开发票吗?",
        "answer": (
            "可以。下单时在订单确认页的「发票」栏选择电子普通发票并填写抬头(个人或单位)"
            "即可,发票会在订单完成后 24 小时内发送至您填写的邮箱。如下单时忘记填写,"
            "可在订单完成后 30 天内联系在线客服补开。"
        ),
        "category": "支付优惠",
    },
    # ---------- 账户会员 ----------
    {
        "question": "会员积分有什么用?",
        "answer": (
            "积分可在下单结算时直接抵现,100 积分抵 1 元,单笔订单最高可抵订单金额的 10%。"
            "消费 1 元累计 1 积分,评价商品、完善会员资料等也可获得积分。"
            "积分有效期 2 年,请留意在过期前使用。"
        ),
        "category": "账户会员",
    },
    {
        "question": "怎么成为会员?",
        "answer": (
            "注册本店账号即为普通会员,可享积分累计与生日礼券。累计消费满 500 元自动升级"
            "银卡会员,满 2000 元升级金卡会员,等级越高折扣与专属客服权益越多。"
            "会员等级按近 12 个月消费额动态计算,不会永久保留。"
        ),
        "category": "账户会员",
    },
    # ---------- 商品咨询 ----------
    {
        "question": "衣服尺码怎么选?",
        "answer": (
            "每件商品的详情页都附有尺码对照表,请先测量自己的胸围、腰围等数据再对照选择。"
            "不同品牌尺码标准略有差异,建议同时参考详情页的「模特试穿参考」;"
            "介于两个码之间时,宽松款建议选小码,修身款建议选大码。拿不准也可以把身高体重"
            "发给在线客服帮您推荐。"
        ),
        "category": "商品咨询",
    },
    {
        "question": "商品是正品吗?",
        "answer": (
            "本店所有商品均从品牌官方或授权渠道采购,每批货均留存采购凭证,支持专柜验货。"
            "如对收到的商品有疑虑,可在签收后 15 天内联系客服安排验证,"
            "确认非正品将按假一赔十处理。"
        ),
        "category": "商品咨询",
    },
]


def seed_faq(session: Session) -> int:
    """幂等灌入 FAQ 种子数据,返回新插入条数。

    按 question 精确匹配判重:已存在同 question 的行直接跳过(不覆盖);
    只负责插入与 flush,不开不 commit 事务——事务边界交由调用方控制
    (测试回滚 / CLI 提交)。
    """
    questions = [item["question"] for item in FAQ_SEED]
    existing = set(
        session.scalars(select(Faq.question).where(Faq.question.in_(questions)))
    )
    inserted = 0
    for item in FAQ_SEED:
        if item["question"] in existing:
            continue
        session.add(
            Faq(
                question=item["question"],
                answer=item["answer"],
                category=item["category"],
            )
        )
        inserted += 1
    if inserted:
        # flush 让同一事务内的后续查询立刻可见(测试断言依赖这一点)
        session.flush()
    return inserted


def main() -> int:
    """CLI 入口:连库灌入种子数据并提交;连接失败给中文报错、退出码 1。"""
    try:
        with get_session_local()() as session:
            with session.begin():
                inserted = seed_faq(session)
    except OperationalError as exc:
        print(
            f"连接数据库失败,请检查 .env 中的 DB_URL 与本机 MySQL 是否正常。原始错误:{exc}",
            file=sys.stderr,
        )
        return 1
    skipped = len(FAQ_SEED) - inserted
    print(f"faq 种子数据灌入完成:新插入 {inserted} 条,跳过 {skipped} 条(共 {len(FAQ_SEED)} 条)。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
