"""Document provenance for a decision state: verify locators, extract located values, and absorb revisions.

Values taken from reports carry a SourceLocator (pdo.docs). This module works at the level of a decision
state and its evidence:

- `verify` checks every locator against the file it names: the file must be the version the locator cites,
  the quote must be in the cited section at its recorded span, and every recorded field must match.
- `extract` turns proposed values with quotes into a change set: each quote is located (a quote that is
  not in the document, or cuts a word or number, is refused), a quote must state its value, new or changed
  values are provisional unless a person accepts them, and re-anchoring a value whose section changed is a
  person's judgement (accept, with their role).
- `revise` absorbs a new version of a document: every locator whose section text is unchanged moves to the
  new version, and every other one stays on the old version, which keeps the locators_current readiness
  condition pending for that value until a person re-anchors it.

Nothing here writes files; pdo.tools previews and writes the change sets.
"""

from __future__ import annotations

import functools
import hashlib
import math
import re
from pathlib import Path
from typing import Any

from pdo import model as m
from pdo.docs import DocumentError, DocumentIndex, QuoteError, compare, index_document, locate, reanchor, verify_locator
from pdo.load import index, items


class ProvenanceError(ValueError):
    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.hint = hint


@functools.lru_cache(maxsize=64)
def _cached_index(path: str, content: str) -> DocumentIndex:
    return index_document(path)


def load_index(path: str | Path) -> DocumentIndex:
    """Index a document, reusing the result while the file's content is unchanged."""
    p = Path(path)
    if not p.is_file():
        return index_document(p)  # raises DocumentError with the path
    return _cached_index(str(p.resolve()), hashlib.sha256(p.read_bytes()).hexdigest())


def document_file(evidence: m.Evidence, base_dir: str | Path) -> Path | None:
    return (Path(base_dir) / evidence.document_path) if evidence.document_path else None


def _relative(path: Path, base_dir: Path) -> str:
    try:
        return path.resolve().relative_to(base_dir.resolve()).as_posix()
    except ValueError:
        raise ProvenanceError(f"{path} is outside {base_dir}",
                              "Put the document inside the program or state directory (e.g. docs/), so the "
                              "program stays portable.") from None


def _tidy(locator: dict) -> dict:
    return {k: v for k, v in locator.items() if v is not None}


_SCIENTIFIC = re.compile(r"(?P<a>\d[\d,]*(?:\.\d+)?)\s?[×x·*]\s?10\^(?P<b>[-+]?\d+)"
                         r"|(?<![\d.])10\^(?P<c>[-+]?\d+)"
                         r"|(?P<d>\d+(?:\.\d+)?)[eE](?P<e>[-+]?\d+)")


def numbers_in(text: str) -> list[float]:
    """The numbers a passage states, as whole tokens: "0.98" is 0.98, "2,000" is 2000, "5-38" is 5 and 38
    (a minus sign only where nothing alphanumeric precedes it), and "1.2×10^-3", "10^5" and "1.2e-3" are one
    number each."""
    out, taken = [], []
    for sci in _SCIENTIFIC.finditer(text):
        if sci.group("a"):
            out.append(float(sci.group("a").replace(",", "")) * 10.0 ** int(sci.group("b")))
        elif sci.group("c") is not None:
            out.append(10.0 ** int(sci.group("c")))
        else:
            out.append(float(f"{sci.group('d')}e{sci.group('e')}"))
        taken.append(sci.span())
    for match in re.finditer(r"\d[\d,]*(?:\.\d+)?", text):
        if any(a <= match.start() < b for a, b in taken):
            continue
        token = match.group(0).rstrip(",")
        try:
            value = float(token.replace(",", ""))
        except ValueError:
            continue
        start = match.start()
        if start > 0 and text[start - 1] == "-" and (start < 2 or not text[start - 2].isalnum()):
            value = -value
        out.append(value)
    return out


def states_value(quote: str, value: float) -> bool:
    """Whether a number in the quote equals the value (or is it as a percentage)."""
    for n in numbers_in(quote):
        if math.isclose(n, value, rel_tol=1e-9, abs_tol=1e-12):
            return True
        if "%" in quote and math.isclose(n / 100, value, rel_tol=1e-9, abs_tol=1e-12):
            return True
    return False


def _indexed(state: m.DecisionState) -> dict[str, m.Evidence]:
    return {e.id: e for e in items(state.evidence_items) if e.document_digest}


def unlocated(state: m.DecisionState) -> list[str]:
    """Observations (not rejected) taken from an indexed document without a locator."""
    docs = _indexed(state)
    return sorted(o.id for o in items(state.observations)
                  if o.evidence in docs and o.locator is None and (o.applicability or "accepted") != "rejected")


