# Load Testing Details — Neuro-San Azure Hackathon — July 9, 2026 (morning)

**Author:** Somesh Pattanaik (somesh.pattanaik@cognizant.com)
**Builds on:** `Load testing Details July 6th 2026.md` (the validated 2,700-VU backend soak).
**Focus today:** close the review gaps — a real **frontend/UI-tier** load test, **design-latency
(time-to-first-token)** measurement, **failure drills**, and a **realistic end-to-end** run with
both tiers up (6 UI pods + 11 backend pods).

---

## 0. TL;DR

- Brought the full fleet up: **11 backend `neuro-san-key-*` pods** (1/D16 node, distinct Azure keys)
  + **6 `ui-node` UI pods**, Helm **rev 30**, no stale HPA.
- Built new load-test tooling: a **frontend/login-wave test** (`frontend_test.py` + `FrontendUser`),
  **TTFT/design-latency tracking** (`metrics.LatencyTracker`), a **failure-drill script**
  (`chaos_drill.sh`), and a **distributed-mode entrypoint** (`fe_locustfile.py`).
- **Frontend stampede (2,500 concurrent browsers, distributed 12-core generator):**
  **0 failures, 0 pod restarts.** UI tier survived. But under this deliberately harsh synthetic
  load the UI pods **hit their 2-core CPU limit** (2 of 6 pinned at 2000m, fleet avg ~85%) — it
  degraded *gracefully* (slower bundle serving, no errors). Memory trivial (~5–9%).
- **Realistic end-to-end (5–15 min think, 2,500 continuously-active VUs):** **100% success, 0
  restarts** — but the fleet **SATURATED** (~85% CPU, ~1,650 concurrent designs, *not* the hoped
  ~700). TTFT stayed fast (p50 0.4s / p95 4.2s) but **full design time was slow under saturation**
  (p50 11.3 min, p95 38.6 min). Throughput ceiling ~72 designs/min; tokens 4.3%. **The fast zone is
  NOT automatic at 2,500 active** — it depends on keeping concurrency under the ~900–1,200-designer
  knee (⇒ stagger arrivals).
- **Key recommendation:** the UI pods' CPU is spent almost entirely **serving the static JS/CSS
  bundle**. Put static assets behind a **CDN / cache** (biggest lever), and/or add UI CPU/replicas,
  and **stagger arrivals** — then the UI tier has ample headroom for the event.

---

## 1. Environment / fleet state (July 9)

| Item | Value |
|---|---|
| AKS cluster | `neuro-san-hackathon-aks` (rg `neuro-san-studio-marketplace-rg`) |
| Namespace | `neuro-san-hackathon` |
| Helm release / revision | `neuro-san` / **rev 30** |
| Backend pods | **11** `neuro-san-key-{1..8,10,11,12}`, 1 per D16 node (pool16), distinct Azure OpenAI keys |
| UI pods | **6** `ui-node-deployment` (label `app=ui-node`), 2-core CPU limit / 6Gi mem limit |
| Frontend | `https://hackathon.evolution.ml` (Next.js UI) |
| Backend API | `https://neurosanhackathon-api.eastus.cloudapp.azure.com` |
| Model | deployment `gpt-5-mini` = **gpt-4o-mini** ($0.15/$0.60 per 1M) |
| HPA | none (correct) |
| Load generator | in-cluster `loadgen` pod on dedicated `loadpool` (1× D16), admin kubeconfig mounted |

**Note on UI pod placement:** the 6 UI pods are packed onto pool16 nodes (several per node), so
per-pod CPU% is measured against the 2-core limit, and balancing across pods was uneven (see §4).

---

## 2. New load-test tooling built today

