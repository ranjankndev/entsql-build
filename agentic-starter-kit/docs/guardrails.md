# Guardrails

## Why three stages

| Stage | What arrives | What you are defending against |
| --- | --- | --- |
| `INPUT` | user text | jailbreaks, injection, out-of-scope requests, PII you must not store |
| `TOOL` | call arguments, then tool results | over-broad calls, and **injection arriving inside retrieved content** — the stage most projects forget |
| `OUTPUT` | the final answer | leaked secrets, leaked PII, unsafe content, missing disclaimers |

A guard that only runs on user input protects against the least sophisticated
attack. If your agent reads a web page, a ticket, or a document, that text is
untrusted input and goes through `TOOL`.

## Policy file

`config/guardrails.yaml` is the source of truth. Order matters — guards run in
file order and each may rewrite the text for the next.

```yaml
fail_mode: block          # block | flag
block_message: "..."
guards:
  - name: max_length
    options: { max_chars: 16000 }
  - name: pii
    options: { action: redact, kinds: [email, phone, credit_card] }
  - name: tool_allowlist
    enabled: true
    options: { allowed: [search_kb, calculator] }
```

## Shadow mode

Never enforce a new guard straight into production. Set `fail_mode: flag`,
deploy, and read the events for a week:

```kusto
ContainerAppConsoleLogs_CL
| where Log_s has "guard"
| extend p = parse_json(Log_s)
| summarize hits = count() by tostring(p.metadata.guard), tostring(p.metadata.blocked)
```

A guard's false-positive rate matters as much as its catch rate: over-blocking
is the failure mode users actually notice. `evals/datasets/safety.jsonl`
includes `safety-benign-not-refused` for exactly this reason — keep adding
benign cases as you tighten policy.

## Severities

| Severity | Effect |
| --- | --- |
| `INFO` | recorded; may rewrite text (e.g. the disclaimer guard) |
| `WARN` | recorded; may rewrite text (e.g. PII redaction) |
| `BLOCK` | raises `GuardrailViolation` under `fail_mode: block`, or is recorded and stops the chain under `flag` |

## Writing a guard

```python
from starter.guardrails import GuardResult, Stage, register_guard

@register_guard("business_hours")
class BusinessHoursGuard:
    name = "business_hours"
    stages = (Stage.TOOL,)

    def __init__(self, tools: list[str] | None = None):
        self.tools = set(tools or [])

    def check(self, text, ctx):
        if ctx.tool_name in self.tools and not is_business_hours():
            return GuardResult.fail(self.name, "this tool is disabled out of hours")
        return GuardResult.ok(self.name)
```

Rules for guards: deterministic where possible, fast (they run on every turn),
never raise, and never call a model unless the guard's whole purpose is a
model-based judgement.

## Coverage this kit ships with

| Threat | Guard | Notes |
| --- | --- | --- |
| Prompt injection / jailbreak | `prompt_injection` | heuristic; pair with a classifier for high-risk deployments |
| PII in prompts or answers | `pii` | redacts by default, can block |
| Credential leakage | `secret_leak` | output and tool stages |
| Harmful topics | `denied_topics` | keyword floor; add a moderation API above it |
| Tool abuse | `tool_allowlist` | plus `risk` and `requires_approval` on the tool itself |
| Context flooding / cost | `max_length` | and the loop budgets in `settings.py` |
| Missing compliance text | `required_disclaimer` | off by default |

What it does **not** give you: semantic topic classification, multilingual
toxicity, or model-based groundedness checking. Those are a moderation API or
an LLM judge — add them as guards behind the same interface.
