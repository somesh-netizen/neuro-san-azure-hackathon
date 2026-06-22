# Neuro-San Azure Hackathon — Full Session Context

This document captures everything done in this session so a new Claude session
can pick up exactly where we left off with full context.

---

## Project Overview

**Goal:** Migrate Neuro-San (frontend + backend) from AWS to Azure for a hackathon deployment.
No SSO/auth for now. Frontend and backend hosted separately with session affinity.

**Repos involved:**
- `/Users/2504436/neuro-san-deploy-main` — Helm chart (deployment)
- `/Users/2504436/neuro-san-studio` — Python backend
- `/Users/2504436/neuro-san-ui` — Next.js frontend

---

## Azure Resources Created

| Resource | Name | Details |
|---|---|---|
| Subscription | cb10268483a-cailindia-az | ID: `776a9397-b4b1-4465-a4a8-f05aa893cf8a` |
| Resource Group | neuro-san-studio-marketplace-rg | eastus |
| VNet | neuro-san-vnet | 10.0.0.0/16 |
| AKS Subnet | aks-subnet | 10.0.2.0/23 |
| AKS Cluster | neuro-san-hackathon-aks | 3 nodes, Standard_D4s_v3 |
| ACR | neurosanhackathonacr | neurosanhackathonacr.azurecr.io |
| Key Vault | neuro-san-hackathon-kv | https://neuro-san-hackathon-kv.vault.azure.net |
| Storage Account | neurosanhackathonsa | Blob container: neuro-san-reservations |
| Managed Identity | neuro-san-wi | clientId: `31036678-9d77-4e78-840b-93fba4233a90`, principalId: `4a038eac-fd77-4f12-9864-f8513c42fa4e` |

---

## Kubernetes State

**Namespace:** `neuro-san-hackathon`

**Helm release:** `neuro-san` (revision 6)
```bash
helm list -n neuro-san-hackathon
```

**Running pods:**
- `neuro-san-key-1-*` — backend (neuro-san-studio:0.0.1)
- `ui-node-deployment-*` — frontend (neuro-san-ui:0.0.2)

**Secrets in namespace (manually created — NOT managed by Helm):**
```bash
# These must be recreated after any helm uninstall / namespace delete:
kubectl create secret generic openai-key-1 \
  --from-literal=OPENAI_API_KEY=<KEY> -n neuro-san-hackathon

kubectl create secret generic azure-storage \
  --from-literal=AZURE_STORAGE_CONNECTION_STRING="$(az storage account show-connection-string \
    -g neuro-san-studio-marketplace-rg -n neurosanhackathonsa --query connectionString -o tsv)" \
  -n neuro-san-hackathon

kubectl create secret docker-registry acr-pull-secret \
  --docker-server=neurosanhackathonacr.azurecr.io \
  --docker-username=neurosanhackathonacr \
  --docker-password=<ACR_ADMIN_PASSWORD> \
  -n neuro-san-hackathon

# TLS certs are managed by cert-manager (Let's Encrypt) — auto-renewed
```

**Cluster add-ons installed:**
- `ingress-nginx-backend` (namespace: ingress-nginx-backend) — `externalTrafficPolicy=Local`
- `ingress-nginx-frontend` (namespace: ingress-nginx-frontend) — `externalTrafficPolicy=Local`
- `external-secrets` (namespace: external-secrets)
- `cert-manager` (namespace: cert-manager)

---

## Live URLs

| Service | URL | Certificate |
|---|---|---|
| **Frontend** | https://neurosanhackathon.eastus.cloudapp.azure.com | ✅ Let's Encrypt (trusted) |
| **Backend** | https://neurosanhackathon-api.eastus.cloudapp.azure.com | ✅ Let's Encrypt (trusted) |

**Public IPs:**
- Frontend NGINX: `20.253.60.144`
- Backend NGINX: `20.127.253.65`

---

## Docker Images in ACR