| File | What it adds |
|---|---|
| `loadtest/metrics.py` → `LatencyTracker` | Records **TTFT** (time-to-first-stream-event) + **full design duration** for the design POSTs only, separate from the fast login GETs that made the old p50/p95 read ~0s. Printed as `DESIGN` / `DESIGN LATENCY` lines and saved to the JSON report. |
| `loadtest/users.py` → `FrontendUser` | Simulates a real **browser first-load** of the Next.js UI: `GET /` (shell) + parse & fetch the JS/CSS **bundle** + `GET /api/environment` + `GET /api/auth/session`. This is the UI-tier stampede driver. Also added `_read_stream_timed()` (TTFT capture) and `_extract_assets()`. |
| `loadtest/frontend_test.py` | Single-process runner for the UI test with inline UI-pod CPU/mem snapshots + per-endpoint latency. |
| `loadtest/fe_locustfile.py` | Distributed-mode entrypoint (`python3 -m locust --processes N`) so the generator uses all D16 cores — required to actually drive 2,500 fast-GET browsers. |
| `loadtest/chaos_drill.sh` | Failure drills: kill a backend pod / drain a node while probing the public API `/readyz` every 2s to prove the *event* stays up. |
| `loadtest/loadgen-pod.yaml` | The in-cluster load-generator pod manifest (previously only lived inside a doc). |
| `NEXT_TESTS.md` | Runbook for all four remaining pre-go-live tests. |

TTFT was the review's #1 gap: the backend runner now prints real design latency, e.g.
`DESIGN LATENCY  TTFT p50 …s / p95 …s | full design p50 …s / p95 …s`, instead of the
login-GET-diluted `p50 0s`.

---

## 3. Test 1 — Frontend / login-wave stampede (UI tier)

**Goal:** does the UI tier survive 2,500 people opening the URL in ~60s?
**How:** `fe_locustfile.py` via distributed Locust (`--processes 12`) so the generator isn't the
bottleneck. Each `FrontendUser` pulls shell + bundle + `/api/environment` + `/api/auth/session`.
`FrontendUser.wait_time` = 15–45s (load-then-read); the arrival burst comes from the 60s ramp.

**Command:**
```
python3 -m locust -f fe_locustfile.py --headless -u 2500 -r 42 -t 6m --processes 12 \
  --host https://hackathon.evolution.ml
```

### Results — PASS on reliability

| Metric | Value |
|---|---|
| Peak concurrent browsers | **2,500** (all spawned) |
| Sustained throughput | ~**700–1,000 req/s** aggregate |
| **Failures** | **0 (0.00%)** — every endpoint, every snapshot |
| Total requests (at truncation) | ~86,000+ |
| UI pod restarts | **0** (all 6 `Running`, AGE 2d — never recreated) |

### Per-endpoint latency (representative, mid-run)
| Endpoint | median | max | fails |
|---|---|---|---|
| `GET /api/environment` | **~19 ms** | 503 ms | 0 |
| `GET /api/auth/session` | ~76 ms | 611 ms | 0 |
| `GET /` (app shell) | ~330 ms | 1,696 ms | 0 |
| `GET /_next/static/*` (bundle) | ~350 ms | **~31 s** | 0 |

### UI-pod resource usage at load (limit = 2000m CPU / 6Gi mem)
| Pod | CPU | Mem |
|---|---|---|
| gqlql | **2003m (pinned)** | 529Mi (9%) |
| f8z7x | **2002m (pinned)** | 385Mi |
| bx7n5 | 1680m (84%) | 282Mi |
| krdfd | 1574m (79%) | 309Mi |
| ss4ms | 1530m (77%) | 311Mi |
| n8bml | 1362m (68%) | 306Mi |

### Verdict
- **Reliable:** 0 errors, 0 restarts, all 6 pods up through 2,500 concurrent arrivals. It never fell over.
- **At its CPU ceiling:** 2 of 6 pods **maxed at the 2-core limit** (~85% fleet avg). The cheap API
  routes stayed at ~19 ms because they're trivial; the **static-bundle serving** is what ate the CPU
  and grew the bundle latency tail (max ~31 s) — CPU throttling, not failure.
- **Harsher than reality:** `FrontendUser` re-pulls the bundle every 15–45s, but a **real browser
  caches it after first load**, so real UI CPU will be a fraction of this. 6×2-core is likely fine
  for the real event.

### Recommendations (by impact)
1. **CDN / cache static assets** (`/_next/static/*`) — removes bundle-serving CPU from the pods; the biggest lever.
2. **Raise UI CPU limit above 2 cores** and/or **add UI replicas** (UI memory is tiny — cheap).
3. **Stagger arrivals** — keeps concurrency below the cap.
4. **Fix uneven balancing** — pods ranged 1362m→2003m; frontend ingress isn't spreading evenly.

