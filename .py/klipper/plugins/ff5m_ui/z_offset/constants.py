## Lightweight Z-offset constants used before declarative pages are loaded.

PAPER_STEPS = (0.005, 0.010, 0.025, 0.100, 0.500)
PAPER_DEFAULT_STEP = 0.010
Z_WEIGHT_DANGER = 400.0


if not PAPER_STEPS:
    raise ValueError("PAPER_STEPS must not be empty")
if len(set(PAPER_STEPS)) != len(PAPER_STEPS):
    raise ValueError("PAPER_STEPS must contain unique values")
if PAPER_DEFAULT_STEP not in PAPER_STEPS:
    raise ValueError("PAPER_DEFAULT_STEP must be present in PAPER_STEPS")
