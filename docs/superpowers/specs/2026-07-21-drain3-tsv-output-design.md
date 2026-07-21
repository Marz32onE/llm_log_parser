# Drain3 TSV output design

## Goal

Replace the `drain3-llmlogs-v1` JSON payload with a smaller, LLM-readable,
lossless TSV representation. Add `with_preamble: bool = False` to
`Drain3Compressor`, matching `LogzipCompressor`: callers can opt into decode
instructions when each payload must explain itself.

## Output contract

The format identifier is `drain3-llmlogs-v2`. Output is divided into a template
legend and an ordered body:

```text
drain3-llmlogs-v2
[legend]
1	<TS> request method=<*> status=<NUM>
2	<TS> fatal error: out of memory
[body]
1	09:15:01	GET	200
2	09:15:03
R	unmatched raw log
E
```

Legend rows contain `template_id<TAB>template`. Normal body rows contain
`template_id<TAB>parameters`, with parameters ordered by placeholder position.
`R<TAB>raw_line` preserves a nonempty line that cannot be reconstructed from a
template. `E` preserves an empty line. Body row order remains source order.

Fields use Python's standard `csv` TSV rules with minimal quoting. Commas remain
literal. Fields containing tabs, newlines, quotes, or other characters requiring
TSV quoting are quoted and embedded quotes are doubled. This keeps arbitrary
field values reversible without a custom escaping convention.

## Optional preamble

When `with_preamble=True`, short comment lines precede the format identifier and
explain:

- legend and body row shapes;
- left-to-right placeholder substitution;
- `R` raw fallback and `E` empty-line records;
- standard TSV quoting.

When false, only the format identifier, section markers, and data are emitted.
Metadata records the selected `with_preamble` value.

## Internal design

Drain3 mining, final-template parameter extraction, masks, and reconstruction
validation remain unchanged. `_encode_line` continues to produce a small
internal typed representation so mining and correctness are independent of
rendering. A dedicated renderer serializes the legend and body to TSV.

The public output intentionally changes in place rather than adding an
`output_format` option. Existing JSON consumers must migrate to v2.

## Errors and edge cases

- Missing or evicted templates remain `R` rows.
- Nonempty whitespace-only lines remain `R` rows and retain exact whitespace.
- Empty lines become `E`.
- Numeric template IDs remain decimal strings in TSV.
- Tabs and quotes in templates, parameters, and raw lines round-trip through
  standard TSV parsing.

## Tests and documentation

Tests will first specify and fail on the new contract, then implementation will
make them pass. Coverage includes basic structure, optional preamble, final
template parameter alignment, masks, raw fallbacks, blank lines, special TSV
characters, metadata, and reconstruction. Existing README examples and token
format discussion will be updated from JSON v1 to TSV v2.

Success means the targeted compressor tests and full `make check` pass, and a
TSV parser can reconstruct every tested source line exactly.
