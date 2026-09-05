<!-- Write plainly: short sentences, concrete detail, no filler. A reviewer
should be able to tell what changed and why in under a minute. -->

## What changed

<!-- One or two sentences. The PR title becomes the commit on main (squash
merge), so keep it conventional: type(scope): summary -->

## Why

<!-- The problem this solves. Link the issue: Fixes #123 -->

## Checks

- [ ] `./venv/bin/ruff check .` and `./venv/bin/ruff format .` pass
- [ ] `./venv/bin/pytest -q` passes
- [ ] Docs updated in this PR, if this changes a config option, an importer,
      or an emissions provider
- [ ] Tests added or updated, if this changes behaviour
- [ ] Skimmed `gh issue list` for open issues this change touches

## Test plan

<!-- On a draft: what you intend to test, so it can be argued with before you
spend the time. On a PR ready for review: what you tested and what you saw. -->

<!-- This file opens with a comment, not a heading, by design. -->
<!-- markdownlint-configure-file {
  "MD041": false
} -->
