"""Import SysML v2 textual models into draft decision states.

An engineering model already states what the configurations are, what values they carry and what they
must satisfy. A decision needs those facts as typed objects that keep their provenance. This module
projects a documented subset of SysML v2 textual notation onto the decision schema by fixed rules. Each
rule has an id (RULES), and every object records the rule that produced it in `mapping_rule` (or in its
comments, for classes without SourceRef):

    variant parts of one part variation, or the part usages of one definition   ->  Alternative
    attribute names of the alternatives (dotted paths into nested parts)        ->  Measure
    literal attribute values of each alternative (`= 40 [gal]`, `:>> x = ...`)  ->  Observation
    each model file that supplies those values                                  ->  Evidence (path + digest)
    `require constraint { subject.attr <op> literal [unit] }`                   ->  Requirement
    `analysis def` / `verification def` with `in`/`return` bound to the subject  ->  EvaluationSpecification
                                                                                    placeholder (no implementation)
    analysis inputs with literal values, `assume constraint` equalities         ->  Assumption (unvalidated)

Everything else becomes a MappingGap (element, kind, reason): ports, connections, states, calculations,
constraints it cannot parse, values that are expressions, units that do not agree. The importer never
guesses: when a rule does not apply cleanly the element is a gap, never a best effort, and when the
model offers several candidate sets of alternatives it picks none. It produces no criteria, weights,
anchors, decision rule or readiness conditions; those come from framing the decision with the people
who own it. An optional mapping file supplies what the model does not say: ids, units for unitless
values, the incumbent, and how a placeholder evaluation is executed. Every value in it is a person's
statement.

The parser is a tolerant statement splitter, not a full SysML grammar. It reads declarations (keyword,
name, typing, specialization, redefinition, value, body) and parses expressions only where a rule needs
one, so syntax outside the subset degrades to gaps instead of failing the import.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass, field
from datetime import date
from functools import cached_property
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from pdo import model as m

RULES = {
    "alternative.variant": "each variant of the selected part variation is an alternative",
    "alternative.same-definition": "each package-level part usage of the selected part definition is an alternative",
    "alternative.specialization": "each package-level part usage specializing the selected usage is an alternative",
    "alternative.listed": "the mapping file lists the alternatives",
    "measure.attribute": "an attribute of the alternatives (dotted path into nested parts) is a measure",
    "observation.attribute-value": "a literal number (with an optional [unit]) on an alternative's attribute",
    "evidence.model-file": "each model file that supplies observed values is one evidence item",
    "requirement.require-constraint": "a require constraint comparing a subject attribute with a literal",
    "assumption.analysis-parameter": "an analysis or verification input with a literal value",
    "assumption.assume-constraint": "an assume constraint equating a subject attribute with a literal",
    "evaluation.analysis-def": "an analysis def whose return is bound to a subject attribute (placeholder)",
    "evaluation.verification-def": "a verification def whose return is bound to a subject attribute (placeholder)",
}

# Gap kinds for partial translations. Elements that were not translated at all carry their SysML keyword
# as the kind instead ("port", "state def", "calc def", ...).
PROBLEM_KINDS = {
    "alternatives": "no unique set of alternatives in the model",
    "attribute-value": "an alternative's attribute value is not a literal number",
    "unit": "a unit is missing, conflicting, or differs from the measure's unit",
    "requirement": "a requirement with nothing the kernel can check",
    "requirement-subject": "a requirement's subject does not identify the alternatives it applies to",
    "constraint": "a constraint that cannot be read as attribute <op> literal",
    "assume-constraint": "an assumption of a requirement that pdo cannot record as an Assumption",
    "evaluation": "an analysis or verification case with no output bound to a measure",
    "evaluation-input": "an evaluation input bound to neither a subject attribute nor a literal",
    "evaluation-output": "an evaluation output not bound to a subject attribute",
    "implementation": "an evaluation placeholder with no executable implementation yet",
    "producer-conflict": "a measure that is both observed and computed for an alternative",
    "mapping": "a mapping-file entry that matches nothing, or matches more than one element",
    "parse": "text the parser could not read",
}

SCALAR_TYPES = {"Real", "Integer", "Natural", "Positive", "Rational", "Number"}
MAX_DEPTH = 8  # reference chains (attribute = attribute = ...) followed before giving up
MAX_FEATURE_DEPTH = 64  # inheritance plus part nesting read for an element's attributes
MAX_NESTING = 100  # body nesting the parser reads

STATE_FILES = (
    ("sysml-alternatives.yaml", ("alternatives",)),
    ("sysml-measures.yaml", ("measures",)),
    ("sysml-requirements.yaml", ("requirements",)),
    ("sysml-evidence.yaml", ("evidence_items", "observations")),
    ("sysml-assumptions.yaml", ("assumptions",)),
    ("sysml-evaluations.yaml", ("evaluation_specifications",)),
)
REPORT_FILE = "sysml-import.json"


class SysMLError(ValueError):
    """The files or the mapping cannot be read, or the mapping produces an invalid state."""


@dataclass(frozen=True)
class MappingGap:
    """Something in the model the importer did not translate, and why."""

    element: str
    kind: str
    reason: str
    source_model: str | None = None
    line: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    def __str__(self) -> str:
        where = f" ({self.source_model}:{self.line})" if self.source_model else ""
        return f"[{self.kind}] {self.element}{where}: {self.reason}"


@dataclass
class ImportResult:
    state: dict  # a partial DecisionState, ready for yaml.safe_dump or DecisionState.model_validate
    gaps: list[MappingGap]
    rules_used: list[str]
    sources: list[dict] = field(default_factory=list)  # [{"path": ..., "sha256": ...}]

    def counts(self) -> dict[str, int]:
        return {slot: len(value) for slot, value in self.state.items() if isinstance(value, list)}

    def missing_to_run(self) -> list[str]:
        """Top-level slots the kernel needs that a SysML model never supplies (framing)."""
        from pdo.load import EXECUTABLE_REQUIRED

        return [slot for slot in EXECUTABLE_REQUIRED if not self.state.get(slot)]

    def to_dict(self) -> dict:
        return {"sources": self.sources, "counts": self.counts(), "rules_used": self.rules_used,
                "gaps": [g.to_dict() for g in self.gaps], "missing_to_run": self.missing_to_run(),
                "state": self.state}


# --- tokens -------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Token:
    kind: str  # name | number | string | comment | sym
    text: str  # unrestricted names are unquoted; comments are their cleaned body
    line: int
    quoted: bool = False


_TOKEN = re.compile(r"""
    (?P<ws>\s+)
  | (?P<note>//\*.*?\*/|//[^\n]*)
  | (?P<comment>/\*.*?\*/)
  | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<uname>'(?:[^'\\]|\\.)*')
  | (?P<number>(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?)
  | (?P<string>"(?:[^"\\]|\\.)*")
  | (?P<sym>::>|:>>|:>|::|:=|===|!==|==|!=|<=|>=|->|\*\*|\.\.|[{}()\[\];,=:<>+\-*/.\#@~?!&|%^$])
""", re.S | re.X)
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _comment_text(raw: str) -> str:
    lines = (re.sub(r"^\s*\*?", "", line) for line in raw[2:-2].splitlines())
    return " ".join(" ".join(lines).split())


def tokenize(text: str) -> tuple[list[Token], list[tuple[int, str]]]:
    """Tokens, and (line, problem) pairs for text that is not SysML."""
    tokens: list[Token] = []
    problems: list[tuple[int, str]] = []
    pos, line = 0, 1
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        if match is None:
            if text.startswith("/*", pos):
                problems.append((line, "unterminated /* comment; the rest of the file is ignored"))
                break
            problems.append((line, f"unexpected character {text[pos]!r}"))
            pos += 1
            continue
        kind, raw = match.lastgroup, match.group()
        if kind == "uname":
            tokens.append(Token("name", re.sub(r"\\(.)", r"\1", raw[1:-1]), line, quoted=True))
        elif kind == "comment":
            tokens.append(Token("comment", _comment_text(raw), line))
        elif kind in ("name", "number", "string", "sym"):
            tokens.append(Token(kind, raw, line))
        line += raw.count("\n")
        pos = match.end()
    return tokens, problems


@dataclass(frozen=True)
class Ref:
    """A reference: a qualified name (A::B) followed by an optional feature chain (.c.d)."""

    parts: tuple[str, ...]
    chain: tuple[str, ...] = ()

    @property
    def last(self) -> str:
        return (self.chain or self.parts)[-1]

    def __str__(self) -> str:
        return _qname(self.parts) + "".join("." + _quote(c) for c in self.chain)


@dataclass(frozen=True)
class Literal:
    value: float | int
    unit: str | None  # the unit text as written (spaces removed), or None

    def __str__(self) -> str:
        return f"{self.value}" + (f" [{self.unit}]" if self.unit else "")


def _quote(name: str) -> str:
    return name if _IDENT.fullmatch(name) else "'" + name.replace("'", "\\'") + "'"


def _qname(parts: Iterable[str]) -> str:
    return "::".join(_quote(p) for p in parts)


def _text(tokens: list[Token]) -> str:
    out = ""
    for t in tokens:
        piece = _quote(t.text) if t.kind == "name" and t.quoted else t.text
        glue = t.kind == "sym" and t.text in (".", "::", ")", "]", ",", ";") or out.endswith(("(", "[", ".", "::"))
        out += piece if glue or not out else " " + piece
    return out


def _is(token: Token | None, *texts: str) -> bool:
    return token is not None and token.kind == "sym" and token.text in texts


def _word(token: Token | None) -> str | None:
    return token.text if token is not None and token.kind == "name" and not token.quoted else None


def _parse_ref(tokens: list[Token], i: int) -> tuple[Ref | None, int]:
    if _is(tokens[i] if i < len(tokens) else None, "~"):
        i += 1
    if i >= len(tokens) or tokens[i].kind != "name":
        return None, i
    parts, chain = [tokens[i].text], []
    i += 1
    while i + 1 < len(tokens) and _is(tokens[i], "::", ".") and tokens[i + 1].kind == "name":
        # Everything after the first '.' is a feature chain, even if '::' follows.
        (chain if chain or tokens[i].text == "." else parts).append(tokens[i + 1].text)
        i += 2
    return Ref(tuple(parts), tuple(chain)), i


def _close(tokens: list[Token], i: int, open_: str, close: str) -> int:
    """Index of the token closing the bracket at i, or len(tokens) when it is never closed."""
    depth = 0
    for j in range(i, len(tokens)):
        if _is(tokens[j], open_):
            depth += 1
        elif _is(tokens[j], close):
            depth -= 1
            if depth == 0:
                return j
    return len(tokens)


def _unit(tokens: list[Token]) -> str:
    return "".join(t.text for t in tokens)


def _number(text: str) -> float | int:
    return int(text) if text.isdigit() else float(text)


def parse_literal(tokens: list[Token] | None) -> Literal | None:
    """A literal number with an optional [unit], e.g. `-1.5e3 [kg]`; None for anything else."""
    if not tokens:
        return None
    sign, i = 1, 0
    if _is(tokens[0], "-", "+"):
        sign, i = (-1 if tokens[0].text == "-" else 1), 1
    if i >= len(tokens) or tokens[i].kind != "number":
        return None
    value = _number(tokens[i].text) * sign
    i += 1
    unit = None
    if i < len(tokens) and _is(tokens[i], "["):
        j = _close(tokens, i, "[", "]")
        if j >= len(tokens) or j - i < 2:
            return None
        unit, i = _unit(tokens[i + 1:j]), j + 1
    return Literal(value, unit) if i == len(tokens) else None


def parse_operand(tokens: list[Token] | None) -> Literal | Ref | None:
    literal = parse_literal(tokens)
    if literal is not None or not tokens:
        return literal
    ref, end = _parse_ref(tokens, 0)
    return ref if ref is not None and end == len(tokens) else None


def _strip_parens(tokens: list[Token]) -> list[Token]:
    while len(tokens) >= 2 and _is(tokens[0], "(") and _close(tokens, 0, "(", ")") == len(tokens) - 1:
        tokens = tokens[1:-1]
    return tokens


def _top_level(tokens: list[Token]) -> Iterator[tuple[int, Token]]:
    depth = 0
    for i, t in enumerate(tokens):
        if _is(t, "(", "[", "{"):
            depth += 1
        elif _is(t, ")", "]", "}"):
            depth -= 1
        elif depth == 0:
            yield i, t


COMPARISONS = ("<=", ">=", "<", ">", "==", "!=", "===", "!==")


def parse_comparisons(tokens: list[Token]) -> tuple[list[tuple[Any, str, Any]], str | None]:
    """`a <op> b` terms joined by `and` / `&`; or an error saying why the expression is outside the subset."""
    tokens = _strip_parens(tokens)
    if not tokens:
        return [], "the constraint has no expression"
    for _, t in _top_level(tokens):
        if _word(t) in ("or", "xor", "implies", "not", "if") or _is(t, "|", "!", "?", "->"):
            return [], f"`{t.text}` is not supported: a requirement is a conjunction of comparisons"
    terms, start = [], 0
    for i, t in _top_level(tokens):
        if _word(t) == "and" or _is(t, "&"):
            terms.append(tokens[start:i])
            start = i + 1
    terms.append(tokens[start:])
    out = []
    for term in terms:
        term = _strip_parens(term)
        ops = [(i, t.text) for i, t in _top_level(term) if _is(t, *COMPARISONS)]
        if len(ops) != 1:
            return [], "expected one comparison per term"
        i, op = ops[0]
        lhs, rhs = parse_operand(term[:i]), parse_operand(term[i + 1:])
        if lhs is None or rhs is None:
            side = _text(term[:i]) if lhs is None else _text(term[i + 1:])
            return [], f"`{side}` is an expression; each side must be an attribute or a literal number"
        out.append((lhs, op, rhs))
    return out, None


# --- statements and elements ----------------------------------------------------------------------------------

@dataclass
class _Stmt:
    head: list[Token]
    body: list[Token] | None
    terminated: bool  # ended by ';' or a body; False for a trailing expression


def _split(tokens: list[Token], problems: list[tuple[int, str]]) -> list[_Stmt]:
    """Split a token run into statements: up to ';' or a {...} body at depth 0, or a doc/comment."""
    out: list[_Stmt] = []
    head: list[Token] = []
    depth, i = 0, 0
    while i < len(tokens):
        t = tokens[i]
        i += 1
        if t.kind == "comment" and depth == 0:
            if not head:
                out.append(_Stmt([t], None, True))
            elif _word(head[0]) in ("doc", "comment", "locale") or _word(head[-1]) == "doc":
                out.append(_Stmt([*head, t], None, True))
                head = []
            continue
        if t.kind == "sym":
            if t.text in ("(", "[") or (t.text == "{" and depth > 0):
                depth += 1
            elif t.text in (")", "]") or (t.text == "}" and depth > 0):
                depth = max(0, depth - 1)
            elif t.text == ";" and depth == 0:
                out.append(_Stmt(head, None, True))
                head = []
                continue
            elif t.text == "{":
                j = _close(tokens, i - 1, "{", "}")
                if j >= len(tokens):
                    problems.append((t.line, "unclosed { (the body runs to the end of the file)"))
                out.append(_Stmt(head, tokens[i:j], True))
                head, i = [], j + 1
                continue
            elif t.text == "}":
                problems.append((t.line, "unmatched }"))
                continue
        head.append(t)
    if head:
        out.append(_Stmt(head, None, False))
    return out


@dataclass(frozen=True, eq=False)
class Source:
    path: Path
    display: str  # the path as recorded in source_model
    sha256: str
    text: str


VISIBILITY = {"public", "private", "protected"}
PREFIXES = {"abstract", "variation", "variant", "readonly", "derived", "constant", "ref", "in", "out", "inout", "end",
            "individual", "snapshot", "timeslice", "return", "require", "assume", "assert", "not", "ordered",
            "nonunique", "composite", "portion", "var", "const"}
KINDS = {"part", "attribute", "requirement", "constraint", "port", "connection", "interface", "flow", "action",
         "state", "calc", "analysis", "verification", "case", "use", "item", "occurrence", "allocation", "view",
         "viewpoint", "rendering", "concern", "enum", "metadata", "package", "transition", "message", "subject",
         "objective", "actor", "stakeholder", "binding", "succession", "event", "namespace"}
RELATIONS = {"import", "alias", "satisfy", "verify", "allocate", "bind", "connect", "dependency", "perform", "exhibit",
             "include", "expose", "render", "frame", "first", "then", "entry", "exit", "do", "accept", "send", "if",
             "else", "while", "for", "loop", "until", "merge", "decide", "join", "fork", "assign", "filter",
             "specialization", "subclassifier", "redefinition", "conjugation", "featured", "subtype"}
RESERVED = {"def", "redefines", "subsets", "specializes", "references", "default", "defined", "by", "about", "to",
            "from", "connect", "for", "of", "via", "then", "first", "parallel", "ordered", "nonunique", "all", "new",
            "as", "and", "or", "xor", "not", "implies", "if", "else", "true", "false", "null", "accept", "do"}
NAMESPACE_KINDS = {"package", "namespace", "import", "alias", "doc", "comment", "locale"}


@dataclass(eq=False)
class Element:
    """One declaration or statement of the model, with its owned members."""

    kind: str  # SysML keyword (part, attribute, ...), a relation keyword, "feature" (no keyword) or "expression"
    src: Source
    line: int
    owner: Element | None = None
    is_def: bool = False
    prefixes: frozenset[str] = frozenset()
    name: str | None = None
    short_name: str | None = None
    types: list[Ref] = field(default_factory=list)  # ':' / defined by
    supers: list[Ref] = field(default_factory=list)  # ':>' / subsets / specializes / '::>'
    redefines: list[Ref] = field(default_factory=list)  # ':>>' / redefines
    value: list[Token] | None = None  # after '=', ':=' or 'default'
    doc: str | None = None
    members: list[Element] = field(default_factory=list)
    tokens: list[Token] = field(default_factory=list)  # the statement head (an expression's tokens)

    @property
    def feature_name(self) -> str | None:
        return self.name or (self.redefines[0].last if self.redefines else None)

    @property
    def keyword(self) -> str:
        words = [p for p in ("variation", "variant", "assert", "require", "assume") if p in self.prefixes]
        return " ".join([*words, self.kind, *(["def"] if self.is_def else [])])

    @cached_property
    def qparts(self) -> tuple[str | None, ...]:
        return (*(self.owner.qparts if self.owner else ()), self.feature_name)

    @property
    def label(self) -> str:
        """The qualified name, with unnamed elements shown by keyword and line."""
        own = self.feature_name or f"({self.keyword}, line {self.line})"
        if self.owner is None:
            return _quote(own) if self.feature_name else own
        return f"{self.owner.label}::{_quote(own) if self.feature_name else own}"

    @property
    def is_package_level(self) -> bool:
        return self.owner is None or self.owner.kind in ("package", "namespace")

    def expression(self) -> Element | None:
        return next((e for e in reversed(self.members) if e.kind == "expression"), None)


def _declaration(stmt: _Stmt, owner: Element | None, src: Source) -> Element:
    toks = stmt.head
    el = Element("feature", src, toks[0].line if toks else (stmt.body[0].line if stmt.body else 0), owner, tokens=toks)
    if not toks:
        el.kind = "block"
        return el
    if not stmt.terminated or toks[0].kind in ("number", "string") or _is(toks[0], "(", "-", "+", "!"):
        el.kind = "expression"
        return el
    if toks[0].kind == "comment":
        el.kind = "comment"
        return el
    i, prefixes = 0, set()
    while i < len(toks):
        w = _word(toks[i])
        if w in VISIBILITY or w in PREFIXES:
            if w in PREFIXES:
                prefixes.add(w)
            i += 1
        elif _is(toks[i], "#") and i + 1 < len(toks) and toks[i + 1].kind == "name":
            prefixes.add("#" + toks[i + 1].text)
            i += 2
        else:
            break
    el.prefixes = frozenset(prefixes)
    w = _word(toks[i]) if i < len(toks) else None
    if i < len(toks) and _is(toks[i], "@"):
        el.kind = "metadata"
        return el
    if w in ("doc", "comment", "locale"):
        el.kind = w
        el.doc = next((t.text for t in reversed(toks) if t.kind == "comment"), None)
        return el
    if w in RELATIONS:
        el.kind = w
        return el
    if w in ("library", "standard"):
        while i < len(toks) and _word(toks[i]) != "package":
            i += 1
        w = "package"
    if w in KINDS:
        words = [w]
        i += 1
        while i < len(toks) and _word(toks[i]) in KINDS:
            words.append(_word(toks[i]))
            i += 1
        el.kind = " ".join(words)
    if el.kind == "feature" and prefixes & {"individual", "snapshot", "timeslice"}:
        el.kind = "occurrence"  # `individual def X`, `snapshot s`: occurrences without a kind keyword
    if i < len(toks) and _word(toks[i]) == "def":
        el.is_def = True
        i += 1
    if i + 2 < len(toks) and _is(toks[i], "<") and toks[i + 1].kind in ("name", "number") and _is(toks[i + 2], ">"):
        el.short_name = toks[i + 1].text
        i += 3
    if (i < len(toks) and toks[i].kind == "name" and (toks[i].quoted or toks[i].text not in RESERVED)
            and not _is(toks[i + 1] if i + 1 < len(toks) else None, ".")):
        el.name = toks[i].text
        i += 1
    while i < len(toks):
        t, w = toks[i], _word(toks[i])
        if _is(t, "["):
            i = _close(toks, i, "[", "]") + 1
        elif _is(t, ":") or (w == "defined" and _word(toks[i + 1] if i + 1 < len(toks) else None) == "by"):
            i = _ref_list(toks, i + (1 if _is(t, ":") else 2), el.types)
        elif _is(t, ":>", "::>") or w in ("subsets", "specializes", "references"):
            i = _ref_list(toks, i + 1, el.supers)
        elif _is(t, ":>>") or w == "redefines":
            i = _ref_list(toks, i + 1, el.redefines)
        elif _is(t, "=", ":="):
            el.value = toks[i + 1:]
            break
        elif w == "default":
            i += 1 + (1 if i + 1 < len(toks) and _is(toks[i + 1], "=", ":=") else 0)
            el.value = toks[i:]
            el.prefixes = el.prefixes | {"default"}
            break
        elif w in ("ordered", "nonunique", "parallel", "abstract") or _is(t, "~"):
            i += 1
        else:
            break
    return el


def _ref_list(toks: list[Token], i: int, into: list[Ref]) -> int:
    while True:
        ref, i = _parse_ref(toks, i)
        if ref is None:
            return i
        into.append(ref)
        if i < len(toks) and _is(toks[i], "["):
            i = _close(toks, i, "[", "]") + 1
        if not (i < len(toks) and _is(toks[i], ",")):
            return i
        i += 1


def _build(stmts: list[_Stmt], owner: Element | None, src: Source, problems: list, depth: int = 0) -> list[Element]:
    out = []
    for stmt in stmts:
        el = _declaration(stmt, owner, src)
        if el.kind in ("doc", "comment", "locale"):
            if el.kind == "doc" and owner is not None and owner.doc is None:
                owner.doc = el.doc
            continue
        if stmt.body is not None and depth >= MAX_NESTING:
            problems.append((el.line, f"nested more than {MAX_NESTING} levels deep; the inner body is not read"))
        elif stmt.body is not None:
            el.members = _build(_split(stmt.body, problems), el, src, problems, depth + 1)
        out.append(el)
    return out


def parse(src: Source) -> tuple[list[Element], list[tuple[int, str]]]:
    """The file's top-level elements, and (line, problem) pairs for what could not be read."""
    tokens, problems = tokenize(src.text)
    return _build(_split(tokens, problems), None, src, problems), problems


def _all(elements: Iterable[Element]) -> Iterator[Element]:
    for el in elements:
        yield el
        yield from _all(el.members)


class _Model:
    """All elements of the imported files, indexed for name resolution."""

    def __init__(self, roots: list[Element]):
        self.roots = roots
        self.elements = list(_all(roots))
        self.by_qparts: dict[tuple, list[Element]] = defaultdict(list)
        self.by_last: dict[str, list[Element]] = defaultdict(list)
        for el in self.elements:
            if el.kind in ("import", "alias", "expression") or el.feature_name is None:
                continue
            self.by_last[el.feature_name].append(el)
            if None not in el.qparts:
                self.by_qparts[el.qparts].append(el)

    def resolve(self, ref: Ref, scope: Element | None, exclude: Element | None = None) -> Element | None:
        """Look the qualified part of `ref` up from `scope` outwards, then as a unique qualified-name suffix."""
        parts = ref.parts
        ns = scope
        while True:
            prefix = ns.qparts if ns is not None else ()
            if None not in prefix:
                hits = [e for e in self.by_qparts.get((*prefix, *parts), []) if e is not exclude]
                if len(hits) == 1:
                    return hits[0]
                if hits:
                    return None
            if ns is None:
                break
            ns = ns.owner
        hits = [e for e in self.by_last.get(parts[-1], []) if e is not exclude and e.qparts[-len(parts):] == parts]
        return hits[0] if len(hits) == 1 else None


# --- the mapping file -----------------------------------------------------------------------------------------

SECTION_SLOTS = {
    "measures": {"id", "unit", "observation", "name", "description"},
    "requirements": {"id", "name", "description", "owner_role", "mandatory", "check_tolerance"},
    "evaluations": {"id", "name", "description", "implementation", "implementation_version", "owner_role",
                    "cost_units", "deterministic", "cacheable", "environment", "parameters"},
    "assumptions": {"id", "name", "description", "unit", "critical", "validation_status", "plausible_low",
                    "plausible_high", "uncertainty", "owner_role"},
    "evidence": {"id", "name", "description", "evidence_kind", "owner_role", "issued_on", "valid_until",
                 "document_version"},
}
ALTERNATIVE_SLOTS = {"id", "name", "description", "is_incumbent"}


@dataclass
class Mapping:
    """What a person adds to the model: ids, units, the alternatives' source, and evaluation wiring."""

    alternatives_from: str | None = None
    alternatives: dict[str, dict] = field(default_factory=dict)
    units: dict[str, str] = field(default_factory=dict)
    sections: dict[str, dict[str, dict]] = field(default_factory=lambda: {s: {} for s in SECTION_SLOTS})


def _entries(section: str, raw: Any, allowed: set[str]) -> dict[str, dict]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise SysMLError(f"mapping: {section} must be a mapping of element names to ids or slot mappings")
    out = {}
    for key, value in raw.items():
        entry = {"id": value} if isinstance(value, str) else value
        if not isinstance(entry, dict):
            raise SysMLError(f"mapping: {section}.{key} must be an id or a mapping")
        unknown = set(entry) - allowed
        if unknown:
            raise SysMLError(f"mapping: {section}.{key}: unknown keys {sorted(unknown)} (allowed: {sorted(allowed)})")
        out[str(key)] = {k: (v.isoformat() if isinstance(v, date) else v) for k, v in entry.items()}
    return out


def load_mapping(mapping: Mapping | dict | str | Path | None) -> Mapping:
    if mapping is None or isinstance(mapping, Mapping):
        return mapping or Mapping()
    if isinstance(mapping, str | Path):
        path = Path(mapping)
        if not path.is_file():
            raise SysMLError(f"{path}: no such mapping file")
        try:
            mapping = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise SysMLError(f"{path}: {exc}") from exc
    if not isinstance(mapping, dict):
        raise SysMLError("mapping: expected a mapping at the top level")
    unknown = set(mapping) - {"alternatives", "units", *SECTION_SLOTS}
    if unknown:
        raise SysMLError(f"mapping: unknown top-level keys {sorted(unknown)}")
    alts = mapping.get("alternatives") or {}
    if not isinstance(alts, dict) or set(alts) - {"from", "ids"} or not isinstance(alts.get("from", ""), str):
        raise SysMLError("mapping: alternatives takes `from` (an element name) and `ids` (element: id)")
    units = mapping.get("units") or {}
    if not isinstance(units, dict) or not all(isinstance(v, str) for v in units.values()):
        raise SysMLError("mapping: units must map SysML unit text to pdo unit strings")
    return Mapping(alternatives_from=alts.get("from"), alternatives=_entries("alternatives.ids", alts.get("ids"),
                                                                            ALTERNATIVE_SLOTS),
                   units={str(k): v for k, v in units.items()},
                   sections={s: _entries(s, mapping.get(s), slots) for s, slots in SECTION_SLOTS.items()})


def _key_parts(key: str) -> tuple[str, ...]:
    return tuple(p.strip("'") for p in key.split("::"))


def _matches(key: str, el: Element) -> bool:
    parts = _key_parts(key)
    return el.qparts[-len(parts):] == parts


# --- the importer ---------------------------------------------------------------------------------------------

@dataclass
class _Feature:
    key: str  # attribute name, or a dotted path into nested parts
    decls: list[Element]  # declarations from the most general to the most specific
    value_el: Element | None  # the element whose value applies

    @property
    def doc(self) -> str | None:
        return next((d.doc for d in reversed(self.decls) if d.doc), None)

    @property
    def scalar(self) -> bool:
        return any(r.parts[-1] in SCALAR_TYPES and r.parts[:-1] in ((), ("ScalarValues",))
                   for d in self.decls for r in d.types)


@dataclass
class _Subject:
    name: str | None
    value_el: Element | None
    classifiers: list[Element]  # the resolved types and supersets
    declared: list[Ref]  # as written, including those not defined in the imported files


@dataclass(frozen=True)
class _Path:
    subject: _Subject | None  # rooted at the requirement's or evaluation's subject
    element: Element | None  # or at a named model element
    chain: tuple[str, ...]
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Lit:
    literal: Literal
    notes: tuple[str, ...] = ()


@dataclass
class _Draft:
    """A requirement, evaluation or assumption before ids and units are settled."""

    element: Element
    key: str = ""
    data: dict = field(default_factory=dict)


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", text).strip("-") or "x"


def _local(oid: str) -> str:
    return oid.split(":", 1)[1] if ":" in oid else oid


class _Importer:
    def __init__(self, sources: list[Source], mapping: Mapping):
        self.sources = sources
        self.mapping = mapping
        self.gaps: dict[tuple[str, str], MappingGap] = {}
        self.rules: set[str] = set()
        self.handled: set[Element] = set()
        self.matched_keys: set[tuple[str, str]] = set()
        roots = []
        for src in sources:
            elements, problems = parse(src)
            roots.extend(elements)
            for line, problem in problems:
                self.gaps[(f"{src.display}:{line}", "parse")] = MappingGap(f"{src.display}:{line}", "parse", problem,
                                                                          src.display, line)
        self.model = _Model(roots)
        self._bases: dict[Element, list[Element]] = {}
        self._ancestors: dict[Element, set[Element]] = {}
        self._features: dict[Element, dict[str, _Feature]] = {}

    # -- recording

    def gap(self, el: Element | str, kind: str, reason: str) -> None:
        label = el if isinstance(el, str) else el.label
        if (label, kind) not in self.gaps:
            src, line = (None, None) if isinstance(el, str) else (el.src.display, el.line)
            self.gaps[(label, kind)] = MappingGap(label, kind, reason, src, line)
        if isinstance(el, Element):
            self.handled.add(el)

    def lookup(self, section: str, el: Element, entries: dict[str, dict] | None = None) -> dict:
        """The mapping entry for an element, if exactly one key names it."""
        entries = self.mapping.sections.get(section, {}) if entries is None else entries
        keys = [k for k in entries if _matches(k, el)]
        for k in keys:
            self.matched_keys.add((section, k))
        if len(keys) > 1:
            self.gap(f"mapping:{section}", "mapping", f"{el.label} is named by several keys: {', '.join(keys)}")
        return dict(entries[keys[0]]) if len(keys) == 1 else {}

    # -- inheritance

    def bases(self, el: Element) -> list[Element]:
        """What el inherits features from: its types and supersets, and for a variant its variation."""
        if el in self._bases:
            return self._bases[el]
        out: list[Element] = []
        owner = el.owner
        if "variant" in el.prefixes and owner is not None and "variation" in owner.prefixes:
            out.append(owner)
        for ref in [*el.types, *el.supers]:
            target = self.model.resolve(ref, owner, exclude=el)
            if target is not None and target not in out:
                out.append(target)
        if "variant" in el.prefixes and el.kind == "feature" and el.name and owner is not None:
            target = self.model.resolve(Ref((el.name,)), owner.owner, exclude=el)
            if target is not None and target not in out:
                out.append(target)
        self._bases[el] = out
        return out

    def ancestors(self, el: Element) -> set[Element]:
        if el not in self._ancestors:
            seen: set[Element] = set()
            todo = list(self.bases(el))
            while todo:
                b = todo.pop()
                if b not in seen and b is not el:
                    seen.add(b)
                    todo.extend(self.bases(b))
            self._ancestors[el] = seen
        return self._ancestors[el]

    def features(self, el: Element) -> dict[str, _Feature]:
        if el not in self._features:
            self._features[el] = self._collect(el, ())
        return self._features[el]

    def _collect(self, el: Element, stack: tuple[Element, ...]) -> dict[str, _Feature]:
        if el in stack:  # a specialization cycle (part p :> p) is cut
            return {}
        if len(stack) >= MAX_FEATURE_DEPTH:
            self.gap(el, "attribute-value", f"inheritance and nesting deeper than {MAX_FEATURE_DEPTH} levels; its "
                     "attributes were not read")
            return {}
        stack = (*stack, el)
        out: dict[str, _Feature] = {}
        for base in self.bases(el):
            _merge(out, self._collect(base, stack))
        for child in el.members:
            key = child.feature_name
            if (key is None or child.kind not in ("attribute", "feature", "part")
                    or child.prefixes & {"variant", "variation", "in", "out", "inout", "return", "require", "assume",
                                         "end", "assert"}):
                continue
            if child.kind == "part":
                _merge(out, {f"{key}.{k}": _Feature(f"{key}.{k}", f.decls, f.value_el)
                             for k, f in self._collect(child, stack).items()})
            else:
                _merge(out, {key: _Feature(key, [child], child if child.value is not None else None)})
        return out

    # -- alternatives

    def variants(self, variation: Element) -> list[Element]:
        return [v for v in variation.members if "variant" in v.prefixes and v.kind in ("part", "feature")]

    def usages_of(self, target: Element) -> list[Element]:
        return [e for e in self.model.elements
                if e.kind == "part" and not e.is_def and e.is_package_level and e is not target
                and not e.prefixes & {"abstract", "variation", "variant"} and target in self.ancestors(e)]

    def select_alternatives(self) -> list[tuple[Element, str, str]]:
        """(element, rule, note) for each alternative, or [] with a gap when there is no unique choice."""
        source = self.mapping.alternatives_from
        if source:
            target = self.model.resolve(Ref(_key_parts(source)), None)
            if target is None:
                self.gap("mapping:alternatives.from", "mapping", f"{source} does not name exactly one element")
                return []
            self.handled.add(target)
            return self.alternatives_from(target)
        if self.mapping.alternatives:
            usages = [e for e in self.model.elements if not e.is_def and (e.kind == "part" or "variant" in e.prefixes)]
            listed = []
            for key in self.mapping.alternatives:
                hits = [e for e in usages if _matches(key, e)]
                if len(hits) > 1:
                    self.matched_keys.add(("alternatives.ids", key))
                    self.gap(f"mapping:alternatives.ids.{key}", "mapping", f"{key!r} names several part usages: "
                             f"{', '.join(e.label for e in hits)}; use the qualified name")
                if len(hits) == 1 and hits[0] not in listed:
                    listed.append(hits[0])
            listed.sort(key=self.model.elements.index)
            return [(e, "alternative.listed", "listed in the mapping file") for e in listed]
        variations = [e for e in self.model.elements if "variation" in e.prefixes and e.kind == "part"
                      and self.variants(e)]
        groups = {e: u for e in self.model.elements if e.kind == "part" and e.is_def and "variation" not in e.prefixes
                  and len(u := self.usages_of(e)) >= 2}
        # When one candidate definition specializes another, only the more specific one counts.
        definitions = [d for d in groups if not any(d in self.ancestors(o) for o in groups if o is not d)]
        candidates = [f"variation {v.label} ({len(self.variants(v))} variants)" for v in variations]
        candidates += [f"part def {d.label} ({len(groups[d])} usages)" for d in definitions]
        if len(candidates) == 1:
            (chosen,) = variations or definitions
            self.handled.add(chosen)
            return self.alternatives_from(chosen)
        where = self.sources[0].display if len(self.sources) == 1 else "model"
        if candidates:
            self.gap(where, "alternatives", "several candidate sets of alternatives: " + "; ".join(candidates)
                     + ". Name one with `alternatives: {from: <element>}` in the mapping file.")
        else:
            self.gap(where, "alternatives", "no part variation with variants and no part definition with two or "
                     "more part usages; list the alternatives in the mapping file (alternatives.ids)")
        return []

    def alternatives_from(self, target: Element) -> list[tuple[Element, str, str]]:
        if "variation" in target.prefixes:
            return [(v, "alternative.variant", f"variant of {target.label}") for v in self.variants(target)]
        if target.kind == "part" and target.is_def:
            return [(u, "alternative.same-definition", f"usage of {target.label}") for u in self.usages_of(target)]
        if target.kind == "part":
            return [(u, "alternative.specialization", f"specializes {target.label}") for u in self.usages_of(target)]
        self.gap(target, "mapping", "alternatives.from must name a part variation, a part definition or a part usage")
        return []

    # -- subjects and operands

    def subject(self, el: Element) -> _Subject | None:
        """The subject el declares or inherits, or else the subject of the requirement group it sits in."""
        found: list[Element] = []
        seen: set[Element] = set()
        todo = [el]
        while todo:
            e = todo.pop(0)
            if e not in seen:
                seen.add(e)
                found.extend(mbr for mbr in e.members if mbr.kind == "subject")
                todo.extend(self.bases(e))
        if not found:
            owner = el.owner
            return self.subject(owner) if owner is not None and owner.kind == "requirement" else None
        declared = next(([*s.types, *s.supers] for s in found if s.types or s.supers), [])
        owner = next(s.owner for s in found if s.types or s.supers) if declared else None
        classifiers = [c for r in declared if (c := self.model.resolve(r, owner, None))]
        self.handled.update(classifiers)
        return _Subject(next((s.feature_name for s in found if s.feature_name), None),
                        next((s for s in found if s.value is not None), None), classifiers, declared)

    def operand(self, node: Literal | Ref, scope: Element, depth: int = 0) -> _Lit | _Path | str:
        """Resolve one side of a comparison: the subject, an attribute with a value, or a part, in that order."""
        if isinstance(node, Literal):
            return _Lit(node)
        if depth > MAX_DEPTH:
            return f"`{node}`: reference chain too deep"
        head = node.parts[0] if len(node.parts) == 1 else None
        subject = self.subject(scope)
        if subject is not None and head is not None and head == subject.name:
            if subject.value_el is None:
                return _Path(subject, None, node.chain)
            bound = parse_operand(subject.value_el.value)
            target = self.model.resolve(Ref(bound.parts), scope) if isinstance(bound, Ref) else None
            if target is None or target.is_def or target.kind not in ("part", "feature"):
                return f"the subject is bound to `{_text(subject.value_el.value)}`, which is not a part in the model"
            return _Path(None, target, bound.chain + node.chain, (f"subject bound to {bound}",))
        for owner in self.scopes(scope):
            feature = self.features(owner).get(head) if head is not None else None
            if feature is None:
                continue
            if feature.value_el is None:
                return f"`{head}` has no value"
            inner = parse_operand(feature.value_el.value)
            if inner is None:
                return f"`{head} = {_text(feature.value_el.value)}` is an expression"
            got = self.operand(inner, scope, depth + 1)
            if isinstance(got, str):
                return got
            if isinstance(got, _Lit) and node.chain:
                return f"`{node}` reads into a literal"
            if isinstance(got, _Lit):
                return _Lit(got.literal, (f"threshold from {head}",))
            return _Path(got.subject, got.element, got.chain + node.chain, (*got.notes, f"measure via {head}"))
        target = self.model.resolve(Ref(node.parts), scope)
        if target is not None and target.kind in ("part", "feature") and not target.is_def:
            return _Path(None, target, node.chain)
        return f"`{node}` does not resolve to the subject, an attribute with a value, or a part"

    def scopes(self, el: Element) -> Iterator[Element]:
        """el, then the requirements it is nested in (whose attributes its expressions can name)."""
        while el is not None and el.kind in ("requirement", "analysis", "verification"):
            yield el
            el = el.owner

    def applies(self, path: _Path, alts: dict[Element, str]) -> tuple[list[str] | None, list[str], str | None]:
        """(applies_to, notes, error) for a measure path; applies_to [] means every alternative."""
        notes: list[str] = []
        if path.subject is not None:
            classifiers = path.subject.classifiers
            missing = ", ".join(str(r) for r in path.subject.declared)
            if not classifiers and missing and alts:
                return None, [], f"the subject's type {missing} is not defined in the imported files"
            if not classifiers:
                notes.append(f"the subject's type {missing} is not defined in the imported files" if missing else
                             "the subject is untyped")
                notes[-1] += ", so it was applied to every alternative"
        else:
            classifiers = [path.element]
        key = ".".join(path.chain)
        if not key:
            return None, [], "it compares the subject itself, not an attribute of it"
        for c in classifiers:
            declared = self.features(c)
            if declared and key not in declared:
                return None, [], f"`{key}` is not a feature of {c.label}"
        if path.subject is not None and (not classifiers or not alts):
            return [], notes, None
        matching = [a for a in alts if a in classifiers or self.ancestors(a) & set(classifiers)]
        if not matching:
            names = ", ".join(c.label for c in classifiers)
            return None, [], f"it is about {names}, which is not an alternative and no alternative specializes it"
        return ([] if len(matching) == len(alts) else [alts[a] for a in matching]), notes, None

    # -- requirements

    def requirement_usages(self) -> Iterator[Element]:
        def walk(elements: list[Element]) -> Iterator[Element]:
            for el in elements:
                if el.kind == "requirement" and not el.is_def:
                    yield el
                    yield from walk(el.members)
                elif el.kind in ("package", "namespace", "part"):
                    yield from walk(el.members)
        yield from walk(self.model.roots)

    def constraints(self, el: Element, which: str, stack: tuple = ()) -> list[Element]:
        """el's require (or assume) constraints, own and inherited; an own one hides the one it redefines."""
        if el in stack:
            return []
        out = [c for c in el.members if which in c.prefixes and c.kind == "constraint"]
        hidden = {c.feature_name for c in out if c.feature_name}
        for base in self.bases(el):
            out.extend(c for c in self.constraints(base, which, (*stack, el))
                       if c not in out and (c.feature_name is None or c.feature_name not in hidden))
        return out

    def requirements(self, alts: dict[Element, str]) -> list[_Draft]:
        drafts: list[_Draft] = []
        for req in self.requirement_usages():
            self.handled.add(req)
            self.handled.update(self.ancestors(req))
            if req.prefixes & {"variant", "variation"}:
                self.gap(req, "requirement", "requirement variations are not supported; choose the requirement first")
                continue
            for ref in (r for r in req.members if "require" in r.prefixes and r.kind == "feature"):
                self.handled.add(ref)
                target = self.model.resolve(Ref((ref.name,)), req) if ref.name else None
                if target is not None and target.kind == "constraint":
                    self.gap(ref, "constraint", "a constraint required by reference is not supported; state the "
                             "comparison inline: require constraint { subject.attr <op> value }")
            required = self.constraints(req, "require")
            if not required:
                if not any(c.kind == "requirement" or "require" in c.prefixes for c in req.members):
                    text = f': "{req.doc[:160]}"' if req.doc else ""
                    self.gap(req, "requirement", "no require constraint, so nothing to check; a person states its "
                             f"measure, comparator and threshold{text}")
                self.assumptions_of(req, imported=False)
                continue
            terms = []
            for c in required:
                self.handled.add(c)
                expr = c.expression()
                if expr is None:
                    self.gap(self.at(req, c), "constraint", "only inline constraint expressions are read (not a "
                             "constraint given by reference to a definition or usage)" + self.inherited(req, c))
                    continue
                comparisons, error = parse_comparisons(expr.tokens)
                if error:
                    self.gap(self.at(req, c), "constraint", f"cannot read `{_text(expr.tokens)}`: {error}"
                             + self.inherited(req, c))
                    continue
                terms.extend((c, expr, comparison) for comparison in comparisons)
            found = 0
            for n, (c, expr, (lhs, op, rhs)) in enumerate(terms, 1):
                draft = self.requirement_term(req, c, expr, lhs, op, rhs, alts)
                if draft is not None:
                    draft.data["term"] = (n, len(terms))
                    drafts.append(draft)
                    found += 1
            self.assumptions_of(req, imported=found > 0)
        return drafts

    @staticmethod
    def at(req: Element, c: Element) -> Element:
        """Where a constraint's gap is reported: the constraint, or the usage that inherited it."""
        return c if c.owner is req else req

    @staticmethod
    def inherited(req: Element, c: Element) -> str:
        return "" if c.owner is req else f" (constraint inherited from {c.owner.label})"

    def requirement_term(self, req: Element, c: Element, expr: Element, lhs, op: str, rhs,
                         alts: dict[Element, str]) -> _Draft | None:
        text, where, origin = _text(expr.tokens), self.at(req, c), self.inherited(req, c)
        if op not in ("<=", ">=", "<", ">"):
            self.gap(where, "constraint", f"`{text}`: {op} is not a requirement comparator (use <=, >=, < or >)"
                     + origin)
            return None
        left, right = self.operand(lhs, req), self.operand(rhs, req)
        for side in (left, right):
            if isinstance(side, str):
                self.gap(where, "constraint", f"`{text}`: {side}{origin}")
                return None
        if isinstance(left, _Lit) == isinstance(right, _Lit):
            self.gap(where, "constraint", f"`{text}`: compare one attribute with one literal threshold{origin}")
            return None
        flipped = isinstance(left, _Lit)
        path, lit = (right, left) if flipped else (left, right)
        applies_to, notes, error = self.applies(path, alts)
        if error:
            self.gap(where, "requirement-subject", f"`{text}`: {error}{origin}")
            return None
        if notes:
            self.gap(req, "requirement-subject", "; ".join(notes) + " (confirm with a person, or type the subject)")
        comparator = {"<=": "le", ">=": "ge", "<": "lt", ">": "gt"}[op]
        if flipped:
            comparator = {"le": "ge", "ge": "le", "lt": "gt", "gt": "lt"}[comparator]
            notes = [*notes, "comparator reversed (threshold on the left)"]
        return _Draft(req, ".".join(path.chain), {"comparator": comparator, "literal": lit.literal,
                                                   "applies_to": applies_to, "text": text,
                                                   "notes": [*path.notes, *lit.notes, *notes]})

    def assumptions_of(self, req: Element, imported: bool) -> None:
        """Assumptions from a requirement's `assume constraint`s, when the requirement itself was imported."""
        for c in self.constraints(req, "assume"):
            self.handled.add(c)
            if not imported:
                self.gap(c, "assume-constraint", "it qualifies a requirement that was not imported")
                continue
            expr = c.expression()
            comparisons, error = parse_comparisons(expr.tokens) if expr is not None else ([], "no inline expression")
            if error:
                self.gap(c, "assume-constraint", f"cannot read the assumption: {error}")
                continue
            for lhs, op, rhs in comparisons:
                sides = (self.operand(lhs, req), self.operand(rhs, req))
                path = next((s for s in sides if isinstance(s, _Path) and s.subject is not None), None)
                lit = next((s for s in sides if isinstance(s, _Lit)), None)
                text = f"{lhs} {op} {rhs}"
                if op != "==" or path is None or lit is None or not path.chain:
                    self.gap(c, "assume-constraint", f"`{text}`: pdo checks requirements unconditionally and records "
                             "only `subject.attr == literal` as an Assumption; a person decides how to treat this one")
                    continue
                key = ".".join(path.chain)
                default_id = f"asm:{_slug(req.feature_name or 'requirement')}.{key}"
                self.assumption_drafts.append(_Draft(c, key, {
                    "label": f"{req.label}::{key}", "default_id": default_id,
                    "literal": lit.literal, "scalar": False, "rule": "assumption.assume-constraint",
                    "about": f"assumed by {req.label}: {text}"}))

    # -- evaluations

    def evaluation_defs(self) -> Iterator[Element]:
        def walk(elements: list[Element]) -> Iterator[Element]:
            for el in elements:
                if el.kind in ("analysis", "verification") and el.is_def and "variation" not in el.prefixes:
                    yield el
                elif el.kind in ("package", "namespace", "part"):
                    yield from walk(el.members)
        yield from walk(self.model.roots)

    def evaluations(self, alts: dict[Element, str]) -> list[_Draft]:
        drafts = []
        for ev in self.evaluation_defs():
            self.handled.update(_all([ev]))  # the body is not translated; the implementation gap says so
            subject = self.subject(ev)
            inputs, outputs, assumptions = [], [], []
            for p in ev.members:
                direction = p.prefixes & {"in", "out", "inout", "return"}
                if not direction or p.kind == "subject":
                    continue
                arg, is_input = p.feature_name or "result", bool(direction & {"in", "inout"})
                kind = "evaluation-input" if is_input else "evaluation-output"
                if p.kind not in ("feature", "attribute"):
                    self.gap(p, kind, f"a {p.kind} parameter is not supported; only attribute parameters")
                    continue
                node = parse_operand(p.value) if p.value else None
                bound = self.operand(node, ev) if isinstance(node, Ref) else None
                if isinstance(bound, _Path) and bound.subject is not None:
                    _, _, error = self.applies(bound, alts)
                    if error:
                        self.gap(p, kind, f"`{arg}`: {error}")
                        continue
                    (inputs if is_input else outputs).append((arg, ".".join(bound.chain), p))
                elif is_input and isinstance(node, Literal):
                    assumptions.append(_Draft(p, "", {
                        "label": p.label, "default_id": f"asm:{_slug(ev.feature_name or 'evaluation')}.{_slug(arg)}",
                        "literal": node, "scalar": any(r.parts[-1] in SCALAR_TYPES for r in p.types),
                        "rule": "assumption.analysis-parameter", "arg": arg, "about": f"input {arg} of {ev.label}"}))
                    inputs.append((arg, None, p))
                else:
                    why = f" ({bound})" if isinstance(bound, str) else ""
                    self.gap(p, kind, f"`{arg}` is bound to neither a subject attribute nor a literal value{why}; "
                             + ("bind it (`in x = subject.attr;`) or give it a value" if is_input else
                                "bind it (`return x = subject.attr;`) so it produces a measure"))
            applies_to: list[str] = []
            if subject is not None and subject.classifiers and alts:
                matching = [a for a in alts if a in subject.classifiers or self.ancestors(a) & set(subject.classifiers)]
                if not matching:
                    self.gap(ev, "evaluation", f"its subject ({', '.join(c.label for c in subject.classifiers)}) is "
                             "not an alternative and no alternative specializes it")
                    continue
                applies_to = [] if len(matching) == len(alts) else [alts[a] for a in matching]
            if not outputs:
                self.gap(ev, "evaluation", "a verification case yields a verdict, not a measure: record the test's "
                         "measured values as Evidence and Observations of the verified requirements' measures"
                         if ev.kind == "verification" else
                         "no return or out parameter is bound to a subject attribute, so there is nothing to compute")
                continue
            self.assumption_drafts.extend(assumptions)
            drafts.append(_Draft(ev, "", {"inputs": inputs, "outputs": outputs, "applies_to": applies_to,
                                          "rule": f"evaluation.{ev.kind}-def"}))
        return drafts


    # -- assembly

    def unique_ids(self, items: list[tuple[Element, str | None]], prefix: str) -> list[str]:
        """Mapped ids where given; otherwise prefix:name, or prefix:<qualified path> where names collide."""
        def default(el: Element, qualified: bool) -> str:
            parts = [p for p in el.qparts if p is not None]
            name = ".".join(parts[1:] or parts) if qualified else (el.feature_name or el.short_name or "x")
            return f"{prefix}:{_slug(name)}"

        first = [mapped or default(el, False) for el, mapped in items]
        counts: dict[str, int] = defaultdict(int)
        for oid in first:
            counts[oid] += 1
        out: list[str] = []
        for (el, mapped), oid in zip(items, first, strict=True):
            if counts[oid] > 1 and not mapped:
                oid = default(el, True)
            base, n = oid, 2
            while oid in out:
                oid, n = f"{base}-{n}", n + 1
            out.append(oid)
        return out

    def run(self) -> ImportResult:
        self.assumption_drafts: list[_Draft] = []
        selected = self.select_alternatives()
        entries = [self.lookup("alternatives.ids", el, self.mapping.alternatives) for el, _, _ in selected]
        ids = self.unique_ids([(el, e.get("id")) for (el, _, _), e in zip(selected, entries, strict=True)], "alt")
        alts: dict[Element, str] = {}
        alternatives = []
        for (el, rule, note), entry, oid in zip(selected, entries, ids, strict=True):
            alts[el] = oid
            self.rules.add(rule)
            self.handled.add(el)
            self.handled.update(self.ancestors(el))
            obj = {"id": oid, "name": entry.get("name", el.feature_name),
                   "description": entry.get("description", el.doc), "is_incumbent": entry.get("is_incumbent")}
            alternatives.append({**_prune(obj), **self.source_ref(el, f"{rule} ({note})")})

        requirement_drafts = self.requirements(alts)
        evaluation_drafts = self.evaluations(alts)

        observed: list[tuple[Element, _Feature, Literal]] = []
        declared: dict[str, _Feature] = {}  # the first declaration of each attribute key, for names and units
        for el in alts:
            for key, feature in self.features(el).items():
                declared.setdefault(key, feature)
                if feature.value_el is None:
                    continue
                literal = parse_literal(feature.value_el.value)
                if literal is None:
                    self.gap(feature.value_el, "attribute-value", f"`{_text(feature.value_el.value)}` is not a "
                             "literal number (the importer does not evaluate expressions or follow references), so "
                             "there is no observation")
                    continue
                observed.append((el, feature, literal))
        # Measures in the order the alternatives declare them, then keys only requirements or evaluations use.
        used = [f.key for _, f, _ in observed] + [d.key for d in requirement_drafts]
        used += [k for d in evaluation_drafts for _, k, _ in [*d.data["inputs"], *d.data["outputs"]] if k]
        keys = [k for k in declared if k in used] + [k for k in dict.fromkeys(used) if k not in declared]
        measures = self.measures(keys, declared, observed, requirement_drafts)

        observations, evidence = self.observations(observed, alts, measures)
        requirements = self.requirement_objects(requirement_drafts, measures)
        assumptions = self.assumption_objects()
        specs = self.evaluation_objects(evaluation_drafts, measures, assumptions)
        self.producer_conflicts(specs, observations, alts)
        self.unhandled()
        self.unmatched_mapping_keys()

        state: dict[str, Any] = {}
        for slot, objects in (("alternatives", alternatives), ("measures", [x["obj"] for x in measures.values()]),
                              ("requirements", requirements), ("evidence_items", evidence),
                              ("observations", observations), ("assumptions", list(assumptions.values())),
                              ("evaluation_specifications", specs)):
            if objects:
                state[slot] = objects
        try:
            m.DecisionState.model_validate(state)
        except ValidationError as exc:
            problems = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
            raise SysMLError("the imported state does not validate (check the mapping's slot values):\n"
                             + "\n".join(problems)) from exc
        gaps = sorted(self.gaps.values(), key=lambda g: (g.source_model or "", g.line or 0, g.element, g.kind))
        return ImportResult(state, gaps, sorted(self.rules),
                            [{"path": s.display, "sha256": s.sha256} for s in self.sources])

    def source_ref(self, el: Element, rule: str) -> dict:
        return {"source_element": el.label, "source_model": el.src.display,
                "source_model_version": f"sha256:{el.src.sha256}", "mapping_rule": rule}

    def measures(self, keys: list[str], declared: dict[str, _Feature], observed,
                 requirement_drafts: list[_Draft]) -> dict[str, dict]:
        """key -> {obj, unit, unit_source, observation}. The unit comes from the mapping, else from the
        model's literals (which must agree), else "1" for scalar-typed attributes, else "1" with a gap."""
        entries = self.mapping.sections["measures"]
        explicit: dict[str, list[str]] = defaultdict(list)
        for _, feature, literal in observed:
            if literal.unit:
                explicit[feature.key].append(self.unit(literal.unit))
        for d in requirement_drafts:
            if d.data["literal"].unit:
                explicit[d.key].append(self.unit(d.data["literal"].unit))
        out: dict[str, dict] = {}
        ids_seen: dict[str, str] = {}
        for key in keys:
            entry = entries.get(key, {})
            if entry:
                self.matched_keys.add(("measures", key))
            feature = declared.get(key)
            where = feature.decls[0] if feature else f"measure:{key}"
            oid = entry.get("id", f"meas:{_slug(key)}")
            if oid in ids_seen:
                self.gap(f"mapping:measures.{key}", "mapping", f"{key} and {ids_seen[oid]} both map to {oid}")
                continue
            ids_seen[oid] = key
            units = list(dict.fromkeys(explicit.get(key, [])))
            if "unit" in entry:
                unit, source = entry["unit"], "mapping"
            elif len(units) == 1:
                unit, source = units[0], "model"
            elif units:
                self.gap(where, "unit", f"values of {key} use several units ({', '.join(units)}); units are matched "
                         "exactly and never converted, so the measure was not created")
                continue
            elif feature is not None and feature.scalar:
                unit, source = "1", "type"
            else:
                unit, source = "1", "assumed"
                self.gap(where, "unit", f'no unit for {key} in the model, so it was recorded as dimensionless "1"; '
                         f"set measures.{key}.unit in the mapping if it has one")
            name = entry.get("name") or (feature.doc if feature and feature.doc else key)
            obj = {"id": oid, "name": name, "description": entry.get("description"), "unit": unit}
            out[key] = {"obj": _prune(obj), "unit": unit, "unit_source": source,
                        "observation": entry.get("observation", key)}
            self.rules.add("measure.attribute")
        return out

    def unit(self, text: str | None) -> str | None:
        return self.mapping.units.get(text, text) if text is not None else None

    def check_unit(self, literal: Literal, measure: dict, el: Element | str, what: str) -> str | None:
        """The unit a literal gets on this measure, or None (with a gap) when it does not fit."""
        unit = self.unit(literal.unit)
        if unit is None:
            if measure["unit_source"] != "model" or measure["unit"] == "1":
                return measure["unit"]
            self.gap(el, "unit", f"{what} ({literal}) has no unit, but {measure['obj']['id']} is in {measure['unit']}")
            return None
        if unit != measure["unit"]:
            self.gap(el, "unit", f"{what} is in {unit} but {measure['obj']['id']} is in {measure['unit']}; units are "
                     "matched exactly and never converted")
            return None
        return unit

    def observations(self, observed, alts: dict[Element, str], measures: dict[str, dict]) -> tuple[list, list]:
        observations: list[dict] = []
        files: dict[Source, list[dict]] = {}
        for el, feature, literal in observed:
            measure = measures.get(feature.key)
            if measure is None:
                continue
            value_el = feature.value_el
            unit = self.check_unit(literal, measure, value_el, f"the value of {feature.key} on {alts[el]}")
            if unit is None:
                continue
            oid = f"obs:{_local(alts[el])}.{measure['observation']}"
            if any(o["id"] == oid for o in observations):
                self.gap(value_el, "mapping", f"observation id {oid} is used twice; give the measures distinct "
                         "`observation` names in the mapping")
                continue
            inherited = "" if value_el.owner is el else f"; inherited from {value_el.owner.label}"
            obs = {"id": oid, "name": f"{el.label}.{feature.key}", "alternative": alts[el],
                   "measure": measure["obj"]["id"], "value": literal.value, "unit": unit, "evidence": None,
                   "comments": [f"observation.attribute-value: {value_el.label} = {_text(value_el.value)} "
                                f"({value_el.src.display}:{value_el.line}){inherited}"]}
            observations.append(obs)
            files.setdefault(value_el.src, []).append(obs)
        evidence: list[dict] = []
        entries = self.mapping.sections["evidence"]
        for src, backed in files.items():
            keys = [k for k in entries if k in (src.display, src.path.name)]
            self.matched_keys.update(("evidence", k) for k in keys)
            entry = dict(entries[keys[0]]) if keys else {}
            oid = base = entry.pop("id", None) or f"ev:sysml-{_slug(src.path.stem)}"
            n = 2
            while any(e["id"] == oid for e in evidence):
                oid, n = f"{base}-{n}", n + 1
            evidence.append({"id": oid, "name": entry.pop("name", f"SysML model {src.display}"),
                             "evidence_kind": entry.pop("evidence_kind", "system model"), **entry,
                             "source_ref": f"{src.display}@sha256:{src.sha256}", "content_sha256": src.sha256,
                             "source_element": src.display, "source_model": src.display,
                             "source_model_version": f"sha256:{src.sha256}", "mapping_rule": "evidence.model-file"})
            for obs in backed:
                obs["evidence"] = oid
        if evidence:
            self.rules.update({"evidence.model-file", "observation.attribute-value"})
        return observations, evidence

    def requirement_objects(self, drafts: list[_Draft], measures: dict[str, dict]) -> list[dict]:
        reqs = list(dict.fromkeys(d.element for d in drafts))
        entry_of = {r: self.lookup("requirements", r) for r in reqs}
        base_ids = dict(zip(reqs, self.unique_ids([(r, entry_of[r].get("id")) for r in reqs], "req"), strict=True))
        out = []
        for d in drafts:
            entry, (n, total) = entry_of[d.element], d.data["term"]
            oid = base_ids[d.element] + (f".{n}" if total > 1 else "")
            measure = measures.get(d.key)
            if measure is None:
                self.gap(d.element, "requirement", f"{oid}: its measure {d.key} was not created (see its unit gap)")
                continue
            unit = self.check_unit(d.data["literal"], measure, d.element, f"the threshold of {oid}")
            if unit is None:
                continue
            notes = [f"`{d.data['text']}`", *d.data["notes"], *([f"term {n} of {total}"] if total > 1 else [])]
            obj = {"id": oid, "name": entry.get("name", d.element.feature_name or d.element.short_name),
                   "description": entry.get("description", d.element.doc), "measure": measure["obj"]["id"],
                   "comparator": d.data["comparator"], "threshold_value": d.data["literal"].value, "unit": unit,
                   "applies_to": d.data["applies_to"],
                   **{k: entry[k] for k in ("owner_role", "mandatory", "check_tolerance") if k in entry}}
            rule = "requirement.require-constraint: " + "; ".join(notes)
            out.append({**_prune(obj), **self.source_ref(d.element, rule)})
            self.rules.add("requirement.require-constraint")
        return out

    def assumption_objects(self) -> dict[Element, dict]:
        """Element -> Assumption. Imported assumptions are unvalidated unless the mapping (a person) says otherwise."""
        entries = self.mapping.sections["assumptions"]
        out: dict[Element, dict] = {}
        for d in self.assumption_drafts:
            label = _key_parts(d.data["label"])
            keys = [k for k in entries if label[-len(_key_parts(k)):] == _key_parts(k)]
            self.matched_keys.update(("assumptions", k) for k in keys)
            entry = dict(entries[keys[0]]) if len(keys) == 1 else {}
            oid = entry.pop("id", d.data["default_id"])
            if any(a["id"] == oid for a in out.values()):
                continue
            literal: Literal = d.data["literal"]
            unit = self.unit(literal.unit)
            if "unit" in entry and unit is not None and entry["unit"] != unit:
                self.gap(d.element, "unit", f"{d.data['about']}: the model says {unit}, the mapping {entry['unit']}")
                continue
            unit = entry.pop("unit", None) or unit
            if unit is None:
                unit = "1"
                if not d.data["scalar"]:
                    self.gap(d.element, "unit", f'{d.data["about"]} has no unit, so it was recorded as dimensionless '
                             '"1"; set its unit in the mapping (assumptions) if it has one')
            self.rules.add(d.data["rule"])
            obj = {"id": oid, "name": entry.pop("name", d.data.get("arg") or d.key),
                   "description": entry.pop("description", d.element.doc or d.data["about"]), "value": literal.value,
                   "unit": unit, "validation_status": entry.pop("validation_status", "unvalidated"), **entry,
                   "comments": [f"{d.data['rule']}: {d.data['about']} ({d.element.src.display}:{d.element.line})"]}
            out[d.element] = _prune(obj)
        return out

    def evaluation_objects(self, drafts: list[_Draft], measures: dict[str, dict],
                           assumptions: dict[Element, dict]) -> list[dict]:
        entries = [self.lookup("evaluations", d.element) for d in drafts]
        ids = self.unique_ids([(d.element, e.get("id")) for d, e in zip(drafts, entries, strict=True)], "spec")
        out = []
        for d, entry, oid in zip(drafts, entries, ids, strict=True):
            inputs, outputs = [], []
            for arg, key, p in d.data["inputs"]:
                if key is None and p in assumptions:
                    inputs.append({"arg": arg, "ref": assumptions[p]["id"], "unit": assumptions[p]["unit"]})
                elif key in measures:
                    inputs.append({"arg": arg, "measure": measures[key]["obj"]["id"], "unit": measures[key]["unit"]})
                else:
                    self.gap(p, "evaluation-input", f"`{arg}` was dropped: its measure or assumption was not created "
                             "(see its unit gap)")
            for arg, key, p in d.data["outputs"]:
                if key in measures:
                    outputs.append({"arg": arg, "measure": measures[key]["obj"]["id"], "unit": measures[key]["unit"]})
                else:
                    self.gap(p, "evaluation-output", f"`{arg}` was dropped: its measure was not created")
            if not outputs:
                continue
            wiring = ("implementation", "implementation_version", "owner_role", "cost_units", "deterministic",
                      "cacheable", "environment", "parameters")
            obj = {"id": oid, "name": entry.get("name", d.element.feature_name),
                   "description": entry.get("description", d.element.doc), "spec_kind": "python",
                   **{k: entry[k] for k in wiring if k in entry},
                   "inputs": inputs, "outputs": outputs, "applies_to": d.data["applies_to"]}
            if "implementation" not in entry:
                self.gap(d.element, "implementation", f"{oid} is a placeholder: SysML does not carry the computation. "
                         "Set implementation ('module.py:function', relative to the decision directory) and "
                         f"implementation_version under evaluations: {d.element.feature_name} in the mapping file, "
                         "then import again. The body (actions, calculations, objective) is not translated.")
            out.append(_prune(obj))
            self.rules.add(d.data["rule"])
        return out

    def producer_conflicts(self, specs: list[dict], observations: list[dict], alts: dict[Element, str]) -> None:
        observed = {(o["alternative"], o["measure"]): o["id"] for o in observations}
        for spec in specs:
            for out in spec["outputs"]:
                for alt in spec.get("applies_to") or alts.values():
                    if (alt, out["measure"]) in observed:
                        self.gap(f"{spec['id']}/{alt}", "producer-conflict",
                                 f"{out['measure']} for {alt} is both observed ({observed[(alt, out['measure'])]}) and "
                                 f"computed by {spec['id']}; a person decides which one stands")

    def unhandled(self) -> None:
        """A gap for every element no rule reached, at the highest level where it was dropped."""
        structural = {"attribute", "feature", "part", "subject", "expression", "requirement"}

        def has_handled(el: Element) -> bool:
            return any(c in self.handled or has_handled(c) for c in el.members)

        def walk(el: Element, inside: bool) -> None:
            if el.kind in NAMESPACE_KINDS:
                for c in el.members:
                    walk(c, inside)
            elif el in self.handled or (inside and el.kind in structural and not el.prefixes & {"variation", "assert"}):
                for c in el.members:
                    walk(c, True)
            elif el.kind in ("part", "requirement") and has_handled(el):
                for c in el.members:
                    walk(c, inside)
            else:
                self.gap(el, el.keyword, _reason(el, inside))

        for root in self.model.roots:
            walk(root, False)

    def unmatched_mapping_keys(self) -> None:
        for section, entries in [("alternatives.ids", self.mapping.alternatives), *self.mapping.sections.items()]:
            for key in entries:
                if (section, key) not in self.matched_keys:
                    self.gap(f"mapping:{section}.{key}", "mapping",
                             f"{section} key {key!r} matches no imported element")


def _merge(out: dict[str, _Feature], new: dict[str, _Feature]) -> None:
    """Later (more specific) declarations override values; declarations accumulate."""
    for key, feature in new.items():
        old = out.get(key)
        out[key] = feature if old is None else _Feature(key, [*old.decls, *feature.decls],
                                                        feature.value_el or old.value_el)


def _prune(obj: dict) -> dict:
    return {k: v for k, v in obj.items() if v is not None and v != []}


STRUCTURE = {"port", "interface", "connection", "flow", "item", "binding", "bind", "connect", "succession", "message",
             "allocation", "succession flow", "end"}
BEHAVIOR = {"action", "state", "transition", "perform", "exhibit", "accept", "send", "do", "entry", "exit", "first",
            "then", "use case", "case", "event", "if", "else", "while", "for", "loop", "merge", "decide", "join",
            "fork", "assign"}
RELATIONSHIP = {"satisfy", "verify", "allocate", "dependency", "frame", "include", "expose", "render", "concern",
                "viewpoint", "view", "rendering"}


def _reason(el: Element, inside: bool) -> str:
    kind = el.kind
    if kind in STRUCTURE:
        return ("structure (ports, interfaces, connections, flows, items) has no counterpart in a decision state; if "
                "it matters to the choice, state it as a requirement or a measured attribute")
    if kind in BEHAVIOR:
        return ("behavior is not part of a decision state; if it determines a measure, model that measure with an "
                "analysis def whose return is bound to a subject attribute")
    if kind == "occurrence":
        return ("individuals, snapshots and other occurrences are specific instances, not definitions or "
                "configurations; if one is a configuration under consideration, model it as a part usage")
    if kind == "calc":
        return ("calculations are not executed; implement it as an evaluator and declare it as an analysis def with "
                "in/return parameters bound to subject attributes")
    if kind in RELATIONSHIP:
        return "a relationship between model elements; requirements reach alternatives through applies_to instead"
    if kind == "constraint" or "assert" in el.prefixes:
        return ("a constraint outside a requirement's require/assume is not checked; if it is mandatory, state it as "
                "a requirement")
    if kind == "metadata":
        return "metadata is not imported; owners, status and rationale come from the people framing the decision"
    if kind in ("analysis", "verification"):
        return ("an analysis or verification usage: its results are evidence; attach a result to an alternative as an "
                "observation with applicability: provisional so a person accepts the association")
    if "variation" in el.prefixes and inside:
        return "a variation point inside an alternative is a further choice; decide it separately or bind a variant"
    if kind == "requirement" and el.is_def:
        return "a requirement definition with no usage: nothing checks it until a requirement uses it"
    if kind in ("part", "attribute", "enum", "feature", "subject", "objective", "requirement"):
        return "not reached by any mapping rule (not an alternative, the definition of one, or a requirement subject)"
    return "not in the supported SysML subset"


# --- entry points ---------------------------------------------------------------------------------------------

def _expand(paths: Iterable[str | Path]) -> list[Path]:
    files: dict[Path, Path] = {}
    for p in paths:
        p = Path(p)
        if p.is_dir():
            found = sorted(p.rglob("*.sysml"))
            if not found:
                raise SysMLError(f"{p}: no .sysml files")
            for f in found:
                files.setdefault(f.resolve(), f)
        elif p.is_file():
            files.setdefault(p.resolve(), p)
        else:
            raise SysMLError(f"{p}: no such file or directory")
    return list(files.values())


def _source(path: Path, base: Path) -> Source:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise SysMLError(f"{path}: {exc}") from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SysMLError(f"{path}: not UTF-8 text ({exc})") from exc
    try:
        display = path.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        display = path.as_posix()
    return Source(path, display, hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest(), text)


def import_sysml(paths: str | Path | Iterable[str | Path], mapping: Mapping | dict | str | Path | None = None,
                 base: str | Path | None = None) -> ImportResult:
    """Import SysML v2 textual files (or directories of them) into a draft decision state.

    `mapping` is a mapping file (path) or its parsed content; `base` is the directory that recorded
    paths are relative to (default: the current directory).
    """
    paths = [paths] if isinstance(paths, str | Path) else list(paths)
    files = _expand(paths)
    if not files:
        raise SysMLError("no .sysml files given")
    base_dir = Path.cwd() if base is None else Path(base)
    sources = sorted((_source(p, base_dir) for p in files), key=lambda s: s.display)
    return _Importer(sources, load_mapping(mapping)).run()


def write_state(result: ImportResult, out_dir: str | Path) -> list[Path]:
    """Write the draft as sysml-*.yaml files plus sysml-import.json (sources, rules, gaps).

    Every file is rewritten on each import, empty or not, so a re-import never leaves a stale part
    behind; other files in out_dir (the framing a person adds) are not touched.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    sources = ", ".join(f"{s['path']} (sha256:{s['sha256'][:12]})" for s in result.sources)
    header = (f"# Generated by `pdo sysml import` from {sources}.\n"
              "# Regenerated on every import: record ids, units and wiring in the mapping file, and put the framing\n"
              f"# (criteria, decision rule, readiness) in files of your own. Gaps: {REPORT_FILE}.\n")
    written = []
    for name, slots in STATE_FILES:
        part = {slot: result.state[slot] for slot in slots if slot in result.state}
        body = yaml.safe_dump(part, sort_keys=False, allow_unicode=True, width=110) if part else "{}\n"
        (out / name).write_text(header + body, encoding="utf-8")
        written.append(out / name)
    report = {k: v for k, v in result.to_dict().items() if k != "state"}
    (out / REPORT_FILE).write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    written.append(out / REPORT_FILE)
    return written
