# Changelog

## [0.3.1](https://github.com/atdr/contrail/compare/v0.3.0...v0.3.1) (2026-09-05)


### Bug Fixes

* **deps:** Update airportsdata requirement from &gt;=20260803 to &gt;=20260902 ([#31](https://github.com/atdr/contrail/issues/31)) ([24f6c2e](https://github.com/atdr/contrail/commit/24f6c2eac97357a3ae01a4887210f8032b4406d8))
* **deps:** Update icalendar requirement from &gt;=7.2.2 to &gt;=7.3.0 ([#29](https://github.com/atdr/contrail/issues/29)) ([640788f](https://github.com/atdr/contrail/commit/640788fd2720046957835ea4036d7ae94e8716fb))


### Documentation

* a file-reading importer now needs its env var in two workflows ([#20](https://github.com/atdr/contrail/issues/20)) ([353b0c4](https://github.com/atdr/contrail/commit/353b0c4774e0e3cca444623bb437813eff13ad79))

## [0.3.0](https://github.com/atdr/contrail/compare/v0.2.0...v0.3.0) (2026-08-16)


### Features

* add a flighty_csv importer and match flights across sources ([ba0d292](https://github.com/atdr/contrail/commit/ba0d2921f04f6e3b92ec4f6c18c7024206cf9c54))


### Bug Fixes

* stop version references drifting from the release ([429507c](https://github.com/atdr/contrail/commit/429507c8d3d121c20c62e67b883cdaeb44776ffb))


### Documentation

* note the new way to reach the per-source identity gap ([941db88](https://github.com/atdr/contrail/commit/941db88d90a1b203aed0e5e7ce1905df389d4ef0))

## [0.2.0](https://github.com/atdr/contrail/compare/v0.1.0...v0.2.0) (2026-08-15)


### ⚠ BREAKING CHANGES

* Python 3.10 is no longer supported. requires-python is now >=3.11.

### Features

* require Python 3.11 ([18cdc6e](https://github.com/atdr/contrail/commit/18cdc6ea6251f10870303a9d17fe20e7f7a26d64))


### Bug Fixes

* **deps:** declare the pyyaml floor once and bound ruff ([8790755](https://github.com/atdr/contrail/commit/8790755521ace3cdbaf39e4a4e658762e6a28a0a))
* **deps:** Update icalendar requirement from &gt;=5.0 to &gt;=7.2.2 ([ee39316](https://github.com/atdr/contrail/commit/ee393167748db7b2cabd5d3667eb50b0837ab6e5))
* **deps:** Update pyyaml requirement from &gt;=6 to &gt;=6.0.3 ([2ed4484](https://github.com/atdr/contrail/commit/2ed4484142c9ef200c743634d54e286a43ca12b9))
* release runtime dependency bumps instead of hiding them ([68ff550](https://github.com/atdr/contrail/commit/68ff550a4d14db328fe017c235d5e59539767445))


### Documentation

* note the Actions setting release-please needs ([5e1778b](https://github.com/atdr/contrail/commit/5e1778b7c0ffde2475b83b00482d21ac8e843446))

## 0.1.0 (2026-08-15)


### Features

* add config resolution and CLI ([ca9f865](https://github.com/atdr/contrail/commit/ca9f86567ec0a223ea8823d75799f5fa79aaadc2))
* add core data model ([5c7b0b6](https://github.com/atdr/contrail/commit/5c7b0b639853c1f18d8a0f080a638baccd02d6d4))
* add CSV storage keyed on cumulative_kg_actual ([c633763](https://github.com/atdr/contrail/commit/c63376384d358b8024dd2eb7d6a842dab2695d54))
* add importer seam and tripit_ical importer ([e52aafd](https://github.com/atdr/contrail/commit/e52aafd810b73730ac003ea2224a39f9069bcddf))
* add TIM emissions provider with route-average fallback ([9badcc4](https://github.com/atdr/contrail/commit/9badcc4c990d618d6363dbc7b01cea2df9672605))
* drop the stored cumulative total from the CSV ([0ce92b6](https://github.com/atdr/contrail/commit/0ce92b6bd8f62dc5837c33dd68df0618f69ab4e3))
* price codeshares as the operating flight ([f456f73](https://github.com/atdr/contrail/commit/f456f73e912d4ae1e1bbac1e3eaaf5bfc5d1d23d))
* re-sync flights that have not yet departed ([372606f](https://github.com/atdr/contrail/commit/372606f365d19fc3ad2f99cd047c5de617000fb7))
* resolve departure dates in the origin airport's timezone ([b9032c9](https://github.com/atdr/contrail/commit/b9032c9c0a3572154cb2ddc20427b6d07c01ff40))
* ship example config files ([17214b8](https://github.com/atdr/contrail/commit/17214b8019f2f47e6e7356951bae029ea62dbf18))
* store departure times and keep everything TIM returns ([4f8840a](https://github.com/atdr/contrail/commit/4f8840a596dd5f3b65240edda8fa224add24bf89))


### Bug Fixes

* **emissions:** keep the TIM key out of URLs and error messages ([6e90e96](https://github.com/atdr/contrail/commit/6e90e968dee6a7b335ca2da6dea7ff655326025a))
* gitignore flight data backups and variants ([7b61808](https://github.com/atdr/contrail/commit/7b618087d6ecb473b3d998dfec7ebacc5f363ad5))
* harden re-sync against silent source failures and data loss ([25073ec](https://github.com/atdr/contrail/commit/25073ec4fad7473bed5e58ebb66596fcb67e4f30))
* **importers:** give UID-less events distinct dedup keys ([4b01c8c](https://github.com/atdr/contrail/commit/4b01c8c8bde1c1cf0353a60edb4586d9e92a5980))
* stop an upgrade downgrading every open exact figure ([2e618eb](https://github.com/atdr/contrail/commit/2e618eb8cd9094a0f48e8bf318aed2c512d75bc5))
* **storage:** count hand-edited rows and write the CSV atomically ([625c8a9](https://github.com/atdr/contrail/commit/625c8a9eefc8cab9bd2edd6225351a7e87e40f95))


### Documentation

* add README ([f43641d](https://github.com/atdr/contrail/commit/f43641d7f8bdd766dd8398c6e1c148d40df7c8a8))
* add working notes, status and build plan ([84c42bb](https://github.com/atdr/contrail/commit/84c42bb3f2608c00def9c9b293c0b66493a6fd9d))
* document the repair workflow and departure-date caveat ([211e7a3](https://github.com/atdr/contrail/commit/211e7a3e263022fffd1c05a01f2582260b6dcb89))
* record findings from the first real-feed run ([28bebe0](https://github.com/atdr/contrail/commit/28bebe08435d66c21fcc853238a056d31ee921af))
* record the changed-itinerary and UTC-date open questions ([d01b09a](https://github.com/atdr/contrail/commit/d01b09ae59d939ab0fd270834a50a6c292d37752))
* record what a change here obliges in contrail-gh ([44eb9e1](https://github.com/atdr/contrail/commit/44eb9e14b09c329c1fbd270814af063c41e023ef))
* split working notes into AGENTS.md and docs/ ([cdb85f2](https://github.com/atdr/contrail/commit/cdb85f2d49569c503fe14095952111317a6da050))
* state what is verified, drop the archaeology ([e2b4d49](https://github.com/atdr/contrail/commit/e2b4d496b047773acb646552b570e688dbd46573))
* use a generic owner for instance repos ([cbfbffd](https://github.com/atdr/contrail/commit/cbfbffde322b37a6a89932c4c258af8e9db414f8))
