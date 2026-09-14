# Evaluation suites

| file | what it protects | runs with `LLM_PROVIDER=echo` |
| --- | --- | --- |
| `datasets/smoke.jsonl` | the wiring is alive: graph runs, memory persists, no crash | yes |
| `datasets/safety.jsonl` | guardrails block what they must and *don't* over-block | yes |
| `datasets/core.jsonl` | task quality: retrieval, tool choice, grounding, efficiency | needs a real model |

Add a case every time you fix a bug. A regression suite that only grows from
real incidents is worth more than a hand-written benchmark.

```bash
starter eval --suite evals/datasets/safety.jsonl --threshold 1.0
starter eval --suite evals/datasets/core.jsonl --threshold 0.85 --report .eval-runs
```

## Scorer selection

Set `scorers` per case. Defaults (when omitted): `contains_all`, `excludes_all`,
`refusal`, `tool_use`, `budget`, `guardrails`.

Add an LLM judge only where a deterministic scorer cannot express the criterion:

```python
from starter.evals import run_suite
from starter.evals.scorers import llm_judge
from starter.llm import get_llm

report = run_suite(
    "evals/datasets/core.jsonl",
    extra_scorers={"faithfulness": llm_judge(get_llm(), "The answer only states facts present in the tool results.")},
)
```

## CI gate

`.github/workflows/ci.yml` runs unit tests plus the smoke and safety suites at
threshold 1.0 on every PR. Core quality runs nightly against the real model so a
provider regression shows up without blocking merges on model variance.
