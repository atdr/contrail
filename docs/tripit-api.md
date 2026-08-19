# The TripIt API — investigated, not used

contrail reads TripIt's **iCal feed**, not its API. This records why, so the
question doesn't get reopened from scratch.

Investigated 2026-08-14, when codeshares needed the operating carrier and the
feed appeared not to carry it. It turned out the feed does
(see [parsing.md](parsing.md)), which made the API unnecessary — but the research
stands if a future importer wants what only the API offers.

## What the API would give

`AirSegment` in `https://api.tripit.com/xsd/tripit-api-obj-v1.xsd` carries both
pairs, and the `operating_*` fields sit on the core object, **not** inside the
`FlightStatus` block the schema marks TripIt Pro only:

```text
marketing_airline, marketing_airline_code, marketing_flight_number
operating_airline, operating_airline_code, operating_flight_number
```

It would retire most of the iCal guesswork:

| field                                              | replaces                                                 |
| -------------------------------------------------- | -------------------------------------------------------- |
| `start_airport_code` / `end_airport_code`          | the airport-code regexes                                 |
| `marketing_flight_number` + codes                  | `FLIGHT_NO_RE`                                           |
| `StartDateTime` (`date`, `timezone`, `utc_offset`) | the whole `airports.py` conversion — already local       |
| `service_class`                                    | **cabin class**, which nothing else can currently supply |
| `id` / `uuid`                                      | the UID-less content-hash fallback                       |
| `aircraft`, `distance`, `stops`                    | not captured at all today                                |

`service_class` is the interesting one: it would make `emissions_kg_actual`
reflect the cabin actually flown instead of assuming economy.

## Getting in

**OAuth is documented and, as far as anyone can tell, still works.** The docs
describe the full three-legged OAuth 1.0 flow — request token, user
authorization, access token — and access tokens are storable "indefinitely",
which suits unattended running well. If a consumer key is already in hand, this
is the better route: an OAuth token is scoped and revocable on its own.

**What's unverified is whether a _new_ consumer key can still be obtained.** The
evidence says probably not, but none of it is first-hand:

- tripit/api#287 and #280, both open since 2023-11-04, report the "Register an
  app" button gone from the developer portal.
- tripit/api#288 has a commenter reporting that TripIt support confirmed by email
  and Twitter that no new client IDs are issued. That is a third-party account,
  not something confirmed here.
- 43 open issues, and the docs footer reads "© 2006-2013".

`tripit.com/developer` returns HTTP 200, but the page is JS-rendered so its
contents were never actually inspected — checking it while logged in would settle
this in a minute and hasn't been done.

**Web Authentication needs no consumer key at all.** HTTP Basic with a TripIt
email and password, which sidesteps the registration question entirely. Two
conditions:

- It is **off by default for every account**; enabling it means emailing
  <support@tripit.com>, an unknown given how unattended the API is.
- The docs restrict it to "testing and development" and warn it "may be limited
  or turned off by default in future versions".

The service is alive: `GET /v1/list/trip` returns 401 rather than failing, and
invalid Basic credentials get `HTTP 400 {"error": "access_denied"}` rather than
a rejection of the scheme, so Basic auth is still processed server-side.

**The security trade is the real objection.** An OAuth token is scoped and
revocable on its own; a Basic credential is the _account password_, and the docs
are explicit that web auth carries "the exact same permissions a user has when
logged in to TripIt via the web" — strictly more than OAuth would grant. contrail
needs read-only and there is no way to ask for only that. A leak is full account
takeover, not a revocable token.

To check whether it's already enabled, in a terminal so the password never
reaches a transcript (`--user` with no colon makes curl prompt):

```bash
curl -s -o /dev/null -w "%{http_code}\n" \
  --user 'you@example.com' \
  'https://api.tripit.com/v1/list/trip?format=json'
```

`200` means enabled. `401` means it isn't — as of 2026-08-14, it isn't.

## If it ever goes ahead

Check the developer portal first, logged in — if a consumer key can still be
registered, use OAuth and skip the password question entirely.

`/list/trip?past=true&include_objects=true` returns full history with segments in
one call, and `format=json` avoids XML parsing. Put auth behind a small seam so
Basic and OAuth 1.0 are interchangeable either way: the OAuth browser dance
happens once and the token then lives as a secret, with no code change.
