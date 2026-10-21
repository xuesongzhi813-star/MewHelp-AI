"""知识库建库材料清单(纯 I/O 之外的声明式路径)。"""

from __future__ import annotations

from pathlib import Path

from app.kb.documents import Source
from app.config import get_settings

# 默认建库材料;路径相对项目根目录。
SOURCES: dict[str, list[Source]] = {'ch03': [
    Source(Path("data/kb/退货政策.md"), "退货政策", "文档章节"),
    Source(Path("data/kb/商品FAQ.md"), "商品FAQ", "商品FAQ"),
    Source(Path("data/kb/售后手册.md"), "售后手册", "文档章节"),
], 'ch04': [
    Source(Path(f'data/kb/ch04/{filename}.md'), title, kind)
    for filename, title, kind in (
        ('returns-policy', 'MewHelp 古着退货政策', '文档章节'),
        ('product-faq', 'MewHelp 古着商品FAQ', '商品FAQ'),
        ('after-sales-manual', 'MewHelp 古着售后手册', '文档章节'),
        ('product-specs', 'MewHelp 古着商品规格', '文档章节'),
        ('member-benefits', 'MewHelp 古着会员与积分', '文档章节'),
        ('billing-shipping', 'MewHelp 古着开票与配送', '文档章节'),
    )
]}


def get_sources(base_dir: Path | None = None, *, corpus_version: str | None = None) -> list[Source]:
    """返回建库材料清单。

    Args:
        base_dir: 自定义 Markdown 目录。为 ``None`` 时返回默认的
            :data:`SOURCES`;传入目录时扫描其中的 ``*.md`` 文件,标题取
            文件名 stem,文件名含 ``FAQ`` 时内容类型为「商品FAQ」,
            否则为「文档章节」。

    Returns:
        按路径排序的 :class:`Source` 列表。
    """
    if base_dir is None:
        version = corpus_version or get_settings().kb_corpus_version
        if version not in SOURCES:
            raise ValueError('未知知识材料版本')
        return list(SOURCES[version])

    directory = Path(base_dir)
    sources: list[Source] = []
    for path in sorted(directory.glob("*.md")):
        content_type = "商品FAQ" if "FAQ" in path.stem else "文档章节"
        sources.append(Source(path, path.stem, content_type))
    return sources
