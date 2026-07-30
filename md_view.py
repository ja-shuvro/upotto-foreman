"""Lightweight Markdown → Tk Text rendering (desktop chat) + Telegram HTML."""

from __future__ import annotations

import html
import re
import tkinter as tk
from tkinter import font as tkfont
from typing import Any


# Inline patterns applied after block structure
_RE_BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_RE_ITALIC = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)|_(.+?)_")
_RE_CODE = re.compile(r"`([^`]+)`")
_RE_STRIKE = re.compile(r"~~(.+?)~~")


def markdown_to_telegram_html(text: str) -> str:
    """Convert common Markdown to Telegram HTML parse_mode."""
    if not text:
        return ""
    lines_out: list[str] = []
    in_code = False
    code_buf: list[str] = []

    for raw in text.splitlines():
        line = raw.rstrip()
        if line.strip().startswith("```"):
            if in_code:
                block = html.escape("\n".join(code_buf))
                lines_out.append(f"<pre>{block}</pre>")
                code_buf = []
                in_code = False
            else:
                in_code = True
            continue
        if in_code:
            code_buf.append(line)
            continue

        if re.match(r"^#{1,6}\s+", line):
            title = re.sub(r"^#{1,6}\s+", "", line)
            lines_out.append(f"<b>{html.escape(title)}</b>")
            continue

        if re.match(r"^[-*]\s+", line):
            body = re.sub(r"^[-*]\s+", "", line)
            lines_out.append(f"• {_inline_to_html(body)}")
            continue

        if re.match(r"^\d+\.\s+", line):
            body = re.sub(r"^\d+\.\s+", "", line)
            num = re.match(r"^(\d+)\.", line)
            prefix = f"{num.group(1)}." if num else "•"
            lines_out.append(f"{prefix} {_inline_to_html(body)}")
            continue

        if not line.strip():
            lines_out.append("")
            continue

        lines_out.append(_inline_to_html(line))

    if in_code and code_buf:
        lines_out.append(f"<pre>{html.escape(chr(10).join(code_buf))}</pre>")

    return "\n".join(lines_out)


def _inline_to_html(text: str) -> str:
    """Escape then restore bold/italic/code spans as HTML."""
    # Protect code spans first
    slots: list[str] = []

    def _code(m: re.Match[str]) -> str:
        slots.append(f"<code>{html.escape(m.group(1))}</code>")
        return f"\x00C{len(slots) - 1}\x00"

    text = _RE_CODE.sub(_code, text)
    text = html.escape(text)

    def _bold(m: re.Match[str]) -> str:
        inner = m.group(1) or m.group(2) or ""
        return f"<b>{inner}</b>"

    def _italic(m: re.Match[str]) -> str:
        inner = m.group(1) or m.group(2) or ""
        return f"<i>{inner}</i>"

    text = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", _bold, text)
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)|(?<!_)_(?!_)(.+?)(?<!_)_(?!_)", _italic, text)
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text)

    for i, slot in enumerate(slots):
        text = text.replace(f"\x00C{i}\x00", slot)
    return text


def configure_md_tags(widget: tk.Text, *, fg: str = "#f0f3f7", bg: str = "#222830") -> None:
    base = tkfont.Font(family="Segoe UI", size=11)
    bold = tkfont.Font(family="Segoe UI", size=11, weight="bold")
    italic = tkfont.Font(family="Segoe UI", size=11, slant="italic")
    h1 = tkfont.Font(family="Segoe UI", size=14, weight="bold")
    h2 = tkfont.Font(family="Segoe UI", size=12, weight="bold")
    code = tkfont.Font(family="Consolas", size=10)
    widget.configure(
        font=base,
        fg=fg,
        bg=bg,
        insertbackground=fg,
        selectbackground="#3a4555",
        relief="flat",
        borderwidth=0,
        highlightthickness=0,
        wrap="word",
        padx=10,
        pady=8,
        cursor="arrow",
    )
    widget.tag_configure("bold", font=bold)
    widget.tag_configure("italic", font=italic)
    widget.tag_configure("h1", font=h1, spacing1=6, spacing3=4)
    widget.tag_configure("h2", font=h2, spacing1=4, spacing3=2)
    widget.tag_configure("code", font=code, background="#12161c", foreground="#9cdcfe")
    widget.tag_configure("bullet", lmargin1=12, lmargin2=28)
    widget.tag_configure("strike", overstrike=True)


