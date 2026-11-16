# R1 final browser, visual and offline evidence

These commands describe the CI procedure. A command being documented or a report being
packaged does **not** mean it passed. Inspect `runner.json`, `results.json`,
`backend-results.json`, `offline-result.json`, and the matching source SHA. Baseline
capture is explicitly not final acceptance.

The copyable owner procedure is in `CANONICAL-CAPTURE.md`. Final browser inventories
are legacy 75, original replacement views 5, core 106 (89+4+12+1), admin 15 and visual
9 tests / 63 images: 210 tests overall. `check_results.py` verifies every project and
single-attempt result, matching source/browser metadata and complete resource cleanup;
the historical R1-5 count of 90 is not the final inventory.

## Environment and isolation

Use Python 3.12, Node 22, the committed frontend lockfile and Playwright 1.65.1.
CI installs its Chromium 159.0.8154.6 (v1255) with system dependencies on
`ubuntu-24.04`. Install local Noto fonts from the application's committed assets.
`MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD` are connection credentials
only; the runner generates a fresh random namespace, verifies every candidate
database is absent. New institution databases additionally require a matching server-derived
hash from that run’s control catalogue before entering the exact owned cleanup list.
Unknown names, another namespace and same-prefix schemas without a catalogue owner
are rejected. Only the recorded exact created databases are dropped in `finally`.
It starts both real services, disables the background worker and cleans private
authentication states and native Chromium profiles. Never supply a production
database or `ONTOLOGY_MYSQL_URL`.

```sh
python -m pip install -r requirements.txt -r requirements-dev.txt
npm --prefix frontend ci
frontend/node_modules/.bin/playwright install --with-deps chromium
python e2e/run.py --target legacy --evidence work/evidence/r1/e2e-legacy-ci
python e2e/run.py --target legacy --evidence work/evidence/r1/e2e-legacy-replacements-ci -- --config e2e/legacy-replacements.config.mjs
python e2e/run.py --target foundation --harness --evidence work/evidence/r1/e2e-foundation-ci
python e2e/run_admin.py --evidence work/evidence/r1/e2e-admin-ci
python e2e/run.py --target foundation --visual-only --evidence work/evidence/r1/e2e-visual-ci
```

Use new evidence paths on every attempt. The runner exports application source from
the exact HEAD; commit intended code before the run. The original target always
exports `1359118dd672431dcd0cde2eb81f2b0f994eef37`, builds that frontend and loads its
original fixture structure. Foundation loads the same fixture and runs all current
migrations before startup. `--fixture PATH` permits the integration fixture location;
the default is `tests/fixtures/v070`. Alternate ports use `--api-port`/`--agent-port`.

The original 75 tests are unchanged. The core dependency chain is
`foundation-pages` (original 75 plus Shell) → `foundation-home` (4 operations) →
`foundation-identity` → `harness-sdk`. Home and identity each use one worker and
default test mode: a failed case does not skip remaining cases in that project.
Dependency projects must all succeed before the next stage starts. In particular,
Home draft creation cannot race its later exact count comparison. The real SDK
runs last with one worker, retaining its actual business deadline. A second runner
uses a fresh namespace and original fixture for visual work with one worker, so
concurrent behavior-test writes and audit order cannot contaminate screenshots. Admin
configuration/demo operations use their own `run_admin.py` scope and provider lifecycle,
so clearing a model credential cannot overwrite the held SDK test’s configuration. Failed dependency projects cannot produce an overall passing result. No
retries or business-deadline extension is used. SDK uses the actual installed SDK and
real cancellation with a local, explicitly synthetic held Messages provider; it does
not certify a live external model. Previous failed and interrupted R1-5 runs remain
documented in `R1-5-ACCEPTANCE.md`.

After the new pages pass their A.2 operation comparisons, the four legacy redirect
checks assert each replacement page's exact level-1 heading instead of the retired
`data-legacy-page` wrapper. The original URL, query and hash assertions remain exact.
This is the A.2 page-contract evolution for account security, users, model and demo;
it is not an H.2 security exception or a weakening of the original 75. The completed
package-level A.2 operation results are recorded separately in the R1-6/7/8 reports;
this final CI reruns the combined behavior on its exact integrated source.

## Visual and accessibility contract

The matrix is 10 authenticated page states × 3 layouts × 2 actual saved view modes,
plus the login page at each layout: 63 PNGs. The states are home, Shell with the old
materials page, account security, users, organizations, audit, model configuration,
backup, system, and demo. Login has no authenticated view preference and is not
misrepresented as having two views. New-page owners provide real initial-load-ready
markers. Screenshots wait for local fonts, disable animation and mask only explicitly
marked dynamic timestamps and request identifiers. Counts, permissions, headings, controls and business
content remain visible. Horizontal overflow fails.

