"""`pdo docs`: index reports into sections, locate quoted values, and absorb revisions section by section."""

from __future__ import annotations

from pathlib import Path

import typer

docs_app = typer.Typer(help="Reports as sections with digests: index, show, locate, extract, revise, verify.",
                       no_args_is_help=True)


def _done(result: dict, as_json: bool, text: str) -> None:
    from pdo.cli import emit, fail

    if not result.get("ok"):
        hint = f"\nhint: {result['hint']}" if result.get("hint") and not as_json else ""
        fail(result.get("error", "failed") + hint, as_json)
    emit({k: v for k, v in result.items() if k != "ok"}, as_json, text)


def _preview_text(result: dict) -> str:
    where = f"written to {result['file']}" if result["written"] else "preview only (--write to add it)"
    lines = [f"episode {result['episode']} (parent {result['parent']}): {where}"]
    for k, v in result.get("changed", {}).items():
        lines.append(f"  {k}: {v['before']} -> {v['after']}")
    lines.append(f"  approval invalidated: {result['approval_invalidated']}; outcome: {result['outcome']}")
    for a in result.get("pending_actions_after", []):
        lines.append(f"  pending: {a['kind']} {a['target']} ({a['role']}): {a['reason']}")
    return "\n".join(lines)


def _pages(pages: list | None) -> str:
    if not pages:
        return ""
    return f"p.{pages[0]}" + (f"-{pages[1]}" if pages[1] != pages[0] else "")


