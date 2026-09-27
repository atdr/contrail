"""The coverage arrangement, enforced rather than written down.

Coverage here reports; it never gates. That is a claim about four files at
once, and every part of it is the kind a tidy-up can undo without anything
going red: `source` narrowed to a path that skips a module, `branch` dropped,
`informational` removed from `codecov.yml`, or `continue-on-error` taken off
the upload so a Codecov outage fails a pull request that is fine.

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
COVERAGE_JOB = CI["jobs"]["coverage"]


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


def test_the_run_is_blocking_and_only_the_upload_is_advisory():
    """A job made entirely advisory is a check that proves nothing."""
    steps = COVERAGE_JOB["steps"]
    uploads = [step for step in steps if step.get("uses", "").startswith("codecov/codecov-action")]

    assert len(uploads) == 1
    upload = uploads[0]
    assert upload["continue-on-error"] is True, (
        "a Codecov outage says nothing about the change under review"
    )
    assert all("continue-on-error" not in step for step in steps if step is not upload), (
        "only the upload may be advisory; the coverage run itself has to be able to fail"
    )


def test_the_report_the_upload_reads_is_the_one_pytest_writes():
    """The two halves are written in different steps and neither names the other."""
    steps = COVERAGE_JOB["steps"]
    run = next(step["run"] for step in steps if "--cov" in step.get("run", ""))
    upload = next(step for step in steps if step.get("uses", "").startswith("codecov/"))

    assert "--cov-report=xml" in run, "Codecov reads the XML report, not the terminal one"
    # pytest-cov writes ./coverage.xml by default, and the upload is pointed at
    # that path explicitly rather than left to the action's own search.
    assert upload["with"]["files"].removeprefix("./") == "coverage.xml"
