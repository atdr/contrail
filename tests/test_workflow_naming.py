"""Conventions about the workflow files themselves, enforced rather than written.

Two of them, both documented in AGENTS.md and both previously held up by prose
that a change could silently ignore:

`name:` must equal the filename. GitHub labels a check
`<workflow name> / <job name>` and never shows the filename, so a workflow
named for what it does leaves a reader guessing which file under
`.github/workflows/` produced a given check. Keeping the name equal to the
filename makes that mapping mechanical; the description goes on the job
instead. See atdr/contrail-gh#7, which added the same guard as a shell step
because that repo has no test suite to host it in — contrail does.

Every job sets `timeout-minutes:`. GitHub's default is six hours, and the
failure that matters is a stall rather than an error: a `pip` or `npx` fetch
that hangs never fails on its own. The publish job in release-please.yml sat
without one for as long as the rule lived only in AGENTS.md, which is the
drift this half exists to catch.
"""

from __future__ import annotations

from pathlib import Path

import yaml

WORKFLOW_DIR = Path(__file__).parent.parent / ".github" / "workflows"
WORKFLOW_PATHS = sorted(WORKFLOW_DIR.glob("*.yml")) + sorted(WORKFLOW_DIR.glob("*.yaml"))


def test_there_are_workflows_to_check():
    """A guard reading the wrong directory would otherwise pass by finding nothing."""
    assert WORKFLOW_PATHS, f"no workflow files found under {WORKFLOW_DIR}"


def test_every_workflow_is_named_after_its_file():
    for path in WORKFLOW_PATHS:
        stem = path.stem
        name = yaml.safe_load(path.read_text()).get("name")
        assert name == stem, (
            f"{path.name} is named {name!r} but its checks should read {stem!r} "
            "so a reader can tell which file produced them"
        )


def test_every_job_sets_a_timeout():
    """A stall would otherwise sit until GitHub's six-hour default.

    The value is each workflow's own call, so this checks only that one is
    set: ten minutes throughout except pr-title.yml, which gets five.
    """
    for path in WORKFLOW_PATHS:
        jobs = yaml.safe_load(path.read_text()).get("jobs", {})
        for job_id, job in jobs.items():
            # A job that only calls a reusable workflow cannot carry the key at
            # all; there, the timeout lives inside the workflow being called.
            if "uses" in job:
                continue
            assert "timeout-minutes" in job, (
                f"{path.name} job {job_id!r} sets no timeout-minutes, so a "
                "stalled step there runs for six hours before GitHub kills it"
            )
            timeout = job["timeout-minutes"]
            assert isinstance(timeout, int) and timeout > 0, (
                f"{path.name} job {job_id!r} has timeout-minutes {timeout!r}, "
                "which is not a positive whole number of minutes"
            )
