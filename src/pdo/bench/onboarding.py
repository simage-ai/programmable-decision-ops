"""A scripted onboarding session: the vehicle trade study built from raw inputs by following `pdo onboard`.

The person's statements (the question, the roles, the mapping, the framing, each acceptance, review and
approval) are taken from the reference example, examples/vehicle, so the session can be checked against
it; the agent's proposals are the quotes from the reports. Each step asks `pdo onboard` for the next step,
checks it is the expected one, and does it through the same entry points a person or an agent would use
(the CLI), recording who acted: the person (a judgement), the agent (a proposal), a pdo tool (mechanical)
or the kernel (a computation).

Used by tests/test_onboard.py and by the onboarding scenario (pdo.bench.scenarios).
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

QUESTION = "Which vehicle configuration should the program carry into development?"
SHEETS, REPORT = "inputs/docs/configuration-sheets.pdf", "inputs/docs/reliability-test-report.docx"
ACTORS = ("person", "agent", "tool", "kernel")


def vehicle_dir() -> Path:
    from pdo.tools import examples_dir

    root = examples_dir()
    if root is None:
        raise FileNotFoundError("the bundled examples are not installed")
    return root / "vehicle"


def roles() -> list[dict]:
    return yaml.safe_load((vehicle_dir() / "baseline" / "governance.yaml").read_text())["roles"]


def mapping() -> dict:
    """The vehicle mapping, as the person completes it: values the reports state come from the reports."""
    data = yaml.safe_load((vehicle_dir() / "sysml" / "mapping.yaml").read_text())
    for key in ("tankCapacity", "fuelEconomy"):
        data["measures"][key]["observation"] = False
    data["measures"]["reliability"] = {"id": "meas:reliability", "observation": False}
    data["evaluations"]["RangeModel"] = {"id": "spec:range-model", "implementation": "evaluators.py:range_model",
                                         "implementation_version": "1.0"}
    data["assumptions"]["RangeModel::terrain_factor"] = {
        "id": "asm:terrain-factor", "critical": True, "validation_status": "validated",
        "plausible_low": 0.80, "plausible_high": 1.05}
    data["evidence"] = {"vehicle.sysml": {"id": "ev:sysml-vehicle", "owner_role": "role:systems-engineer"}}
    return data


def framing() -> dict:
    """What the decision owner states: criteria, weights, rule, scenarios, readiness, risks and a review to do."""
    out: dict = {}
    for name in ("decision.yaml", "governance.yaml"):
        data = yaml.safe_load((vehicle_dir() / "baseline" / name).read_text())
        out.update({k: v for k, v in data.items() if k not in ("measures", "requirements", "roles")})
    for review in out["reviews"]:
        review["review_status"] = "pending"  # the bias reviewer has not looked yet
    return out


def sheets_proposals() -> dict:
    """The agent's proposals from the configuration sheets, recorded by their owner (accept)."""
    evidence = [{"id": f"ev:design-{a}", "name": f"Configuration sheet {a}", "evidence_kind": "specification",
                 "source_ref": f"Synthetic configuration sheet, vehicle {a}, rev 1", "document_path": SHEETS,
                 "issued_on": "2026-11-02", "owner_role": "role:systems-engineer"} for a in "ABC"]
    tanks = {"A": "40", "B": "45", "C": "47.5"}
    observations = []
    for a in "ABC":
        observations += [
            {"id": f"obs:{a}.tank", "alternative": f"alt:{a}", "measure": "meas:tank-capacity",
             "value": float(tanks[a]), "evidence": f"ev:design-{a}",
             "quote": f"configuration {a} holds a usable fuel capacity of {tanks[a]} gal"},
            {"id": f"obs:{a}.mpg", "alternative": f"alt:{a}", "measure": "meas:fuel-economy", "value": 8.0,
             "evidence": f"ev:design-{a}", "quote": f"configuration {a} is rated at a fuel economy of 8.0 mi/gal"}]
    return {"accept": True, "evidence_items": evidence, "observations": observations}


def report_proposals() -> tuple[dict, dict]:
    """The reliability report: A and C recorded by the test engineer; B is the agent's proposal, reviewed later."""
    evidence = [{"id": f"ev:reliability-{a}", "name": f"Reliability test report {a}", "evidence_kind": "test report",
                 "source_ref": f"Synthetic reliability growth test, vehicle {a}, phase 1", "document_path": REPORT,
                 "issued_on": "2026-12-10", "owner_role": "role:test-engineer"} for a in "ABC"]
    quote = {"A": ("0.97", "3.1"), "B": ("0.98", "3.2"), "C": ("0.94", "3.3")}

    def obs(a: str) -> dict:
        value, section = quote[a]
        return {"id": f"obs:{a}.reliability", "alternative": f"alt:{a}", "measure": "meas:reliability",
                "value": float(value), "evidence": f"ev:reliability-{a}", "section": section,
                "quote": f"Its demonstrated mission reliability is {value}"}

    return ({"accept": True, "evidence_items": evidence, "observations": [obs("A"), obs("C")]},
            {"observations": [obs("B")]})


