"""知识库建库材料清单测试(Task 4)。"""

import re
from pathlib import Path

from app.kb.documents import build_chunks
from app.kb.sources import SOURCES, get_sources

KB_DIR = Path("data/kb")


def test_sources_are_three_markdown_files():
    assert len(SOURCES) == 3
    assert {s.content_type for s in SOURCES} == {"文档章节", "商品FAQ"}
    assert all(s.path.exists() for s in SOURCES)


def test_shipping_fee_section_exists():
    text = (Path("data/kb/退货政策.md")).read_text(encoding="utf-8")
    assert "运费" in text or "邮费" in text


def test_get_sources_respects_base_dir(tmp_path):
    (tmp_path / "a.md").write_text("# 标题\n\n正文", encoding="utf-8")
    # 自定义目录只返回该目录下的 md,content_type 默认 文档章节
    result = get_sources(tmp_path)
    assert len(result) == 1
    assert result[0].content_type == "文档章节"


def test_get_sources_marks_faq_content_type(tmp_path):
    (tmp_path / "商品FAQ.md").write_text("# FAQ\n\n正文", encoding="utf-8")
    (tmp_path / "b.md").write_text("# B\n\n正文", encoding="utf-8")
    result = {source.path.name: source for source in get_sources(tmp_path)}
    assert result["商品FAQ.md"].content_type == "商品FAQ"
    assert result["b.md"].content_type == "文档章节"


def test_source_title_is_file_stem_for_custom_dir(tmp_path):
    (tmp_path / "售后手册.md").write_text("# 手册\n\n正文", encoding="utf-8")
    assert get_sources(tmp_path)[0].title == "售后手册"


# ---------------------------------------------------------------------------
# 样例文档内容要求
# ---------------------------------------------------------------------------


def _read(name: str) -> str:
    return (KB_DIR / name).read_text(encoding="utf-8")


def _max_section_body_length(text: str) -> int:
    longest = 0
    current: list[str] = []
    for line in text.splitlines():
        if re.match(r"^#{1,6}\s+", line):
            longest = max(longest, len("\n".join(current).strip()))
            current = []
        else:
            current.append(line)
    return max(longest, len("\n".join(current).strip()))


def test_sample_documents_use_multi_level_headings():
    for name in ("退货政策.md", "商品FAQ.md", "售后手册.md"):
        levels = {
            len(line) - len(line.lstrip("#"))
            for line in _read(name).splitlines()
            if re.match(r"^#{1,6}\s+", line)
        }
        assert {1, 2}.issubset(levels), name


def test_return_policy_has_long_section_and_large_table():
    text = _read("退货政策.md")
    assert _max_section_body_length(text) > 500
    table_blocks = re.findall(r"(?:^\|.*\n){2,}", text, re.MULTILINE)
    assert any(block.count("\n") - 2 > 20 for block in table_blocks)


def test_faq_headings_are_user_questions():
    headings = [
        line[3:].strip()
        for line in _read("商品FAQ.md").splitlines()
        if line.startswith("## ")
    ]
    assert headings
    assert all(heading.endswith("?") for heading in headings)


def test_each_document_has_a_key_clause():
    for source in SOURCES:
        chunks = build_chunks(_read(source.path.name), source)
        assert any(chunk.is_key_clause for chunk in chunks), source.title
