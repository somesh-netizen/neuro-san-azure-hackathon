# Neuro-San Azure Hackathon Deployment Guide

Complete step-by-step guide to deploy Neuro-San (frontend + backend) on Azure AKS
with multiple OpenAI key pods, session affinity, and CORS support.

---

## Architecture Overview

```
Browser
  └── http://20.241.198.56          (Frontend — NGINX ingress-nginx-frontend)
        └── ui-node-deployment       (Next.js UI pod)

  └── http://20.127.253.65          (Backend — NGINX ingress-nginx-backend)
        └── neuro-san-key-1          (Backend pod, OpenAI Key 1)
        └── neuro-san-key-2          (Backend pod, OpenAI Key 2)  [optional]
        └── Shared Azure Blob Storage (session state across pods)
```

---

## Prerequisites

Install these tools before starting:

```bash
# Azure CLI
brew install azure-cli

# kubectl
brew install kubectl

# Helm
brew install helm

# Verify
az version
kubectl version --client
helm version
```

Login to Azure:

```bash
az login
az account show   # confirm subscription: cb10268483a-cailindia-az
```

---

## Step 1 — Create Azure Infrastructure

### 1.1 Resource Group
Already exists: `neuro-san-studio-marketplace-rg` in `eastus`.

### 1.2 Create AKS Subnet in existing VNet

```bash
az network vnet subnet create \
  -g neuro-san-studio-marketplace-rg \
  --vnet-name neuro-san-vnet \
  -n aks-subnet \
  --address-prefixes 10.0.2.0/23
```

**Why:** AKS needs its own subnet (10.0.2.0/23 = 512 IPs) inside the existing VNet.
The existing subnets use 10.0.0.0/24 and 10.0.1.0/26 so we use the next available range.

### 1.3 Create Azure Container Registry (ACR)

```bash
az acr create \
  -g neuro-san-studio-marketplace-rg \
  -n neurosanhackathonacr \
  --sku Standard \
  --location eastus
```

**Result:** `neurosanhackathonacr.azurecr.io`

### 1.4 Create AKS Cluster

```bash
SUBNET_ID=$(az network vnet subnet show \
  -g neuro-san-studio-marketplace-rg \
  --vnet-name neuro-san-vnet \
  -n aks-subnet \
  --query id -o tsv)

az aks create \
  -g neuro-san-studio-marketplace-rg \
  -n neuro-san-hackathon-aks \
  --location eastus \
  --node-count 3 \
  --node-vm-size Standard_D4s_v3 \
  --network-plugin azure \
  --service-cidr 10.1.0.0/16 \
  --dns-service-ip 10.1.0.10 \
  --enable-oidc-issuer \
  --enable-workload-identity \
  --generate-ssh-keys
```

**Why:**
- `--service-cidr 10.1.0.0/16` — must not overlap with VNet (10.0.0.0/16)
- `--enable-oidc-issuer` + `--enable-workload-identity` — lets pods authenticate to Azure services without passwords
- `--node-count 3` — 3 nodes for reliability

Get kubeconfig:

```bash
az aks get-credentials \
  -g neuro-san-studio-marketplace-rg \
  -n neuro-san-hackathon-aks \
  --overwrite-existing

kubectl get nodes   # should show 3 nodes Ready
```

### 1.5 Create Key Vault

```bash
az keyvault create \
  -g neuro-san-studio-marketplace-rg \
  -n neuro-san-hackathon-kv \
  --location eastus \
  --enable-rbac-authorization true
```

**Result:** `https://neuro-san-hackathon-kv.vault.azure.net`

### 1.6 Create Storage Account + Blob Container

```bash
az storage account create \
  -g neuro-san-studio-marketplace-rg \
  -n neurosanhackathonsa \
  --location eastus \
  --sku Standard_LRS \
  --kind StorageV2 \
  --allow-blob-public-access false

CONN_STR=$(az storage account show-connection-string \
  -g neuro-san-studio-marketplace-rg \
  -n neurosanhackathonsa \
  --query connectionString -o tsv)

az storage container create \
  --name neuro-san-reservations \
  --connection-string "$CONN_STR"
```

