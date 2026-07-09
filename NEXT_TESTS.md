# Remaining Pre-Go-Live Tests — build complete, ready to run

Four gaps from the readiness review are now built. This is what each is, and the exact
command to run it. All run from the in-cluster `loadgen` pod (see
`Load testing Details July 6th 2026.md` §9.2 for pod setup); the chaos drill runs from
your Mac (or the loadgen pod — it has the admin kubeconfig).

> After editing `loadtest/`, re-copy it into the pod:
> `kubectl -n neuro-san-hackathon cp loadtest loadgen:/tmp/loadtest`
> Shell prefix used below: `EXEC='export PATH=/tmp/bin:$PATH KUBECONFIG=/etc/loadgen-kube/config; cd /tmp/loadtest;'`

---

## 1. Design latency (time-to-first-token) — ✅ now measured automatically
**What changed:** `metrics.LatencyTracker` + `users.SessionUser` now record, for the
*design POSTs only* (not the fast login GETs that made p50/p95 read ~0s):
- **TTFT** — request sent → first streamed byte ("did it start responding?")
- **full design duration** — request sent → whole stream drained

It prints on every snapshot and in the summary of **`hackathon_test.py`**, and is saved
to the JSON report under `summary.design_latency`. No new command — just run any backend
test and read the new **`DESIGN`** / **`DESIGN LATENCY`** lines.

**Pass targets:** TTFT p95 < 30–60s · full design p95 < 3–5 min (realistic profile).

---

## 2. Realistic-profile run (natural think-time, ~750 concurrent)
Measures the **actual event zone** directly instead of extrapolating from the 2,700
worst case. Same runner, longer think time (5–15 min) so only ~30% are mid-design.

```bash
kubectl -n neuro-san-hackathon exec loadgen -- sh -c '
  export PATH=/tmp/bin:$PATH KUBECONFIG=/etc/loadgen-kube/config THINK_TIME_MIN=300 THINK_TIME_MAX=900;
  cd /tmp/loadtest; TS=$(date +%Y%m%d_%H%M%S);
  nohup python3 -u hackathon_test.py --vus 2500 --duration 60 --ramp-min 10 \
    > reports/realistic_$TS.log 2>&1 & echo started $! log=reports/realistic_$TS.log'
```
**Pass targets:** backend CPU well under saturation (not pinned at ~87%), **no**
`⚠ SATURATED` flags, DESIGN full p95 in the low minutes, 0 errors.

---

## 3. Frontend / login-wave test (the UI tier — ~6 UI pods) — ✅ new
Answers *"when 2,500 people open the URL in the same minute, does the UI stay up and
fast?"* — the arrival spike the UI replicas exist for. Each virtual browser pulls the
app shell + JS/CSS bundle + `/api/environment` + `/api/auth/session` (`users.FrontendUser`).

```bash
# STAMPEDE: 2,500 browsers arriving in 60s, hold 5 min
kubectl -n neuro-san-hackathon exec loadgen -- sh -c '
  export PATH=/tmp/bin:$PATH KUBECONFIG=/etc/loadgen-kube/config;
  cd /tmp/loadtest; TS=$(date +%Y%m%d_%H%M%S);
  nohup python3 -u frontend_test.py --vus 2500 --ramp-min 1 --duration 6 \
    > reports/frontend_$TS.log 2>&1 & echo started $! log=reports/frontend_$TS.log'
```
**Pass targets:** frontend 5xx < 1% · UI CPU not pinned · p95 shell < 3–5s · 0 UI pod
restarts. (HTTP-level, not a headless browser — it drives the Node UI pods, which is
what a login stampede actually hits. No SSO flow is exercised because auth is OFF; if
you enable Auth0 for the event, tell me and I'll add the callback/session path.)

---

## 4. Failure drills (kill a pod / drain a node) — ✅ new
Confirms the platform survives real failures mid-event: a pod dying (do sticky sessions
survive via blob state + reroute?) and a node draining. Run it **while a moderate load
test is running** (~200–500 VUs, *not* 2,700), from your Mac:

```bash
cd loadtest
./chaos_drill.sh                 # pod-kill drill only (safe default)
./chaos_drill.sh --node-drain    # also cordon+drain one pool16 node (more disruptive)
```
It kills one backend pod, times its recovery, and — crucially — probes the **public
API `/readyz` every 2s** throughout, reporting how many probes stayed 200. **Pass:** API
stays ~100% (only the sticky users on the killed pod see a blip), replacement pod
returns to Ready, restart counts stay 0 elsewhere.

---

## 5. Staggered arrivals (event-day lever — operational, not code)
The #1 smoothness lever. It keeps concurrency near ~750 (fast zone) instead of a 2,700
stampede. **This is an event-day action, not a test:**
- Release the URL in **waves** (e.g. 5 waves of ~500, 10 min apart), **or** open the app
  ~30 min early so arrivals spread naturally.
- You can *see* the payoff by comparing the frontend test at `--ramp-min 1` (stampede)
  vs `--ramp-min 5` (staggered): the UI CPU peak and p95 should drop markedly.

---

## Suggested order for one cluster-up session
1. Bring the cluster up + apply the validated config (July 6 doc §9.1), pre-warm.
2. **Realistic-profile run** (#2) — the headline "is it fast?" measurement, now with real DESIGN latency (#1).
3. **Frontend login-wave** (#3) — stampede then staggered.
4. **Failure drills** (#4) at ~300 VUs.
5. `az aks stop` to stop billing.
