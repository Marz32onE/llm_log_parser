"""Helpers for asserting the Drain3 TSV wire format."""

from __future__ import annotations

import csv
import io


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