**Why:** Multiple backend pods share session state via Azure Blob Storage.
Without this, a user's request hitting pod-2 would lose the session started on pod-1.

---

## Step 2 — Build and Push Docker Images

**Important:** AKS nodes run `linux/amd64`. If you are on Apple Silicon (M1/M2/M3 Mac),
you must cross-compile using `--platform linux/amd64`.

### 2.1 Login to ACR

```bash
az acr login -n neurosanhackathonacr
```

### 2.2 Build and Push Backend

```bash
cd /path/to/neuro-san-studio

docker buildx build \
  --platform linux/amd64 \
  -t neurosanhackathonacr.azurecr.io/neuro-san/neuro-san-studio:0.0.1 \
  -f deploy/Dockerfile \
  --push \
  .
```

### 2.3 Build and Push Frontend

```bash
cd /path/to/neuro-san-ui

docker buildx build \
  --platform linux/amd64 \
  -t neurosanhackathonacr.azurecr.io/neuro-san/neuro-san-ui:0.0.1 \
  -f apps/main/Dockerfile \
  --push \
  .
```

**Note:** Always use the repo root (`.`) as the build context — not `apps/main/`.
The Dockerfile copies files from the monorepo root (`yarn.lock`, `packages/`, etc.).

### 2.4 Verify Images

```bash
az acr repository list -n neurosanhackathonacr -o table
az acr manifest list-metadata -r neurosanhackathonacr -n neuro-san/neuro-san-ui \
  --query "[].{arch:architecture, os:os}" -o table
# Should show: linux / amd64
```

---

## Step 3 — Install Cluster Add-ons

### 3.1 Add Helm Repos

```bash
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
helm repo add external-secrets https://charts.external-secrets.io
helm repo update
```

### 3.2 Install NGINX Ingress — Backend

```bash
helm install ingress-nginx-backend ingress-nginx/ingress-nginx \
  --namespace ingress-nginx-backend \
  --create-namespace \
  --set controller.ingressClassResource.name=nginx-backend \
  --set controller.ingressClassResource.controllerValue="k8s.io/nginx-backend" \
  --set controller.ingressClassResource.enabled=true \
  --set controller.service.type=LoadBalancer
```

### 3.3 Install NGINX Ingress — Frontend

```bash
helm install ingress-nginx-frontend ingress-nginx/ingress-nginx \
  --namespace ingress-nginx-frontend \
  --create-namespace \
  --set controller.ingressClassResource.name=nginx-frontend \
  --set controller.ingressClassResource.controllerValue="k8s.io/nginx-frontend" \
  --set controller.ingressClassResource.enabled=true \
  --set controller.service.type=LoadBalancer
```

**Why two NGINX controllers?** No domain name is available for this hackathon.
Two controllers = two separate Azure public IPs. Frontend and backend each get their own IP.

### 3.4 Install External Secrets Operator

```bash
helm install external-secrets external-secrets/external-secrets \
  --namespace external-secrets \
  --create-namespace \
  --set installCRDs=true
```

**Why:** Syncs secrets from Azure Key Vault into Kubernetes secrets automatically.

### 3.5 Get Public IPs

```bash
kubectl -n ingress-nginx-backend get svc ingress-nginx-backend-controller \
  -o jsonpath='{.status.loadBalancer.ingress[0].ip}'
# e.g. 20.127.253.65  (BACKEND IP)

kubectl -n ingress-nginx-frontend get svc ingress-nginx-frontend-controller \
  -o jsonpath='{.status.loadBalancer.ingress[0].ip}'
# e.g. 20.241.198.56  (FRONTEND IP)
```

---

## Step 4 — Create Workload Identity

Workload Identity lets pods authenticate to Azure Key Vault and Blob Storage
without storing credentials inside the container.