Layouts are 1366×768 and 1920×1080 at 100%, and the same physical browser window as
1366×768 content at real 125%. Each case first measures Chromium's outer/inner
height difference at 100%, then computes the window height. A fresh persistent
profile uses `partition.default_zoom_level = {x: log(1.25)/log(1.2)}`; `viewport:null`
prevents viewport emulation. Required observations include DPR 1→1.25,
`visualViewport.scale === 1`, approximately 1093×614 CSS pixels at 125%, actual PNG
dimensions and the exact preference object. There is no CDP pinch or emulated DPR.
`metrics.json` records calibration and actual browser version per image. The older
R1-5 1093×614 screenshots remain labeled CSS-equivalent, not retroactive native zoom.

Pinned dev-only `@axe-core/playwright@4.14.0` scans the complete rendered page. All
violations and incomplete rule IDs are recorded; serious/critical violations fail.
No rule is disabled and no arbitrary region is excluded. The MPL-2.0 test-tool
exception is recorded in `frontend/dependency-licenses.json`; axe is not an
application runtime import.

## Canonical baseline bootstrap and strict comparison

The committed default `visual-policy.json` is `{"mode":"compare"}`. Missing images
fail and are not written; a small real Playwright contract test verifies that behavior
without starting a browser. Zero differing pixels are permitted. CLI snapshot-update
flags are rejected by the runner.

After all new-page behavior is accepted, the owner can make a dedicated explicit
commit changing the policy to
`{"mode":"capture","reason":"R1 canonical visual baseline bootstrap"}`. The normal
branch push CI runs without a workflow-dispatch API. Capture is allowed only inside
GitHub Actions with Chromium 159.0.8154.6 and no executable override. Behavior, axe,
font, overflow and native zoom assertions still run. `runner.json` and each image's
metadata say `baseline-capture-not-acceptance`. A green capture run is not the final
acceptance gate.

The owner downloads and reviews the 33 small `r1-visual-LAYOUT-PAGE-RUN` artifacts.
Each includes the actual PNG, source/browser/native metrics, axe findings and a
SHA-256 index with a link to the complete artifact. For each reviewed
`visual/LAYOUT/MODE--PAGE/actual.png`, copy the image to
`e2e/visual-baselines/LAYOUT--MODE--PAGE.png` and its `metrics.json` to the same stem
with `.json`. Login's state stem is `login--unauthenticated`. Commit all 63 reviewed
PNGs and their provenance **together with resetting the policy to compare**.
Then the ordinary two-job CI must pass on that exact integrated SHA. Do not change
the protected/default branch or use hidden API calls to create a capture run.

Native visual contexts pin the official full Chromium channel (`channel: "chromium"`),
not the separate headless shell. `E2E_CHROMIUM=/usr/bin/chromium` is permitted for local behavioral diagnostics and
its actual version is reported. The explicit executable path overrides the bundled
executable for diagnostics only; capture rejects any executable override. It cannot
capture canonical baselines. Local
Chromium 156 results must never be relabeled canonical Chromium 159 evidence.

## Real upgrade/recovery and final offline dependencies

```sh
git worktree add --detach work/v070-source 1359118dd672431dcd0cde2eb81f2b0f994eef37
python scripts/drills/upgrade_from_v070.py --v070-source work/v070-source --output work/evidence/r1/ci/upgrade-drill.json
python scripts/drills/compare_identity_v070.py --v070-source work/v070-source --current-source . --output work/evidence/r1/ci/identity-v070-comparison.json
python ops/offline/prepare.py --output work/offline/ci --export-images
python ops/offline/verify.py --bundle work/offline/ci --evidence work/evidence/r1/offline
```

The independent offline CI job checks out the same SHA and prepares a fresh bundle
online, including the final axe dependency and every control/tenant/restore migration.
It does not cache source or previous results. Verification uses a MySQL container
with `--network none`; the install/build/start container shares only that isolated
network namespace. It proves no default route/public TCP connection, installs Python
with `--no-index` and npm with `--offline`, builds the frontend, executes migrations,
starts the real backend and fetches health, HTML and its built JS. The exact bundle
manifest, image IDs, source SHA, logs and observed results are uploaded. CI always
uses `--export-images`, so the dependency artifact includes `images.tar` for both the
tool and MySQL images. On another same-architecture Docker host, download and unpack
the complete dependency artifact, check out the recorded source SHA, then run the
verify command above: it verifies the manifest and loads `images.tar` before the
network-isolated installation. No registry access or pre-existing image IDs are
required. The large dependency artifact is separate from readable evidence packages.
Dependency changes require fresh preparation; old-lock cache reuse fails.

The six audit visual states use the real UI filters for the unique original event
id=11 / demo_loaded / business-demo-0.7 / dept-01 / success, verify the complete real
response and leave its details expanded. No audit content is masked, including its
fixed historical timestamp and request ID. The identity operation suite separately
tests latest records, pagination, and real mutation audits; visual selection neither
deletes audit rows nor substitutes a mock response.

