"""Helpers for asserting the Drain3 CSV wire format."""

from __future__ import annotations

import csv
import io
import re

_PLACEHOLDER = re.compile(r"<[^<>\s]*>")
_BODY_HEADER = re.compile(r"\[body(?: default=(\d+))?\]")


def parse_drain3_csv(
    text: str,
) -> tuple[list[str], dict[str, str], list[list[str]]]:
    # Preamble lines are written raw (may contain commas); only the marker and
    # below are CSV fields.
    lines = text.splitlines()
    marker_line = lines.index("drain3-llmlogs-v4")
    preamble = lines[:marker_line]
    rows = list(csv.reader(io.StringIO("\n".join(lines[marker_line:])), delimiter=","))
    legend_index = rows.index(["[legend]"])
    body_index, default_id = _find_body_header(rows)
    legend = {row[0]: row[1] for row in rows[legend_index + 1 : body_index]}
    body = [_resolve_default(row, default_id) for row in rows[body_index + 1 :]]
    return preamble, legend, body


def _find_body_header(rows: list[list[str]]) -> tuple[int, str | None]:
    for index, row in enumerate(rows):
        if len(row) == 1 and (match := _BODY_HEADER.fullmatch(row[0])):
            return index, match.group(1)
    raise AssertionError("no [body] header found")


def _resolve_default(row: list[str], default_id: str | None) -> list[str]:
    """Rewrite an id-elided row (leading empty field) to its explicit form."""
    if row and not row[0]:
        assert default_id is not None, "elided row without [body default=N]"
        return [default_id, *row[1:]]
    return row


def reconstruct_lines(legend: dict[str, str], body: list[list[str]]) -> list[str]:
    """Rebuild the source lines from parsed rows: R=raw, E=empty, else fill.

    Placeholders are matched generically (any ``<...>`` token) on purpose —
    importing the production ``placeholder_pattern``/``fill_template`` here
    would make the round-trip tests verify production code with itself.
    """
    lines: list[str] = []
    for row in body:
        if row[0] == "R":
            lines.append(row[1])
        elif row[0] == "E":
            lines.append("")
        else:
            values = iter(row[1:])
            lines.append(_PLACEHOLDER.sub(lambda _: next(values), legend[row[0]]))
    return lines
