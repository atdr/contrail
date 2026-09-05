"""Guards for the GitHub issue forms in .github/ISSUE_TEMPLATE.

Two things a YAML linter can't catch, because the YAML is valid either way:

- A `value`/`description` `|` literal block is rendered as comment Markdown on
  github.com, where a newline becomes a `<br>`. A paragraph hard wrapped in the
  YAML arrives on the live form broken at the column it was wrapped at, and
  never reflows to the reader's width — see
  https://github.com/atdr/homebridge-philipsair-platform/pull/92, which hit
  this for real.
- The "Sources configured" checkboxes in bug_report.yml are typed out by hand,
  so they silently stop matching the importer registry the day a new importer
  is added.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from contrail.importers import IMPORTERS

TEMPLATE_DIR = Path(__file__).parent.parent / ".github" / "ISSUE_TEMPLATE"
FORM_PATHS = sorted(p for p in TEMPLATE_DIR.glob("*.yml") if p.name != "config.yml")

_OPENER = re.compile(r"^(\s*)(value|description):\s*\|[-+]?\s*$")


def _literal_blocks(text: str) -> list[tuple[str, int, list[str]]]:
    """Every `<key>: |` literal block, as (key, first body line number, body lines)."""
    lines = text.splitlines()
    blocks = []
    for index, line in enumerate(lines):
        opener = _OPENER.match(line)
        if not opener:
            continue
        indent = len(opener.group(1))
        body = []
        for next_line in lines[index + 1 :]:
            if next_line.strip() and len(next_line) - len(next_line.lstrip()) <= indent:
                break
            body.append(next_line)
        blocks.append((opener.group(2), index + 2, body))
    return blocks


def test_every_form_has_a_literal_block():
    """A guard reading the wrong shape would otherwise pass by finding nothing."""
    for path in FORM_PATHS:
        assert _literal_blocks(path.read_text()), f"{path.name} has no literal block"


def test_literal_blocks_keep_each_paragraph_on_one_line():
    for path in FORM_PATHS:
        for key, first_line, body in _literal_blocks(path.read_text()):
            for offset, line in enumerate(body):
                previous = body[offset - 1] if offset > 0 else ""
                assert not (line.strip() and previous.strip()), (
                    f"{path.name}:{first_line + offset} continues a paragraph on a "
                    f"second line. GitHub renders the '{key}' block's newlines as "
                    f"<br>, so unwrap the paragraph onto one line"
                )


def test_sources_checkboxes_match_the_importer_registry():
    form = yaml.safe_load((TEMPLATE_DIR / "bug_report.yml").read_text())
    sources_field = next(item for item in form["body"] if item.get("id") == "sources")
    labels = {option["label"] for option in sources_field["attributes"]["options"]}
    assert labels == set(IMPORTERS), (
        "bug_report.yml's 'Sources configured' checkboxes have drifted from the "
        "importer registry in contrail.importers.IMPORTERS"
    )