# --- describing where values come from -------------------------------------------------------------

def sources(state: m.DecisionState, observation_ids) -> list[dict]:
    """Where each observation's value was taken from: its evidence, and the document passage when located.
    `current` is False for a locator made against an earlier version of the evidence's document."""
    objects = index(state)
    out = []
    for oid in observation_ids:
        o = objects.get(oid)
        if not isinstance(o, m.Observation):
            continue
        evidence = objects.get(o.evidence)
        row: dict[str, Any] = {"observation": o.id, "evidence": o.evidence,
                               "source_ref": getattr(evidence, "source_ref", None)}
        loc = o.locator
        if loc is not None:
            row.update(document=loc.document_path or getattr(evidence, "document_path", None), section=loc.section,
                       section_path=loc.section_path, page=loc.page, paragraph=loc.paragraph, quote=loc.quote,
                       current=getattr(evidence, "document_digest", None) in (None, loc.document_digest))
        out.append(row)
    return out


def cite(row: dict) -> str:
    """One line: document, section, page and quote (or the evidence citation when the value is not located)."""
    if "quote" not in row:
        return f"{row['evidence']}: {row.get('source_ref') or 'no stated source'}"
    where = f"{row.get('document') or row['evidence']} § {row['section']}"
    if row.get("section_path"):
        where += f" ({row['section_path']})"
    if row.get("page"):
        where += f", p. {row['page']}"
    text = f'{where}: "{row["quote"]}"'
    return text if row.get("current", True) else text + " [earlier version of the document; awaiting review]"


# --- verification ---------------------------------------------------------------------------------

def contradicted(problems: list[dict]) -> list[str]:
    """The errors where a readable document contradicts a locator (not a missing file): what must never be
    published."""
    return [f"{x['where']}: {x['message']}" for x in problems
            if x["severity"] == "error" and not x["message"].startswith("no document at")]


def _missing_extra(exc: Exception) -> bool:
    return isinstance(exc, DocumentError) and "--extra docs" in str(exc)


def verify(state: m.DecisionState, base_dir: str | Path) -> list[dict]:
    """Problems with the documents and locators of a state, as {severity, where, message}.

    Errors: a missing or unreadable document; an evidence item whose file is not the version it indexed
    (a file replaced in place); a locator on the current version whose file differs from it, whose quote is
    not at its recorded span, or whose recorded fields the document does not give. Warnings: a current quote
    that does not state its value; a value taken from an indexed document without a locator; anything on an
    earlier version (the locators_current readiness condition reports it); Word or PDF files when the docs
    extra is not installed."""
    problems: list[dict] = []
    objects = index(state)
    base = Path(base_dir)

    def problem(severity: str, where: str, message: str) -> None:
        problems.append({"severity": severity, "where": where, "message": message})

    def indexed(path: str, where: str, severity: str) -> DocumentIndex | None:
        try:
            return load_index(base / path)
        except ValueError as exc:
            problem("warning" if _missing_extra(exc) else severity, where, str(exc))
            return None

    for e in items(state.evidence_items):
        if not e.document_path:
            continue
        ix = indexed(e.document_path, f"{e.id}.document_path", "error")
        if ix is not None and e.document_digest and ix.digest != e.document_digest:
            problem("error", f"{e.id}.document_path",
                    f"{e.document_path} is not the version indexed in document_digest: a new version goes beside "
                    "the old file and is absorbed with pdo docs revise, never copied over it")
    for oid in unlocated(state):
        problem("warning", f"{oid}.locator", f"taken from the indexed document of {objects[oid].evidence} but not "
                "located in it; locators_current keeps readiness pending until it is (pdo docs extract)")
    for o in items(state.observations):
        loc = o.locator
        evidence = objects.get(o.evidence)
        if loc is None or not isinstance(evidence, m.Evidence) or not evidence.document_digest:
            continue  # not an indexed document (check_state warns)
        current = loc.document_digest == evidence.document_digest
        path = loc.document_path or (evidence.document_path if current else None)
        if path is None:
            continue  # an earlier version whose file is not named
        severity = "error" if current else "warning"
        ix = indexed(path, f"{o.id}.locator", severity)
        if ix is None:
            continue
        found = verify_locator(ix, loc.model_dump())
        if ix.digest != loc.document_digest:
            problem(severity, f"{o.id}.locator", f"{path} is not the version this locator cites")
            found = [text for text in found if "not in section" in text or "no section" in text]
        for text in found:
            problem(severity, f"{o.id}.locator", text)
        if current and o.value is not None and not states_value(loc.quote, o.value):
            problem("warning", f"{o.id}.locator", f"the quote does not state the value {o.value:g}: {loc.quote!r}")
    return problems