def insert_markdown(widget: tk.Text, markdown: str) -> None:
    """Clear widget and insert formatted markdown."""
    widget.configure(state="normal")
    widget.delete("1.0", "end")
    if not markdown:
        widget.configure(state="disabled")
        return

    in_code = False
    code_lines: list[str] = []

    for raw in markdown.splitlines():
        line = raw.rstrip("\n")
        if line.strip().startswith("```"):
            if in_code:
                start = widget.index("end-1c")
                widget.insert("end", "\n".join(code_lines) + "\n")
                end = widget.index("end-1c")
                widget.tag_add("code", start, end)
                code_lines = []
                in_code = False
            else:
                in_code = True
            continue
        if in_code:
            code_lines.append(line)
            continue

        if re.match(r"^#\s+", line):
            _insert_inline(widget, re.sub(r"^#\s+", "", line) + "\n", extra=("h1", "bold"))
            continue
        if re.match(r"^##\s+", line):
            _insert_inline(widget, re.sub(r"^##\s+", "", line) + "\n", extra=("h2", "bold"))
            continue
        if re.match(r"^###\s+", line):
            _insert_inline(widget, re.sub(r"^###\s+", "", line) + "\n", extra=("h2", "bold"))
            continue
        if re.match(r"^[-*]\s+", line):
            body = re.sub(r"^[-*]\s+", "", line)
            start = widget.index("end-1c")
            widget.insert("end", "• ")
            _insert_inline(widget, body + "\n")
            end = widget.index("end-1c")
            widget.tag_add("bullet", start, end)
            continue
        if re.match(r"^\d+\.\s+", line):
            m = re.match(r"^(\d+)\.\s+(.*)$", line)
            if m:
                start = widget.index("end-1c")
                widget.insert("end", f"{m.group(1)}. ")
                _insert_inline(widget, m.group(2) + "\n")
                end = widget.index("end-1c")
                widget.tag_add("bullet", start, end)
            continue
        if not line.strip():
            widget.insert("end", "\n")
            continue
        _insert_inline(widget, line + "\n")

    if in_code and code_lines:
        start = widget.index("end-1c")
        widget.insert("end", "\n".join(code_lines) + "\n")
        end = widget.index("end-1c")
        widget.tag_add("code", start, end)

    widget.configure(state="disabled")


def _insert_inline(widget: tk.Text, text: str, extra: tuple[str, ...] = ()) -> None:
    """Insert a line applying **bold**, *italic*, `code`, ~~strike~~."""
    pos = 0
    pattern = re.compile(
        r"(\*\*(.+?)\*\*|__(.+?)__|(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)|"
        r"(?<!_)_(?!_)(.+?)(?<!_)_(?!_)|`([^`]+)`|~~(.+?)~~)"
    )
    for m in pattern.finditer(text):
        if m.start() > pos:
            widget.insert("end", text[pos : m.start()], extra)
        if m.group(2) or m.group(3):
            widget.insert("end", m.group(2) or m.group(3), (*extra, "bold"))
        elif m.group(4) or m.group(5):
            widget.insert("end", m.group(4) or m.group(5), (*extra, "italic"))
        elif m.group(6):
            widget.insert("end", m.group(6), (*extra, "code"))
        elif m.group(7):
            widget.insert("end", m.group(7), (*extra, "strike"))
        pos = m.end()
    if pos < len(text):
        widget.insert("end", text[pos:], extra)


def markdown_text_height(markdown: str, *, min_lines: int = 2, max_lines: int = 18) -> int:
    lines = max(min_lines, min(max_lines, (markdown or "").count("\n") + 2))
    return lines


def create_markdown_bubble(
    parent: Any,
    markdown: str,
    *,
    fg: str,
    bg: str,
    width: int = 560,
) -> tk.Text:
    """Create a read-only Text widget showing rendered markdown."""
    height = markdown_text_height(markdown)
    widget = tk.Text(parent, height=height, width=1)
    configure_md_tags(widget, fg=fg, bg=bg)
    insert_markdown(widget, markdown or "")
    # Approximate wrap width via widget width in chars
    widget.configure(width=max(40, width // 8))
    return widget