| Image | Tag | Architecture |
|---|---|---|
| neuro-san/neuro-san-studio | 0.0.1 | linux/amd64 |
| neuro-san/neuro-san-ui | 0.0.2 | linux/amd64 |

**Important:** Always build with `--platform linux/amd64` — AKS nodes are amd64, Mac is arm64.

```bash
# Frontend rebuild (increment tag each time to force image pull)
cd /Users/2504436/neuro-san-ui
docker buildx build --platform linux/amd64 \
  -t neurosanhackathonacr.azurecr.io/neuro-san/neuro-san-ui:0.0.3 \
  -f apps/main/Dockerfile --push .

# Backend rebuild
cd /Users/2504436/neuro-san-studio
docker buildx build --platform linux/amd64 \
  -t neurosanhackathonacr.azurecr.io/neuro-san/neuro-san-studio:0.0.2 \
  -f deploy/Dockerfile --push .
```

---

## Helm Chart Location

All Azure hackathon files are in:
```
/Users/2504436/neuro-san-deploy-main/azure-hackathon/
├── Chart.yaml
├── values-azure-hackathon.yaml          ← main config file
├── DEPLOYMENT-GUIDE.md                  ← full step-by-step guide
├── loadtest/
│   └── hackathon-loadtest.js            ← k6 load test
└── templates/
    ├── _helpers.tpl
    ├── cluster-issuer.yaml              ← Let's Encrypt ClusterIssuer
    ├── cluster-secret-store.yaml        ← disabled (no Key Vault access yet)
    ├── configMap.yaml
    ├── deployment.yaml
    ├── ingress.yaml
    ├── namespace.yaml
    ├── sa.yaml
    ├── secrets.yaml                     ← disabled (secrets created manually)
    └── service.yaml
```

**To redeploy:**
```bash
cd /Users/2504436/neuro-san-deploy-main/azure-hackathon
helm upgrade neuro-san . -f values-azure-hackathon.yaml -n neuro-san-hackathon
```

---

## Code Changes Made

### neuro-san-ui (frontend)

| File | Change | Why |
|---|---|---|
| `apps/main/app/api/auth/[...nextauth]/route.ts` | Returns 200 noop when `NEXT_PUBLIC_ENABLE_AUTHENTICATION=false` | Prevents Auth0 redirect loop |
| `apps/main/pages/multiAgentAccelerator/index.tsx` | `authRequired: false` | Page was redirecting to auth |
| `apps/main/pages/UserGuide.tsx` | `authRequired: false` | Same |
| `apps/main/.env.production` | `NEXT_PUBLIC_ENABLE_AUTHENTICATION=false` | Baked into build (NEXT_PUBLIC_ vars are compile-time) |
| `apps/main/pages/api/userInfo/index.ts` | Returns `{oidcHeaderFound: false}` | Removed AWS ALB OIDC header logic |

### neuro-san-studio (backend)

| File | Change | Why |
|---|---|---|
| `neuro_san_studio/azure_blob_reservations_storage.py` | New file — Azure Blob session storage | Replaces S3ReservationsStorage |
| `requirements.txt` | Added `azure-storage-blob>=12.19.0` | For Azure Blob SDK |

---

## Open Issues / Pending Work

### Blocked on IAM (admin ticket raised: INC008100760)

| Role | Identity | Object ID | Status |
|---|---|---|---|
| `Key Vault Secrets Officer` | Group `cb10268483a-cailindia-az` | `ad2f923e-339a-4bf5-ac19-ade816d44359` | Pending |
| `Key Vault Secrets User` | Managed Identity `neuro-san-wi` | `4a038eac-fd77-4f12-9864-f8513c42fa4e` | Pending |
| `Storage Blob Data Contributor` | Managed Identity `neuro-san-wi` | `4a038eac-fd77-4f12-9864-f8513c42fa4e` | Pending |

Once granted → enable Key Vault + External Secrets in the Helm chart.