# --- extraction -----------------------------------------------------------------------------------

def extract(state: m.DecisionState, base_dir: str | Path, proposals: dict,
            role: str | None = None) -> tuple[dict, list[dict]]:
    """A change set adding or updating located observations, and a row per proposal.

    proposals:
      evidence: default evidence id (else an existing observation's own evidence)
      accept: true when the person recording the change (`role`) accepts the values
      evidence_items: new evidence objects to add (each with document_path inside the directory)
      observations: [{id, alternative, measure, value, unit, quote, section, evidence}] where an id that
        exists updates that observation (its value and locator) and a new id adds one

    Refused: a quote that is not in the document (or cuts a word or number); a quote that does not state its
    value, unless accepted; re-anchoring a value whose locator is on an earlier version, unless accepted; and
    accept without a role. Without accept, new and changed values are provisional.
    """
    base = Path(base_dir)
    objects = index(state)
    accept = bool(proposals.get("accept"))
    if accept and not role:
        raise ProvenanceError("accept needs the role of the person accepting the values",
                              "Pass the role (--role, or role over MCP); only that person may accept.")
    roles = sorted(r.id for r in items(state.roles))
    if role and role not in roles:
        raise ProvenanceError(f"{role} is not a role in this state", f"Roles: {', '.join(roles) or 'none'}.")
    new_evidence = {e["id"]: m.Evidence.model_validate(e) for e in proposals.get("evidence_items") or []}
    clash = sorted(set(new_evidence) & set(objects))
    if clash:
        raise ProvenanceError(f"evidence already exists: {', '.join(clash)}", "List only new evidence under "
                              "evidence_items; existing evidence is referred to by id.")
    for e in new_evidence.values():
        if e.document_path:
            e.document_path = _relative(base / e.document_path, base)
    add_obs: list[dict] = []
    assign: list[dict] = []
    rows: list[dict] = []
    errors: list[str] = []
    digests: dict[str, str] = {}  # evidence id -> digest of the version the locators are made against
    for i, item in enumerate(proposals.get("observations") or []):
        where = item.get("id") or f"observations[{i}]"
        existing = objects.get(item.get("id")) if item.get("id") else None
        if existing is not None and not isinstance(existing, m.Observation):
            errors.append(f"{where}: {item['id']} is a {type(existing).__name__}, not an observation")
            continue
        ev_id = item.get("evidence") or proposals.get("evidence") or getattr(existing, "evidence", None)
        evidence = new_evidence.get(ev_id) or objects.get(ev_id)
        if not isinstance(evidence, m.Evidence):
            errors.append(f"{where}: evidence {ev_id!r} does not exist")
            continue
        file = document_file(evidence, base)
        if file is None:
            errors.append(f"{where}: {ev_id} has no document_path, so there is no document to locate a quote in")
            continue
        try:
            ix = load_index(file)
        except ValueError as exc:
            errors.append(f"{where}: {exc}")
            continue
        if evidence.document_digest and evidence.document_digest != ix.digest:
            errors.append(f"{where}: {evidence.document_path} is not the version {ev_id} indexed; absorb the new "
                          "version with pdo docs revise first")
            continue
        if not item.get("quote"):
            errors.append(f"{where}: a quote is required: the passage that states the value")
            continue
        try:
            loc = {"document_path": evidence.document_path, **locate(ix, item["quote"], item.get("section"))}
        except QuoteError as exc:
            errors.append(f"{where}: {exc} ({exc.hint})")
            continue
        value = item.get("value", getattr(existing, "value", None))
        if value is not None and not states_value(loc["quote"], float(value)):
            if not accept:
                errors.append(f"{where}: the quote does not state {value}; quote the passage that does, or have the "
                              "person who derived the value accept it (accept, with their role)")
                continue
            warning = f"the quote does not state {value} (accepted by {role})"
        else:
            warning = None
        owner = getattr(existing, "owner_role", None) or item.get("owner_role") or evidence.owner_role
        if accept and owner and role != owner:
            errors.append(f"{where}: only {owner}, who owns this value's evidence, may accept it (not {role})")
            continue
        digests[ev_id] = ix.digest
        accepted_note = f"Accepted by {role} from section {loc['section']} of {evidence.document_path}."
        if isinstance(existing, m.Observation):
            indexed = bool(getattr(objects.get(existing.evidence), "document_digest", None))
            unanchored = existing.locator is None or existing.locator.document_digest != ix.digest
            if indexed and unanchored and not accept:
                errors.append(f"{where}: its locator is missing or on an earlier version of the document; whether the "
                              "value still stands is the evidence owner's judgement: record it with accept and "
                              "their role")
                continue
            changed_value = value is not None and value != existing.value
            if changed_value:
                assign.append({"target": existing.id, "slot": "value", "new_value": value})
            if ev_id != existing.evidence:
                assign.append({"target": existing.id, "slot": "evidence", "new_value": ev_id})
            assign.append({"target": existing.id, "slot": "locator", "new_value": _tidy(loc)})
            applicability = existing.applicability or "accepted"
            if accept:
                if applicability != "accepted":
                    assign.append({"target": existing.id, "slot": "applicability", "new_value": "accepted"})
                assign.append({"target": existing.id, "slot": "applicability_rationale", "new_value": accepted_note})
            elif changed_value:
                assign += [{"target": existing.id, "slot": "applicability", "new_value": "provisional"},
                           {"target": existing.id, "slot": "applicability_rationale",
                            "new_value": f"Value proposed from section {loc['section']} of {evidence.document_path};"
                                         " awaiting review."}]
            rows.append({"observation": existing.id, "action": "update" if changed_value else "re-anchor",
                         "value": value, "section": loc["section"], "page": loc["page"], "quote": loc["quote"],
                         "warning": warning})
            continue
        missing = [k for k in ("id", "alternative", "measure", "value") if item.get(k) is None]
        if missing:
            errors.append(f"{where}: a new observation needs {', '.join(missing)}")
            continue
        measure = objects.get(item["measure"])
        obs: dict[str, Any] = {"id": item["id"], "alternative": item["alternative"], "measure": item["measure"],
                               "value": value, "unit": item.get("unit") or getattr(measure, "unit", None),
                               "evidence": ev_id, "locator": _tidy(loc)}
        if accept:
            obs["applicability_rationale"] = accepted_note
        else:
            obs["applicability"] = "provisional"
            obs["applicability_rationale"] = (f"Proposed from section {loc['section']} of {evidence.document_path}; "
                                              "awaiting review.")
        add_obs.append(obs)
        rows.append({"observation": item["id"], "action": "add", "value": value, "section": loc["section"],
                     "page": loc["page"], "quote": loc["quote"], "warning": warning})
    if errors:
        raise ProvenanceError("; ".join(errors), "Nothing was written. Each quote must be copied exactly from the "
                              "document as whole words and numbers (pdo docs show prints the text pdo matches).")
    for ev_id, dig in sorted(digests.items()):
        if ev_id in new_evidence:
            new_evidence[ev_id].document_digest = dig
        elif objects[ev_id].document_digest != dig:
            assign.append({"target": ev_id, "slot": "document_digest", "new_value": dig})
    change: dict[str, Any] = {"trigger": "evidence"}
    add: dict[str, Any] = {}
    if new_evidence:
        add["evidence_items"] = [e.model_dump(exclude_none=True) for e in new_evidence.values()]
    if add_obs:
        add["observations"] = add_obs
    if add:
        change["add"] = add
    if assign:
        change["assign"] = assign
    return change, rows


