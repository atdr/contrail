"""The coverage arrangement, enforced rather than written down.

Coverage here reports; it never gates. That is a claim about several files
at once, and every part of it is the kind a tidy-up can undo without anything
going red: `source` narrowed to a path that skips a module, `branch` dropped,
`informational` removed from `codecov.yml`, or the coverage run switched off
in `ci.yml`.

The coverage job itself, the blocking `pytest --cov` run and the
`continue-on-error` upload, lives in atdr/github-boilerplate's python-ci
workflow, which `ci.yml` calls at a pinned tag. This file checks the call.

The scope settings in particular look like defaults worth deleting and are
not. `source` naming the package and maintainer scripts is what makes a module
no test imports appear at 0% instead of vanishing from the report; without it
the least covered files are the ones that silently leave, and the number goes
*up*.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text())
CODECOV = yaml.safe_load((ROOT / "codecov.yml").read_text())
CI = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
PYTHON_JOB = CI["jobs"]["python"]


def test_coverage_measures_the_package_and_maintainer_scripts():
    """Not `--cov=` on the CI command line: the config is what a local run reads too."""
    source = PYPROJECT["tool"]["coverage"]["run"]["source"]

    assert source == ["src/contrail", "scripts"], (
        f"coverage source is {source!r}; it should name the package and maintainer "
        "script directories so an untested module is reported at 0% rather than left out"
    )
    for package in PYPROJECT["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]:
        assert package in source, f"{package} ships but is not measured"


def test_branch_coverage_is_on():
    assert PYPROJECT["tool"]["coverage"]["run"]["branch"] is True


def test_the_coverage_tool_is_installed_by_the_dev_extra():
    dev = PYPROJECT["project"]["optional-dependencies"]["dev"]
    assert any(spec.startswith("pytest-cov") for spec in dev), (
        "the coverage job runs `pytest --cov`, so pytest-cov has to arrive with "
        'pip install -e ".[dev]"'
    )


def test_codecov_can_never_block_a_merge():
    """Both of Codecov's status checks, not just the one it comments about."""
    statuses = CODECOV["coverage"]["status"]

    for kind in ("project", "patch"):
        assert statuses[kind]["default"]["informational"] is True, (
            f"the {kind} status is not informational, so Codecov owns a check on "
            "every pull request that this repo does not control"
        )


def test_ci_runs_the_shared_coverage_job_and_hands_it_the_token():
    """Turning coverage off, or dropping the secret, is quiet: the badge just goes stale."""
    assert PYTHON_JOB["uses"].startswith("atdr/github-boilerplate/.github/workflows/python-ci.yml@")
    assert PYTHON_JOB.get("with", {}).get("coverage", True) is not False, (
        "python-ci runs coverage by default; ci.yml should not switch it off"
    )
    assert PYTHON_JOB.get("secrets", {}).get("CODECOV_TOKEN") == "${{ secrets.CODECOV_TOKEN }}", (
        "without the token the upload fails quietly, since it is advisory"
    )
