"""Proposal figures: copy the scenario figures the proposal uses into its version directory.

    uv run python -m scripts.proposal_figures --proposal-dir DIR [--results results/scenarios]

The proposal uses three of the scenario figures (pdo.bench.scenarios): the trade study, the program over
refresh cycles and the perturbation benchmark. The others (report revisions, uncertainty) stay generated in
results/scenarios/ and are not part of the proposal's set. Each figure is copied as
DIR/figures/<id>/<id>.{pdf,png}, only when its bytes changed. The manifest entries and the captions are the
proposal's own; this prints the ids the manifest must declare.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PROPOSAL_FIGURES = ("trade-study", "program-refresh", "perturbations")
FORMATS = ("pdf", "png")


def copy_figures(results: Path, proposal_dir: Path) -> list[Path]:
    written = []
    for name in PROPOSAL_FIGURES:
        for ext in FORMATS:
            src = results / name / f"{name}.{ext}"
            if not src.exists():
                raise SystemExit(f"{src} does not exist: run `make scenarios` first")
            dest = proposal_dir / "figures" / name / f"{name}.{ext}"
            data = src.read_bytes()
            if dest.exists() and dest.read_bytes() == data:
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            written.append(dest)
    return written


def undeclared(proposal_dir: Path) -> list[str]:
    manifest = proposal_dir / "manifest.yaml"
    if not manifest.exists():
        return list(PROPOSAL_FIGURES)
    declared = (yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}).get("figures") or {}
    return [name for name in PROPOSAL_FIGURES if name not in declared]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--proposal-dir", type=Path, required=True)
    parser.add_argument("--results", type=Path, default=ROOT / "results" / "scenarios")
    args = parser.parse_args(argv)
    if not args.proposal_dir.is_dir():
        print(f"{args.proposal_dir}: no such directory", file=sys.stderr)
        return 1
    written = copy_figures(args.results, args.proposal_dir)
    print(f"{len(written)} figure files written under {args.proposal_dir / 'figures'}"
          + "".join(f"\n  {p.relative_to(args.proposal_dir)}" for p in written))
    missing = undeclared(args.proposal_dir)
    if missing:
        print("declare under figures: in manifest.yaml: " + ", ".join(missing))
    return 0


if __name__ == "__main__":
    sys.exit(main())
