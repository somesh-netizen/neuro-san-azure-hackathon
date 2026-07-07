# Run the Hackathon Load Test — Command Sequence

Run these in order. Stages 1–2 are one-time setup (~40–60 min); Stage 4 is the 30-min
test. **Cost runs from Stage 1 (cluster billing) — do Stage 5 teardown when done.**

Identifiers: cluster `neuro-san-hackathon-aks` · RG `neuro-san-studio-marketplace-rg`
· app release `neuro-san` @ ns `neuro-san-hackathon` · ingress releases
`ingress-nginx-backend` / `ingress-nginx-frontend`.

---

## STAGE 1 — Start cluster + apply the redeploy (the NEW topology)

```bash
cd /Users/2508345/Downloads/neuro-san-deploy-main/neuro-san-azure-hackathon

# 1a. Start the cluster
az aks start --name neuro-san-hackathon-aks --resource-group neuro-san-studio-marketplace-rg
az aks get-credentials --name neuro-san-hackathon-aks --resource-group neuro-san-studio-marketplace-rg --overwrite-existing
until kubectl get --raw='/readyz' >/dev/null 2>&1; do echo "waiting for API..."; sleep 15; done
kubectl get nodes

# 1b. Add the dedicated D16 backend pool — FIXED 12 nodes, NO autoscaler
az aks nodepool add --cluster-name neuro-san-hackathon-aks -g neuro-san-studio-marketplace-rg \
  --name pool16 --node-vm-size Standard_D16s_v3 --node-count 12 --mode User \
  --labels workload=neuro-san-backend
kubectl get nodes -l agentpool=pool16 -w      # Ctrl-C once all 12 are Ready

# 1c. Upgrade ingress controllers FIRST (HA + rate-limit zones). MUST precede 1d.
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx && helm repo update
helm get values ingress-nginx-backend  -n ingress-nginx-backend  -o yaml > /tmp/inb.yaml
helm upgrade ingress-nginx-backend  ingress-nginx/ingress-nginx --version 4.15.1 \
  -n ingress-nginx-backend  -f /tmp/inb.yaml -f ingress-nginx-backend-values.yaml
helm get values ingress-nginx-frontend -n ingress-nginx-frontend -o yaml > /tmp/inf.yaml
helm upgrade ingress-nginx-frontend ingress-nginx/ingress-nginx --version 4.15.1 \
  -n ingress-nginx-frontend -f /tmp/inf.yaml -f ingress-nginx-frontend-values.yaml

# 1d. Deploy the app chart (11 pods → pool16, UI → 6, rate-limit annotation, 600s timeout)
helm upgrade neuro-san . -f values-azure-hackathon.yaml -n neuro-san-hackathon
kubectl -n neuro-san-hackathon rollout status deploy/neuro-san-key-1 --timeout=6m

# 1e. Verify the new topology
kubectl -n neuro-san-hackathon get pods -o wide | grep -E "key|ui"   # 11 keys on pool16 (1/node), 6 UI, no key-9
kubectl -n ingress-nginx-backend get pods                             # 2 replicas
kubectl -n neuro-san-hackathon get ingress neuro-san \
  -o jsonpath='{.metadata.annotations.nginx\.ingress\.kubernetes\.io/proxy-read-timeout}{"\n"}'  # 600
```

---

## STAGE 2 — Load generator = an in-cluster POD (VM creation is policy-blocked)

