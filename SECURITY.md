# Security policy

## Supported versions

Only the latest release receives security fixes. Releases are cut automatically
from `main` by release-please, so the current version on PyPI is always the
supported one — see the [Install](README.md#install) section for the pinned
command.

## Reporting a vulnerability

**Please do not report security vulnerabilities through public issues.**

Report them privately through GitHub Security Advisories, using the [Report a
vulnerability](https://github.com/atdr/contrail/security/advisories/new) form.
That opens a private thread visible only to you and the maintainer.

Please include:

- The contrail version (`contrail --version`) and Python version you are running
- What an attacker can do, and what access they need to do it
- Steps to reproduce, or a proof of concept

**Do not include your `TRIPIT_ICAL_URL`, your TIM API key, or the contents of
`config.json`/`config.yaml`.** Describe the exposure instead — for example, "an
attacker who can read the CSV output can also recover the TripIt feed URL" is
enough detail; the actual URL or key is not needed and is itself a secret.

You can expect an acknowledgement within a week. If the report is accepted, a
fix and an advisory will follow; if it is declined, you will get the reasoning.

## Scope

contrail talks to two third-party services at runtime: TripIt's iCal feed and
Google's Travel Impact Model API. Vulnerabilities in how contrail constructs
those requests, stores the credentials involved, or handles the responses it
gets back are in scope. Vulnerabilities in TripIt's or Google's own services are
not — report those to TripIt or Google directly.
