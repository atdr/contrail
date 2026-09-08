"""Every workflow's top-level `name:` must equal its own filename.

GitHub labels a check `<workflow name> / <job name>` and never shows the
filename, so a workflow named for what it does leaves a reader guessing which
file under `.github/workflows/` produced a given check. Keeping the name equal
to the filename makes that mapping mechanical; the description goes on the
job instead. See atdr/contrail-gh#7, which added the same guard as a shell
step because that repo has no test suite to host it in — contrail does, so it
belongs here instead of prose in AGENTS.md that a change can silently ignore.
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