> ⚠️ Standalone VM creation is **denied by subscription policy** ("CloudBoost restricted
> guardrail-54" blocks creating the VM's NIC/NSG). AKS node pools ARE allowed, so the
> generator runs as a **pod on a dedicated, tainted `loadpool` node** — same region,
> dedicated 16-vCPU D16s_v3, within policy. **This is already set up** (loadpool node +
> `loadgen` pod + code + locust + kubectl + admin kubeconfig all in place and verified).
> Commands below are for reference / rebuild.

```bash
# (already done — reference only)
az aks nodepool add --cluster-name neuro-san-hackathon-aks -g neuro-san-studio-marketplace-rg \
  --name loadpool --node-vm-size Standard_D16s_v3 --node-count 1 --mode User \
  --labels role=loadgen --node-taints dedicated=loadgen:NoSchedule
az aks get-credentials --admin --name neuro-san-hackathon-aks -g neuro-san-studio-marketplace-rg -f /tmp/loadgen-kubeconfig --overwrite-existing
kubectl -n neuro-san-hackathon create secret generic loadgen-kubeconfig --from-file=config=/tmp/loadgen-kubeconfig
kubectl apply -f <scratchpad>/loadgen-pod.yaml           # pod on loadpool, mounts kubeconfig
kubectl -n neuro-san-hackathon cp loadtest loadgen:/tmp/loadtest
kubectl -n neuro-san-hackathon exec loadgen -- python3 -m pip install --user locust requests python-dotenv
# kubectl binary fetched into the pod at /tmp/bin via python urllib
```

Inside the pod: code at `/tmp/loadtest`, `kubectl` at `/tmp/bin` (admin kubeconfig at
`/etc/loadgen-kube/config`). `az` is NOT in the pod → the per-key TPM line shows "n/a";
the client-side token total (captured by the runner) is authoritative. For a per-key
breakdown, run the optional LOCAL command in Stage 4.

---

## STAGE 3 — Pre-warm all pods (run from your Mac; executes in the pod)

```bash
kubectl -n neuro-san-hackathon exec loadgen -- sh -c '
  export PATH=/tmp/bin:$PATH KUBECONFIG=/etc/loadgen-kube/config
  cd /tmp/loadtest && python3 prewarm.py
'                                   # → "All pods are warm"
```

Re-run if >15 min pass before Stage 4 (Azure keep-alive connections idle out).

---

## STAGE 4 — Run the 30-min test (detached in the pod, so a dropped terminal won't kill it)

```bash
# 4a. kick off the run in the background INSIDE the pod
kubectl -n neuro-san-hackathon exec loadgen -- sh -c '
  export PATH=/tmp/bin:$PATH KUBECONFIG=/etc/loadgen-kube/config
  cd /tmp/loadtest
  nohup python3 -u hackathon_test.py --vus 2700 --duration 30 --ramp-min 8 \
    > /tmp/loadtest/run.log 2>&1 &
  echo "started PID $!"
'

# 4b. watch it live (Ctrl-C on tail does NOT stop the test; reconnect any time)
kubectl -n neuro-san-hackathon exec loadgen -- tail -f /tmp/loadtest/run.log

# 4c. (optional, on your MAC — has az) per-key token usage over the last 30 min:
cd loadtest && python3 -c "from config import AZURE_OPENAI_RESOURCES; from metrics import get_per_key_tpm; import json; print(json.dumps(get_per_key_tpm(AZURE_OPENAI_RESOURCES,'PT30M'), indent=2))"
```

- Ramps 0→2,700 over 8 min (the scaling curve — watch for a non-linear knee), then holds.
- Snapshot every 60s; JSON report written to `/tmp/loadtest/reports/` in the pod.
- Sanity-watch the generator isn't the bottleneck: `kubectl top nodes -l role=loadgen`
  (if the loadpool node CPU pins ~100%, it's generator-bound — note it).

**No token cap** (per your call). Expected burn ~180–280M tokens (~$35–60); bounded by
cluster completion throughput (~50–150 designs/min), so it can't run away.

```bash
# 4d. pull the JSON report back to your Mac when done
kubectl -n neuro-san-hackathon cp loadgen:/tmp/loadtest/reports ./reports-from-pod
```

---

## STAGE 5 — Teardown (STOP BILLING)

```bash
# load generator
kubectl -n neuro-san-hackathon delete pod loadgen
kubectl -n neuro-san-hackathon delete secret loadgen-kubeconfig
az aks nodepool delete --cluster-name neuro-san-hackathon-aks -g neuro-san-studio-marketplace-rg --name loadpool

# backend D16 pool (keep cluster) …
az aks nodepool delete --cluster-name neuro-san-hackathon-aks -g neuro-san-studio-marketplace-rg --name pool16
# … OR stop the whole cluster
az aks stop --name neuro-san-hackathon-aks --resource-group neuro-san-studio-marketplace-rg
```
