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

#: Anchored bare clock, left over once a shared date has been factored out
#: of a rendered line by ``pod_logs_to_text``. Anchored on purpose: this is
#: the one shape that only ever appears at the line start.
_TIMESTAMP_MASKS: tuple[MaskingSpec, ...] = ((r"^\d{2}:\d{2}:\d{2}(?:\.\d+)?", "TS"),)

#: Timestamps and UUIDs anywhere in the line, boundary-guarded rather than
#: anchored: JSON-formatted log messages carry their own timestamp and
#: request/trace ids *inside* the message body, not just at the line start,
#: and those must mask the same way or the catch-all ``NUM`` mask below
#: shreds them into one wildcard per digit run.
_INLINE_MASKS: tuple[MaskingSpec, ...] = (
    # Full ISO timestamp: "2024-01-01T12:34:56.789Z" / "...+00:00" / naive.
    # Also matches a leading (unfactored) full-form timestamp -- the "|^"
    # boundary alternative is satisfied at line start, so this subsumes what
    # used to be a separate anchored full-form mask.
    (
        r"((?<=[^A-Za-z0-9])|^)(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?"
        r"(?:Z|[+-]\d{2}:\d{2})?)((?=[^A-Za-z0-9])|$)",
        "TS",
    ),
    # UUID: 8-4-4-4-12 hex, case-insensitive.
    (
        r"((?<=[^A-Za-z0-9])|^)([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
        r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})((?=[^A-Za-z0-9])|$)",
        "UUID",
    ),
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
DEFAULT_MASKS: tuple[MaskingSpec, ...] = _TIMESTAMP_MASKS + _INLINE_MASKS + _UPSTREAM_MASKS