```bash
# Create managed identity
az identity create \
  -g neuro-san-studio-marketplace-rg \
  -n neuro-san-wi \
  --location eastus

# Get values needed for federation
UAMI_CLIENT_ID=$(az identity show -g neuro-san-studio-marketplace-rg \
  -n neuro-san-wi --query clientId -o tsv)

UAMI_PRINCIPAL_ID=$(az identity show -g neuro-san-studio-marketplace-rg \
  -n neuro-san-wi --query principalId -o tsv)

OIDC_ISSUER=$(az aks show \
  -g neuro-san-studio-marketplace-rg \
  -n neuro-san-hackathon-aks \
  --query oidcIssuerProfile.issuerUrl -o tsv)

# Federate to app service account
az identity federated-credential create \
  --name fed-app \
  -g neuro-san-studio-marketplace-rg \
  --identity-name neuro-san-wi \
  --issuer "$OIDC_ISSUER" \
  --subject "system:serviceaccount:neuro-san-hackathon:unileaf-account" \
  --audience api://AzureADTokenExchange

# Federate to External Secrets Operator service account
az identity federated-credential create \
  --name fed-eso \
  -g neuro-san-studio-marketplace-rg \
  --identity-name neuro-san-wi \
  --issuer "$OIDC_ISSUER" \
  --subject "system:serviceaccount:external-secrets:external-secrets" \
  --audience api://AzureADTokenExchange
```

Note the following values — needed in `values-azure-hackathon.yaml`:
- `UAMI_CLIENT_ID` → `azure.workloadIdentityClientId`
- `UAMI_PRINCIPAL_ID` → for IAM role assignment requests to admin

---

## Step 5 — IAM Role Assignments (Admin Request)

Since role assignment requires elevated permissions, raise a request to the admin
for the following assignments on subscription `cb10268483a-cailindia-az`:

| # | Role | Identity | Object ID |
|---|---|---|---|
| 1 | `AcrPull` | AKS Kubelet Managed Identity | `ee3c7de0-57a4-4596-8e11-41fd64965eb2` |
| 2 | `Key Vault Secrets Officer` | Security Group `cb10268483a-cailindia-az` | `ad2f923e-339a-4bf5-ac19-ade816d44359` |
| 3 | `Key Vault Secrets User` | Managed Identity `neuro-san-wi` | `4a038eac-fd77-4f12-9864-f8513c42fa4e` |
| 4 | `Storage Blob Data Contributor` | Managed Identity `neuro-san-wi` | `4a038eac-fd77-4f12-9864-f8513c42fa4e` |

**Workaround for AcrPull (if admin access is delayed):**
Use ACR admin credentials to create an `imagePullSecret` directly:

```bash
# Enable admin on ACR (ask admin for username/password)
kubectl create secret docker-registry acr-pull-secret \
  --docker-server=neurosanhackathonacr.azurecr.io \
  --docker-username=neurosanhackathonacr \
  --docker-password=<ACR_ADMIN_PASSWORD> \
  --namespace neuro-san-hackathon
```

---

## Step 6 — Create Kubernetes Secrets

Create the app namespace first:

```bash
kubectl create namespace neuro-san-hackathon
```

Annotate it so Helm can manage it:

```bash
kubectl annotate namespace neuro-san-hackathon \
  meta.helm.sh/release-name=neuro-san \
  meta.helm.sh/release-namespace=neuro-san-hackathon \
  --overwrite

kubectl label namespace neuro-san-hackathon \
  app.kubernetes.io/managed-by=Helm \
  --overwrite
```

Create secrets directly (bypasses Key Vault for hackathon):

```bash
# OpenAI key (one per pod — add more if using multiple keys)
kubectl create secret generic openai-key-1 \
  --from-literal=OPENAI_API_KEY=<YOUR_OPENAI_KEY> \
  -n neuro-san-hackathon

# Azure Blob Storage connection string (fetched automatically from storage account)
kubectl create secret generic azure-storage \
  --from-literal=AZURE_STORAGE_CONNECTION_STRING="$(az storage account show-connection-string \
    -g neuro-san-studio-marketplace-rg \
    -n neurosanhackathonsa \
    --query connectionString -o tsv)" \
  -n neuro-san-hackathon
```