def register(app: typer.Typer) -> None:
    from pdo.cli import EpisodeOpt, JsonOpt, PathArg

    app.add_typer(docs_app, name="docs")
    file_arg = typer.Argument(..., help="A .docx, .pdf, .md or .txt report.")
    write_opt = typer.Option(False, "--write", help="Add the change set to the program (default: preview).")
    role_opt = typer.Option(None, "--role", help="The role recording the change (author_role).")
    parent_opt = typer.Option(None, "--parent", help="Parent episode (default: the newest).")
    id_opt = typer.Option(None, "--id", help="The new episode's id (default: the next in the parent's numbering).")
    as_of_opt = typer.Option(None, "--as-of", help="The new episode's date, YYYY-MM-DD (default: the parent's).")

    @docs_app.command("index")
    def index_cmd(file: Path = file_arg, as_json: bool = JsonOpt) -> None:
        """Sections of a report with heading paths, pages and content digests."""
        from pdo import tools

        r = tools.docs_index(file)
        text = ""
        if r.get("ok"):
            kind = r["format"] + (f", {r['pages']} pages" if r["pages"] else "")
            rows = [f"{r['path']}  digest {r['digest']}  ({kind})"]
            for s in r["sections"]:
                rows.append(f"  {s['id']:<7} {s['path'][:64]:<64} {_pages(s['pages']):<7} {s['characters']:>6} ch"
                            f"  {s['digest'][:12]}")
            rows += [f"  warning: {w}" for w in r["warnings"]]
            text = "\n".join(rows)
        _done(r, as_json, text)

    @docs_app.command("show")
    def show_cmd(file: Path = file_arg,
                 section: str = typer.Argument(None, help="Section id, heading or heading path (default: all)."),
                 as_json: bool = JsonOpt) -> None:
        """Section text exactly as pdo matches quotes against it."""
        from pdo import tools

        r = tools.docs_read(file, section)
        text = ""
        if r.get("ok"):
            text = "\n\n".join(f"§ {s['id']}  {s['path']}\n" + "\n".join(f"  {p}" for p in s["text"])
                               for s in r["sections"])
        _done(r, as_json, text)

    @docs_app.command("locate")
    def locate_cmd(file: Path = file_arg,
                   quote: str = typer.Argument(..., help="The exact passage stating the value."),
                   section: str = typer.Option(None, "--section", "-s", help="Search only this section."),
                   as_json: bool = JsonOpt) -> None:
        """Locate a quote: section, page, paragraph, span and digests. Refuses a quote that is not in the report."""
        from pdo import tools

        r = tools.docs_locate(file, quote, section)
        text = ""
        if r.get("ok"):
            loc = r["locator"]
            page = f", page {loc['page']}" if loc.get("page") else ""
            numbers = ", ".join(f"{n:g}" for n in r["numbers"]) or "none"
            text = (f"section {loc['section']} ({loc['section_path']}){page}, paragraph {loc['paragraph']}, "
                    f"characters {loc['char_start']}-{loc['char_end']}\n"
                    f"  ...{loc['quote_prefix']}[{loc['quote']}]{loc['quote_suffix']}...\n"
                    f"  numbers in the quote: {numbers}")
        _done(r, as_json, text)

    @docs_app.command("verify")
    def verify_cmd(path: Path = PathArg, episode: str = EpisodeOpt, as_json: bool = JsonOpt) -> None:
        """Check every locator against its document (every episode of a program unless --episode)."""
        from pdo import tools

        r = tools.docs_verify(path, episode)
        text = ""
        if r.get("ok"):
            rows = [f"{r['errors']} error(s), {r['warnings']} warning(s)"]
            for p in r["problems"]:
                ep = f"{p['episode']}: " if p.get("episode") else ""
                rows.append(f"  {p['severity']}: {ep}{p['where']}: {p['message']}")
            text = "\n".join(rows)
        _done(r, as_json, text)
        if r.get("ok") and r["errors"]:
            raise typer.Exit(1)

    @docs_app.command("extract")
    def extract_cmd(path: Path = PathArg,
                    proposals: Path = typer.Argument(..., help="YAML: evidence, accept, observations with quotes."),
                    write: bool = write_opt, role: str = role_opt, parent: str = parent_opt,
                    episode_id: str = id_opt, as_of: str = as_of_opt, as_json: bool = JsonOpt) -> None:
        """Add or update observations from quoted passages, each located in its evidence's document."""
        from pdo import tools

        r = tools.docs_extract(path, str(proposals), write=write, role=role, parent=parent, episode_id=episode_id,
                               as_of=as_of)
        text = ""
        if r.get("ok"):
            rows = []
            for x in r["rows"]:
                page = f", page {x['page']}" if x.get("page") else ""
                warning = f"  warning: {x['warning']}" if x.get("warning") else ""
                rows.append(f"  {x['action']:<9} {x['observation']} = {x['value']}  (section {x['section']}{page})"
                            f"{warning}")
            text = "\n".join(rows) + ("\n" + _preview_text(r) if "episode" in r else f"\n{r.get('note', '')}")
        _done(r, as_json, text)

    @docs_app.command("revise")
    def revise_cmd(path: Path = PathArg,
                   document: str = typer.Argument(..., help="An evidence id, or the document_path being revised."),
                   new_file: Path = typer.Argument(..., help="The new version, inside the program directory."),
                   write: bool = write_opt, role: str = role_opt, parent: str = parent_opt,
                   episode_id: str = id_opt, as_of: str = as_of_opt, as_json: bool = JsonOpt) -> None:
        """Absorb a new version of a report: move unchanged sections' locators, flag the values in changed ones."""
        from pdo import tools

        r = tools.docs_revise(path, document, str(new_file), write=write, role=role, parent=parent,
                              episode_id=episode_id, as_of=as_of)
        text = ""
        if r.get("ok"):
            rev = r["revision"]
            rows = [f"{rev['document']['from']} -> {rev['document']['to']}"]
            rows += [f"  section {s['path']}: {s['status']}" for s in rev["sections"] or []
                     if s["status"] != "unchanged"]
            rows += [f"  re-anchor {x['observation']} (section {x['from']} -> {x['to']}, {x['status']})"
                     for x in rev["reanchored"]]
            for x in rev["flagged"]:
                why = "not located" if x["section"] is None else f"section {x['section']} {x['status']}"
                rows.append(f"  REVIEW    {x['observation']} = {x['value']} ({why})"
                            + ("" if x["applies"] else "; rejected, so no effect"))
            rows += [f"  warning: {w}" for w in rev.get("warnings", [])]
            text = "\n".join(rows) + "\n" + _preview_text(r)
        _done(r, as_json, text)