# --- revisions ------------------------------------------------------------------------------------

def _within(path: str | Path, base: Path) -> Path:
    """A path given relative to the directory, relative to the working directory, or absolute."""
    p = Path(path)
    return p if p.is_absolute() else ((base / p) if (base / p).exists() else p.resolve())


def revise(state: m.DecisionState, base_dir: str | Path, document: str, new_file: str | Path) -> tuple[dict, dict]:
    """A change set moving every evidence item of `document` (an evidence id or a document_path) to
    `new_file`, and re-anchoring each locator whose section text is unchanged. Locators in changed sections,
    and values from the document without a locator, are flagged: readiness stays pending until a person
    re-anchors them."""
    base = Path(base_dir)
    objects = index(state)
    target = objects.get(document)
    if isinstance(target, m.Evidence):
        old_path = target.document_path
        if not old_path:
            raise ProvenanceError(f"{document} has no document_path", "Revise an indexed document.")
    elif any(e.document_path == Path(document).as_posix() for e in items(state.evidence_items)):
        old_path = Path(document).as_posix()  # as the evidence names it (the old file may be gone)
    else:
        old_path = _relative(_within(document, base), base)
    affected = [e for e in items(state.evidence_items)
                if e.document_path and Path(e.document_path).as_posix() == old_path]
    if not affected:
        paths = sorted({e.document_path for e in items(state.evidence_items) if e.document_path})
        raise ProvenanceError(f"no evidence names the document {old_path!r}",
                              "Indexed documents: " + (", ".join(paths) or "none"))
    new_path = _within(new_file, base)
    rel = _relative(new_path, base)
    if rel == old_path:
        raise ProvenanceError(f"{rel} is the file being revised",
                              "Put the new version beside the old one (e.g. docs/report-rev2.docx), so earlier "
                              "episodes still verify against the file they cite.")
    new_index = load_index(new_path)  # an identical re-export is allowed: the evidence moves to the new file
    try:
        old_index = load_index(base / old_path)
        sections = compare(old_index, new_index)
    except ValueError:
        old_index, sections = None, None  # the old file is gone; locators are re-anchored by digest alone
    warnings = []
    if old_index is not None and {e.document_digest for e in affected} != {old_index.digest}:
        warnings.append(f"{old_path} is not the version the evidence indexed; section changes are reported "
                        "against the file as it is now")
    for e in affected:
        if e.content_sha256:
            warnings.append(f"{e.id} has content_sha256 set: whole-file identity makes this revision material "
                            "for every value from it; leave it empty for a located document")
    assign: list[dict] = []
    for e in affected:
        if e.document_path != rel:
            assign.append({"target": e.id, "slot": "document_path", "new_value": rel})
        if e.document_digest != new_index.digest:
            assign.append({"target": e.id, "slot": "document_digest", "new_value": new_index.digest})
    reanchored, flagged = [], []
    ids = {e.id for e in affected}
    for o in items(state.observations):
        if o.evidence not in ids:
            continue
        applies = (o.applicability or "accepted") != "rejected"
        if o.locator is None:
            if applies:
                flagged.append({"observation": o.id, "section": None, "section_path": None, "value": o.value,
                                "quote": None, "status": "not located", "now": None, "quote_found_in": [],
                                "quote_in_section": False, "applies": True})
            continue
        new_loc, info = reanchor(o.locator.model_dump(exclude_none=True), new_index, old_index)
        if new_loc is not None:
            assign.append({"target": o.id, "slot": "locator", "new_value": _tidy({"document_path": rel, **new_loc})})
            reanchored.append({"observation": o.id, "from": o.locator.section, "to": new_loc["section"],
                               "status": info["status"]})
        else:
            flagged.append({"observation": o.id, "section": o.locator.section, "section_path": o.locator.section_path,
                            "value": o.value, "quote": o.locator.quote, "status": info["status"],
                            "now": info["section"], "quote_found_in": info["quote_found_in"],
                            "quote_in_section": info["quote_in_section"], "applies": applies})
    if any(e.document_version or e.source_ref for e in affected):
        warnings.append("document_version, source_ref and issued_on are left as they were; update them in a change "
                        "set if they describe the version")
    change = {"trigger": "evidence", "assign": assign}
    report = {"document": {"from": old_path, "to": rel, "digest": new_index.digest}, "title": new_index.title,
              "evidence": sorted(ids), "sections": sections, "reanchored": reanchored, "flagged": flagged,
              "warnings": warnings}
    return change, report


