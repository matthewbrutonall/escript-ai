# HTRflow follow-up

HTRflow is not integrated into Escript AI. No Escript AI action calls it, and this delivery does not add an HTRflow engine configuration or background task.

HTRflow is a page-level layout and recognition pipeline. It is not a line-only recognizer. A later integration has to keep the page identity and the line identity, then import its structured output into a new transcription layer.

`ai/htrflow_adapter.py` is the seam for that later work. `plan_htrflow_import` accepts one page payload:

- `document_id` and `part_id` must match the page Escript AI already trusts. A mismatch is refused and contributes no lines.
- `regions[].lines[]` carries `line_id`, `text`, and `reading_order`.
- A line id that is not already on that page is skipped. The planner does not create a line.
- Empty text is valid and stays included.
- The planner does not open a network connection and does not write a transcription.

The next parser sits in front of this planner. It turns HTRflow ALTO or PAGE XML into that payload without dropping the page id. A later importer, separate from the line-only external HTR import, creates regions and lines in one transaction when the page has no matching lines, and stores the engine line id on `Line.external_id`. It still writes a new layer behind the raw review gate.

Calamari, Loghi, TrOCR, DRetHTR, and Laypa remain future candidates. This delivery does not add adapters for them.
