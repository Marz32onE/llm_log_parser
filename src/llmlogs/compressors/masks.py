"""Default drain3 masking preset.

Masking runs *before* drain3 tokenizes a line, so it decides the token
sequence the parse tree sees. Without it, every distinct id or timestamp
keeps its own token and splits clusters that are semantically identical.

Ordering is load-bearing: ``LogMasker.mask`` applies instructions in
sequence, feeding each one the previous one's output. A general pattern
placed early eats the digits a specific pattern was waiting for, so the
catch-all ``NUM`` mask must stay last.
"""

from __future__ import annotations

#: ``(regex, mask_name)`` pairs, the caller-facing form of a drain3 mask.
MaskingSpec = tuple[str, str]

#: Leading rendered timestamps, in both shapes ``pod_logs_to_text`` emits.
#:
#: Anchored at the line start on purpose: the leading timestamp is
#: structural, while a date *inside* the message is content and must reach
#: the template intact.
_TIMESTAMP_MASKS: tuple[MaskingSpec, ...] = (
    # Full form: "2024-01-01T12:34:56.789Z" / "2024-01-01 12:34:56+00:00".
    (r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?", "TS"),
    # Bare clock, left over once a shared date has been factored out.
    (r"^\d{2}:\d{2}:\d{2}(?:\.\d+)?", "TS"),
)

#: The masking set shipped in upstream Drain3's ``examples/drain3.ini``.
#:
#: Two deliberate deviations: upstream's ``CMD`` example is dropped (it keys
#: off the literal text ``executed cmd ``, which no Kubernetes log emits),
#: and ``NUM`` is moved to the end so it cannot cannibalise the specific
#: patterns above it.
_UPSTREAM_MASKS: tuple[MaskingSpec, ...] = (
    (r"((?<=[^A-Za-z0-9])|^)(([0-9a-f]{2,}:){3,}([0-9a-f]{2,}))((?=[^A-Za-z0-9])|$)", "ID"),
    (r"((?<=[^A-Za-z0-9])|^)(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})((?=[^A-Za-z0-9])|$)", "IP"),
    (r"((?<=[^A-Za-z0-9])|^)([0-9a-f]{6,} ?){3,}((?=[^A-Za-z0-9])|$)", "SEQ"),
    (r"((?<=[^A-Za-z0-9])|^)([0-9A-F]{4} ?){4,}((?=[^A-Za-z0-9])|$)", "SEQ"),
    (r"((?<=[^A-Za-z0-9])|^)(0x[a-f0-9A-F]+)((?=[^A-Za-z0-9])|$)", "HEX"),
    (r"((?<=[^A-Za-z0-9])|^)([\-\+]?\d+)((?=[^A-Za-z0-9])|$)", "NUM"),
)

#: Masks applied when a caller does not supply their own.
#:
#: Pass ``masking_instructions=[]`` to mine without masking, which is what
#: bare drain3 does.
DEFAULT_MASKS: tuple[MaskingSpec, ...] = _TIMESTAMP_MASKS + _UPSTREAM_MASKS
