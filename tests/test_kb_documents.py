"""知识库文档切分器测试(Task 3)。"""

from app.kb.documents import (
    FEATURE_OVERLAP,
    FEATURE_RECURSIVE,
    FEATURE_SENTENCE,
    FEATURE_TABLE_HEADER,
    SENTENCE_ENDS,
    Section,
    split_sections,
)


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


def test_long_section_overlap_starts_at_sentence_boundary():
    # 唯一句子编号避免重复内容让「无 overlap」也偶然通过。
    sentences = "".join(f"句{index:04d}。" for index in range(200))
    sections = split_sections("# 条款\n\n" + sentences, "手册")
    assert len(sections) >= 2
    first, second = sections[0], sections[1]

    overlap = 0
    for size in range(min(100, len(first.body), len(second.body)), 0, -1):
        if second.body.startswith(first.body[-size:]):
            overlap = size
            break

    assert 1 <= overlap <= 100
    start = len(first.body) - overlap
    assert start == 0 or first.body[start - 1] in SENTENCE_ENDS or first.body[start - 1] == "\n"


def test_sentence_boundary_beyond_396_is_used():
    # 回归 Important 1:目标窗口内存在句号时不得提前硬切。
    text = "甲" * 450 + "。" + ("乙" * 9 + "。") * 100
    sections = split_sections("# 条款\n\n" + text, "手册")
    assert len(sections) >= 2
    assert sections[0].body.endswith("。")
    assert 451 <= len(sections[0].body) <= 500
    assert all(len(section.body) <= 600 for section in sections)


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


def test_long_section_carries_recursive_and_overlap_features():
    sentences = "".join(f"句{index:04d}。" for index in range(200))
    sections = split_sections("# 条款\n\n" + sentences, "手册")
    assert all(FEATURE_RECURSIVE in s.features for s in sections)
    assert FEATURE_OVERLAP in sections[1].features


def test_sentence_trimmed_feature_marks_sentence_boundary():
    # 约定:块尾字符落在句末标点上时设置 sentence_trimmed。
    sections = split_sections("# 条款\n\n" + "这是一句测试文本。" * 120, "手册")
    assert sections
    assert all(FEATURE_SENTENCE in s.features for s in sections)


def test_hard_cut_has_no_sentence_feature():
    sections = split_sections("# 长文\n\n" + "字" * 2000, "手册")
    assert sections
    assert all(FEATURE_SENTENCE not in s.features for s in sections)


def test_table_chunks_carry_table_header_feature():
    table = "| 商品 | 尺码 | 价格 |\n|---|---|---|\n" + "\n".join(
        f"| 商品{i} | M | {i}.00 |" for i in range(80)
    )
    sections = split_sections(f"# 价目表\n\n{table}\n", "商品FAQ")
    assert len(sections) >= 2
    assert all(FEATURE_RECURSIVE in s.features for s in sections)
    assert all(FEATURE_TABLE_HEADER in s.features for s in sections)
