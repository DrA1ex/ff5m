# Vision Regression Benchmark Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent false visual-regression reviews for permitted live/mock
value differences, enlarge screenshot grids twofold, and benchmark every
downloaded LM Studio vision model up to 12B parameters on one saved parity
corpus with load/unload isolation.

**Architecture:** The provider-neutral OpenAI-compatible evaluator gains a
strict evidence classification and rejects non-pass verdicts based only on
allowed variations. The offline HTML renderer changes presentation size only.
A separate host-only LM Studio benchmark command owns catalog filtering,
load/unload lifecycle, measurements, and subprocess execution of the existing
single-model regression.

**Tech Stack:** Python standard library, `unittest`, OpenAI-compatible HTTP,
optional local LM Studio REST API/CLI, offline HTML/CSS.

## Global Constraints

- All visual-check code remains under `tests/` and is never imported by
  printer, Feather, Klipper, startup, deployment, or sync runtime.
- No Python package, model, service, or system dependency is downloaded.
- One explicitly selected model is evaluated per regression report.
- Models with unknown parameter count or a declared count above `12B` are
  excluded.
- The printer is not contacted; every model uses the same saved parity
  artifacts.
- Credentials and endpoint addresses are never serialized or logged.
- Every new source file receives the standard 2026 GPLv3 copyright header.

---

### Task 1: Semantic evidence contract

**Files:**
- Modify: `tests/visual_checks/openai_compatible.py:247-617`
- Modify: `tests/test_feather_ui_vision.py:35-52`
- Test: `tests/test_feather_ui_vision.py`

**Interfaces:**
- Consumes: existing `CHECKLIST`, `_completion_payload()`,
  `validate_verdict()`, and the evaluator corrective-retry loop.
- Produces: required `evidence_class` values `none`, `dynamic_runtime`,
  `rendering_only`, and `product_semantic` on every checklist result.

- [ ] **Step 1: Write a failing allowed-variation retry test**

Add a fake first response whose non-pass check is classified
`dynamic_runtime`, followed by a valid all-pass response:

```python
def test_allowed_runtime_difference_is_retried_instead_of_reviewed(self):
    first = verdict(
        "warn", evidence_class="dynamic_runtime",
        reason="Only live footer temperatures differ.")
    with FakeOpenAIEndpoint(
            ("vision-a",),
            {"vision-a": [first, verdict("pass")]}) as server:
        result = server.evaluator(enabled_settings(server)).evaluate(
            b"designer", "image/png", {
                "_comparison_image": (b"printer", "image/png"),
            })

    model = result["models"][0]
    self.assertEqual(model["verdict"], "pass")
    self.assertEqual(model["attempts"], 2)
```

The production change this catches is accepting a warning that cites only a
permitted live/mock value difference.

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
python3 -m unittest \
  tests.test_feather_ui_vision.FeatherVisionTests.test_allowed_runtime_difference_is_retried_instead_of_reviewed
```

Expected: FAIL because `evidence_class` is not part of the response contract
and allowed evidence is not rejected.

- [ ] **Step 3: Write a failing product-semantic acceptance test**

Add a response with `evidence_class="product_semantic"` and a genuine
non-pass reason, then assert it remains a valid warning. This prevents the
policy from hiding missing controls, clipping, typed-state mismatch, or an
explicit numeric requirement.

- [ ] **Step 4: Run the product-semantic test and verify RED**

Run the single new test. Expected: FAIL because the schema does not accept or
preserve `evidence_class`.

- [ ] **Step 5: Implement the minimal response contract**

In `openai_compatible.py`:

```python
EVIDENCE_CLASSES = frozenset((
    "none", "dynamic_runtime", "rendering_only", "product_semantic",
))
ALLOWED_VARIATION_EVIDENCE = frozenset((
    "dynamic_runtime", "rendering_only",
))
```

Require `evidence_class` in `_response_schema()` and
`_response_json_schema()`. In `validate_verdict()` reject:

```python
if status != "pass" and evidence_class in ALLOWED_VARIATION_EVIDENCE:
    raise ValueError(
        "allowed runtime or rendering variation cannot be non-pass")
```

Preserve the field in normalized checks and non-pass reasons. Expand the task
payload and system instruction with these rules:

- live/mock values are semantic slots, not literals;
- compare plausible format, role, readability, and location;
- exact or approximate numbers are strict only when explicitly named by the
  textual expectation;
- permitted differences must pass and cannot be mentioned as defects;
- structural, content, dialog, selection, typed-state, clipping, overlap, and
  explicitly constrained value defects use `product_semantic`.

The corrective-retry instruction must tell the model that a previous response
may have misclassified permitted evidence, not only violated JSON shape.

- [ ] **Step 6: Update existing fake verdict fixtures**

Make the shared `verdict()` helper emit `evidence_class="none"` for pass
checks and `product_semantic` for ordinary non-pass checks. Update literal
response fixtures to mirror the complete schema.

- [ ] **Step 7: Run the semantic profile and verify GREEN**

Run:

```bash
python3 -m unittest tests.test_feather_ui_vision
```

Expected: all visual-check tests pass.

- [ ] **Step 8: Commit**

```bash
git add tests/visual_checks/openai_compatible.py \
  tests/test_feather_ui_vision.py
