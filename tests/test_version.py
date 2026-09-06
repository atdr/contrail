"""The reported version, and the two names that have to agree for it to work.

`contrail --version` and the Passport footer both read `__version__`, which comes
from installed package metadata. Asking for a distribution that isn't installed
raises nothing a caller sees — it falls back to the dev version — so the failure
is silent and only visible to someone who installed the package.
"""

import tomllib
from pathlib import Path

from contrail import __version__

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def project() -> dict:
    with PYPROJECT.open("rb") as f:
        return tomllib.load(f)["project"]


def test_the_version_comes_from_the_installed_distribution():
    """Fails if `__init__` asks for a distribution name `pyproject.toml` does not
    declare — which is how the PyPI rename to `contrails` left every install
    reporting 0.0.0.dev0."""
    from importlib.metadata import version

    assert __version__ == version(project()["name"])


def test_the_version_is_not_the_source_tree_fallback():
    assert __version__ != "0.0.0.dev0"


def test_the_declared_version_is_what_is_installed():
    """`pyproject.toml` is the single source of truth, and release-please owns it.
    A stale editable install is the usual reason these drift apart."""
    assert __version__ == project()["version"]