def describe_revision(report: dict, state: m.DecisionState | None = None) -> str:
    """The change set's change_description: what the new version changed, and what goes back for review, in
    words a reviewer reads on the timeline."""
    objects = index(state) if state is not None else {}

    def value_label(oid: str) -> str:
        o = objects.get(oid)
        if not isinstance(o, m.Observation):
            return oid
        measure = objects.get(o.measure)
        return f"{o.alternative.split(':', 1)[-1]}'s {(measure.name if measure else o.measure).lower()}"

    doc = report["document"]
    title = report.get("title") or doc["to"]
    changed = [s["path"].split(" > ")[-1] for s in report["sections"] or [] if s["status"] in ("changed", "removed")]
    added = [s["path"].split(" > ")[-1] for s in report["sections"] or [] if s["status"] == "added"]
    flagged = [value_label(f["observation"]) for f in report["flagged"] if f["applies"]]
    n = len(report["reanchored"])
    text = f"New version of {title} ({doc['to']})"
    if changed or added:
        text += ": " + "; ".join(([f"changed {', '.join(changed)}"] if changed else [])
                                 + ([f"added {', '.join(added)}"] if added else []))
    text += f". {n} value{'s' if n != 1 else ''} carried over (their sections are unchanged)"
    if flagged:
        many = len(flagged) > 1
        text += (f"; {' and '.join(flagged)} {'need' if many else 'needs'} review because "
                 f"{'their' if many else 'its'} section changed.")
    else:
        text += "; nothing needs review."
    return text
