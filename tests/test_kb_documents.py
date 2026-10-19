"""知识库文档切分器测试(Task 3)。"""

from app.kb.documents import Section, split_sections


def test_heading_paths_and_ordinals():
    md = "# 售后政策\n\n## 退货\n\n正文一。\n\n### 退款\n\n正文二。\n"
    sections = split_sections(md, "售后手册")
    assert [s.heading_path for s in sections] == [("售后政策", "退货"), ("售后政策", "退货", "退款")]
    assert [s.ordinal for s in sections] == [0, 1]
    assert sections[0].body.strip() == "正文一。"


def test_content_before_first_heading_uses_document_title():
    sections = split_sections("前言正文。\n\n# 第一章\n\n第一章正文。", "手册")
    assert sections[0].heading_path == ("手册",)
    assert "前言正文" in sections[0].body


def test_long_section_splits_at_sentence_boundary():
    md = "# 条款\n\n" + "这是一句测试文本。" * 120  # 960 字,必然超 500
    sections = split_sections(md, "手册")
    assert len(sections) >= 2
    for s in sections:
        assert len(s.body) <= 500
        assert s.body.rstrip().endswith(("。", "！", "？", "；"))


def test_long_section_overlap_at_most_100():
    md = "# 条款\n\n" + "甲乙丙丁。" * 200
    sections = split_sections(md, "手册")
    # 后一块开头应与前一块末尾有非空交集,且不超过 100 字
    prev_end = sections[0].body[-100:]
    assert any(prev_end[i:i + 20] and prev_end[i:i + 20] in sections[1].body
               for i in range(0, 80, 10))
    assert len(sections[1].body) <= 600


def test_table_split_repeats_header():
    table = "| 商品 | 尺码 | 价格 |\n|---|---|---|\n" + "\n".join(
        f"| 商品{i} | M | {i}.00 |" for i in range(80)
    )
    md = f"# 价目表\n\n{table}\n"
    sections = split_sections(md, "商品FAQ")
    assert len(sections) >= 2
    for s in sections:
        assert "| 商品 | 尺码 | 价格 |" in s.body
        assert "|---|---|---|" in s.body


def test_huge_single_line_terminates():
    sections = split_sections("# 长文\n\n" + "字" * 2000, "手册")
    assert sections
    assert all(len(s.body) <= 600 for s in sections)


def test_empty_and_table_only_markdown():
    assert split_sections("", "手册") == []
    table_only = "| a | b |\n|---|---|\n| 1 | 2 |\n"
    sections = split_sections(table_only, "表格")
    assert len(sections) == 1
    assert "| a | b |" in sections[0].body
