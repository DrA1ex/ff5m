# Vision regression benchmark and semantic parity policy

## Goal

Make FF5M visual regression reports easier to inspect and compare every
locally available LM Studio vision model with no more than 12 billion
parameters against one identical saved parity corpus.

The visual reviewer must not request human attention solely because mock and
live runtime values differ. Exact values remain test requirements only when a
case baseline explicitly requires an exact or approximate value.

## Semantic comparison policy

Every observed difference belongs to one of three classes:

- `dynamic_runtime`: live or synthetic values such as temperatures, network
  addresses, clocks, progress, filenames, and runtime status text;
- `rendering_only`: permitted rasterization and other explicitly allowed
  renderer differences;
- `product_semantic`: page structure, controls, dialogs, selection, typed
  component state, missing content, clipping, overlap, or a value constrained
  by the textual baseline.

`dynamic_runtime` and `rendering_only` differences must pass when the value
keeps the expected role, plausible format, readability, and location.
`product_semantic` differences may produce `warn` or `fail` according to the
existing fixed checklist.

The OpenAI-compatible request will state this policy and require the model to
classify parity evidence before producing its verdict. A response that reports
only an allowed variation as a defect is semantically invalid and receives
the existing single corrective retry. The harness will not silently rewrite a
model verdict after validation.

## Screenshot-first report

The report keeps its existing standalone and Designer-to-printer grids,
problem highlighting, filters, and detail dialog. The minimum visual size is
doubled:

- a standalone tile targets approximately 480 CSS pixels;
- a parity pair targets approximately 720 CSS pixels.

Responsive rules continue to reduce the column count on narrow screens. Image
files are not resampled or duplicated; only their default presentation size
changes.

## Model selection and execution

The benchmark discovers downloaded models through the local LM Studio model
catalog. A candidate is eligible only when:

- its declared capabilities include vision;
- its declared parameter count can be parsed and is no greater than `12B`;
- it is already present locally.

Unknown parameter counts are excluded rather than guessed. No models,
packages, or services are downloaded.

Each eligible model runs sequentially against the same saved parity corpus:

1. confirm no prior test model remains loaded;
2. load one model;
3. record load duration and memory information;
4. execute one ordinary single-model OpenAI-compatible regression;
5. save its independent JSON and HTML reports;
6. record total runtime, per-frame latency, JSON validity, verdicts, review
   rate, and errors;
7. unload the model and confirm the instance is gone before continuing.

The regression remains provider-neutral. LM Studio native model-management
operations are an external benchmark procedure and are not imported by the
visual-check runtime.

## Measurements and ranking

The comparison includes:

- valid structured-response rate;
- pass, warning, and failure counts;
- false review rate on permitted parity variations;
- normalized errors;
- model load time;
- full corpus wall time;
- mean model-request latency;
- reported or estimated model memory.

Correctness ranks before speed and memory. A model with schema errors, missed
control defects, or repeated false reviews cannot win solely by being faster.
Among models with equivalent correctness, lower latency and memory determine
the recommendation.

## Safety and artifacts

The benchmark uses saved screenshots only and does not contact the printer.
Endpoint credentials stay in the ignored local environment file and are never
written to reports or command output. Generated reports and benchmark
measurements remain under the ignored `tests/artifacts/` directory.

Host-side tests cover the semantic policy, corrective retry contract, doubled
grid sizing, and report comparison fields. Existing deterministic and UI
contract tests remain the primary regression checks.