---

## Step 7 — Deploy with Helm

### 7.1 Update values-azure-hackathon.yaml

Fill in these placeholders before deploying:

```yaml
image:
  ui:
    repository: neurosanhackathonacr.azurecr.io/neuro-san/neuro-san-ui
    tag: "0.0.2"                          # match your pushed tag
  neuroSan:
    repository: neurosanhackathonacr.azurecr.io/neuro-san/neuro-san-studio
    tag: "0.0.1"

azure:
  workloadIdentityClientId: "31036678-9d77-4e78-840b-93fba4233a90"  # UAMI_CLIENT_ID
  keyVaultUrl: "https://neuro-san-hackathon-kv.vault.azure.net"

NEURO_SAN_SERVER_URL: "http://20.127.253.65"   # BACKEND PUBLIC IP
AGENT_EXTERNAL_SERVER_URL: "http://20.127.253.65"
```

### 7.2 Dry-run (sanity check)

```bash
cd neuro-san-deploy-main/azure-hackathon

helm template neuro-san . -f values-azure-hackathon.yaml | head -80
```

### 7.3 Install

```bash
helm upgrade --install neuro-san . \
  -f values-azure-hackathon.yaml \
  -n neuro-san-hackathon
```

### 7.4 Verify

```bash
kubectl get pods -n neuro-san-hackathon
# Expected:
# neuro-san-key-1-xxxxx   1/1   Running
# ui-node-deployment-xxx  1/1   Running

kubectl get ingress -n neuro-san-hackathon
# Expected:
# neuro-san     nginx-backend   *   20.127.253.65   80
# neuro-san-ui  nginx-frontend  *   20.241.198.56   80
```

---

## Step 8 — Frontend Code Changes

Three changes were made to `neuro-san-ui` to disable authentication for Azure deployment:

### 8.1 Disable NextAuth route handler
**File:** `apps/main/app/api/auth/[...nextauth]/route.ts`

Added a check — when `NEXT_PUBLIC_ENABLE_AUTHENTICATION=false`, all auth routes
return 200 immediately instead of trying to contact Auth0:

```typescript
const authDisabled = process.env["NEXT_PUBLIC_ENABLE_AUTHENTICATION"] === "false"
const noopHandler = (_req: NextRequest) =>
    NextResponse.json({status: "auth disabled"}, {status: 200})

export const GET = authDisabled ? noopHandler : handlers.GET
export const POST = authDisabled ? noopHandler : handlers.POST
```

### 8.2 Remove authRequired from protected pages
**Files:** `pages/multiAgentAccelerator/index.tsx`, `pages/UserGuide.tsx`

Changed `authRequired: true` → `authRequired: false` so pages load without login.

### 8.3 Bake NEXT_PUBLIC env var into build
**File:** `apps/main/.env.production`

```
NEXT_PUBLIC_ENABLE_AUTHENTICATION=false
```

`NEXT_PUBLIC_` variables are compiled at build time in Next.js —
setting them only at runtime (via ConfigMap) does not work.

### 8.4 Remove ALB OIDC header
**File:** `apps/main/pages/api/userInfo/index.ts`

Removed AWS ALB OIDC header parsing — returns `{ oidcHeaderFound: false }` directly:

```typescript
export default async function handler(_req: NextApiRequest, res: NextApiResponse<UserInfoResponse>) {
    res.status(httpStatus.OK).json({oidcHeaderFound: false})
}
```

---

## Step 9 — Backend Code Changes

### 9.1 Azure Blob Reservations Storage
**File:** `neuro_san_studio/azure_blob_reservations_storage.py`

New class that replaces `S3ReservationsStorage` for Azure deployments.
Reads from env vars:
- `AZURE_STORAGE_CONNECTION_STRING` — storage account connection string
- `AGENT_RESERVATIONS_BLOB_CONTAINER` — container name (`neuro-san-reservations`)

