# Agent-team refactor verification — 2026-11-04

## Delivered

- Retired the root `demo/` runtime package; active imports use `doc_understanding`.
- Split role prompts, response models and one-call execution into `agents/`.
- Exposed table-team membership in `teams/table_review/profiles.py`, graph assembly
  in `graph.py`, shared state in `state.py`, and request execution in `service.py`.
- Preserved legacy, evidence-first and source-axis workflows because queued modes
  and evaluation clients still depend on their behavior.
- Extracted evidence tools, deterministic validators, model transport, credentials,
  budget error, queue, user feedback, HTTP assets, and document worker responsibilities.
- Moved former demo tests into standard `tests/` collection and extracted shared fixtures.
- Included the model-free parser refresh path and retained the recent retry behavior
  for `validation_failed` experiments while reconciling the source changes.
- Added the `nh-review` entry point and packaged browser assets.

No pre-existing Python implementation under `src/` was changed; the new modules
were extracted from the old application. Existing Physical/Structured/Canonical
contracts and parser implementation remain intact. No provider was enabled, no
paid model call was made, and no push, deployment or financial-fact publication ran.

## Cleanup and recovery

The former `demo/` and twelve root `.tmp-*` directories are no longer in the active
root tree. Their originals are archived under the `tmp/retired-layout-20261104/`
entry in `tmp/archive/agent-team-refactor-20261104.zip`.
The twelve temporary directories contain **551 files**; the earlier chat count of
651 was an arithmetic error.

Before relocation, source and temporary-output ZIP archives were verified against
SHA-256 manifests. The consolidated archive also contains the former recovery ZIPs
`tmp/retired-demo-20261104.zip`, `tmp/archived-root-test-outputs-20261104.zip`, and
the initial source/test/document backup `tmp/agent-team-refactor-backup-20261104.zip`.
It preserves the intermediate scripts, build output and test-run directories.
The adjacent `.manifest.json` records each archived file's SHA-256 and the archive
hash. Extract into a separate directory to inspect; entries retain project-relative
paths. Extract the temporary runner back to project `tmp/` only if reproducing the
host-specific verification command below.

Follow-up cleanup verified all **4,431 files** in the consolidated ZIP. Automatic
approval review rejected removal because this session cannot request sandbox
approval. No duplicate originals were deleted. Instead, the 33 top-level temporary
entries were moved to `tmp/archive/agent-team-refactor-20261104-originals/`.
This reduces loose entries in `tmp/`, not disk usage. The retained originals and
ZIP are intentionally redundant until deletion can be performed in an authorized
environment. Original `tmp/pdfs/` and `tmp/resume-review/` were left in place.
VS Code now hides the two Python environments, `tmp/`, and the reference UI tool
installation. All 52 executables are retained (27 in `.venv/`, 23 in
`.venv-baseline/`, 2 in `work/reference-ui-tools/`). The 86 `tests/test_*.py` files
remain visible. Settings JSON parsing, role imports and LangGraph compilation
passed after these navigation-only changes; the regression suite was not rerun.
These are local recovery material, not another active runtime package. `/tmp/` and
`/work/` are excluded from future Git additions. Original documents, user records,
historical reports, frozen code snapshots and `.agent/` remain preserved.

## Checks and observed results

| Check | Result |
|---|---|
| Ruff on all newly separated implementation packages | Passed |
| Parse active Python source and inspect imports | Passed; no import of root `demo` |
| Pre-migration fingerprints for 11 role prompts and JSON schemas | Identical |
| Team/queue/feedback/worker-related subset in full run | 105 passed, 7 HTTP tests skipped, 2 pre-existing image-upload failures |
| Final parser-refresh, parser-experiment and migration tests | 16 passed |
| Wheel build using locally available setuptools | Passed, no downloads |
| Wheel imported from an isolated extracted location | Passed; 15 static assets present, team registry and entry point valid, paid default disabled |
| Full regression run | 683 passed, 5 failed, 14 skipped, 12 subtests passed |
| Batch failure recheck with a shorter temporary path | 1 passed |

The full run is **not a completely passing suite**. After the batch-path recheck,
four observed failures remain outside the moved team logic:

1. `test_web.py::test_image_input_preserves_pixels_without_claiming_ocr[png]`
2. `test_web.py::test_image_input_preserves_pixels_without_claiming_ocr[jpeg]`
   - Both old and migrated upload functions returned HTTP 422 in a direct comparison.
   - Installed PyMuPDF's `image_profile(bytes)` raised `TypeError` in
     `fz_recognize_image_format`. The upload implementation was preserved.
3. `test_semantic_resolver_real.py::test_frozen_upstream_contracts`
   - Saved expected hash differs from `table_understanding/semantic_models.py`.
   - That file is byte-identical to the pre-refactor backup; the frozen expectation
     was not rewritten to make the test pass.
4. `test_table_identity.py::test_previous_sidecar_is_readable_but_not_silently_revalidated`
   - Expects `LINKAGE_ALGORITHM_MISMATCH`, receives `SNAPSHOT_MISMATCH`.
   - `document_rag/nested_content.py` is byte-identical to the pre-refactor backup;
     the historical sidecar was not modified.

The fifth full-run failure was `test_document_rag_batch.py`: the long `--basetemp`
path exceeded this Windows environment's usable path length when creating a nested
`*-structured.json`. An isolated rerun with `--basetemp=tmp/t1` passed unchanged.

Thirteen HTTP tests were explicitly skipped by the temporary verification runner
after both pre-refactor and post-refactor runs hung in Windows
`socket.socketpair → accept` while constructing the asyncio event loop. A separate
PostgreSQL opt-in test was skipped by its existing guard. No production test was
weakened or permanently marked skipped. Direct route/static-file checks passed;
HTTP integration and a live browser session remain unverified on this host.

## Reproduce

Normal regression command (on a host where asyncio sockets work):

```powershell
.venv/Scripts/python.exe -B -m pytest tests -q -p no:cacheprovider
```

Targeted migration check:

```powershell
.venv/Scripts/python.exe -B -m pytest tests/test_parser_refresh.py tests/test_parser_repair.py tests/test_agent_team_migration.py -q -p no:cacheprovider
```

This session's environment-limited full run used `tmp/run_refactor_checks.py`
(now retained in the consolidated archive). Final evidence is available under
`reports/agent-team-refactor-20261104/`: `team-final-20261104.xml` and `.log`,
`team-final-targets-20261104.xml`, `team-batch-short-path-20261104.xml`, and
`team-wheel-verification.json`. The first pip wheel attempt lacked setuptools
in `.venv`; the successful build used the installed local setuptools package
without installing dependencies or changing the application environment.

Passing implementation checks do not establish PDF semantic accuracy or approve
any financial facts. See [AGENT_TEAMS.md](AGENT_TEAMS.md) for the current architecture
and application startup command.
