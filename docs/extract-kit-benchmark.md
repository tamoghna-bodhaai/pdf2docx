# Hybrid extractor quality gate

`vision` remains the shipped default until a representative local corpus passes
this gate. Run the same PDFs through `vision` and `hybrid`; retain page-level
outputs and job diagnostics so fallback calls can be audited.

The corpus must include clean and noisy scans, at least two multi-column layout
families, figure-heavy pages, and equation-heavy pages. Record:

| Metric | Method | Gate |
|---|---|---|
| OCR word/character error rate | Normalize Unicode/whitespace and compare with reviewed ground truth | No material regression against vision |
| Formula exact/normalized match | Compare raw LaTeX and a whitespace/alias-normalized form | No material regression |
| Reading order | Human-reviewed block sequence per page | No material regression; ambiguous pages must fall back |
| Figure placement | IoU plus human crop review | No material regression |
| Remote calls | `calls` from job diagnostics/usage | At least 80% fewer on the supported scan/equation corpus |
| Runtime and VRAM | Wall time plus `nvidia-smi` peak sampling | Record p50/p95 and peak below the deployment limit |

Do not count the sidecar health check as extraction work. Count only actual
OpenRouter calls. Investigate pages tagged `sidecar_error`,
`low_ocr_confidence`, `implausibly_empty`, `invalid_formula`, or
`ambiguous_reading_order`; those tags are part of the `/api/jobs/{id}` response.

After the gate passes, change the default in `app/config.py` to `hybrid`. Until
then, opt in with `PDF2DOCX_EXTRACTION_PROVIDER=hybrid`. Rollback is a single
setting: `PDF2DOCX_EXTRACTION_PROVIDER=vision`.