@dataclass
class Step:
    n: int
    funnel: str  # construct | run | study | done
    step: str  # the onboard step it answers ("" for intake)
    actor: str  # person | agent | tool | kernel
    action: str
    command: str
    seconds: float = 0.0
    result: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Session:
    directory: Path
    steps: list[Step]
    final: dict  # pdo onboard's report at the end

    def actors(self) -> dict[str, int]:
        return {a: sum(1 for s in self.steps if s.actor == a) for a in ACTORS}


class SessionError(AssertionError):
    pass


def _cli(*args) -> dict:
    from typer.testing import CliRunner

    from pdo.cli import app

    out = CliRunner().invoke(app, [str(a) for a in args] + ["--json"])
    if out.exit_code != 0:
        raise SessionError(f"pdo {' '.join(str(a) for a in args)} failed: {out.output}")
    return json.loads(out.output)


def _write(path: Path, data: dict) -> Path:
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def run_session(workdir: str | Path) -> Session:
    """Build the vehicle decision from its raw inputs in workdir/vehicle, following pdo onboard step by step."""
    work = Path(workdir)
    d = work / "vehicle"
    vehicle = vehicle_dir()
    steps: list[Step] = []

    def record(funnel: str, step: str, actor: str, action: str, command: str, fn) -> dict:
        start = time.perf_counter()
        result = fn() or {}
        steps.append(Step(len(steps) + 1, funnel, step, actor, action, command, time.perf_counter() - start, result))
        return result

    def expect(step: str, stage: str) -> dict:
        report = _cli("onboard", d)
        nxt = report["next"] or {}
        if nxt.get("step") != step or report["stage"] != stage:
            raise SessionError(f"expected the next step {stage}/{step}, onboard says "
                               f"{report['stage']}/{nxt.get('step')}: {nxt.get('do')}")
        return nxt

    def run_command(command: str) -> dict:
        return _cli(*command.split()[1:])

    docs = vehicle / "docs"
    record("construct", "", "person", "state the question and the owner; hand over the reports and the model",
           "pdo init", lambda: _cli("init", d, "--question", QUESTION, "--owner", "role:program-manager", "--name",
                                    "Vehicle", "--doc", docs / "configuration-sheets.pdf", "--doc",
                                    docs / "reliability-test-report.docx",
                                    "--model", vehicle / "sysml" / "vehicle.sysml"))
    record("construct", "roles", "person", "say who holds which role", "baseline/roles.yaml",
           lambda: _write(d / "baseline" / "roles.yaml", {"roles": roles()}) and {})
    expect("models", "construct")
    record("construct", "models", "person", "state ids, units, sources of record and the range model",
           "inputs/models/mapping.yaml", lambda: (_write(d / "inputs" / "models" / "mapping.yaml", mapping()),
                                                  shutil.copy(vehicle / "evaluators.py", d / "evaluators.py")) and {})
    nxt = expect("models", "construct")
    record("construct", "models", "tool", "import the SysML model", "pdo sysml import",
           lambda cmd=nxt["command"]: run_command(cmd))
    expect("documents", "construct")
    record("construct", "documents", "agent", "propose the configuration sheets' values with quotes (owner accepts)",
           "pdo docs extract --baseline",
           lambda: _cli("docs", "extract", d, _write(work / "sheets.yaml", sheets_proposals()), "--write", "--baseline",
                        "--role", "role:systems-engineer"))
    accepted, proposed = report_proposals()
    record("construct", "documents", "agent", "propose the reliability values with quotes (owner accepts A, C)",
           "pdo docs extract --baseline",
           lambda: _cli("docs", "extract", d, _write(work / "report.yaml", accepted), "--write", "--baseline",
                        "--role", "role:test-engineer"))
    record("construct", "documents", "agent", "propose B's reliability (provisional)", "pdo docs extract --baseline",
           lambda: _cli("docs", "extract", d, _write(work / "proposed.yaml", proposed), "--write", "--baseline"))
    expect("framing", "construct")
    record("construct", "framing", "person", "frame the decision: criteria, weights, rule, readiness, risks",
           "baseline/framing.yaml", lambda: _write(d / "baseline" / "framing.yaml", framing()) and {})
    record("construct", "valid", "kernel", "validate and run", "pdo validate", lambda: _cli("validate", d))
    nxt = expect("evidence", "run")
    record("run", "evidence", "person", "the test engineer accepts B's reliability", "pdo program record",
           lambda cmd=nxt["command"]: run_command(cmd))
    for kind in ("review", "approval"):
        nxt = expect("study", "study")
        record("study", "study", "kernel", "study the decision", "pdo study",
               lambda cmd=nxt["command"]: run_command(cmd))
        nxt = expect("authorize", "study")
        who = "the bias reviewer completes the framing review" if kind == "review" else "the authority approves B"
        if f"--kind {kind}" not in nxt["command"]:
            raise SessionError(f"expected a {kind} next, onboard says {nxt['command']}")
        record("study", "authorize", "person", who, "pdo program record", lambda cmd=nxt["command"]: run_command(cmd))
    nxt = expect("study", "study")
    record("study", "study", "kernel", "study what will be acted on", "pdo study",
           lambda cmd=nxt["command"]: run_command(cmd))
    final = _cli("onboard", d)
    if final["stage"] != "done":
        raise SessionError(f"the session ends at {final['stage']}: {final['next']}")
    record("done", "", "kernel", "refresh: every episode selectively, verified against full recomputation",
           "pdo program refresh", lambda: _cli("program", "refresh", d))
    return Session(d, steps, final)