---

## 4. Test 2 — Realistic end-to-end (both tiers, 5–15 min think) — RESULTS

Ran the July 6 methodology at **realistic think time** (5–15 min). Each `SessionUser` loads the UI
once (login wave) then runs the closed-loop design→refine backend load; the new TTFT/design-latency
is recorded.

- **Config:** 2,500 VUs · think 300–900s (5–15 min) · 60 min · 10-min ramp · gpt-4o-mini.
- **Log/report:** `realistic_20260709_012306.log` / `hackathon_test_20260709_012306.json`.
- **Command:** `THINK_TIME_MIN=300 THINK_TIME_MAX=900 python3 -u hackathon_test.py --vus 2500 --duration 60 --ramp-min 10` (in the loadgen pod; §5).

### Results
| Metric | Value |
|---|---|
| Peak concurrent VUs | 2,500 |
| Peak in-flight designs | **1,910** (steady ~1,600–1,690) |
| Designs completed | **4,360** (0 left in-flight at end) |
| Total requests / success | 9,530 / **100.0%** (2 failed) |
| Completion throughput | **~72 designs/min** (~1.2/s) |
| Backend CPU | **~85–87% pinned (SATURATED)** |
| Backend memory | 16–21% of 48Gi (no pressure) |
| **DESIGN TTFT** | **p50 0.4s · p95 4.2s** (fast to first token) |
| **DESIGN full duration** | **p50 679s (11.3 min) · p95 2,314s (38.6 min) · p99 2,763s (46 min) · max 3,331s (55.5 min)** |
| Token burn | 14.35M/min = **4.3% of 330M TPM** |
| Cost | **$146** |
| 429s / pod restarts / OOM | 0 / 0 / 0 |
| UI pods | **0.1% CPU** (light login load only) |
| Per-turn tokens | t1 269k · t2 132k · t3 98k · t4 40k · t5 13k · t6 4k · t7 4k |

### Analysis — survives, but SATURATED (not the "fast zone")
At **2,500 continuously-active** users with 5–15 min think, in-flight settled ~**1,650** — *not* the
~700 predicted — and the 11-pod fleet **saturated** (~85% CPU), exactly like the 2,700-VU worst case.
Why: the fleet's **completion ceiling is ~70 designs/min** (CPU-bound, same as July 6), but 2,500
active users *demand* far more, so a queue builds and each design slows to an **11-min median (up to
55 min)** — self-sustaining saturation.

**But it held:** 100% success, 0 restarts, 0 OOM, 0 rate-limit hits, queue fully drained by the end —
graceful degradation, same as July 6. And **TTFT stayed fast (p50 0.4s / p95 4.2s)**, so a participant
sees the design *start streaming* within seconds even when full completion is slow — "responding but
slow to finish," not "frozen." Tokens (4.3%) and the UI tier (0.1%) were non-issues.

**Why this is harsher than the real event (so real ≈ better):**
1. It models **2,500 users looping continuously** (think → design → think, never idle). Real
   participants code/discuss/break between designs — a **much lower duty cycle** → fewer concurrent designers.
2. The load-test prompts are **deliberately brutal** (11–14 agents, ~270k tokens, 11-min designs).
   Real prompts are lighter → faster designs → higher throughput → less queuing.

**The real lesson — the fast experience is NOT automatic at 2,500; it depends on concurrency.** The
fleet stays fast only while **concurrent active designers stay under ~900–1,200** (the knee where
arrival ≤ ~70/min completion). Above that, it queues (survives, but slow).

