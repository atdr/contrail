# Contributing

Bug reports, feature requests and pull requests are all welcome.

- **Something's wrong?**
  [Open a bug report](https://github.com/atdr/contrail/issues/new?template=bug_report.yml).
- **Missing a capability?**
  [Open a feature request](https://github.com/atdr/contrail/issues/new?template=feature_request.yml).
- **Sending code?** Read on.

Everyone taking part is expected to follow the
[Code of Conduct](CODE_OF_CONDUCT.md). Security issues go through the
[security policy](SECURITY.md), not a public issue.

## Development setup

```bash
git clone https://github.com/atdr/contrail.git && cd contrail
python3.12 -m venv venv && ./venv/bin/pip install -e ".[dev]"
./venv/bin/pytest -q
./venv/bin/ruff check . && ./venv/bin/ruff format .
```

## Conventions

Commit style, the PR/issue workflow, architecture, and the gotchas most likely
to bite are all in [`AGENTS.md`](AGENTS.md) — read that before sending a PR,
rather than this file duplicating it.
