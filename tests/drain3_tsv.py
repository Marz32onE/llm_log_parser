"""Helpers for asserting the Drain3 TSV wire format."""

from __future__ import annotations

import csv
import io
import re

_PLACEHOLDER = re.compile(r"<[^<>\s]*>")


def parse_drain3_tsv(
    text: str,
) -> tuple[list[str], dict[str, str], list[list[str]]]:
    rows = list(csv.reader(io.StringIO(text), delimiter="\t"))
    marker_index = rows.index(["drain3-llmlogs-v2"])
    legend_index = rows.index(["[legend]"])
    body_index = rows.index(["[body]"])
    preamble = [row[0] for row in rows[:marker_index]]
    legend = {row[0]: row[1] for row in rows[legend_index + 1 : body_index]}
    body = rows[body_index + 1 :]
    return preamble, legend, body


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
