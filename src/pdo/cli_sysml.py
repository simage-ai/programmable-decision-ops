"""`pdo sysml`: import SysML v2 textual models into draft decision states, and list what does not map.

`pdo sysml import` is the mechanical half of turning a SysML model into a decision: alternatives,
measures, requirements, observations with their evidence, assumptions and evaluation placeholders, each
traced to its source element. `pdo sysml gaps` lists only what the import could not translate. The other
half, framing the decision (criteria, weights, decision rule, readiness), is done with the people who own
it; skills/sysml-to-decision/SKILL.md is the procedure.
"""

from __future__ import annotations

from pathlib import Path

import typer

FilesArg = typer.Argument(..., help="SysML v2 textual files (.sysml), or directories to search for them.")
MapOpt = typer.Option(None, "--map", help="Mapping file: ids, units and wiring the model does not state.")
JsonOpt = typer.Option(False, "--json", help="Print JSON.")


def _import(files: list[Path], mapping: Path | None, as_json: bool):
    from pdo.cli import fail
    from pdo.sysml import SysMLError, import_sysml

    try:
        return import_sysml(files, mapping)
    except SysMLError as exc:
        fail(str(exc), as_json)


def render_gaps(gaps) -> list[str]:
    return [f"  {g}" for g in gaps]


def render_import(result, written: list[Path]) -> str:
    counts = result.counts()
    names = {"alternatives": "alternative", "measures": "measure", "requirements": "requirement",
             "observations": "observation", "evidence_items": "evidence item", "assumptions": "assumption",
             "evaluation_specifications": "evaluation specification"}
    files = ", ".join(f"{s['path']} (sha256:{s['sha256'][:12]})" for s in result.sources)
    found = [f"{counts[k]} {v}{'s' if counts[k] != 1 else ''}" for k, v in names.items() if counts.get(k)]
    lines = [f"Imported {files}", "  " + (", ".join(found) or "nothing mapped")]
    for alt in result.state.get("alternatives", []):
        lines.append(f"  {alt['id']} <- {alt['source_element']}  [{alt['mapping_rule']}]")
    lines.append("  rules: " + (", ".join(result.rules_used) or "none"))
    lines.append(f"{len(result.gaps)} gap{'s' if len(result.gaps) != 1 else ''}" + (":" if result.gaps else ""))
    lines += render_gaps(result.gaps)
    if written:
        lines.append(f"Wrote {written[0].parent}/: {', '.join(p.name for p in written)}")
    missing = result.missing_to_run()
    if missing:
        lines.append("To run it, frame the decision with its owner and add: " + ", ".join(missing)
                     + " (see skills/sysml-to-decision/SKILL.md)")
    return "\n".join(lines)


def register(app: typer.Typer) -> None:
    """Add the `sysml` group to the root app."""
    sysml = typer.Typer(no_args_is_help=True, help="Import SysML v2 textual models into draft decision states.")
    app.add_typer(sysml, name="sysml")

    @sysml.command("import")
    def import_(files: list[Path] = FilesArg, mapping: Path = MapOpt,
                out: Path = typer.Option(None, "--out", help="Write the draft state (sysml-*.yaml) here."),
                as_json: bool = JsonOpt) -> None:
        """Import SysML into a draft decision state: alternatives, measures, requirements, observations."""
        from pdo.cli import emit
        from pdo.sysml import write_state

        result = _import(files, mapping, as_json)
        written = write_state(result, out) if out is not None else []
        emit({**result.to_dict(), "written": [str(p) for p in written]}, as_json, render_import(result, written))

    @sysml.command("gaps")
    def gaps(files: list[Path] = FilesArg, mapping: Path = MapOpt, as_json: bool = JsonOpt) -> None:
        """What in the SysML model the import cannot translate, and why."""
        from pdo.cli import emit

        result = _import(files, mapping, as_json)
        items = [g.to_dict() for g in result.gaps]
        text = "\n".join([f"{len(items)} gap{'s' if len(items) != 1 else ''}", *render_gaps(result.gaps)])
        emit({"gaps": items, "count": len(items), "sources": result.sources}, as_json, text)
