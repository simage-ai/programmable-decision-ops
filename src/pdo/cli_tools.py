"""`pdo mcp`, `pdo schema describe` and `pdo example ...`: the agent tool surface from the command line.

    pdo mcp                         serve the pdo tools (pdo.tools) over MCP stdio, for agent harnesses
    pdo schema describe [CLASS]     the schema's classes and enums, or one class's slots
    pdo example list                the bundled example decisions
    pdo example copy NAME DEST      copy one to a new directory to work on it

The commands print what the pdo.tools functions return (with --json, exactly that dict) and exit 1
with the error and a hint when the request cannot be carried out. The examples ship in the wheel as
pdo/examples_data; in a source checkout they are the repository's examples/.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer


def _finish(data: dict, as_json: bool, render) -> None:
    """Print a tool result (JSON, or text from `render`), exiting 1 on {"ok": false}."""
    if as_json:
        typer.echo(json.dumps(data, indent=1))
    elif data["ok"]:
        typer.echo(render(data))
    else:
        typer.echo(f"error: {data['error']}\nhint: {data['hint']}", err=True)
    if not data["ok"]:
        raise typer.Exit(1)


def _first_sentence(text: str | None) -> str:
    text = " ".join((text or "").split())
    end = text.find(". ")
    return text if end < 0 else text[:end + 1]


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    widths = [max(len(str(c)) for c in col) for col in zip(header, *rows, strict=True)]
    return ["  ".join(str(c).ljust(w) for c, w in zip(row, widths, strict=True)).rstrip() for row in [header, *rows]]


def render_schema(data: dict) -> str:
    if data.get("kind") == "enum":
        lines = [f"# {data['name']} (enum)", ""]
        if data.get("description"):
            lines += [data["description"], ""]
        lines += [f"- {value}" + (f": {doc}" if doc else "") for value, doc in data["values"].items()]
        return "\n".join(lines)
    if data.get("kind") == "class":
        title = f"# {data['name']}" + (f" (is_a {data['is_a']})" if data.get("is_a") else "")
        lines = [title, ""]
        if data.get("description"):
            lines += [" ".join(data["description"].split()), ""]
        if data.get("state_collection"):
            lines += [f"In a decision state: {data['state_collection']}", ""]
        rows = []
        for s in data["slots"]:
            rng = s["range"] + ("[]" if s["multivalued"] else "")
            rows.append([s["name"], rng, "required" if s["required"] else "", s["identity"] or "",
                         _first_sentence(s.get("description"))])
        return "\n".join(lines + _table(["slot", "range", "", "identity", "description"], rows))
    lines = [f"# pdo schema ({data['source']})", "", "## Classes", ""]
    lines += _table(["class", "is_a", "description"],
                    [[c["name"], c["is_a"] or "", _first_sentence(c["description"])] for c in data["classes"]])
    lines += ["", "## Enums", ""]
    lines += [f"{e['name']}: {', '.join(e['values'])}" for e in data["enums"]]
    lines += ["", "pdo schema describe CLASS shows one class's slots."]
    return "\n".join(lines)


def render_examples(data: dict) -> str:
    lines = [f"Examples in {data['directory']}:", ""]
    for e in data["examples"]:
        n = e.get("change_sets", 0)
        kind = f"program, {n} change set{'s' if n != 1 else ''}" if e["kind"] == "program" else "state"
        lines.append(f"{e['name']}  ({kind})  {e.get('title') or ''}".rstrip())
        if e.get("question"):
            lines.append(f"    {' '.join(e['question'].split())}")
    lines += ["", "pdo example copy NAME DEST copies one to work on."]
    return "\n".join(lines)


def register(app: typer.Typer) -> None:
    """Add `mcp`, the `schema` group and the `example` group to the root app."""
    from pdo.cli import JsonOpt

    @app.command("mcp")
    def mcp_server() -> None:
        """Serve the pdo tools over MCP on stdio, for agent harnesses (logs go to stderr)."""
        import importlib.util

        if importlib.util.find_spec("mcp") is None:
            typer.echo("error: the MCP server needs the mcp extra: pip install 'programmable-decision-ops[mcp]'",
                       err=True)
            raise typer.Exit(1)
        from pdo.mcp_server import main

        main()

    schema = typer.Typer(no_args_is_help=True, help="The decision schema: classes, slots and enums.")
    app.add_typer(schema, name="schema")

    @schema.command("describe")
    def describe(class_name: str = typer.Argument(None, metavar="[CLASS]",
                                                  help="A class (Observation, ChangeSet, ...) or an enum."),
                 as_json: bool = JsonOpt) -> None:
        """Every class and enum, or one class's slots: range, required, hashable or unhashable, description."""
        from pdo.tools import describe_schema

        _finish(describe_schema(class_name), as_json, render_schema)

    example = typer.Typer(no_args_is_help=True, help="The example decisions bundled with pdo.")
    app.add_typer(example, name="example")

    @example.command("list")
    def list_(as_json: bool = JsonOpt) -> None:
        """The bundled example decisions."""
        from pdo.tools import list_examples

        _finish(list_examples(), as_json, render_examples)

    @example.command("copy")
    def copy(name: str = typer.Argument(..., help="Example name (see pdo example list)."),
             dest: Path = typer.Argument(..., help="New directory (must not exist, or be empty)."),
             as_json: bool = JsonOpt) -> None:
        """Copy an example to a new directory to run and change it."""
        from pdo.tools import copy_example

        _finish(copy_example(name, dest), as_json,
                lambda d: f"copied {d['example']} to {d['path']} ({len(d['files'])} files)\n"
                          f"next: pdo validate {d['path']} && pdo run {d['path']}")