git commit -m "Make visual parity ignore permitted runtime values"
```

---

### Task 2: Twofold screenshot grid

**Files:**
- Modify: `tests/visual_checks/html_report.py:481-531`
- Modify: `tests/test_feather_ui_vision.py:776-853`
- Modify: `openwiki/testing-and-change-guide.md:223-242`

**Interfaces:**
- Consumes: existing `.shot-grid` and `.shot-grid.pair-grid` report classes.
- Produces: 480 px standalone and 720 px parity minimum desktop tile widths.

- [ ] **Step 1: Write the failing report behavior test**

Render the existing report fixture and assert the generated CSS contains:

```python
self.assertIn(
    ".shot-grid{display:grid;grid-template-columns:"
    "repeat(auto-fill,minmax(480px,1fr))", page)
self.assertIn(
    ".shot-grid.pair-grid{grid-template-columns:"
    "repeat(auto-fill,minmax(720px,1fr))", page)
```

The production change this catches is a regression to thumbnails too small
for direct human parity inspection.

- [ ] **Step 2: Run the focused report test and verify RED**

Run:

```bash
python3 -m unittest \
  tests.test_feather_ui_vision.FeatherVisionTests.test_html_report_contains_images_baselines_and_model_evidence
```

Expected: FAIL with the current 240 px and 360 px values.

- [ ] **Step 3: Implement the minimum CSS change**

Change the desktop grid minimums to 480 px and 720 px. Keep the current mobile
rules, object-fit behavior, dialog, filters, and source separation unchanged.

- [ ] **Step 4: Update technical documentation**

Document that the default report deliberately prioritizes screenshot
inspection with doubled cards and fewer columns. Do not add internal details
to root user documentation.

- [ ] **Step 5: Run the focused report test and verify GREEN**

Run the same command. Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add tests/visual_checks/html_report.py \
  tests/test_feather_ui_vision.py \
  openwiki/testing-and-change-guide.md
git commit -m "Enlarge visual regression screenshots"
```

---

### Task 3: Isolated LM Studio benchmark command

**Files:**
- Create: `tests/visual_checks/lmstudio_benchmark.py`
- Modify: `tests/test_feather_ui_vision.py`
- Modify: `tests/visual_checks/compare_reports.py`
- Modify: `openwiki/testing-and-change-guide.md`

**Interfaces:**
- Consumes: local LM Studio `GET /api/v1/models`,
  `POST /api/v1/models/load`, `POST /api/v1/models/unload`, the existing
  `tests.visual_checks.regression` CLI, and `compare_reports.summarize()`.
- Produces:
  - `parse_parameter_billions(value: str) -> float | None`;
  - `eligible_models(catalog: dict, max_billions: float) -> list[dict]`;
  - `BenchmarkRunner.run(models: list[dict]) -> dict`;
  - ignored `benchmark.json` with one isolated record per model.

- [ ] **Step 1: Write failing catalog-filter tests**

Use a complete literal native catalog fixture containing:

- a vision `4B` model;
- a vision `12B` model;
- a vision `12.1B` model;
- a non-vision `7B` model;
- a vision model with unknown parameters.

Assert only the first two are eligible, ordered by catalog key, and no
heuristic name matching is used.

- [ ] **Step 2: Run the catalog tests and verify RED**

Run the two new tests. Expected: import failure because
`lmstudio_benchmark.py` does not exist.

- [ ] **Step 3: Implement catalog parsing and safe HTTP management**

Create the new file with the standard copyright header. Use only
`argparse`, `json`, `os`, `pathlib`, `subprocess`, `time`, and
`urllib.request`. Derive the native API origin from the configured
OpenAI-compatible `/v1` URL without serializing it. Send the API key only in
the Authorization header.

Reject malformed catalogs, unsupported parameter suffixes, zero/negative
sizes, and non-vision candidates. Never call a download endpoint.

- [ ] **Step 4: Write a failing lifecycle-order test**

Use a fake management transport and fake regression runner. Assert exact event
order for two models:

```text
load(model-a), regression(model-a), unload(instance-a),
load(model-b), regression(model-b), unload(instance-b)
```

