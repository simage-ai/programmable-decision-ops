#!/usr/bin/env bash
# Build the synthetic reports of the vehicle example from their Markdown sources (needs pandoc and
# xelatex). The built files are committed, so tests and users never need these tools.
set -euo pipefail
cd "$(dirname "$0")/../examples/vehicle/docs"
export SOURCE_DATE_EPOCH=1767225600 FORCE_SOURCE_DATE=1  # 2026-01-01, for reproducible metadata
pandoc src/reliability-test-report-rev1.md -o reliability-test-report.docx
pandoc src/reliability-test-report-rev2.md -o reliability-test-report-rev2.docx
pandoc src/reliability-test-report-rev3.md -o reliability-test-report-rev3.docx
pandoc src/configuration-sheets.md --pdf-engine=xelatex -V geometry:margin=1in -V fontsize=11pt \
  -o configuration-sheets.pdf
ls -l *.docx *.pdf
