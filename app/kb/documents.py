"""Markdown 结构感知切分器(纯函数,零 I/O)。

切分流程:
1. 逐行识别 ATX 标题,维护标题栈;任意级别的新标题都会结束当前 section,
   子标题内容归子 section(父 section 不重复计入)。
2. section 内的正文按空行聚合成段落 block;列表项每行一个 block;
   连续以 ``|`` 开头且第二行是分隔行的识别为表格 block。
3. 未超过目标长度的 section 直接成块;超长 section 递归切分:
   优先在 ``。！？；``(或换行)处切成小片,再贪心打包;
   表格按行分组,每组重复表头 + 分隔行。
4. 第二块起在开头附带上一块末尾最多 100 字,并对齐到句子开头。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

CHUNK_TARGET = 500
CHUNK_OVERLAP = 100
SENTENCE_ENDS = "。！？；"

FEATURE_RECURSIVE = "recursive_split"
FEATURE_SENTENCE = "sentence_trimmed"
FEATURE_OVERLAP = "overlap_applied"
FEATURE_TABLE_HEADER = "table_header_replicated"

# 递归切分时单个片段的上限:预留 overlap 与分隔符的空间,
# 使「上一块末尾重叠 + 本片段」通常能落在 CHUNK_TARGET 内。
_PIECE_LIMIT = CHUNK_TARGET - CHUNK_OVERLAP - 4  # 396

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_CLOSING_HASHES_RE = re.compile(r"\s+#+\s*$")
_TABLE_SEPARATOR_RE = re.compile(r"^\|(?:\s*:?-{3,}:?\s*\|)+$")
_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")


@dataclass(frozen=True)
class Section:
    """一个可入库的文本块。"""

    ordinal: int
    heading_path: tuple[str, ...]
    body: str
    features: tuple[str, ...]


@dataclass(frozen=True)
class _Block:
    kind: str  # "text" | "list" | "table"
    text: str


@dataclass(frozen=True)
class _Atom:
    """切分最小单元:片段文本、前置分隔符与来源类型。"""

    kind: str
    text: str
    sep: str = ""
    replicated_header: bool = False


def split_sections(markdown: str, document_title: str) -> list[Section]:
    """把 Markdown 切分成结构感知的 section 列表。

    Args:
        markdown: 原始 Markdown 文本。
        document_title: 文档标题;首个标题之前的前言内容用它作为标题路径。

    Returns:
        按文档顺序排列的 :class:`Section`,ordinal 从 0 连续递增。
    """
    sections: list[Section] = []
    ordinal = 0
    for heading_path, blocks in _parse_document(markdown, document_title):
        body = _render_blocks(blocks)
        if not body.strip():
            continue
        if len(body) <= CHUNK_TARGET:
            sections.append(Section(ordinal, heading_path, body.strip(), ()))
            ordinal += 1
            continue
        for text, overlapped, replicated, sentence_trimmed in _pack_atoms(
            _build_atoms(blocks)
        ):
            features = [FEATURE_RECURSIVE]
            if sentence_trimmed:
                features.append(FEATURE_SENTENCE)
            if overlapped:
                features.append(FEATURE_OVERLAP)
            if replicated:
                features.append(FEATURE_TABLE_HEADER)
            sections.append(Section(ordinal, heading_path, text, tuple(features)))
            ordinal += 1
    return sections


# ---------------------------------------------------------------------------
# 文档解析:标题与 section
# ---------------------------------------------------------------------------


def _parse_heading(line: str) -> tuple[int, str] | None:
    """识别 ATX 标题,返回 (级别, 标题文本)。"""
    match = _HEADING_RE.match(line)
    if not match:
        return None
    text = _CLOSING_HASHES_RE.sub("", match.group(2)).strip()
    if not text:
        return None
    return len(match.group(1)), text


def _parse_document(
    markdown: str, document_title: str
) -> list[tuple[tuple[str, ...], list[_Block]]]:
    """按标题切出 (heading_path, blocks),跳过正文为空的 section。"""
    sections: list[tuple[tuple[str, ...], list[_Block]]] = []
    stack: list[tuple[int, str]] = []
    heading_path: tuple[str, ...] = (document_title,)
    buffer: list[str] = []

    def flush() -> None:
        raw = "\n".join(buffer).strip("\n")
        if raw.strip():
            blocks = _parse_blocks(raw.splitlines())
            if _render_blocks(blocks).strip():
                sections.append((heading_path, blocks))

    for line in markdown.splitlines():
        parsed = _parse_heading(line)
        if parsed is None:
            buffer.append(line)
            continue
        flush()
        buffer = []
        level, title = parsed
        # 任意级别的新标题都结束当前 section;同级/更高级则回退标题栈。
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        heading_path = tuple(text for _, text in stack)
    flush()
    return sections


# ---------------------------------------------------------------------------
# block 解析
# ---------------------------------------------------------------------------


def _is_table_line(line: str) -> bool:
    return line.lstrip().startswith("|")


def _is_table_separator(line: str) -> bool:
    return bool(_TABLE_SEPARATOR_RE.match(line.strip()))


def _is_list_item(line: str) -> bool:
    return bool(_LIST_ITEM_RE.match(line))


def _parse_blocks(lines: list[str]) -> list[_Block]:
    """把 section 正文行解析成段落/列表项/表格 block。"""
    blocks: list[_Block] = []
    index = 0
    total = len(lines)
    while index < total:
        line = lines[index]
        if not line.strip():
            index += 1
            continue

        if _is_table_line(line):
            end = index
            while end < total and _is_table_line(lines[end]):
                end += 1
            table_lines = [item.strip() for item in lines[index:end]]
            if len(table_lines) >= 2 and _is_table_separator(table_lines[1]):
                blocks.append(_Block("table", "\n".join(table_lines)))
            else:
                # 第二行不是分隔行,按普通文本处理。
                blocks.append(_Block("text", "\n".join(table_lines)))
            index = end
            continue

        if _is_list_item(line):
            blocks.append(_Block("list", line.strip()))
            index += 1
            continue

        # 连续的非空行聚合成一个段落,直到空行、表格或列表项。
        end = index
        paragraph: list[str] = []
        while end < total:
            current = lines[end]
            if (
                not current.strip()
                or _is_table_line(current)
                or _is_list_item(current)
            ):
                break
            paragraph.append(current.strip())
            end += 1
        blocks.append(_Block("text", "\n".join(paragraph)))
        index = end
    return blocks


def _block_separator(block: _Block, previous: _Block | None) -> str:
    if previous is None:
        return ""
    if block.kind == "list" and previous.kind == "list":
        return "\n"
    return "\n\n"


def _render_blocks(blocks: list[_Block]) -> str:
    parts: list[str] = []
    previous: _Block | None = None
    for block in blocks:
        parts.append(_block_separator(block, previous))
        parts.append(block.text)
        previous = block
    return "".join(parts)


# ---------------------------------------------------------------------------
# 超长 section 递归切分
# ---------------------------------------------------------------------------


def _split_text(text: str, limit: int) -> list[str]:
    """把文本切成不超过 limit 的片段,优先句末/换行,否则硬切。"""
    if len(text) <= limit:
        return [text]
    pieces: list[str] = []
    start = 0
    total = len(text)
    while start < total:
        if total - start <= limit:
            pieces.append(text[start:])
            break
        window = text[start : start + limit]
        cut = -1
        for offset in range(len(window) - 1, -1, -1):
            if window[offset] in SENTENCE_ENDS or window[offset] == "\n":
                cut = offset + 1
                break
        if cut <= 0:
            cut = limit  # 没有句子边界,硬切;cut>0 保证一定前进。
        pieces.append(text[start : start + cut])
        start += cut
    return pieces


def _split_table(text: str) -> tuple[list[str], list[bool]]:
    """把表格切成若干块,每块重复表头 + 分隔行。

    Returns:
        (片段列表, 是否使用了复制表头)。首个片段沿用原始表头,标记为 False。
    """
    lines = text.split("\n")
    if len(lines) < 2 or not _is_table_separator(lines[1]):
        pieces = _split_text(text, _PIECE_LIMIT)
        return pieces, [False] * len(pieces)

    header, separator = lines[0], lines[1]
    prefix = f"{header}\n{separator}"
    limit = _PIECE_LIMIT
    rows_capacity = limit - len(prefix) - 1
    if rows_capacity <= 0:
        # 表头本身过长:退化为普通文本硬切,保证终止。
        pieces = _split_text(text, _PIECE_LIMIT)
        return pieces, [False] * len(pieces)

    pieces: list[str] = []
    replicated: list[bool] = []

    def emit(rows: list[str]) -> None:
        pieces.append(f"{prefix}\n" + "\n".join(rows))
        replicated.append(len(pieces) > 1)  # 第一块是原始表头,之后均为复制

    buffer: list[str] = []
    buffer_length = 0
    for row in lines[2:]:
        row_pieces = (
            _split_text(row, rows_capacity) if len(row) > rows_capacity else [row]
        )
        for row_piece in row_pieces:
            addition = len(row_piece) + (1 if buffer else 0)
            if buffer and buffer_length + addition > rows_capacity:
                emit(buffer)
                buffer = []
                buffer_length = 0
                addition = len(row_piece)
            buffer.append(row_piece)
            buffer_length += addition
    if buffer:
        emit(buffer)
    if not pieces:  # 只有表头与分隔行
        return [text], [False]
    return pieces, replicated


def _build_atoms(blocks: list[_Block]) -> list[_Atom]:
    """把 block 展开成不超过 _PIECE_LIMIT 的原子片段。"""
    atoms: list[_Atom] = []
    previous: _Block | None = None
    for block in blocks:
        block_separator = _block_separator(block, previous)
        if block.kind == "table":
            pieces, replicated = _split_table(block.text)
            for position, (piece, was_replicated) in enumerate(
                zip(pieces, replicated)
            ):
                atoms.append(
                    _Atom(
                        "table",
                        piece,
                        block_separator if position == 0 else "\n\n",
                        was_replicated,
                    )
                )
        else:
            pieces = _split_text(block.text, _PIECE_LIMIT)
            for position, piece in enumerate(pieces):
                atoms.append(
                    _Atom(
                        block.kind,
                        piece,
                        block_separator if position == 0 else "",
                    )
                )
        previous = block
    return atoms


def _overlap_tail(text: str) -> str:
    """取上一块末尾最多 CHUNK_OVERLAP 字,并对齐到句子/行开头。"""
    if not text:
        return ""
    start = max(0, len(text) - CHUNK_OVERLAP)
    position = start
    while position < len(text):
        if position > 0 and (
            text[position - 1] in SENTENCE_ENDS or text[position - 1] == "\n"
        ):
            break
        position += 1
    if position >= len(text):
        return ""  # 找不到句子/行边界,宁可不重叠也不从句子中间开始。
    return text[position:]


def _pack_atoms(atoms: list[_Atom]) -> list[tuple[str, bool, bool, bool]]:
    """贪心打包原子片段。

    Returns:
        (块文本, 是否带重叠, 是否复制表头, 是否在句子边界结束)。
    """
    chunks: list[tuple[str, bool, bool, bool]] = []
    index = 0
    overlap = ""
    previous_kind: str | None = None

    while index < len(atoms):
        parts: list[str] = []
        length = 0
        used_overlap = bool(overlap)
        if overlap:
            parts.append(overlap)
            length = len(overlap)
        replicated = False
        last_kind = previous_kind

        while index < len(atoms):
            atom = atoms[index]
            separator = atom.sep if parts else ""
            candidate = length + len(separator) + len(atom.text)
            if candidate <= CHUNK_TARGET:
                if separator:
                    parts.append(separator)
                parts.append(atom.text)
                length = candidate
                replicated = replicated or atom.replicated_header
                last_kind = atom.kind
                index += 1
                continue

            overlap_length = len(overlap) if used_overlap else 0
            if length > overlap_length:
                break  # 当前块已有实际内容,收块。
            if used_overlap:
                # 重叠占满空间导致原子放不下:丢弃重叠,用完整目标空间。
                parts = []
                length = 0
                used_overlap = False
                overlap = ""
                continue
            if len(atom.text) > CHUNK_TARGET:
                # 防御性硬切,确保任何输入都能终止。
                head = atom.text[:CHUNK_TARGET]
                rest = atom.text[CHUNK_TARGET:]
                atoms[index : index + 1] = [
                    _Atom(atom.kind, head, atom.sep, atom.replicated_header),
                    _Atom(atom.kind, rest, "", atom.replicated_header),
                ]
                continue
            break  # 理论上不可达;避免死循环。

        text = "".join(parts)
        if text:
            chunks.append(
                (
                    text,
                    used_overlap,
                    replicated,
                    text.rstrip().endswith(tuple(SENTENCE_ENDS)),
                )
            )
        previous_kind = last_kind

        if index < len(atoms) and previous_kind in ("text", "list"):
            overlap = (
                _overlap_tail(text) if atoms[index].kind in ("text", "list") else ""
            )
        else:
            overlap = ""

    return chunks
