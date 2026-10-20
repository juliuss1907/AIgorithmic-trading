"""One module per tab of `aigt ui`. Every pane takes the whole fetched data (endpoint name → payload) in `show`
and reads what it needs, so a slow or failing endpoint only blanks the parts built from it."""

from __future__ import annotations

from datetime import datetime, timezone

from textual.containers import VerticalScroll
from textual.widgets import DataTable


class Pane(VerticalScroll):
    """A tab body; `show` is called after every fetch, with `None` for endpoints not fetched yet."""

    def show(self, data: dict) -> None:
        raise NotImplementedError

    @staticmethod
    def now() -> datetime:
        return datetime.now(timezone.utc)


def table(*columns: str, id: str | None = None) -> DataTable:
    widget = DataTable(id=id, cursor_type="row", zebra_stripes=True)
    widget.add_columns(*columns)
    return widget


def fill(widget: DataTable, rows, keys=None) -> None:
    """Replace the rows; the cursor stays on the same index when it still exists."""
    cursor = widget.cursor_row
    widget.clear()
    for k, row in enumerate(rows):
        widget.add_row(*row, key=None if keys is None else keys[k])
    if widget.row_count:
        widget.move_cursor(row=min(cursor, widget.row_count-1))