## Readable and complete evidence

```sh
node e2e/evidence.mjs work/evidence/r1/e2e-legacy-ci work/e2e-ci-packages
node e2e/evidence.mjs work/evidence/r1/e2e-foundation-ci work/e2e-foundation-ci-packages
node e2e/evidence.mjs work/evidence/r1/e2e-visual-ci work/e2e-visual-ci-packages
node e2e/evidence.mjs work/evidence/r1/e2e-admin-ci work/e2e-admin-ci-packages
node e2e/evidence.mjs work/evidence/r1/e2e-legacy-replacements-ci work/e2e-replacements-ci-packages
python scripts/build_evidence.py --input work/evidence/r1/ci --input work/e2e-ci-packages --input work/e2e-foundation-ci-packages --input work/e2e-visual-ci-packages --input work/e2e-admin-ci-packages --input work/e2e-replacements-ci-packages --steps e2e/REPRODUCE.md --output work/packages/foundation
python scripts/build_evidence.py --input work/evidence/r1/offline-preparation --input work/evidence/r1/offline --steps e2e/REPRODUCE.md --output work/packages/offline
```

CI sets `E2E_COMPLETE_ARTIFACT_URL` after uploading the complete evidence. Small
browser packages have a hard 30 MiB budget including their index, leaving ZIP
overhead below the connector's 32 MiB limit. They omit authentication state, raw
trace archives and HTML reports; content and symlink checks apply before copying.
The explicit Python builder records SHA-256 per file, source SHA/version and run
metadata, plus manifest, archive and archive digest. It makes no inferred pass claim.
`backend-results.json` verifies all 489 frozen baseline IDs and exactly 39 permitted
Windows-native skips from the complete unittest output. Both foundation and offline
jobs must succeed for final R1 acceptance.

`historical-format.json` independently verifies the frozen formatting commit
`353b9b2784e84b006394c7b982a2002477676bb3` against its exact parent
`50e160bbb49cc7be25446e23de088e563cba68c3`: identical inventories of 151 Python files,
150 textual changes, 150 strict AST matches, 151 matches after only docstring
whitespace normalization, zero behavioral differences and no non-Python/out-of-scope
diff. Per-file before/after SHA-256 values are included. The historical
`ast_equiv.py` is unchanged; its successful exit code is not accepted as proof.

```sh
python scripts/checks/check_historical_format.py --output work/evidence/r1/ci/historical-format.json
```

The original replacement pages use an additional isolated `legacy` runner scope:
Overview/modeler, empty Login, Security/admin, Settings/admin and Administration/HQ.
Their five full-page screenshots are independent page packages, each subject to the
same 30 MiB content-plus-index budget, with a separate runner/environment/results
summary and the complete-artifact link. These are actual original `1359118` pages,
not a reconstruction of the old design. They supplement the unchanged original 75
and supply A.2 visual comparison evidence; they are not new-version visual baselines.

Authenticated visual states create a real fresh session with the same browser
context's request client before the first application navigation. The login request
uses the browser's actual user agent; cookies stay in its private profile and the
response body is never inspected or written. This avoids collecting the expected
unauthenticated bootstrap `/api/auth/session` 401 as an authenticated-page error.
Every console error (including any later 401) and uncaught page error still fails;
none are filtered or cleared. Login UI behavior remains in identity acceptance and
the three unauthenticated login screenshots.

Native screenshot correction: image dimensions are the complete **physical** viewport,
1366×768 (including the native 125% layout) or 1920×1080. They are not the CSS inner
viewport dimensions. Playwright 1.65.1's ordinary screenshot clip crops native zoom:
local Chromium 156 reproduced `device` 1093×614 with missing right/bottom corners,
and `css` 874×491. The helper therefore calls standard CDP `Page.captureScreenshot`
with `format: png`, `fromSurface: true`, `captureBeyondViewport: false`, no clip or
scale and no Emulation commands. It records `screenshotMethod: cdp-physical-viewport`
and `screenshotScale: physical`. PNG width/height must equal the expected physical
viewport exactly; profile/DPR/inner/visualViewport checks remain independent and strict.
Only explicit `data-visual-dynamic=timestamp|request-id` rectangles receive temporary
white overlays (audit passes an empty list). Carets and animations are temporarily
suppressed. DOM overlays/styles and animation state are restored and the CDP session
detached in finally, including capture errors. No screenshot is cropped or resized.

Expanded audit evidence naturally scrolls the complete fixed historical row into
view and requires both the row and JSON details to intersect the viewport at ratio 1.
This keeps the smaller native-125% image useful without masking, resizing, or changing
the real record. Review must also check that the fixed header does not cover the row.