**Levers (in order):**
1. **Stagger arrivals** (#1) — release the URL in waves so you never have ~2,500 designing at once;
   keeps concurrency under the knee → fast designs.
2. **Add backend pods/nodes** if you genuinely expect >~1,200 concurrent designers (compute is the
   only lever — NOT tokens/keys).
3. Real (lighter) prompts naturally raise the ceiling above what this brutal test shows.

**Caveat:** the runner is single-process Locust; near the end it logged a generator CPU warning, so
the extreme latency *tail* may be slightly generator-inflated — but the backend was independently at
~85% CPU (kubectl), so the saturation itself is real, not an artifact. For a fully clean latency tail,
re-run with distributed Locust (`--processes`), as the frontend test used.

---

## 5. Command reference (July 9)

All run from the Mac; load executes in the in-cluster `loadgen` pod.

### Bring the fleet up (already done this session)
```
az aks start --name neuro-san-hackathon-aks -g neuro-san-studio-marketplace-rg
az aks get-credentials --name neuro-san-hackathon-aks -g neuro-san-studio-marketplace-rg --overwrite-existing
az aks nodepool scale --cluster-name neuro-san-hackathon-aks -g neuro-san-studio-marketplace-rg --name pool16 --node-count 12
helm upgrade neuro-san . -f values-azure-hackathon.yaml -n neuro-san-hackathon
kubectl -n neuro-san-hackathon rollout status deploy/neuro-san-key-1 --timeout=8m
kubectl -n neuro-san-hackathon get pods -o wide | grep -E "key|ui-node"
```

### Load-gen pod setup
```
kubectl apply -f loadtest/loadgen-pod.yaml
tar cf - -C loadtest --exclude='.venv' --exclude='__pycache__' --exclude='reports' --exclude='reports_archive' . | kubectl -n neuro-san-hackathon exec -i loadgen -- sh -c 'mkdir -p /tmp/loadtest && tar xf - -C /tmp/loadtest'
kubectl -n neuro-san-hackathon exec loadgen -- mkdir -p /tmp/loadtest/reports
kubectl -n neuro-san-hackathon exec loadgen -- python3 -c "import urllib.request as u,os; os.makedirs('/tmp/bin',exist_ok=True); v=u.urlopen('https://dl.k8s.io/release/stable.txt').read().decode().strip(); u.urlretrieve('https://dl.k8s.io/release/'+v+'/bin/linux/amd64/kubectl','/tmp/bin/kubectl'); os.chmod('/tmp/bin/kubectl',0o755); print('kubectl',v,'ready')"
kubectl -n neuro-san-hackathon exec loadgen -- python3 -m pip install --user locust requests python-dotenv
```

### Frontend / login-wave stampede
```
kubectl -n neuro-san-hackathon exec loadgen -- sh -c 'export PATH=/tmp/bin:$PATH KUBECONFIG=/etc/loadgen-kube/config; cd /tmp/loadtest; TS=$(date +%Y%m%d_%H%M%S); nohup python3 -m locust -f fe_locustfile.py --headless -u 2500 -r 42 -t 6m --processes 12 --host https://hackathon.evolution.ml > reports/fe_stampede_$TS.log 2>&1 & echo started log=reports/fe_stampede_$TS.log'
```

### Realistic end-to-end (both tiers, 5–15 min think)
```
kubectl -n neuro-san-hackathon exec loadgen -- sh -c 'export PATH=/tmp/bin:$PATH KUBECONFIG=/etc/loadgen-kube/config; cd /tmp/loadtest; python3 prewarm.py'
kubectl -n neuro-san-hackathon exec loadgen -- sh -c 'export PATH=/tmp/bin:$PATH KUBECONFIG=/etc/loadgen-kube/config THINK_TIME_MIN=300 THINK_TIME_MAX=900; cd /tmp/loadtest; TS=$(date +%Y%m%d_%H%M%S); nohup python3 -u hackathon_test.py --vus 2500 --duration 60 --ramp-min 10 > reports/realistic_$TS.log 2>&1 & echo started log=reports/realistic_$TS.log'
```

### Watch / collect / teardown
```
kubectl -n neuro-san-hackathon exec loadgen -- sh -c 'tail -f $(ls -t /tmp/loadtest/reports/realistic_*.log | head -1)'
kubectl -n neuro-san-hackathon top pods -l app=ui-node
kubectl -n neuro-san-hackathon top pods -l app=neuro-san
kubectl -n neuro-san-hackathon cp loadgen:/tmp/loadtest/reports ./loadtest/reports_archive/july9_reports
kubectl -n neuro-san-hackathon exec loadgen -- pkill -f locust
az aks stop --name neuro-san-hackathon-aks -g neuro-san-studio-marketplace-rg
```

---

## 6. Overall findings (July 9)

- **Both tiers survive worst-case arrival load with zero errors.** Backend was already proven (July 6:
  90-min soak, 0 errors). Frontend now proven too: 2,500 concurrent browsers, 0 failures, 0 restarts.
- **BUT "survives" ≠ "fast."** The realistic run (§4) showed 2,500 continuously-active users
  saturate the fleet (~85% CPU, ~1,650 concurrent, 11-min median design) — it stays up with 0 errors
  and fast TTFT (<5s), but full designs queue and slow down. **The fleet's ceiling is ~70 designs/min**;
  the experience is fast only while concurrent designers stay under ~900–1,200. **Staggering arrivals
  is therefore not optional — it's the difference between a fast event and an 11-minute wait.**
- **The UI tier's limit is static-asset (bundle) CPU**, not memory or the API routes — addressable with
  a CDN/cache and/or a bit more CPU. Not a blocker; degrades gracefully.
- **Design latency is now measured** (TTFT + full duration), so the realistic run will finally quantify
  the participant experience, not just error rate.
- **Event-day levers unchanged:** stagger arrivals (#1), pre-warm, CDN for static assets, do not add
  Azure keys/TPM (tokens are ~40× over-provisioned).

---

## 7. Reducing design latency (11.3 min → target 5 min) + scaling the fleet

**What the 11.3 min is:** queue wait + intrinsic design time. Little's law on the run
(1,650 in-flight ÷ 72/min ≈ 23 min mean) shows **most of it is queue** — the fleet completes
~70 designs/min but 2,500 active users demanded ~178/min. So **reducing 11→5 min = removing the
queue (don't saturate) + trimming the intrinsic (lighter work).**

### Lever 1 — de-saturate (removes the queue)
- **(A) FREE — stagger arrivals.** Keep concurrent-active under the ~1,000 knee → the fleet stops
  queuing → design time drops to ~intrinsic (~5–6 min) on the **current 11 pods**. No scaling, no
  quota, no cost.
- **(B) 3× the pods** — `replicasPerKey: 1 → 3` = **33 pods** (configured in
  `values-azure-hackathon.yaml`; token-safe — 33 pods share 11 keys at ~13% of each key's 30M).
  Lifts the ceiling ~70 → ~210 designs/min so 2,500 concurrent-active doesn't saturate.
  **BLOCKED BY A COMPUTE-QUOTA WALL:** Standard **DSv3 vCPU limit in eastus = 350**; 33 pods +
  loadpool + system ≈ **556 vCPU** (even 22 pods ≈ 380 > 350). Needs a **DSv3 vCPU quota increase**
  (separate Azure form from the OpenAI one) before the nodes can come up.

  | Target | pool16 vCPU | total vCPU | vs 350 |
  |---|---|---|---|
  | 12 nodes (now) | 192 | 220 | ✅ |
  | 17 nodes | 272 | 300 | ✅ |
  | 22 pods (2/key) | 352 | 380 | ❌ needs quota |
  | 33 pods (3/key) | 528 | 556 | ❌ needs quota |

### Lever 2 — trim the intrinsic design (to get *under* 5 min)
- **Lighter/guided first prompts** (test used brutal 14-agent/270k-token designs; real ones finish faster).
- **App-team tuning of `agent_network_designer`:** cap sub-agent fan-out; **truncate/summarize
  `chat_context`** on later turns (turn 3 was 98k tokens). Less context + fewer LLM round-trips =
  less CPU per design = higher ceiling. Biggest structural lever; needs the neuro-san app team.

### Recommended order
1. **Measure the intrinsic design time first (free):** re-run the realistic profile at **800 VUs**
   (under the ~1,000 knee) — if design p50 ≈ 5 min there, **staggering alone solves it** on current
   hardware.
2. If it must handle ~2,500 *simultaneously* fast → request the DSv3 vCPU increase (350 → ~600),
   scale pool16 to 33, `helm upgrade` (replicasPerKey already 3), re-test.
3. Pursue Lever 2 (prompts/app) to push under 5 min regardless of scale.

*Note: `replicasPerKey` was set to 3 in the chart but must NOT be applied until pool16 has 33 nodes
(else excess pods stay Pending). Dial to 2 (22 pods) or 1 (11 pods, original) as needed.*

---

*End of document — Load Testing Details, Neuro-San Azure Hackathon, July 9, 2026 (morning).*