Make the first fake regression fail and assert unload still occurs in
`finally` before model B loads.

- [ ] **Step 5: Run the lifecycle test and verify RED**

Expected: FAIL because lifecycle orchestration is absent.

- [ ] **Step 6: Implement sequential lifecycle and measurements**

For every eligible model:

- load with a fixed 8192-token context and echoed load configuration;
- measure load wall time and keep LM Studio's `load_time_seconds`;
- record catalog `size_bytes`;
- optionally run `lms load --estimate-only <key> --context-length 8192` and
  parse estimated total memory without treating estimator absence as fatal;
- invoke the existing parity regression in a subprocess with an explicit
  output directory and one `--model`;
- summarize its report;
- record complete wall time;
- unload the returned `instance_id` in `finally`;
- query the catalog and fail the model record if that instance remains loaded.

Continue after a model-specific load, inference, schema, or unload error, but
never begin the next model until unload verification completes.

- [ ] **Step 7: Extend read-only report comparison**

Permit `compare_reports.summarize()` to include optional benchmark metadata:

```python
{
    "load_time_seconds": ...,
    "wall_time_seconds": ...,
    "model_size_bytes": ...,
    "estimated_memory_bytes": ...,
}
```

Missing metadata remains `None`, preserving existing report compatibility.

- [ ] **Step 8: Run focused benchmark tests and verify GREEN**

Run:

```bash
python3 -m unittest tests.test_feather_ui_vision
```

Expected: all visual-check and benchmark tests pass without opening sockets,
loading models, or contacting a printer.

- [ ] **Step 9: Document the explicit benchmark command**

Add a technical example using placeholders for Designer root and saved
printer artifacts. State the `≤12B` fail-closed filter, one-model lifecycle,
no-download rule, metrics, ignored output, and that the command never contacts
the printer.

- [ ] **Step 10: Commit**

```bash
git add tests/visual_checks/lmstudio_benchmark.py \
  tests/visual_checks/compare_reports.py \
  tests/test_feather_ui_vision.py \
  openwiki/testing-and-change-guide.md
git commit -m "Add isolated local vision model benchmark"
```

---

### Task 4: Local semantic-review rule

**Files:**
- Modify: ignored local task-instruction file

**Interfaces:**
- Produces: future visual-review tasks apply the same dynamic-value policy.

- [ ] **Step 1: Add the local rule without changing tracked files**

Record:

- live/mock values may differ between Designer and printer;
- compare their role, plausible format, readability, and position;
- do not warn solely for a permitted value difference;
- require exact or approximate equality only when a reviewed textual baseline
  explicitly constrains the value;
- never weaken checks for structure, controls, dialogs, selection, typed state,
  clipping, overlap, or missing content.

- [ ] **Step 2: Verify it remains excluded**

Run:

```bash
git check-ignore -v <local-instruction-path>
git status --short
```

Expected: the local file is ignored and absent from status.

---

### Task 5: Verification and real local benchmark

**Files:**
- Generated only: `tests/artifacts/ui-regression-benchmarks/<timestamp>/`

**Interfaces:**
- Consumes: two saved printer artifact directories from the last completed
  parity capture and every eligible downloaded local model.
- Produces: one HTML/JSON report per model plus a comparison JSON.

- [ ] **Step 1: Run fresh source verification**

Run:

```bash
python3 -m unittest tests.test_feather_ui_vision
python3 -m unittest discover -s tests
git diff --check
```

Expected: profile and full FF5M suite pass, and whitespace validation exits 0.

- [ ] **Step 2: Start only the local LM Studio service**

Start the already installed service if it is not running. Do not download or
update LM Studio, libraries, or models.

- [ ] **Step 3: Inspect and record the eligible catalog**

Use the native catalog capabilities and `params_string`; report all exclusions
with non-secret reasons. Abort rather than guess when no eligible models are
found.

- [ ] **Step 4: Run the benchmark**

Invoke the new command with the saved UI and COMPONENT artifacts from the
latest complete parity run. Confirm each model's log shows unload verification
before the next load.

- [ ] **Step 5: Inspect every HTML and JSON report**

For each model verify:

- the corpus count and case IDs match;
- JSON validation and errors are accounted for;
- permitted footer/live-value differences do not create reviews;
- any warning/failure cites product-semantic evidence;
- all image links resolve;
- timings and memory measurements are present or explicitly marked
  unavailable.

- [ ] **Step 6: Rank models**

Rank correctness first, then false-review rate, schema validity, latency,
memory, and load time. State any tie or measurement limitation explicitly.

- [ ] **Step 7: Final repository check**

Run:

```bash
git status --short
git log -5 --oneline
```

Expected: tracked implementation is committed; generated reports and local
configuration remain ignored.