### Blocked on AWS SSO (ticket: INC008099338)
- Can't pull existing Docker images from ECR
- Can't read `prod_sso_client_id` / `prod_sso_client_secret` from Secrets Manager for Cognizant SSO

### Authentication (deferred)
- **Option A (preferred):** Reuse existing Cognizant Entra App Registration
  - Need: `prod_sso_client_id`, `prod_sso_client_secret` from AWS Secrets Manager
  - Need: Add redirect URI to existing app: `https://neurosanhackathon.eastus.cloudapp.azure.com/api/auth/callback/microsoft-entra-id`
  - Code change: Update `route.ts` to use `MicrosoftEntraId` provider
- **Option B (simple):** Username/password login page (decided to build this — not yet implemented)

### Multi-pod / Rate Limit (load test done, fix pending)
Load test results (80 VUs, 4 min):
- Chat success rate: **0%** (pod overwhelmed at 50+ concurrent)
- 288 × 5xx errors
- No 429 rate limit errors (quota is fine)
- Fix: increase to 2 pods + raise `AGENT_MAX_CONCURRENT_REQUESTS` to 100

```yaml
# values-azure-hackathon.yaml changes needed:
openaiKeys:
  - id: "1"
  - id: "2"
replicasPerKey: 1
AGENT_MAX_CONCURRENT_REQUESTS: "100"
```

Also consider enabling fallback chain (already built in `cluster_llm_config.hocon`):
- OpenAI → Anthropic Claude → Google Gemini (auto-fallback on rate limit)
- Just needs `llmConfigEnabled: true` + Anthropic/Google API keys added

### Real domain (future)
When a real domain is available, only 5 files need updating — see DEPLOYMENT-GUIDE.md.

---

## Key Identity / Object IDs Reference

```
Subscription ID:           776a9397-b4b1-4465-a4a8-f05aa893cf8a
User object ID:            a1c2e135-9714-475d-9f48-c696e6c25af0
User UPN:                  2504436@cognizant.com
Cognizant tenant ID:       de08c407-19b9-427d-9fe8-edf254300ca7
Group (cb10268483a-*):     ad2f923e-339a-4bf5-ac19-ade816d44359
AKS kubelet object ID:     ee3c7de0-57a4-4596-8e11-41fd64965eb2
AKS control plane ID:      001c435c-e0dc-43fe-a692-3a04804327aa
Managed identity clientId: 31036678-9d77-4e78-840b-93fba4233a90
Managed identity principal:4a038eac-fd77-4f12-9864-f8513c42fa4e
OIDC Issuer URL:           https://eastus.oic.prod-aks.azure.com/de08c407-19b9-427d-9fe8-edf254300ca7/6de57dd3-b91c-4b0a-95f2-00a92d1cfc62/
```

---

## Quick Commands Reference

```bash
# Get AKS credentials
az aks get-credentials -g neuro-san-studio-marketplace-rg -n neuro-san-hackathon-aks --overwrite-existing

# Check pods
kubectl get pods -n neuro-san-hackathon

# Check logs
kubectl logs -n neuro-san-hackathon deployment/ui-node-deployment --tail=50
kubectl logs -n neuro-san-hackathon deployment/neuro-san-key-1 --tail=50

# Check certs
kubectl get certificate -n neuro-san-hackathon

# Run load test
cd /Users/2504436/neuro-san-deploy-main/azure-hackathon/loadtest
k6 run --out web-dashboard hackathon-loadtest.js
# Dashboard at: http://127.0.0.1:5665

# Test endpoints
curl -sk https://neurosanhackathon-api.eastus.cloudapp.azure.com/readyz
curl -sk https://neurosanhackathon.eastus.cloudapp.azure.com/api/environment

# Check IAM roles
az role assignment list --assignee ad2f923e-339a-4bf5-ac19-ade816d44359 \
  --scope /subscriptions/776a9397-b4b1-4465-a4a8-f05aa893cf8a \
  --query "[].roleDefinitionName" -o table
```
