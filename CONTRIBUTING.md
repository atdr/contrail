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

### Browser tests

`tests_browser/` opens a rendered Passport in Chromium. It is outside the
default test path, so the commands above never need a browser:

```bash
./venv/bin/pip install -e ".[dev,browser]"
./venv/bin/python -m playwright install chromium
./venv/bin/pytest tests_browser
```

Where Playwright ships no Chromium build for the OS (macOS 13, for one), use
an installed Chrome instead:

```bash
./venv/bin/pytest tests_browser --browser-channel chrome
```

## Conventions

Commit style, the PR/issue workflow, architecture, and the gotchas most likely
to bite are all in [`AGENTS.md`](AGENTS.md) — read that before sending a PR,
rather than this file duplicating it.
