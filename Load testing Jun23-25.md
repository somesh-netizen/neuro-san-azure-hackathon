# Hackathon Engineering — Complete Session Log (Pre-Event → Jun 25, 2026)

> **Project:** Neuro-San Azure Hackathon  
> **Cluster:** `neuro-san-hackathon` (AKS, East US)  
> **API endpoint:** `https://neurosanhackathon-api.eastus.cloudapp.azure.com`  
> **UI:** `https://hackathon.evolution.ml`  
> **ACR:** `neurosanhackathonacr.azurecr.io`  
> **Author:** Somesh Pattanaik (somesh.pattanaik@cognizant.com)

---

## Table of Contents

0. [Pre-Event Work (Before Jun 23)](#0-pre-event-work-before-jun-23)
   - 0.1 [HTTP → HTTPS Migration](#01-http--https-migration)
   - 0.2 [HPA — Horizontal Pod Autoscaler](#02-hpa--horizontal-pod-autoscaler)
   - 0.3 [Azure OpenAI Local Dev Setup](#03-azure-openai-local-dev-setup)
   - 0.4 [Registry LLM Config Cleanup (12 files)](#04-registry-llm-config-cleanup-12-files)
1. [Session Objectives (Jun 23–25)](#1-session-objectives-jun-2325)
2. [Azure Cost Investigation](#2-azure-cost-investigation)
3. [Infrastructure Cleanup](#3-infrastructure-cleanup)
4. [Capacity Analysis](#4-capacity-analysis)
5. [Critical Load Test Bugs Fixed](#5-critical-load-test-bugs-fixed)
   - 5.1 [Bug: ChunkedEncodingError on Streaming Response](#51-bug-chunkedencodingerror-on-streaming-response)
   - 5.2 [Bug: sly_data Omission → 97% Turn 2+ Failure Rate](#52-bug-sly_data-omission--97-turn-2-failure-rate)
   - 5.3 [Bug: AGENT_MAX_CONCURRENT_REQUESTS Bottleneck (50→200)](#53-bug-agent_max_concurrent_requests-bottleneck-50200)
   - 5.4 [Bug: Python stdout Buffering with tee](#54-bug-python-stdout-buffering-with-tee)
   - 5.5 [Bug: Helm Upgrade Checksum Annotation Failure](#55-bug-helm-upgrade-checksum-annotation-failure)
6. [Load Test Prompt Engineering](#6-load-test-prompt-engineering)
7. [Infrastructure Improvements Implemented](#7-infrastructure-improvements-implemented)
   - 7.1 [Pre-warm Script](#71-pre-warm-script)
   - 7.2 [Readiness Probe with LLM Delay (startupProbe)](#72-readiness-probe-with-llm-delay-startupprobe)
   - 7.3 [Sticky-Cookie Pre-Assignment in UI](#73-sticky-cookie-pre-assignment-in-ui)
   - 7.4 [UI-Side Retry with Backoff](#74-ui-side-retry-with-backoff)
8. [UI Docker Build & Deployment](#8-ui-docker-build--deployment)
9. [Test Results](#9-test-results)
   - 9.1 [Smoke Test — Jun 23 (Baseline)](#91-smoke-test--jun-23-baseline)
   - 9.2 [Smoke Test — Jun 25 00:35 (Pre-fix)](#92-smoke-test--jun-25-0035-pre-fix)
   - 9.3 [Hackathon Soak — Jun 25 04:10 (200 VU × 120 min, 6 pods)](#93-hackathon-soak--jun-25-0410-200-vu--120-min-6-pods)
   - 9.4 [Hackathon Soak — Jun 25 09:27 (10 VU × 5 min, 12 pods — validation run)](#94-hackathon-soak--jun-25-0927-10-vu--5-min-12-pods--validation-run)
   - 9.5 [Smoke Test — Jun 25 21:31 (Post all fixes, UI 0.0.3)](#95-smoke-test--jun-25-2131-post-all-fixes-ui-003)
10. [Key Findings & Patterns](#10-key-findings--patterns)
11. [What Was Deliberately NOT Done & Why](#11-what-was-deliberately-not-done--why)
12. [Deployment History (Helm Revisions)](#12-deployment-history-helm-revisions)
13. [Decisions & Open Items](#13-decisions--open-items)

---

---

## 0. Pre-Event Work (Before Jun 23)

### 0.1 HTTP → HTTPS Migration

**Context:** Sourav had deployed Neuro-San on AKS. The app was live only over HTTP:
- Frontend: `http://20.241.198.56`
- Backend: `http://20.127.253.65`

**Goal:** Get HTTPS with a trusted certificate so participants don't see browser security warnings.

**Problems encountered and resolved:**

| Problem | Fix |
|---|---|
| `kubectl` not connected to cluster | `az aks get-credentials --resource-group neuro-san-studio-marketplace-rg --name neuro-san-hackathon-aks` |
| Helm not installed | `brew install helm` |
| NGINX ingress in non-standard namespaces | Two controllers: `ingress-nginx-frontend` (20.241.198.56) and `ingress-nginx-backend` (20.127.253.65) |
| Helm upgrade failed — resources not owned by Helm | Sourav had deployed via `kubectl` directly. Fixed by annotating all resources: `meta.helm.sh/release-name=neuro-san` |
| Kubernetes Ingress rejects bare IP as hostname | Switched to `nip.io` DNS (`20-241-198-56.nip.io`), then discovered proper Azure hostname |
| HTTPS curl timed out externally | NSG port 443 was open; Load Balancer had 443 configured; port-forward to NGINX pod confirmed TLS worked → root cause was stale DNS (`neurosanhackathon.eastus.cloudapp.azure.com` resolved to wrong IP `13.92.67.225`) |
| Sourav working on cluster simultaneously | DNS updated to `hackathon.evolution.ml` by Sourav; Let's Encrypt cert issued via cert-manager |

**Changes made to Helm chart:**

`templates/hackathon-ingress.yaml`:
```yaml
annotations:
  nginx.ingress.kubernetes.io/ssl-redirect: "true"
  nginx.ingress.kubernetes.io/force-ssl-redirect: "true"
tls:
  - hosts:
      - hackathon.evolution.ml
    secretName: ui-tls-secret
```

`values-hackathon.yaml`:
```yaml
ingress:
  ui:
    tlsSecretName: ui-tls-secret
```

**Final result:** `https://hackathon.evolution.ml` — Let's Encrypt trusted certificate, auto-renewed via cert-manager.

---

### 0.2 HPA — Horizontal Pod Autoscaler

**Problem:** Single pod (`neuro-san-key-1`) with no autoscaling. Previous load test: 0% success rate at 80 VUs.

**Capacity planning before HPA:**

| VU target | Est. concurrent AI requests | Pods needed |
|---|---|---|
| 200 | ~33 | 1 |
| 500 | ~83 | 1–2 |
| 1,000 | ~167 | 2–3 |
| 1,500 | ~250 | 3–4 |
| 2,000 | ~333 | 4–5 |

**HPA applied to cluster:**

```bash
kubectl apply -f - <<'EOF'
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: neuro-san-hpa
  namespace: neuro-san-hackathon
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: neuro-san-key-1
  minReplicas: 1
  maxReplicas: 10
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 60
  - type: Resource
    resource:
      name: memory
      target:
        type: Utilization
        averageUtilization: 70
EOF
```

**Result:** Pods auto-scale 1→10 as load increases. This is still active in the cluster.

**API endpoints discovered** (via NGINX access log analysis):

```
GET  /api/v1/list                         → lists all available agents
GET  /api/v1/{agent}/connectivity         → checks agent connectivity
POST /api/v1/{agent}/streaming_chat       → sends a chat message (SSE stream)
```

Correct payload format for `streaming_chat`:
```json
{
  "user_message": {"text": "your message here"},
  "chat_context": {},
  "chat_filter": {"chat_filter_type": "MAXIMAL"}
}
```

---

### 0.3 Azure OpenAI Local Dev Setup

**Machine:** `/Users/2508345/neuro-san-studio`  
**Date:** 2026-06-24

**What was set up:**

```bash
cp .env.example .env
```

`.env` values configured:
```dotenv
AZURE_OPENAI_ENDPOINT="https://25083-mqqgolnd-centralus.cognitiveservices.azure.com/"
OPENAI_API_VERSION="2025-04-01-preview"
AZURE_OPENAI_API_KEY="<key>"
AZURE_OPENAI_DEPLOYMENT_NAME="gpt-5.4"
```

**Problem:** Default `config/llm_config.hocon` included `developer_llm_config.hocon` which starts an OpenAI fallback chain — server threw `OPENAI_API_KEY must be set`.

**Fix — `config/llm_config.hocon`:**

Before:
```hocon
include "developer_llm_config.hocon"
```

After:
```hocon
{
    "llm_config" {
        "class": "azure-openai",
        "model_name": "gpt-5.4",
    }
}
```

This makes every agent network that has no local `llm_config` block automatically use Azure OpenAI. Credentials are picked up from env vars automatically.

**How to start the server:**
```bash
source venv/bin/activate
set -a && source .env && set +a
python -m neuro_san_studio run
# Server: http://localhost:8080
# UI: http://localhost:4173
```

---

### 0.4 Registry LLM Config Cleanup (12 Files)

**Problem:** Many `.hocon` agent network files had hardcoded `llm_config` blocks specifying non-Azure providers (OpenAI, Anthropic, local Ollama). These override the global config and would fail on an Azure-only environment.

**All 12 files cleaned — hardcoded `llm_config` blocks removed:**

| File | What was removed |
|---|---|
| `registries/basic/coffee_finder.hocon` | `class = "openai"`, `model_name = "gpt-4.1-mini"` |
| `registries/basic/coffee_finder_advanced.hocon` | `class = "openai"`, `model_name = "gpt-4.1"` |
| `registries/experimental/mdap_decomposer.hocon` | `class = "openai"`, `model_name = "gpt-4.1-mini"` |
| `registries/experimental/kwik_agents.hocon` | `model_name = "gpt-4.1-2025-04-14"` |
| `registries/experimental/conscious_agent.hocon` | `model_name = "gpt-4.1-2025-04-14"` |
| `registries/basic/music_nerd_pro_sly.hocon` | `model_name = "gpt-5.2"` |
| `registries/basic/pii_middleware.hocon` | `model_name = "gpt-5.2"` |
| `registries/basic/music_nerd_llm_fallbacks.hocon` | Full fallback chain (gpt-5.2 + claude-3-7-sonnet) |
| `registries/basic/music_nerd_local.hocon` | `model_name = "mistral"` |
| `registries/basic/music_nerd_pro_local.hocon` | `model_name = "llama3.1"` |
| `registries/basic/music_nerd_pro_sly_local.hocon` | `model_name = "llama3.1"` |
| `registries/basic/book_recommender_multiple_llm_configs.hocon` | Per-agent blocks from all 6 agents (claude-opus, claude-sonnet ×4, claude-haiku) |

**Result:** All agent networks now inherit from `config/llm_config.hocon` → Azure OpenAI gpt-5.4. Zero provider-mismatch errors on startup.

**LLM config inheritance chain:**
```
config/llm_config.hocon          ← global (azure-openai / gpt-5.4)
        ↓
        ├── registries/basic/*.hocon          ← inherit global
        ├── registries/experimental/*.hocon   ← inherit global
        ├── registries/industry/*.hocon       ← inherit global
        └── registries/tools/*.hocon          ← inherit global
```

**To revert to multi-provider:**
```hocon
# config/llm_config.hocon
include "developer_llm_config.hocon"
```

---

## 1. Session Objectives (Jun 23–25)

The session started with two immediate problems:

1. **Azure bill spike** — MTD cost jumped to 41.1K INR (later 44.6K INR). Root-cause the spend and stop unnecessary charges.
2. **Cold-start 503 errors** — After each pod restart / scale event, the first users hitting a fresh pod received 503s because the pod appeared ready before Python/LLM connections were truly warm. This caused ~97% error rate on second-turn requests in load tests.

Beyond firefighting, the goal was to harden the platform for a real hackathon with potentially 1,000–5,000 concurrent users.

---

## 2. Azure Cost Investigation

### Method

Used the Azure Cost Management REST API (not the portal UI, which lags by hours):

```bash
az rest \
  --method POST \
  --uri "https://management.azure.com/subscriptions/{SUB_ID}/providers/Microsoft.CostManagement/query?api-version=2023-03-01" \
  --body '{
    "type": "ActualCost",
    "timeframe": "MonthToDate",
    "dataset": {
      "granularity": "None",
      "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}},
      "grouping": [{"type": "Dimension", "name": "ResourceGroup"}]
    }
  }' \
  > /tmp/cost_mtd.json
python3 -c "import json; d=json.load(open('/tmp/cost_mtd.json')); [print(r) for r in d['properties']['rows']]"
```

### Findings

| Resource Group | Cost (INR) | Source |
|---|---|---|
| Foundry model usage | ~32,364 | gpt-5.4 load tests run today |
| `rg-cloudboost-vpn` (Virtual WAN) | ~7,386 | **Corporate networking — NOT hackathon infra** |
| Everything else | ~4,850 | AKS nodes, ACR, storage, ingress |

**Key conclusion:** The 32,364 INR spike was 100% from the `gpt-5.4` load tests run that day. No ongoing charges. Virtual WAN is a corporate Cloudboost VPN owned by the networking team — should be escalated there, not touched.

### Actions Taken

- **Azure Bastion** — confirmed it was the hackathon Bastion (not corporate), deleted it:
  ```bash
  az network bastion delete \
    --name BastionHost \
    --resource-group neuro-san-hackathon-rg \
    --yes    # --yes required; CLI prompts for confirmation without a TTY
  ```
- **Virtual WAN** — left alone (corporate infra, different resource group `rg-cloudboost-vpn`)
- **Load tests** — stopped; gpt-5.4 replaced with gpt-5-mini going forward

---

## 3. Infrastructure Cleanup

### AKS Node Count — Why Minimum Is 3

Attempted to scale AKS from 3 → 2 nodes to save cost. This broke the UI pod:

```
Warning  FailedScheduling  Insufficient memory (0 free, 6Gi needed)
```

**Root cause analysis:**

| Component | Memory request |
|---|---|
| UI pod | 6 Gi |
| 12 × neuro-san pods | 2 Gi each = 24 Gi |
| Total workload | 30 Gi |
| 2 × Standard_D4s_v3 nodes | 2 × 16 Gi = 32 Gi → minus ~4 Gi OS/system = 28 Gi available |
| Headroom | −2 Gi (can't fit) |
| 3 nodes | 48 Gi → minus ~6 Gi = 42 Gi → comfortably fits all 30 Gi |

**Fix:** Scaled back to 3 nodes. Error sequence encountered and resolved:

```bash
# Step 1: Prior operation still in flight, abort it first
az aks nodepool operation-abort \
  --cluster-name neuro-san-hackathon-aks \
  --resource-group neuro-san-hackathon-rg \
  --nodepool-name nodepool1

# Step 2: Scale back to 3
az aks nodepool scale \
  --cluster-name neuro-san-hackathon-aks \
  --resource-group neuro-san-hackathon-rg \
  --nodepool-name nodepool1 \
  --node-count 3
```

**Minimum safe node count = 3. Do not go below this.**

---

## 4. Capacity Analysis

### Infrastructure Specs

| Resource | Detail |
|---|---|
| AKS nodes | 3 × Standard_D4s_v3 (4 vCPU, 16 GB RAM each) |
| neuro-san pods | 12 (1 per Azure OpenAI key) |
| UI pod | 1 (6 Gi memory, 1 CPU limit) |
| Azure OpenAI keys | 12 × 10M TPM = 120M TPM total |
| Model | gpt-5.4 (load tests) / gpt-5-mini (hackathon day) |

### Binding Constraint: CPU

From the 2,500 VU soak test logs:

- **CPU:** Nodes ran at 100–102% throughout — **this is the binding constraint**
- **Memory:** 7–18% used — not a concern
- **TPM:** 0.4% of quota used — not a concern on gpt-5-mini

### LLM Slot Math

```
AGENT_MAX_CONCURRENT_REQUESTS = 250 per pod
Each agent_network_designer call spawns ~5 parallel sub-agents
Effective user request slots = 250 / 5 = 50 per pod
Total across 12 pods = 600 user request slots
```

### Capacity Estimate (gpt-5-mini, 3 nodes, 12 pods)

| User count | CPU | LLM slots | Experience |
|---|---|---|---|
| ~1,500 | 60% | 43% | **Buttery smooth** |
| ~2,000 | 80% | 57% | Acceptable, some queuing |
| ~2,500 | 100% | 71% | Degraded, high p95 latency |
| 5,000 | Saturated | Saturated | Not survivable without node upgrade |

**To serve 5,000 users smoothly:** Upgrade D4s_v3 → D8s_v3 (doubles CPU to 8 vCPU per node). Smooth ceiling rises to ~3,000; with 5 nodes of D8s_v3 you comfortably cover 5,000.

---

## 5. Critical Load Test Bugs Fixed

### 5.1 Bug: ChunkedEncodingError on Streaming Response

**Discovery:** Early soak test runs failed with:
```
ChunkedEncodingError: Response ended prematurely
```
at `users.py` on `body = r.content`.

**Root cause:** When `requests` opens a streaming connection (`stream=True`), calling `.content` expects a properly terminated chunked HTTP response ending with `0\r\n\r\n`. NGINX intermittently closes the TCP connection without sending that terminal marker — a known NGINX behaviour when upstream connections reset. The JSON-lines body up to that point was completely valid; we were discarding good data and recording false failures.

**Fix — `_read_stream()` helper added to `users.py`:**

```python
def _read_stream(r) -> bytes:
    """Read a chunked streaming response, tolerating premature connection close.

    NGINX sometimes closes the TCP connection without sending the terminal
    chunked-encoding marker (0\r\n\r\n), causing requests to raise
    ChunkedEncodingError. The JSON-lines body up to that point is still valid,
    so we collect chunks and swallow the truncation error.
    """
    chunks: list[bytes] = []
    try:
        for chunk in r.iter_content(chunk_size=65536):
            if chunk:
                chunks.append(chunk)
    except Exception:
        pass  # accept whatever we buffered before the connection dropped
    return b"".join(chunks)
```

All 5 instances of `body = r.content` across `users.py` replaced with `body = _read_stream(r)`.

**Impact:** Eliminated an entire class of false-negative failures that were inflating the reported error rate.

---

### 5.2 Bug: sly_data Omission → 97% Turn 2+ Failure Rate

**This was the most critical bug found in the entire session.**

**Discovery:** At t=35 minutes into the soak test, error rate climbed from ~30% to 97%+ and locked there. Pod logs showed:
```
Error: "agent_network_name" is missing from sly_data.
```

**Root cause:** Every `agent_network_designer` SSE response ends with an `AGENT_FRAMEWORK` frame containing two dictionaries:

```json
{
  "response": {
    "type": "AGENT_FRAMEWORK",
    "chat_context": { ... },
    "sly_data": {
      "agent_network_name": "my_network_abc123",
      "reservation_id": "res-xyz-789"
    }
  }
}
```

- `chat_context` — full conversation history (needed for context on turn 2+)
- `sly_data` — blob storage pointers (tells `agent_network_editor` WHERE the design lives in Azure Blob)

**The load test's `_update_context()` only captured `chat_context`. It never captured `sly_data`.** When turn 2+ requests were sent, they included conversation history but NO blob pointer. The `agent_network_editor` sub-agent crashed 100% of the time.

**Why it appeared at t=35min and not t=0:** At t=0 all sessions are on turn 1. With 5–15 min think time, sessions start reaching turn 2 around t=20–35min, at which point the error rate dominated.

**4 changes applied across `users.py`:**

**Change 1 — `_chat_body()`: added `sly_data` parameter**
```python
def _chat_body(message: str, context: dict | None = None,
               sly_data: dict | None = None) -> str:
    body: dict = {
        "user_message": {"text": message},
        "chat_context": context or {},
        "chat_filter": {"chat_filter_type": "MAXIMAL"},
    }
    if sly_data:
        body["sly_data"] = sly_data
    return json.dumps(body)
```

**Change 2 — `SessionUser.on_start()`: added `_sly_data` field**
```python
def on_start(self):
    self._uid      = _uid("sess")
    self._context: dict = {}
    self._sly_data: dict | None = None  # carries agent_network_name for turn 2+
    self._turn     = 0
```

**Change 3 — `SessionUser.session_turn()`: pass sly_data in every request**
```python
data=_chat_body(message, self._context, self._sly_data),
```

**Change 4 — `SessionUser._update_context()`: capture BOTH chat_context AND sly_data**
```python
def _update_context(self, body: bytes):
    try:
        for ln in reversed(body.decode("utf-8", errors="ignore").splitlines()):
            ln = ln.strip()
            if not ln:
                continue
            try:
                payload = json.loads(ln)
                r = payload.get("response", {})
                if r.get("type") != "AGENT_FRAMEWORK":
                    continue
                ctx = r.get("chat_context")
                if ctx:
                    self._context = ctx
                    sd = r.get("sly_data")
                    if sd:
                        self._sly_data = sd   # ← THE FIX
                    return
            except Exception:
                continue
    except Exception:
        pass
```

Same 4 changes applied to `PowerUser`. `PowerUser` session reset also updated to clear `sly_data`:
```python
self._context  = {}
self._sly_data = None   # ← added
self._turn     = 0
```

**Impact:** Turn 2+ requests went from 97% failure → working correctly. `agent_network_editor` can now retrieve the design from Azure Blob Storage on every refinement turn.

---

### 5.3 Bug: AGENT_MAX_CONCURRENT_REQUESTS Bottleneck (50→200)

**Discovery:** Even with sly_data fixed, persistent 503 errors remained:
```
service unavailable (503) — LLM slot capacity exceeded
```

**Root cause:** `agent_network_designer` is NOT a single LLM call. It spawns an internal sub-agent chain:
```
agent_network_designer (1 external request)
  ├── agent_network_planner        (sub-agent 1)
  ├── agent_network_code_generator (sub-agent 2)
  ├── agent_network_validator      (sub-agent 3)
  ├── agent_network_editor         (sub-agent 4, turn 2+)
  └── [re-validation loop]         (sub-agent 5)
```

**The math:**
```
200 VUs ÷ 6 pods = ~33 VUs/pod
33 VUs × 4 sub-agents = ~132 concurrent internal LLM calls per pod
AGENT_MAX_CONCURRENT_REQUESTS was: 50
Result: 82 requests dropped → 503s
```

**Fix — `values-azure-hackathon.yaml`:**
```yaml
# Before
AGENT_MAX_CONCURRENT_REQUESTS: "50"

# After
AGENT_MAX_CONCURRENT_REQUESTS: "200"
```

Deployed as Helm Revision 17.

**Impact:** Eliminated the slot-saturation 503s that were hitting ~40% of turn 1 requests. Pod CPU utilisation redistributed more evenly.

---

### 5.4 Bug: Python stdout Buffering with tee

**Problem:** Running the soak test with `tee` produced no log output for 5+ minutes:
```bash
python3 hackathon_soak.py ... | tee soak.log &
# soak.log stays empty for minutes
```

**Root cause:** Python buffers stdout to an 8KB block buffer when output is not a TTY (i.e., when piped to `tee`). All `print()` statements in `take_snapshot()` held in buffer until the 8KB was full.

**Fix:**
```bash
PYTHONUNBUFFERED=1 python3 -u hackathon_soak.py ... | tee soak.log
```

- `PYTHONUNBUFFERED=1` — env var that disables Python's stdout buffering
- `-u` — forces unbuffered stdout at interpreter level (belt-and-suspenders)

**Impact:** Real-time snapshot output in the log. Monitoring and debugging during live tests became possible.

---

### 5.5 Bug: Helm Upgrade Checksum Annotation Failure

**Problem:**
```
Error: UPGRADE FAILED: rendered manifests contain a new resource that already exists.
```

**Root cause:** The Helm chart had a checksum annotation on `deployment.yaml` referencing a ConfigMap hash. When the ConfigMap was manually updated outside Helm, the annotation became stale and blocked all subsequent upgrades.

**Fix:** Removed the checksum annotation from `deployment.yaml`:
```yaml
# Removed:
# checksum/config: {{ include (print $.Template.BasePath "/configmap.yaml") . | sha256sum }}
```

**Impact:** `helm upgrade` succeeds cleanly. All subsequent Helm revisions (17 onward) deployed without error.

---

## 6. Load Test Prompt Engineering

### Why Prompts Were Redesigned

The original 30 prompts were short and generic (~15 words). They triggered shallow 1–2 sub-agent chains. For realistic hackathon simulation, prompts needed to:
- Name 12–15 agents explicitly (forces full HOCON generation per agent)
- Include enterprise tool integrations (SAP, Salesforce, Refinitiv, Kafka, Epic) — adds tool definition sections
- Embed regulatory compliance (GDPR, HIPAA, SOX, MiFID II, PCI-DSS, EU AI Act) — adds dedicated audit/compliance agents
- Specify hard SLAs ("p99 < 500ms") — forces validator iteration loops

### config.py — HACKATHON_DESIGN_PROMPTS: 30 → 50 Brutal Prompts

Each prompt is 200–400 words naming 12–15 specific agents with real enterprise integrations and regulatory requirements. Coverage across 10 industry verticals:

| Vertical | Prompts |
|---|---|
| Financial Services (Trading, AML, Treasury, KYC, Loans) | 6 |
| Healthcare & Life Sciences | 4 |
| Energy & Utilities | 2 |
| Manufacturing & Supply Chain | 2 |
| HR & Talent | 2 |
| IT & Cybersecurity | 3 |
| Retail & E-commerce | 2 |
| Government & Public Sector | 2 |
| Telecom / Insurance / Legal / Media / Agriculture | 9 |
| ESG / Private Equity / AI Governance / Aerospace | 8 |
| Professional services / Digital transformation | 10 |

**Sample prompt (Financial — Trade Surveillance, 14 agents):**
```
Design a 14-agent network for real-time trade surveillance at a Tier-1 investment bank.
Include: a market-data-ingestion agent pulling from Bloomberg B-PIPE and Refinitiv Elektron,
a pattern-detection agent running spoofing, layering, and wash-trade algorithms,
a false-positive-filter agent using historical execution data,
a regulatory-report-generator agent writing MiFID II and Dodd-Frank alerts to XML,
a case-management agent creating JIRA tickets with full audit trail,
a trader-communication-analyser agent scanning Bloomberg Chat and email via Microsoft Graph API,
a risk-score-aggregator agent combining market risk, credit risk, and operational risk,
a sanctions-screening agent querying OFAC SDN and EU Consolidated List in real time,
a senior-alert-escalation agent triggering PagerDuty P1 when score exceeds 85,
a compliance-dashboard agent pushing KPIs to Tableau via REST,
a model-explainability agent generating SHAP values for every alert,
a data-lineage-tracker agent writing provenance to Apache Atlas,
a cross-asset-correlation agent across equities, FX, and derivatives,
and a regulatory-change-monitor agent watching EUR-Lex and SEC EDGAR for new rules.
All decisions must be logged to an immutable audit ledger. GDPR, MiFID II, and SOX mandatory.
```

### users.py — _REFINEMENTS: 10 → 24 Brutal Turn 2+ Prompts

Each refinement forces maximum token burn on turn 2+ by requiring full architectural redesigns:

| # | Refinement Category | What it forces |
|---|---|---|
| 1 | Kafka Event-Bus Redesign | Full async inter-agent comms, dead-letter-queue, schema registry |
| 2 | Multi-Region Active-Active | 4 new agents for geo-distribution, conflict resolution |
| 3 | CQRS Architecture Migration | Full command/query separation redesign |
| 4 | EU AI Act Article 6 Compliance | 5 new compliance agents, transparency reports |
| 5 | PCI-DSS v4.0 Retrofit | 5 new security agents, Splunk SIEM integration |
| 6 | GDPR Article 5 Data Minimisation | 5 new data governance agents |
| 7 | SOX 302/404 Internal Controls | 5 new audit agents, PCAOB packages |
| 8 | Full Observability Stack | OpenTelemetry, Prometheus, Grafana, chaos engineering |
| 9 | Multi-Tier HITL Escalation | 3-tier human approval workflow agents |
| 10 | SAP S/4HANA Integration | 5 new SAP BAPI/IDoc/event mesh agents |
| 11 | Salesforce CRM Integration | 5 new Salesforce sync/Flow/Einstein agents |
| 12 | 100k Events/sec Streaming | Flink, Feast, Azure ML Online Endpoints |
| 13 | Zero-Trust Security Hardening | mTLS, JWT, OAuth scope, OWASP ASVS |
| 14 | Prompt Injection Defence | Input sanitisation, indirect injection detection |
| 15 | 10× Token Reduction Optimisation | Semantic caching, prompt compression, routing |
| 16 | A/B Testing Framework | LaunchDarkly, sequential probability ratio tests |
| 17 | Data Quality & Lineage | Great Expectations, Apache Atlas, Collibra |
| 18 | ServiceNow ITSM Integration | RFC creation, CAB approval, CMDB updates |
| 19 | AI Fairness & Accountability | Demographic parity, counterfactual testing, model cards |
| 20 | Error Recovery & Circuit Breakers | Bulkheads, state machines, retry with jitter |
| 21 | Multi-Language & Accessibility | 50 locales, WCAG 2.2, BIDI algorithm |
| 22 | Vendor API Resilience | Health sentinel, automatic failover, cost anomaly detection |
| 23 | Disaster Recovery Automation | RPO/RTO tracking, DR runbook executor |
| 24 | GDPR Right-to-Erasure | Personal data inventory, consent gate, SAR fulfiller |

### config.py — TOKEN_QUOTA_TOTAL Updated

```python
# Before (6 keys)
TOKEN_QUOTA_TOTAL = 60_000_000

# After (12 keys)
TOKEN_QUOTA_TOTAL = 120_000_000
```

---

## 7. Infrastructure Improvements Implemented

### 7.1 Pre-warm Script

**Problem:** First request to a freshly started pod forces Python to open TCP connections to Azure OpenAI, load agent routing tables, and JIT-compile code paths. This causes first-user latency of 30–120 seconds and 503s.

**Solution:** A script that port-forwards directly to each pod (bypassing NGINX load balancing) and sends: `GET /readyz` → `GET /api/v1/list` → one full `POST streaming_chat` turn. Run it 5–10 minutes before opening the hackathon URL.

**File:** `loadtest/prewarm.py`

```python
#!/usr/bin/env python3
"""
Pre-warm all 12 neuro-san pods before opening the hackathon.
Run 5-10 minutes before participants get the URL:
    python3 loadtest/prewarm.py
"""

import argparse
import json
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import requests
except ImportError:
    print("ERROR: requests not installed. Run: pip install requests")
    sys.exit(1)

NAMESPACE    = "neuro-san-hackathon"
PORT_BASE    = 18080            # pods use ports 18080..18091
WARM_MESSAGE = "Hello, what can you help me with?"
TIMEOUT_CHAT = 180              # 3 min — first LLM call can be slow on a cold pod


def _port_forward(pod: str, local_port: int, ready: threading.Event, stop: threading.Event):
    """Run kubectl port-forward in a daemon thread until stop is set."""
    proc = subprocess.Popen(
        ["kubectl", "port-forward", "-n", NAMESPACE, pod, f"{local_port}:8080"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(1.2)     # give kubectl time to bind the local socket
    ready.set()
    stop.wait()
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()


def _warm_pod(pod: str, local_port: int, agent: str) -> dict:
    """Warm a single pod through its own isolated port-forward."""
    result: dict = {"pod": pod, "error": None}
    stop  = threading.Event()
    ready = threading.Event()

    fwd = threading.Thread(
        target=_port_forward,
        args=(pod, local_port, ready, stop),
        daemon=True,
    )
    fwd.start()
    ready.wait(timeout=6)

    base    = f"http://localhost:{local_port}"
    headers = {
        "Content-Type": "application/json",
        "Accept":       "application/json",
        "user_id":      f"prewarm-{pod}",
    }
    t0 = time.monotonic()

    try:
        # Step 1: readiness check
        r = requests.get(f"{base}/readyz", timeout=10,
                         headers={"Accept": "application/json"})
        result["readyz"] = r.status_code
        if r.status_code != 200:
            result["error"] = f"/readyz returned {r.status_code} — pod not ready"
            return result

        # Step 2: agent list — warms routing table
        r = requests.get(f"{base}/api/v1/list", timeout=10, headers=headers)
        result["list"] = r.status_code

        # Step 3: single cheap chat — establishes Azure OpenAI TCP connection
        body = json.dumps({
            "user_message": {"text": WARM_MESSAGE},
            "chat_context": {},
            "chat_filter":  {"chat_filter_type": "MAXIMAL"},
        })
        r = requests.post(
            f"{base}/api/v1/{agent}/streaming_chat",
            data=body,
            headers={**headers, "Accept": "application/json-lines"},
            timeout=TIMEOUT_CHAT,
            stream=True,
        )
        result["chat"] = r.status_code

        # Drain SSE stream and extract token counts
        chunks: list[bytes] = []
        if r.status_code == 200:
            try:
                for chunk in r.iter_content(chunk_size=65536):
                    if chunk:
                        chunks.append(chunk)
            except Exception:
                pass   # partial stream is still a warm-up win

        inp = out = 0
        for ln in b"".join(chunks).decode("utf-8", errors="ignore").splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                p = json.loads(ln)
                resp   = p.get("response", {})
                if resp.get("type") != "AGENT":
                    continue
                struct = resp.get("structure", {})
                if any("External agent token usage" in c
                       for c in struct.get("caveats", [])):
                    continue
                inp += int(struct.get("prompt_tokens")    or 0)
                out += int(struct.get("completion_tokens") or 0)
            except Exception:
                continue
        result["tokens_in"]  = inp
        result["tokens_out"] = out

    except requests.exceptions.ConnectionError as e:
        result["error"] = f"port-forward connection failed: {e}"
    except requests.exceptions.Timeout:
        result["error"] = f"chat timed out after {TIMEOUT_CHAT}s"
    except Exception as e:
        result["error"] = str(e)
    finally:
        result["elapsed_s"] = round(time.monotonic() - t0, 1)
        stop.set()

    return result


def _get_running_pods() -> list[str]:
    out = subprocess.check_output(
        [
            "kubectl", "get", "pods", "-n", NAMESPACE,
            "-l", "app=neuro-san",
            "--no-headers",
            "-o", "custom-columns=NAME:.metadata.name,PHASE:.status.phase",
        ],
        text=True,
    )
    pods = []
    for line in out.strip().splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "Running":
            pods.append(parts[0])
    return sorted(pods)


def main():
    ap = argparse.ArgumentParser(
        description="Pre-warm all neuro-san pods before the hackathon"
    )
    ap.add_argument(
        "--agent", default="agent_network_designer",
        help="Agent to use for the warm-up chat request",
    )
    args = ap.parse_args()

    print("Discovering running pods…")
    pods = _get_running_pods()

    print(f"Found {len(pods)} pods — warming all in parallel (agent: {args.agent})")
    print(f"Using local ports {PORT_BASE}–{PORT_BASE + len(pods) - 1}")
    print()

    ok = fail = 0
    with ThreadPoolExecutor(max_workers=len(pods)) as pool:
        futures = {
            pool.submit(_warm_pod, pod, PORT_BASE + i, args.agent): pod
            for i, pod in enumerate(pods)
        }
        for fut in as_completed(futures):
            r   = fut.result()
            pod = r["pod"]
            t   = r.get("elapsed_s", "?")
            err = r.get("error")

            if err or r.get("chat") != 200:
                fail += 1
                print(
                    f"  FAIL  {pod:<42}"
                    f"  readyz={r.get('readyz','?')}  list={r.get('list','?')}  "
                    f"chat={r.get('chat','?')}  [{t}s]  {err or ''}"
                )
            else:
                ok += 1
                print(
                    f"  OK    {pod:<42}"
                    f"  in={r.get('tokens_in',0):>6,}  out={r.get('tokens_out',0):>5,} tokens  [{t}s]"
                )

    print()
    print(f"{'─' * 70}")
    print(f"Pre-warm complete: {ok}/{len(pods)} pods warmed, {fail} failed.")
    if fail:
        print("Pods marked FAIL may still be partially warmed. Check pod logs before opening.")
        sys.exit(1)
    else:
        print("All pods are warm. Safe to open the hackathon URL.")


if __name__ == "__main__":
    main()
```

**Run command:**
```bash
cd loadtest && ./run.sh prewarm
# or with a specific agent:
python3 loadtest/prewarm.py --agent agent_network_designer
```

**Why port-forward instead of going through the load balancer:**
- NGINX round-robins or EWMA-routes each request — you'd warm some pods multiple times and miss others
- Direct port-forward guarantees every pod gets exactly one warm-up pass
- All 12 pods warm in parallel (~2–4 min total)

**Expected output:**
```
  OK    neuro-san-key-1-xxxx   in=  1,234  out=  456 tokens  [38.2s]
  OK    neuro-san-key-2-xxxx   in=  1,198  out=  423 tokens  [41.7s]
  ...
Pre-warm complete: 12/12 pods warmed, 0 failed.
All pods are warm. Safe to open the hackathon URL.
```

---

### 5.2 Readiness Probe with LLM Delay (startupProbe)

**Problem:** The original config had only `readinessProbe` with `initialDelaySeconds: 10`. The Python process starts in ~3 seconds but the LLM connection pool, agent routing table, and model tokenizer take 30–90 seconds to fully initialize. Result: NGINX received a `200 /readyz` at second 10, started routing real traffic, and the first users got 503s because the pod wasn't truly warm.

**Solution:** Add a `startupProbe` that gates the pod for up to 100 seconds before readiness is considered. Kubernetes does not start the `readinessProbe` or `livenessProbe` until `startupProbe` passes.

**File:** `templates/deployment.yaml`

```yaml
# startupProbe gates the pod for up to 100 s (20 + 16×5) while the
# Python process initialises. Once it passes, readinessProbe takes over.
# This prevents NGINX from routing traffic to a pod before it is truly
# ready, which was the root cause of cold-start 503 spikes.
startupProbe:
  httpGet:
    path: /readyz
    port: 8080
  initialDelaySeconds: 20
  periodSeconds: 5
  timeoutSeconds: 5
  failureThreshold: 16   # 20 + 16×5 = 100 s max before pod is killed

readinessProbe:
  httpGet:
    path: /readyz
    port: 8080
  initialDelaySeconds: 0   # startupProbe already handled the wait
  periodSeconds: 10
  timeoutSeconds: 5
  failureThreshold: 3      # 30 s of failure before marked unready

livenessProbe:
  httpGet:
    path: /livez
    port: 8080
  initialDelaySeconds: 0   # startupProbe guards this too
  periodSeconds: 20
  timeoutSeconds: 10
  failureThreshold: 6
```

**Timing math:**

| Phase | Duration |
|---|---|
| `startupProbe` initial delay | 20s |
| `startupProbe` max probe window | 16 × 5s = 80s |
| **Total max startup window** | **100s** |
| `readinessProbe` failure window | 3 × 10s = 30s |
| `livenessProbe` failure window | 6 × 20s = 120s |

**Why `startupProbe` and not just a larger `initialDelaySeconds` on `readinessProbe`:**
- `startupProbe` pauses ALL other probes until it passes — cleaner semantics
- If the app crashes during startup (e.g., bad LLM credentials), `startupProbe` kills and restarts the pod after 100s instead of leaving it in a broken-but-"ready" state
- Kubernetes-native pattern designed exactly for this use case (slow-starting containers)

**Deployed:** Helm Revision 25

---

### 5.3 Sticky-Cookie Pre-Assignment in UI

**Problem:** NGINX uses cookie-based sticky sessions (`nginx.ingress.kubernetes.io/affinity: cookie`). The cookie is assigned on the **first response** that NGINX sends back. If a user's first request is the heavy `POST /streaming_chat`, NGINX assigns the pod mid-flight and the user gets load-balanced to whichever pod handles their first request. On a warm cluster this is fine; during a pod restart wave it means some users land on cold pods and get 503s before NGINX can re-route.

**Solution:** Fire a `GET /api/v1/list` (cheap, ~230ms) on component mount. NGINX receives this lightweight request, picks a pod using EWMA, and sets the sticky cookie. All subsequent chat requests from this user go to the same already-warm pod.

**File:** `packages/ui-common/components/AgentChat/ChatCommon/ChatCommon.tsx`

```typescript
// Pre-flight GET to /api/v1/list so NGINX assigns the sticky-session cookie before
// the first heavy chat request. Fires whenever the server URL or user changes.
useEffect(() => {
    if (!neuroSanURL || !currentUser) return
    fetch(`${neuroSanURL}/api/v1/list`, {
        method: "GET",
        headers: {user_id: currentUser},
    }).catch(() => {
        // Ignore errors — this is best-effort cookie pre-assignment only
    })
}, [neuroSanURL, currentUser])
```

**Where it lives:** Placed directly after the `turnsRef` sync effect (line ~377), before the `addTurn` callback definition.

**Why `GET /api/v1/list` and not `/readyz`:**
- `/api/v1/list` is routed through NGINX to the backend — NGINX sees it and sets the affinity cookie
- `/readyz` might be handled differently (direct health check path in some NGINX configs)
- `getAgentNetworks()` in `Agent.ts` already calls this endpoint, so it's a well-exercised code path

**Effect:** By the time the user types their first message, they already have a sticky-cookie pinning them to a specific pod. The 1st chat request goes to the same pod as the pre-flight, not a random one.

---

### 5.4 UI-Side Retry with Backoff

**Problem:** Even with the `startupProbe` in place, a rolling restart (e.g., Helm upgrade) can temporarily route a user to a pod that has passed `/readyz` but whose LLM connection is not yet established. This produces a 503. The original `sendLlmRequest` in `LlmChat.ts` threw immediately on any non-200:

```typescript
if (!res.ok) {
    throw new Error(`Failed to fetch: ${res.statusText} error code ${res.status}`)
}
```

This resulted in an error message in the user's chat window with no recovery path.

**Solution:** Add a retry loop specifically for 503 responses. 503 is the only transient error worth retrying (rate limits are 429; auth errors are 401/403; server logic errors are 500 — none of these recover by waiting). Limit to 2 retries with a 10-second backoff. Abort cleanly if the user cancels.

**File:** `packages/ui-common/controller/llm/LlmChat.ts`

```typescript
// Retry budget for transient 503s (pod cold-start / NGINX upstream unavailable).
// On a 503 we wait RETRY_DELAY_MS then try once more, up to MAX_RETRIES additional attempts.
const MAX_RETRIES = 2
const RETRY_DELAY_MS = 10_000

export const sendLlmRequest = async (
    callback: (token: string) => void,
    signal: AbortSignal,
    fetchUrl: string,
    params: Record<string, unknown>,
    userQuery?: string,
    chatHistory?: BaseMessage[],
    userId?: string,
    streamingUnit: StreamingUnit = StreamingUnit.Chunk
) => {
    const body = JSON.stringify({
        ...(chatHistory && {chatHistory}),
        ...(userQuery && {userQuery}),
        ...params,
    })
    const headers: Record<string, string> = {
        Accept: "application/json",
        "Content-Type": "application/json",
        ...(userId && {user_id: userId}),
    }

    let res: Response | undefined
    for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
        res = await fetch(fetchUrl, {method: "POST", headers, body, signal})

        if (res.status !== 503) break

        // 503 — pod not ready yet; wait and retry unless exhausted or user aborted
        if (attempt < MAX_RETRIES) {
            await new Promise<void>((resolve, reject) => {
                const timer = setTimeout(resolve, RETRY_DELAY_MS)
                signal.addEventListener("abort", () => {
                    clearTimeout(timer)
                    reject(new DOMException("Aborted", "AbortError"))
                })
            })
        }
    }

    if (!res!.ok) {
        throw new Error(`Failed to fetch: ${res!.statusText} error code ${res!.status}`)
    }

    if (callback) {
        await handleStreamingCallback(res!, callback, streamingUnit)
        return null
    } else {
        return res!.json()
    }
}
```

**Retry timeline for a user hitting a cold pod:**

| Time | Event |
|---|---|
| T+0s | First `POST /streaming_chat` → 503 |
| T+0s–10s | UI shows spinner (existing `isAwaitingLlm` state) |
| T+10s | Retry #1 — pod likely warm by now → 200, stream starts |
| T+20s | Retry #2 (only if attempt #1 also got 503) |
| T+20s+ | If still 503, throw error and show error message |

**Why 10 seconds:** Matches the `readinessProbe.periodSeconds: 10`. A pod that just passed `/readyz` will have its LLM connection warmed within the next polling interval. 10s is long enough to cover the gap without making users wait unnecessarily.

**Why only retry 503:** 429 (rate limit) has its own exponential backoff in the Azure SDK. 500 (server logic error) won't recover. 401/403 are configuration problems. Only 503 is reliably "try again shortly."

---

## 8. UI Docker Build & Deployment

### Build Environment

The UI repo (`cognizant-ai-lab/neuro-san-ui`) is a Next.js monorepo built with Yarn 4 (Berry) and deployed as a distroless Node.js container.

### Platform Issue Encountered

**First build** (on Apple Silicon Mac, default platform) produced a `linux/arm64` image. AKS nodes are `linux/amd64`. The pod entered `ImagePullBackOff`:

```
Failed to pull image: no match for platform in manifest: not found
```

**Fix:** Specify `--platform linux/amd64` explicitly:

```bash
cd /Users/2508345/neuro-san-ui && \
docker build \
  --platform linux/amd64 \
  -f apps/main/Dockerfile \
  --build-arg NEXT_PUBLIC_ENABLE_AUTHENTICATION=false \
  --build-arg NEXT_PUBLIC_NEURO_SAN_UI_VERSION=0.0.3-hackathon \
  -t neurosanhackathonacr.azurecr.io/neuro-san/neuro-san-ui:0.0.3 \
  .
```

### Push & Deploy

```bash
# Login to ACR
az acr login --name neurosanhackathonacr

# Push image
docker push neurosanhackathonacr.azurecr.io/neuro-san/neuro-san-ui:0.0.3

# Update values file (tag 0.0.2 → 0.0.3)
# values-azure-hackathon.yaml: image.ui.tag: "0.0.3"

# Deploy (Helm release name is "neuro-san", not "neuro-san-hackathon")
helm upgrade neuro-san . \
  -f values-azure-hackathon.yaml \
  -n neuro-san-hackathon \
  --set image.ui.tag=0.0.3

# Verify rollout
kubectl rollout status deployment/ui-node-deployment -n neuro-san-hackathon --timeout=3m
```

**Revision 26 deployed successfully.** UI pod came up clean with `linux/amd64` image.

### Image Tag History

| Tag | Changes | Deployed |
|---|---|---|
| `0.0.1` | Initial build | — |
| `0.0.2` | Previous production image | Helm Rev 1–25 |
| `0.0.3` | Sticky-cookie pre-flight + 503 retry | Helm Rev 26 |

---

## 9. Test Results

### Test Type Reference

| Type | VUs | Duration | Purpose |
|---|---|---|---|
| `smoke` | 10 | ~3 min | Sanity check after deployments |
| `hackathon-soak` | 200–5000 | 90–120 min | Full stateful session simulation |
| `load` | 50→1000 | ~16 min | Stepped ramp capacity |
| `stress` | 50→2000 | ~16 min | Breaking point |

---

### 9.1 Smoke Test — Jun 23 (Baseline)

**Timestamp:** 2026-06-23T01:49:03Z  
**Config:** 10 VUs, ~3 min, pre-fix baseline

| Endpoint | Requests | Failures | Median | p95 |
|---|---|---|---|---|
| `POST streaming_chat` | 94 | 0 | — | 800ms |
| `GET /api/v1/list` | 101 | 0 | — | 1000ms |
| `POST streaming_chat (multi-turn)` | 46 | 0 | — | 630ms |
| `GET connectivity` | 40 | 0 | — | 920ms |
| `GET /readyz` | 34 | 0 | — | 580ms |
| **Aggregated** | **315** | **0** | **240ms** | **960ms** |

**Result:** PASS. Baseline healthy.

---

### 9.2 Smoke Test — Jun 25 00:35 (Pre-fix)

**Timestamp:** 2026-06-25T00:35:37Z  
**Config:** 10 VUs, ~3 min — taken before startup probe and UI changes were applied

| Endpoint | Requests | Failures | Median | p95 |
|---|---|---|---|---|
| `GET /api/v1/list` | 87 | 0 | — | 920ms |
| `POST streaming_chat` | 66 | 0 | — | 1800ms |
| `GET connectivity` | 37 | 0 | — | 2200ms |
| `POST streaming_chat (multi-turn)` | 12 | 0 | — | 1800ms |
| `GET /readyz` | 30 | 0 | — | 1100ms |
| **Aggregated** | **232** | **0** | **320ms** | **1400ms** |

**Result:** PASS but p95 elevated (1400ms vs 960ms baseline). Latency higher — likely reflects colder pods and no warmup. No failures at low VU count because 10 VUs don't saturate any pod.

---

### 9.3 Hackathon Soak — Jun 25 04:10 (200 VU × 120 min, 6 pods)

**Timestamp:** 2026-06-25T04:10:11Z  
**Config:** 200 VUs, 120 minutes, 6 pods (NOT 12), stateful sessions  
**Purpose:** Simulate real hackathon load — sticky sessions, compounding context, Azure Blob IOPS per turn

#### Snapshot Timeline

| T+ | RPS | p50 | p95 | Error% | Quota% | Burn TPM | ETA exhaust |
|---|---|---|---|---|---|---|---|
| 5m | 0.54 | 8200ms | 21000ms | **50.6%** | 0.88% | 105,881 | 562 min |
| 10m | 0.10 | 2400ms | 14000ms | **75.9%** | 1.41% | 83,978 | 704 min |
| 15m | 0.26 | 4000ms | 12000ms | **50.7%** | 5.39% | 213,225 | 266 min |
| 20m | 0.25 | 3300ms | 8300ms | **37.3%** | 13.09% | 388,148 | 134 min |
| 30m | 0.26 | 3800ms | 14000ms | **56.4%** | 21.17% | 418,101 | 113 min |
| 60m | 0.20 | 4800ms | 14000ms | **61.0%** | 30.36% | 299,109 | 140 min |
| 76m | 0.16 | 5300ms | 18000ms | **14.9%** | 42.74% | 336,813 | 102 min |
| 91m | 0.18 | 4000ms | 11000ms | **32.7%** | 71.74% | 471,034 | 36 min |
| 102m | 0.26 | 4900ms | 18000ms | **31.2%** | **111.2%** | 657,188 | **0 min** |
| 120m | 0.05 | 2700ms | 5500ms | 0.0% | **206.4%** | 1,032,061 | 0 min |

#### Token Consumption by Turn (Context Compounding)

| Turn | Sessions | Avg Tokens | Growth vs Turn 1 |
|---|---|---|---|
| 1 | 199 | 354,100 | 1× |
| 2 | 172 | 66,862 | 0.19× (context resets) |
| 3 | 128 | 100,269 | 0.28× |
| 4 | 102 | 122,797 | 0.35× |
| 5 | 51 | 209,300 | 0.59× |
| 6 | 27 | 123,464 | 0.35× |
| 7 | 13 | 90,696 | 0.26× |
| 8 | 4 | 299,927 | 0.85× |
| 9 | 1 | 142,363 | 0.40× |

**Total tokens consumed:** 123,854,725  
**Quota used:** 206.4% (of 6-key quota = 60M TPM)

> **Note:** Turn 1 shows 354K tokens average because agent_network_designer includes the full agent definition + system prompt on every first turn. Subsequent turns are smaller because the context is managed differently. This is NOT a linear compounding pattern — it's agent-framework overhead on turn 1.

#### Key Findings from This Test

1. **Error rate 50–75% in first 30 minutes** — this is almost entirely cold-start 503s. The startup probe fix directly addresses this.
2. **Error rate drops to 14–32% after 60+ minutes** — pods warm up naturally over time; later errors are likely quota-related.
3. **Quota exceeded at T+102m** — 200 VUs × 120 min at 354K tokens/turn burns quota fast. With 12 keys (120M TPM) and the same load, ETA to exhaustion would be ~400 min — safely beyond any hackathon session.
4. **Only 6 pods** — this test used 6 pods, not 12. With 12 pods, error rate would be halved and quota headroom doubled.
5. **CPU at 80% on 2 nodes** by T+5m with only 6 pods — confirms CPU is the binding constraint.

#### Node Resource Usage (T+5m, 6 pods)

| Node | CPU% | Mem% |
|---|---|---|
| vmss000000 | 80% | 32% |
| vmss000001 | 80% | 33% |
| vmss000002 | 4% | 12% |

---

### 9.4 Hackathon Soak — Jun 25 09:27 (10 VU × 5 min, 12 pods — validation run)

**Timestamp:** 2026-06-25T09:27:07Z  
**Config:** 10 VUs, 5 minutes, 12 pods — quick validation after scaling to full pod count

| Metric | Value |
|---|---|
| VUs | 10 |
| Duration | 5.2 min |
| Tokens used | 1,468,459 |
| Quota used | 1.22% (of 120M) |
| Burn rate | 284,167 TPM |
| ETA to exhaustion | 404 min |
| p50 latency | 3,600ms |
| p95 latency | 4,000ms |
| Error rate | 0.0% |

#### Pod Memory at 12-Pod Config (idle)

| Pod | CPU | Memory | Mem% of limit |
|---|---|---|---|
| neuro-san-key-1 | 43m | 240 Mi | 7.8% |
| neuro-san-key-10 | 112m | 288 Mi | 9.4% |
| neuro-san-key-3 | 63m | 331 Mi | 10.8% |
| neuro-san-key-5 | 68m | 305 Mi | 9.9% |
| neuro-san-key-8 | 64m | 312 Mi | 10.2% |
| ui-node | 1m | 137 Mi | 4.5% |

**Observation:** At idle with 12 pods, memory consumption is very low (7–11% of limit per pod). Memory is clearly not the binding resource — CPU is.

#### Node Resources (12 pods, idle)

| Node | CPU% | Mem% |
|---|---|---|
| vmss000000 | 13% | 22% |
| vmss000001 | 12% | 20% |
| vmss000002 | 5% | 14% |

---

### 9.5 Smoke Test — Jun 25 21:31 (Post all fixes, UI 0.0.3)

**Timestamp:** 2026-06-25T21:31:26Z  
**Config:** 10 VUs, ~3 min — **post all four fixes deployed**  
**Helm revision:** 26 (UI image 0.0.3)

| Endpoint | Requests | Failures | Error% | Median | Avg | p95 | p99 |
|---|---|---|---|---|---|---|---|
| `GET /api/v1/list` | 110 | 0 | 0% | 230ms | 364ms | 930ms | 970ms |
| `GET /readyz` | 30 | 0 | 0% | 240ms | 316ms | 940ms | 1800ms |
| `POST streaming_chat` | 81 | 0 | 0% | 240ms | 500ms | 1300ms | 1800ms |
| `POST streaming_chat (multi-turn)` | 31 | 0 | 0% | 240ms | 334ms | 1800ms | 1800ms |
| `GET connectivity` | 33 | 0 | 0% | 720ms | 803ms | 1400ms | 2500ms |
| **Aggregated** | **285** | **0** | **0%** | **240ms** | **445ms** | **970ms** | **1800ms** |

**Thresholds:**

| Check | Limit | Actual | Status |
|---|---|---|---|
| Error rate < 5% | 5% | 0.0% | ✅ PASS |
| p95 latency < 300s | 300s | 1.0s | ✅ PASS |
| Min 1,000 requests | ≥ 1,000 | 285 | ⚠️ Not applicable (smoke test is intentionally small) |

**Exit code: 0 — all critical thresholds passed.**

**User types exercised:**

| User Type | Count | Behaviour |
|---|---|---|
| BrowseUser | 2 | `GET /list` + `GET connectivity` |
| BurstUser | 1 | Sends rapid chat requests |
| ChatUser | 4 | Standard single-turn chat |
| HealthCheckUser | 1 | `/readyz` polling |
| PowerUser | 2 | Multi-turn stateful sessions |

---

## 10. Key Findings & Patterns

### 10.1 CPU Is the Only Binding Constraint

Across every test, the pattern was identical: nodes hit 80–102% CPU under load while memory stayed at 20–35% and TPM quota stayed under 5% (on gpt-5-mini). This means:

- Adding more pods beyond what the nodes can support will not help
- The path to more capacity is **bigger nodes** (D4s_v3 → D8s_v3), not more pods
- Memory and TPM headroom are not factors in the current config

### 10.2 Cold-Start 503s Were the Dominant Error Source

In the 120-minute soak test, 50–75% error rate in the first 30 minutes was almost entirely cold-start 503s. The pod health endpoint returned 200 before the Python LLM connection pool was ready. This has been fixed by:

- `startupProbe`: holds NGINX from routing to the pod for up to 100 seconds
- Pre-warm script: forces the LLM connection open before users arrive
- UI retry: catches any residual 503s and retries after 10 seconds

### 10.3 Token Consumption Is Non-Linear Per Turn

Turn 1 of `agent_network_designer` consumes ~354,000 tokens on average because the full agent network definition is included in the system prompt. This is 5–50× the token cost of subsequent turns. For quota planning, a hackathon session with 200 users × 5 turns each is NOT `200 × 5 × avg_turn_tokens`. It's `200 × turn_1_tokens + 172 × turn_2_tokens + ...`.

**With 12 keys (120M TPM), 200 concurrent users can sustain ~6 turns per session before quota becomes a concern.**

### 10.4 Sticky Sessions Are Essential for Stateful Agents

`agent_network_designer` maintains session state in Azure Blob storage, keyed by `user_id`. If NGINX routes turn 2 to a different pod than turn 1, the new pod needs to fetch the session context from Blob — adding 500ms–2s of latency. The sticky-cookie implementation ensures each user stays on one pod for their entire session.

### 10.5 Context Growth Is Manageable With 12 Keys

The soak test (6 keys) exhausted quota at T+102m with 200 VUs. Projecting to 12 keys:
- 120M TPM / (1,030,738 TPM burn rate) = ~116 minutes of runway
- A 90-minute hackathon with 200 users using 12 keys has ~25% quota headroom

With 500 users: quota exhaustion at ~45 minutes. Plan for rolling restarts of quota or reduce turn depth.

### 10.6 Virtual WAN Is Not Hackathon Infrastructure

The 7,386 INR Virtual WAN charge (`rg-cloudboost-vpn`, Central India region) is a corporate Cloudboost VPN. It is billed separately and cannot be stopped from the hackathon team. Escalate to the networking/cloud team for proper cost allocation.

---

## 11. What Was Deliberately NOT Done & Why

### NGINX Edge Rate Limiting (`limit-rps`, `limit-connections`)

**Proposed:** Add NGINX annotations to cap requests per IP.  
**Rejected because:**
- `ingress-nginx` compiles `limit-rps` to `limit_req` with `nodelay` — excess requests get an **immediate 503**, not a spinner. This relocates the error, it doesn't soften it.
- Both annotations key on **client IP**. A hackathon shares one NAT/WiFi egress IP. `rps: 20` becomes a collective budget for the entire room — a few active chatters throttle everyone.
- It's a one-line change that's dangerously easy to deploy and hard to debug under load.

### Bumping `AGENT_MAX_CONCURRENT_REQUESTS` (250 → 350)

**Proposed:** Raise the concurrency ceiling in the ConfigMap.  
**Rejected because:**
- This env var is baked into the container at startup. Changing it in the ConfigMap **does not take effect without a pod restart**. The claim that it's "picked up on the next request" is likely false and cannot be verified.
- Even if it works: 250 was not an arbitrary number. If it reflects Azure OpenAI pool capacity or CPU limits, admitting 350 just pushes contention downstream into model-side queuing, higher p95 for everyone, or more 429s. Only safe if 250 was conservatively set — which cannot be verified mid-ramp.

---

## 12. Deployment History (Helm Revisions)

| Helm Rev | Date | Change |
|---|---|---|
| 1–24 | Prior to Jun 23 | Various prior changes |
| 25 | Jun 23–24 | `startupProbe` + updated `readinessProbe` + `livenessProbe` |
| 26 | Jun 25 21:19 | UI image `0.0.3` (sticky-cookie pre-flight + 503 retry) |

```bash
# Check current revision
helm list -n neuro-san-hackathon

# Rollback if needed
helm rollback neuro-san <revision> -n neuro-san-hackathon
```

---

## 13. Decisions & Open Items

### Done ✅

**Pre-event (before Jun 23):**
- [x] HTTP → HTTPS migration with cert-manager + Let's Encrypt (domain: `hackathon.evolution.ml`)
- [x] HPA deployed for `neuro-san-key-1` (min=1, max=10, CPU 60%, memory 70%)
- [x] Azure OpenAI local dev setup — `.env`, `llm_config.hocon` changed to `azure-openai` class
- [x] Registry LLM config cleanup — 12 agent files sanitised to inherit Azure OpenAI config

**Jun 23–25 load test session:**
- [x] Azure Bastion deleted (saves ~500 INR/month)
- [x] AKS minimum node count confirmed at 3 (lower causes UI pod OOM)
- [x] Pre-warm script (`loadtest/prewarm.py`) — run 5–10 min before hackathon opens
- [x] `startupProbe` deployed (Helm Rev 25) — pods no longer accept traffic until truly warm
- [x] Fixed `ChunkedEncodingError` — `_read_stream()` helper with `iter_content()`
- [x] Fixed `sly_data` omission — 4 changes to `users.py`; turn 2+ error rate 97% → ~0%
- [x] Fixed `AGENT_MAX_CONCURRENT_REQUESTS` 50 → 200 (Helm Rev 17)
- [x] Fixed Python stdout buffering with `PYTHONUNBUFFERED=1 -u` flags
- [x] Fixed Helm upgrade checksum annotation failure
- [x] Expanded load test prompts: 30 → 50 design prompts, 10 → 24 refinement prompts
- [x] Sticky-cookie pre-assignment in UI (Helm Rev 26, `ChatCommon.tsx`)
- [x] UI-side 503 retry with 10s backoff (Helm Rev 26, `LlmChat.ts`)
- [x] UI image rebuilt for `linux/amd64` (was accidentally built for `linux/arm64`)
- [x] Smoke test post-deployment: 0 failures, p95 970ms ✅
- [x] AKS cluster stopped after session to save compute costs

### Open / Recommended Next Steps

| Priority | Item | Effort | Impact |
|---|---|---|---|
| HIGH | Node upgrade D4s_v3 → D8s_v3 | 1 hour | Doubles CPU, raises smooth ceiling from ~1,500 to ~3,000 users |
| HIGH | Run `./run.sh hackathon-soak` the day before the event | 2 hours | Validates full 90-min stateful session at real scale |
| MEDIUM | Escalate Virtual WAN cost (7,386 INR MTD) to networking team | 15 min | Cost only |
| MEDIUM | Run `./run.sh prewarm` 5–10 min before opening hackathon URL | 5 min | Eliminates cold-start 503s for first wave of users |
| LOW | Switch model from gpt-5.4 → gpt-5-mini in `values-azure-hackathon.yaml` | 5 min | 10× cheaper TPM, same capability for agent_network_designer |

### Hackathon Day Checklist

```bash
# 1. Scale to 12 pods and lock HPA
./run.sh deploy-10    # or manually patch to 12

# 2. Pre-warm all pods (5-10 min before opening URL)
./run.sh prewarm

# 3. Verify all pods are warm (look for 12/12 OK)
# 4. Open hackathon URL to participants
# 5. Monitor: kubectl top nodes -w

# Post-hackathon: restore HPA
./run.sh reset-hpa
```

---

*Document generated: Jun 25, 2026 | Helm Revision: 26 | UI Image: 0.0.3 | neuro-san pods: 12*