### 9.2 requirements.txt

Added:
```
azure-storage-blob>=12.19.0
```

---

## Step 10 — CORS Configuration

CORS is handled at two levels:

### 10.1 Backend application
`AGENT_ALLOW_CORS_HEADERS=true` enables CORS in the neuro-san server.

### 10.2 NGINX ingress annotations
In `templates/ingress.yaml` for the backend ingress:

```yaml
nginx.ingress.kubernetes.io/enable-cors: "true"
nginx.ingress.kubernetes.io/cors-allow-origin: "*"
nginx.ingress.kubernetes.io/cors-allow-methods: "GET, POST, PUT, DELETE, OPTIONS"
nginx.ingress.kubernetes.io/cors-allow-headers: "Authorization, Content-Type, Accept, user_id, DNT"
```

**Important:** The `user_id` header must be explicitly listed — browsers block
preflight requests for any header not in this list.

---

## Redeployment Workflow

When you make code changes:

```bash
# 1. Build new image with incremented tag
cd /path/to/neuro-san-ui
docker buildx build --platform linux/amd64 \
  -t neurosanhackathonacr.azurecr.io/neuro-san/neuro-san-ui:0.0.3 \
  -f apps/main/Dockerfile --push .

# 2. Update tag in values file
# Edit azure-hackathon/values-azure-hackathon.yaml: tag: "0.0.3"

# 3. Helm upgrade
cd neuro-san-deploy-main/azure-hackathon
helm upgrade neuro-san . -f values-azure-hackathon.yaml -n neuro-san-hackathon

# 4. Watch rollout
kubectl rollout status deployment/ui-node-deployment -n neuro-san-hackathon
```

---

## Useful Commands

```bash
# Check all pods
kubectl get pods -n neuro-san-hackathon

# Check pod logs
kubectl logs -n neuro-san-hackathon deployment/ui-node-deployment --tail=50
kubectl logs -n neuro-san-hackathon deployment/neuro-san-key-1 --tail=50

# Check ingress and IPs
kubectl get ingress -n neuro-san-hackathon

# Restart a deployment without changing image
kubectl rollout restart deployment/ui-node-deployment -n neuro-san-hackathon

# Check secrets exist
kubectl get secrets -n neuro-san-hackathon

# Test backend health
curl http://20.127.253.65/readyz

# Test CORS preflight
curl -X OPTIONS http://20.127.253.65/api/v1/tools/anthropic_code_execution/connectivity \
  -H 'Origin: http://20.241.198.56' \
  -H 'Access-Control-Request-Method: GET' \
  -H 'Access-Control-Request-Headers: Content-Type,user_id' -I

# Helm status
helm list -n neuro-san-hackathon
helm history neuro-san -n neuro-san-hackathon
```

---

## Resource Summary

| Resource | Name | Value |
|---|---|---|
| Subscription | cb10268483a-cailindia-az | `776a9397-b4b1-4465-a4a8-f05aa893cf8a` |
| Resource Group | neuro-san-studio-marketplace-rg | eastus |
| AKS Cluster | neuro-san-hackathon-aks | 3 nodes, Standard_D4s_v3 |
| Container Registry | neurosanhackathonacr | neurosanhackathonacr.azurecr.io |
| Key Vault | neuro-san-hackathon-kv | https://neuro-san-hackathon-kv.vault.azure.net |
| Storage Account | neurosanhackathonsa | Blob container: neuro-san-reservations |
| Managed Identity | neuro-san-wi | Client ID: 31036678-9d77-4e78-840b-93fba4233a90 |
| Backend IP | NGINX ingress-nginx-backend | http://20.127.253.65 |
| Frontend IP | NGINX ingress-nginx-frontend | http://20.241.198.56 |
| Backend Image | neuro-san/neuro-san-studio | tag: 0.0.1 |
| Frontend Image | neuro-san/neuro-san-ui | tag: 0.0.2 |
