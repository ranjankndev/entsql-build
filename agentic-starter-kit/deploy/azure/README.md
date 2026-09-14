# Deploying the agent on Azure

End-to-end guide: from an empty subscription to a production agent with
identity-based auth, private data, tracing, evals in CI and a rollback path.

Everything below is done with `az` + Bicep. Nothing requires the portal.

---

## 0. Decide the hosting shape first

| Option | Pick it when | Skip it when |
| --- | --- | --- |
| **Azure Container Apps** (this guide) | HTTP agent, scale-to-zero in dev, no cluster to run, KEDA scaling, revisions for blue/green | you need DaemonSets, GPUs or a service mesh |
| Azure Kubernetes Service | you already run AKS, need node-level control or GPU inference | a single API is all you have |
| Azure Functions | short, event-driven turns under ~10 min, bursty and cheap | long agent loops, streaming responses, websockets |
| Azure App Service | lift-and-shift of an existing web app | you want per-revision traffic splitting |

Container Apps is the default here: an agent is a long-ish request/response
service with spiky traffic, which is exactly its sweet spot.

---

## 1. Prerequisites

```bash
az version                       # 2.60+
az extension add --name containerapp --upgrade
az login
az account set --subscription "<subscription-id>"
az provider register -n Microsoft.App -n Microsoft.OperationalInsights --wait
```

You need `Contributor` **and** `User Access Administrator` on the target
resource group (the template creates role assignments), plus quota for the
Azure OpenAI model in your chosen region. Check quota before anything else —
it is the single most common blocker:

```bash
az cognitiveservices usage list -l swedencentral -o table
```

---

## 2. What gets created

`deploy/azure/bicep/main.bicep` provisions, in one deployment:

| Resource | Why |
| --- | --- |
| User-assigned managed identity | one identity the app uses for *every* Azure dependency |
| Log Analytics + Application Insights | container logs, request telemetry, KQL |
| Azure OpenAI account + model deployment | the model; `disableLocalAuth: true` so there is no API key to leak |
| Cosmos DB (serverless in dev) | conversation turns and long-term memory, `disableLocalAuth: true` |
| Key Vault | third-party keys only (Langfuse); Azure services use the identity |
| Container Registry | the image, pulled with the identity (`adminUserEnabled: false`) |
| Container Apps environment + app | the agent, with liveness/readiness probes and HTTP scaling |

Role assignments granted to the identity: `Cognitive Services OpenAI User`,
`AcrPull`, `Key Vault Secrets User`, and the Cosmos **data-plane** built-in
Data Contributor (a separate RBAC system from ARM — a common trip-up).

---

## 3. Provision

```bash
export RG=rg-agentkit-dev LOCATION=swedencentral ENVIRONMENT=dev
./deploy/azure/scripts/provision.sh
```

The script runs `what-if` and waits for confirmation before applying, then
writes the outputs (app URL, ACR login server, endpoints) to
`deploy/azure/.outputs.dev.json`.

Then store the Langfuse keys:

```bash
./deploy/azure/scripts/set_secrets.sh
```

---

## 4. Build and ship the image

```bash
export RG=rg-agentkit-dev ENVIRONMENT=dev TAG=$(git rev-parse --short HEAD)
./deploy/azure/scripts/build_push.sh
```

`az acr build` builds inside Azure, so no local Docker daemon and no registry
credentials on your laptop. The script creates a new revision, then curls
`/healthz` and `/readyz`.

### Blue/green

Container Apps revisions give you this for free:

```bash
# ship the new revision with no traffic
az containerapp update -g $RG -n ca-agentkit-prod --image $IMAGE --revision-suffix r$(date -u +%s)
az containerapp ingress traffic set -g $RG -n ca-agentkit-prod \
  --revision-weight latest=0 --revision-weight <current-revision>=100

# smoke it on its own revision URL, then shift
az containerapp ingress traffic set -g $RG -n ca-agentkit-prod --revision-weight latest=10 <current>=90
az containerapp ingress traffic set -g $RG -n ca-agentkit-prod --revision-weight latest=100
```

Rollback is the same command with the weights swapped back — seconds, not a
redeploy.

---

## 5. Authentication

**Inside Azure — no keys.** The app reads `AZURE_CLIENT_ID` and uses
`DefaultAzureCredential`, which picks up the user-assigned identity. The same
code works locally with `az login`, which is why there is no separate local
auth path to maintain.

```python
from azure.identity import DefaultAzureCredential, get_bearer_token_provider

token_provider = get_bearer_token_provider(
    DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"
)
# pass `azure_ad_token_provider=token_provider` to AzureChatOpenAI instead of api_key
```

Set `AZURE_OPENAI_API_KEY` empty and wire the token provider in
`starter/llm/provider.py` when you flip `disableLocalAuth` on (the template
already does).

**In front of the app — authenticate callers.** Pick one:

* Container Apps built-in auth (Easy Auth) against Entra ID:
  ```bash
  az containerapp auth microsoft update -g $RG -n ca-agentkit-prod \
    --client-id <app-reg-id> --tenant-id <tenant> --yes
  az containerapp auth update -g $RG -n ca-agentkit-prod --unauthenticated-client-action Return401
  ```
* Or Azure API Management / Front Door in front, with subscription keys, rate
  limits and WAF. Use this if the agent is public.

---

## 6. Networking and data protection for production

```bash
# internal-only ingress, reachable through App Gateway/Front Door
az containerapp ingress update -g $RG -n ca-agentkit-prod --type internal
```

* Deploy the Container Apps environment into a VNet subnet
  (`infrastructureSubnetId`), then put **private endpoints** on Azure OpenAI,
  Cosmos DB and Key Vault and set `publicNetworkAccess: Disabled` on each.
* Customer-managed keys on Cosmos if your data classification requires it.
* The template sets a 30-day TTL on conversation documents. Match it to your
  retention policy and say so in your privacy notice — agent transcripts are
  personal data in most jurisdictions.

---

## 7. Observability in production

Two layers, both already wired:

1. **Langfuse** — prompt/response/tool traces, per-trace eval scores, cost.
   Cloud, or self-hosted on Container Apps with a Postgres flexible server if
   transcripts may not leave your tenancy.
2. **Application Insights / Log Analytics** — infrastructure truth: latency,
   replica count, restarts, 5xx. The `NoOpTracer` emits one JSON line per span,
   so even with Langfuse off you can answer questions in KQL:

```kusto
ContainerAppConsoleLogs_CL
| where Log_s has "run.finished"
| extend p = parse_json(Log_s)
| summarize runs=count(),
            blocked=countif(p.blocked == true),
            p95_latency=percentile(todouble(p.latency_ms), 95)
          by bin(TimeGenerated, 5m), tostring(p.stop_reason)
```

Alerts worth having on day one:

| Alert | Condition | Why |
| --- | --- | --- |
| Guardrail block rate | `blocked/runs > 5%` over 15 min | a prompt attack, or an over-tight policy |
| Loop exhaustion | `stop_reason == "max_iterations"` > 2% | the agent is thrashing; costs spike first |
| Tool error rate | `ok == false` > 5% | a downstream dependency is down |
| p95 latency | > 10 s | model throttling (429) or a slow tool |
| OpenAI 429s | any sustained | raise PTU/capacity; the client already retries with backoff and opens a circuit breaker (`LLM_*` settings) |

---

## 8. CI/CD

`.github/workflows/ci.yml` (tests + guardrail/smoke evals on every PR) and
`.github/workflows/deploy-azure.yml` (build → push → deploy → smoke) are
included. Authenticate with **OIDC federated credentials**, not a stored
service-principal secret:

```bash
az ad app create --display-name gh-agentkit
# add a federated credential for repo:<org>/<repo>:ref:refs/heads/main
az role assignment create --assignee <app-id> --role Contributor \
  --scope /subscriptions/<sub>/resourceGroups/$RG
```

Repository secrets: `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`,
`AZURE_SUBSCRIPTION_ID`. No passwords anywhere.

The deploy job runs the safety eval suite against the *deployed* URL before
shifting traffic. An agent that passes unit tests and fails its safety suite in
staging must not reach users.

---

## 9. Cost control

* Start serverless (Cosmos) and scale-to-zero (`minReplicas: 0`) in dev.
* Pin `AGENT_MAX_ITERATIONS` and `AGENT_MAX_TOOL_CALLS` — an unbounded loop is a
  billing incident, and these are enforced in code, not prompts.
* Budget alert on the resource group:
  ```bash
  az consumption budget create --budget-name agentkit-dev --amount 200 \
    --time-grain Monthly --resource-group $RG
  ```
* Azure OpenAI: pay-as-you-go until traffic is predictable, then Provisioned
  Throughput Units. Track cost per conversation in Langfuse, not per token —
  tokens per answer is what regressions move.

---

## 10. Teardown

```bash
az group delete -n $RG --yes --no-wait
# Key Vault and Azure OpenAI are soft-deleted; purge if you want the names back:
az keyvault purge --name <vault> --location $LOCATION
az cognitiveservices account purge -g $RG -n <account> -l $LOCATION
```

---

## Checklist before you call it production

- [ ] Ingress is internal or behind Entra ID / APIM
- [ ] `disableLocalAuth` on Azure OpenAI and Cosmos; no keys in env vars
- [ ] Private endpoints on OpenAI, Cosmos, Key Vault
- [ ] `minReplicas >= 2` and zone redundancy on
- [ ] Guardrail policy reviewed by whoever owns the risk, not just the author
- [ ] Safety eval suite green in the deploy pipeline, gate set to 1.0
- [ ] Alerts on block rate, loop exhaustion, tool errors, p95 latency, 429s
- [ ] Retention and deletion for transcripts documented and enforced by TTL
- [ ] Rollback rehearsed at least once
- [ ] Load tested at the target concurrency, and the scale rule matched to it (see [`docs/performance.md`](../../docs/performance.md))
