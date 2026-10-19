---
name: document-provenance
description: >-
  Take values from Word, PDF or Markdown reports into a pdo decision with section-level provenance: index
  the report into sections with content digests, propose each value with the exact passage that states it
  (a locator: document, section, page, quote), and absorb new versions of a report so that only the values
  whose section changed go back to a person. Use when someone has test reports, specifications, cost
  estimates or other documents to extract decision values from, asks where a value came from, wants to
  check that quoted values still match their sources, or has received a revised report.
---

# Values from reports, with section-level provenance

A value read from a report is only as trustworthy as the passage behind it. pdo keeps that passage:
every value taken from an indexed report carries a **locator** (the document and section digests, the
section's heading path, the page and paragraph, and the exact quote), computed by the tools, never typed.
Two things follow:

1. **Traceability.** `pdo explain`, the Decision Package and the dashboard show, for every value, the
   document, section, page and quote, so a reviewer knows exactly where to look. `pdo docs verify` (and
   `pdo validate`) checks every quote against its document; a quote that is not there is an error.
2. **Section-level staleness.** When a report is revised, `pdo docs revise` compares section digests.
   A value whose section text is unchanged has its locator moved to the new version automatically; a
   value whose section changed is flagged, and the `locators_current` readiness condition keeps the
   decision pending until a person reviews it. A revision changes no value, so it invalidates no
   approval; only a person's corrected value does.

Your job: read the report, propose values with exact quotes, and leave the judgements to the person.

## What you need

- The `pdo` command (`uv run pdo ...` in the repository) with the docs extra for Word and PDF
  (`uv sync --extra docs`). Every command takes `--json`. Over MCP the same operations are
  `pdo_docs_index`, `pdo_docs_read`, `pdo_docs_locate`, `pdo_docs_extract`, `pdo_docs_revise` and
  `pdo_docs_verify`.
- The report inside the program (or state) directory, e.g. `docs/test-report.docx`, so the program
  stays portable. Word headings must use heading styles; PDFs need bookmarks or numbered headings
  ("3.2 Results"); Markdown uses `#` headings. `pdo docs index` warns when it finds no structure.
- An evidence item for the report with `document_path` set (several evidence items may share one
  document, e.g. one per vehicle section). The tools write `document_digest`; you never do.

## Ground rules

- **Quote, never paraphrase.** Copy the passage exactly as `pdo docs show` prints it (pdo normalizes
  typography and whitespace, so the printed text always matches). The quote must state the value: a
  quote that does not is refused unless the person who derived the value accepts it. A quote that is not
  in the document, or cuts a word or number ("0.9" out of "0.98"), is refused; one that occurs more than
  once must be lengthened or restricted to a section (by its printed number, e.g. `section: "3.2"`).
- **Your values are provisional.** `pdo docs extract` adds new values (and changed values) with
  `applicability: provisional`, which the kernel treats as unknown. Only the person who owns the
  evidence accepts them: `accept: true` with their role (`--role`, which must be the role that owns the
  evidence; pdo refuses any other) when they record it themselves, or afterwards
  `pdo_record_human_action` with kind `applicability` (MCP), or a change set setting the observation's
  `applicability: accepted` (`pdo impact` previews it).
- **Re-anchoring a changed section is a judgement.** After a revision, a flagged value may still be
  right. Ask the owner of the evidence (the pending action names the role) to read the new section; only
  when they confirm the value (or give the new one) run `pdo docs extract` with that observation's id
  and the new quote.
- **Never edit locators or digests by hand,** and never copy a new version over an older file (`pdo docs
  verify` reports that as an error, and `pdo docs revise` refuses it): put the new version beside it (e.g.
  `docs/report-rev2.docx`) and absorb it with `pdo docs revise`, so earlier episodes still verify against
  the file they cite. Generic change sets (`pdo_apply_change`) cannot set locators or document digests.
- **Every value from an indexed document needs a locator.** One without (or whose locator was deleted)
  keeps `locators_current` pending until it is located or cites other evidence.
- **Framing is not in the report.** Criteria, weights, thresholds and readiness conditions come from
  the person, as in the sysml-to-decision skill.

## Procedure

1. **Index the report.** `pdo docs index docs/report.docx` lists the sections with ids, heading paths,
   pages and digests. Check the structure matches the document's outline; if headings are missing,
   ask for a version with heading styles or bookmarks.
2. **Read the sections that hold values.** `pdo docs show docs/report.docx 3.2` prints a section's text
   exactly as pdo matches quotes against it.
3. **Register the document.** If no evidence item names the report, add one (in the proposals'
   `evidence_items`, or in the baseline) with `document_path`, `source_ref`, `evidence_kind`, `issued_on`
   and `owner_role` (ask who owns it). Leave `content_sha256` empty: identity is section by section.
4. **Propose the values.** Write a proposals file:

   ```yaml
   evidence: ev:reliability-B          # default evidence for the observations below
   observations:
     - id: obs:B.reliability           # a new id adds an observation; an existing id updates it
       alternative: alt:B
       measure: meas:reliability
       value: 0.98
       quote: "Its demonstrated mission reliability is 0.98."
       section: "3.2"                  # optional: restrict the search
   ```

   Preview it: `pdo docs extract PROGRAM proposals.yaml`. Each row shows the section and page found and
   warns when the quote does not state the value. Fix refusals by copying the passage exactly.
5. **Write it with the person's go-ahead:** add `--write --role <their role>`. The values are
   provisional until accepted (`accept: true`, with the owner's role, only when the owner records the
   change themselves). While the decision is under construction (no episode built on the baseline yet),
   add `--baseline`: the values go into `baseline/documents.yaml` instead of a new episode. For a plain
   state directory, `--write` writes its `documents.yaml`.
6. **Check.** `pdo docs verify PROGRAM` must report no errors; `pdo validate` runs the same check.
   `pdo explain PROGRAM y/alt:B/meas:reliability` shows the passage behind the value.
7. **Make provenance a readiness condition** (once, with the decision owner): add a readiness condition
   with `predicate: locators_current`, so a revised report keeps the decision pending until its
   changed values are reviewed.

## When a report is revised

1. Put the new version beside the old one inside the program directory.
2. Preview: `pdo docs revise PROGRAM docs/report.docx docs/report-rev2.docx` (or name any evidence id
   on the document). It lists the sections that changed, the locators it moves (`re-anchor`, section
   text unchanged), and the values to review (`REVIEW`), with readiness and approval before and after.
3. Write it (`--write --role <evidence owner>`). Readiness becomes pending if any value was flagged;
   the approval still matches.
4. For each flagged value, show the owner the old quote and the new section (`pdo docs show
   docs/report-rev2.docx 3.3`). When they confirm or correct it, record it with `pdo docs extract`
   using the observation's id, the confirmed value and the new quote, with `accept: true` and their role
   (pdo refuses to re-anchor a flagged value otherwise). A corrected value is a material change: it can
   change the recommendation and invalidates the approval, as it should.

## Reading the results

- `review_locator <obs> (<role>)` in the pending actions: a value whose section changed; resolved by
  `pdo docs extract` after the role confirms it.
- `¶` on a dashboard node: the value comes from a report section; `¶?` means it awaits review.
- A Decision Package's "Where the values come from" table lists every value's passage.

The vehicle example (`examples/vehicle`) shows the whole cycle: the configuration sheets (PDF) and the
reliability report (Word) are indexed in the baseline, and episodes `ep:r1`..`ep:r4` absorb two
revisions of the report. Revision 2 flags only C's value (section 3.3), keeping the approval of B;
revision 3 raises C to 0.96 and C wins. `bench/provenance` measures the policy on 23 edits.
