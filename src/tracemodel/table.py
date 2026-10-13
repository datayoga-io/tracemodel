"""Table: a small read-only grid that prints as text and renders as HTML in Jupyter."""

from __future__ import annotations

import html
from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any

from . import dates


class Table:
    def __init__(self, columns: Sequence[str], rows: Sequence[Sequence[Any]], title: str | None = None):
        self.columns = list(columns)
        self.rows = [list(r) for r in rows]
        self.title = title

    def show(self) -> None:
        """Display the table: HTML in Jupyter, text elsewhere."""
        try:
            from IPython import get_ipython
            from IPython.display import display

            if get_ipython() is not None:
                display(self)
                return
        except ImportError:
            pass
        print(self)

    def to_records(self) -> list[dict[str, Any]]:
        return [dict(zip(self.columns, r)) for r in self.rows]

    def __len__(self) -> int:
        return len(self.rows)

    def __str__(self) -> str:
        cells = [[_fmt(v) for v in r] for r in self.rows]
        widths = [max(len(c) for c in col) for col in zip(self.columns, *cells)]

        def line(values: list[str]) -> str:
            return "  ".join(
                v.rjust(w) if i and _right(self.rows, i) else v.ljust(w)
                for i, (v, w) in enumerate(zip(values, widths))
            ).rstrip()

        out = [self.title] if self.title else []
        out += [line(self.columns), *(line(c) for c in cells)]
        return "\n".join(out)

    __repr__ = __str__

    def _repr_pretty_(self, p, cycle) -> None:  # IPython terminal
        p.text(str(self))

    def _repr_html_(self) -> str:  # Jupyter
        def cell(tag: str, v: Any, i: int) -> str:
            align = "right" if i and _right(self.rows, i) else "left"
            return f'<{tag} style="text-align:{align}">{html.escape(_fmt(v))}</{tag}>'

        head = "".join(cell("th", c, i) for i, c in enumerate(self.columns))
        body = "".join(
            "<tr>" + "".join(cell("td", v, i) for i, v in enumerate(r)) + "</tr>" for r in self.rows
        )
        caption = f"<caption style='text-align:left'><b>{html.escape(self.title)}</b></caption>" if self.title else ""
        return f"<table>{caption}<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _right(rows: list[list[Any]], i: int) -> bool:
    """Right-align a column when it holds numbers."""
    return any(isinstance(r[i], (int, float)) and not isinstance(r[i], bool) for r in rows)


def _fmt(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, int):
        return f"{v:,}"
    if isinstance(v, float):
        v = round(v, 6)
        return f"{int(v):,}" if v == int(v) else f"{v:,.6f}".rstrip("0")
    if isinstance(v, (date, timedelta)):
        return dates.fmt(v)
    return str(v)
