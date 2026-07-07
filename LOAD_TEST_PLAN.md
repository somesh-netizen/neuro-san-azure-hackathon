# Thorough Load Test — Parameters, Results & Validated Config

**Status:** EXECUTED (3 runs, 2026-07-06). Findings + validated config below; the
original parameter spec follows.

---

## RESULTS — validated config (2026-07-06)

Three 30-min runs at 2,700 VUs, each after a fix, isolated the real bottleneck chain:

| Run | Change | Success | Restarts | Backend CPU | Bottleneck found |
|-----|--------|---------|----------|-------------|------------------|
| 1 | baseline (1 proc + liveness) | 40.6% | 10–11 (liveness SIGKILL) | 7% (1 core) | liveness kills busy pods → crash-cascade |
| 2 | `AGENT_HTTP_SERVER_INSTANCES=16` + liveness removed | 91.8% | 7–8 (OOMKilled) | 87% | 6Gi mem too small for 16 procs |
| 3 | + memory limit 6Gi→48Gi | 100% (0 fail) | **0** | 87% (pinned) | **CPU — the true ceiling** |

**Validated production config (all live, helm rev 29):**
- `AGENT_HTTP_SERVER_INSTANCES: "16"` — one HTTP process per core (default was 1 → single event loop maxing ~1 core). This was the single biggest fix.
- **No livenessProbe** on backend — readiness alone sheds load gracefully; a busy pod is never SIGKILL'd.
- Memory: request 8Gi / **limit 48Gi** (D16 = 64GB, 1 pod/node; actual peak ~9Gi).
- Plus: key-9 dropped (11 pods), ingress 2 replicas + PDB, backend ingress timeout 600s, per-user rate limit 1 query/30s, backend pinned 1-per-node on the D16 `pool16`.

**The true ceiling (now that it's CPU-bound, not crash-bound):**
- CPU saturates (~87%) at **~1,200 concurrent in-flight designs**; sustainable completion throughput ≈ **1–2 designs/sec** for the 11-pod fleet.
- Beyond ~1,500 concurrent it **queues gracefully** (in-flight piles up, latency balloons) — it does NOT crash. Run 3 at 2,700 hit 100% "success" but ~2,600 designs were still *queued* at the end; only ~1,800 completed.

**Verdicts:**
- **Tokens are NOT a constraint.** Burn at CPU-saturated 2,700 was ~8M tok/min = **~2.4% of the 330M TPM capacity**. Keys are ~40× over-provisioned — do **not** add keys/TPM.
- **Compute (pods) is the only lever** for higher concurrency. Realistic event peak (~750 concurrent, staggered) sits well under the ~1,200 ceiling → current 11 pods suffice. Add pods only to cover concurrency beyond ~1,000–1,200.
- Load-test designs are *brutal* (~147k tokens each, full completion); real participant prompts are lighter → real capacity is likely higher than the test shows.
- Reporting fixes applied post-run-3: mem% baseline → 48Gi; token line shows **burn-rate vs TPM capacity** (not a total-vs-rate %); snapshot now flags **SATURATED** and reports **completed vs queued** so "100% success" can't hide queuing.

---

Legend: ✅ = already in the suite · ➕ = I will add on approval · ⚙️ = tunable knob

---

## 0. What we are testing and why

Validate the **post-redeploy** topology for a **2,500-participant** event:
- Backend: 11 pods, one per dedicated D16s_v3 node (12 vCPU request, burst to 16).
- Frontend: 6 UI replicas.
- Per-user rate limit (429) on the design endpoint.
- Ingress: 2 replicas each + PDB.
- 11 Azure OpenAI keys @ 30M TPM = 330M TPM.

Two independent load dimensions, because they stress different tiers:
1. **Backend design load** — `agent_network_designer` streaming calls (CPU-bound).
2. **Frontend login wave** — concurrent page-load + `/api/environment` on the UI pods.

---

## 1. Prerequisites (hard gates before any run)

| # | Gate | Why |
|---|---|---|
| P1 | Redeploy applied (controllers → app), all 11 backend + 6 UI pods Ready | Testing the new topology, not the old one |
| P2 | `prewarm.py` run at T-0 of the test | No cold-start 503s skewing results |
| P3 | **Distributed Locust** (1 master + N workers) OR load run from an **Azure VM in East US** | ⚠️ A single Locust process / a laptop over home wifi **cannot** drive 2,500 VUs — you'd measure the load generator's limit, not the cluster's |
| P4 | `az login` with reader on the 11 OpenAI resources + storage | Per-pod token metrics + Blob metrics |
| P5 | Token budget cap set (`--max-tokens`) | Cost guardrail (real Azure spend) |
| P6 | `OPENAI_MODEL=gpt-4o-mini` in config | Correct cost math (currently gpt-5.4 → 25–33× overstated) |

---

## 2. Test profile parameters ⚙️ (propose → you adjust)

I recommend **two runs**: a capacity ladder to find the ceiling, then a realistic
event soak to validate the wave plan. Plus a short frontend login-wave burst.

### 2A. Capacity ladder — climb to 2,700 and HOLD (no failure push) 🔒
| Parameter | Value | Notes |
|---|---|---|
| VU stages (concurrent) | 200 → 600 → 1200 → 1800 → 2400 → **2700** | 🔒 top target 2,700 (2,500 + buffer) |
| Hold per stage | 5 min; **10 min at 2,700** | sustained target soak at the top |
| Ramp rate | 25 VU/s | between stages |
| Think time | 5–15 s (compressed) | ladder = stress, not realism |
| User mix | 100% SessionUser (design) | pure backend ceiling |
| Total duration | ~25–30 min | reaches 2,700 within the window; CPU stabilises per stage in minutes |
| Failure push | **NO** 🔒 | stop climbing at 2,700; do not drive past it |
| Watch (not stop) | err %, node CPU %, 429s at each stage | report, don't auto-abort at 2,700 |

### 2B. Realistic event soak — PRODUCTION SETTINGS, observe 30 min, predict 90 🔒
**Principle (your call):** every parameter is IDENTICAL to the real 90-min event.
Nothing is compressed or scaled to fit the shorter window. We simply watch for 30 min
and extrapolate the steady state to 90.
| Parameter | Value | Notes |
|---|---|---|
| VUs (participants) | ramp to **2,700**, then HOLD | each = 1 participant, fixed user_id |
| Turn pacing | **CLOSED-LOOP** 🔒 | wait for the answer, THEN think, THEN next turn (see box) |
| Think time | **2–4 min (real)** 🔒 | pause AFTER reading the answer; ~2× heavier than 5–15 min |
| Response timeout | 360 s | if a design exceeds 6 min the client gives up → counts as timeout failure |
| Rate limit | **1 query / 30 s** 🔒 | unchanged from event — NOT loosened |
| Turns per session | 1–6 (weighted 1–3) | context compounds naturally |
| Ramp to peak | ~5 min (fast, then hold) | reach 2,700 early so we observe it within 30 min |
| User mix | 80% SessionUser + 20% BrowseUser | ⚙️ |
| Observation window | **30 min** 🔒 | predicts the 90-min event (see box below) |
| Arrival staggering | **skipped** for the test | test = worst case (all active at once); real event's waves make it *easier*, so a pass here is conservative |

> **Turn pacing — closed-loop (why, not fixed-rate):** a participant submits a turn,
> waits for the answer, reads it, thinks 2–4 min, then submits the next turn. We do NOT
> fire turns on a fixed timer, because (1) turn N+1 must carry turn N's chat_context +
> sly_data — firing early = invalid request that fails with "agent_network_name missing"
> (the 97%-failure bug); (2) a human can't react to an unseen answer; (3) closed-loop
> reproduces real backpressure — a slow cluster naturally slows users, instead of
> piling on requests that never happen in reality. The 360s response timeout is the only
> "hard cap" and it acts as failure detection, not pacing.
>
> **Why 30 min at real settings predicts the 90-min event:**
> - CPU %, error rate, latency, per-pod distribution, 429 behaviour all reach **steady
>   state within minutes** of hitting 2,700 and then stay flat — minute 30 looks like
>   minute 90. These are measured directly and extrapolate 1:1. ✅
> - The ONE thing not fully seen in 30 min: with real 5–15 min think time, sessions
>   reach ~turns 1–3, so turn 4–6 token costs are **projected** (via context-compounding
>   math from the measured turn-1→3 curve), not measured. This affects only the *token
>   total* estimate — and tokens are 50× over-provisioned (330M), so it changes no
>   pass/fail verdict and carries no risk.
> - Trade-off accepted: a 30-min run won't catch a slow memory leak over hours — low
>   risk here (pods used 4–16% memory in the 2,500-VU test).

### 2C. Frontend login-wave burst — "can the UI take the stampede?"
| Parameter | Proposed | Notes |
|---|---|---|
| Concurrent page loads | 500 → 1500 → 2500 | ➕ new FrontendUser (GET `/`, `/api/environment`) |
| Ramp | 100/s | models doors-open |
| Duration | 10 min | |
| Auth | current: OFF | ⚙️ if you enable Auth0, add the login round-trip |

---

## 3. Metrics — everything measured, mapped to your asks

### 3A. Per-pod evaluation — BACKEND (per snapshot, tagged stage + time + VU)
| Metric | Status | Source |
|---|---|---|
| CPU **% of node** (16 vCPU, 1 pod/node) | ➕ | `kubectl top pod` cpu ÷ 16000m |
| Memory **% of limit** (6 Gi) | ✅➕ | fix stale 3072→6144 |
| Request **distribution share %** across pods | ✅ | `get_pod_cpu_distribution` (CPU as proxy for sticky routing) |
| Node placement (which D16 node) | ➕ | so you see 1-pod-per-node holds |
| Hot-pod flag (share > 2× ideal) | ✅ | sticky imbalance detector |

### 3B. Per-pod evaluation — FRONTEND (UI) ➕ entirely new
| Metric | Status | Source |
|---|---|---|
| UI pod CPU % (of 2-core limit) | ➕ | `kubectl top pod -l app=ui-node` |
| UI pod memory % (of 6 Gi) | ➕ | same |
| UI replicas serving / spread across nodes | ➕ | pod list |
| UI request latency + error rate (page load, `/api/environment`) | ➕ | FrontendUser stats |

### 3C. Nodes (both pools)
| Metric | Status |
|---|---|
| CPU % / mem % per node — backend pool16 **and** system pool | ✅ (already per-node) |

### 3D. Tokens
| Metric | Status | Source |
|---|---|---|
| Total input / output / combined | ✅ | TokenTracker |
| Burn rate (tok/min) + ETA to 330M | ✅ | fix quota label 10M→330M |
| Per-turn escalation (turn 1 vs 3 vs 6) | ✅ | TurnTracker |
| **Per-pod / per-key token usage vs 30M TPM limit** | ➕ | Azure Monitor `ProcessedPromptTokens`+`GeneratedTokens` per OpenAI resource |
| **TPM headroom % per key** | ➕ | used ÷ 30M per resource |
| Total tokens + total cost at end (gpt-4o-mini pricing) | ✅➕ | final summary + per-pod split |

### 3E. Rate limiting (429) ➕ new dedicated tracking
| Metric | Status |
|---|---|
| Total 429 count + 429 rate % | ➕ RateLimitTracker (client-side, separate from 5xx) |
| `limit_req` vs `limit_conn` rejections | ➕ (NGINX log parse + 429 body) |
| Which user_ids hit the limit (top N) | ➕ so you see it's abuse-guarding, not blocking normals |
| 429 per stage/VU level | ➕ |

### 3F. Concurrency
| Metric | Status |
|---|---|
| VU count (target vs actual) | ✅ |
| **Peak concurrent VUs** | ➕ tracked to end |
| **In-flight requests gauge** (designs running right now) | ➕ counter inc/dec around each request |
| Completed designs/min | ➕ derived from turn completions |

### 3G. Errors & latency
| Metric | Status |
|---|---|
| p50 / p95 | ✅ |
| **p99** | ➕ |
| Error breakdown: 503 / 502 / 504 / 429 / empty-body / timeout — counts + % | ✅➕ (split 429 out) |

### 3H. Storage
| Metric | Status |
|---|---|
| Azure Blob IOPS + E2E latency (grows with session depth) | ✅ |

---

## 4. Sample output you'll get (mockup)

### Live snapshot (every 30–60s in ladder, 5 min in soak)
```
────────────────────────────────────────────────────────────────────
 STAGE 4/7  t=22min  VUs 1000/1000  in-flight 143  RPS 4.6  p50 9s p95 71s p99 140s  err 3.1%
────────────────────────────────────────────────────────────────────
 TOKENS  : 41.2M / 330M (12.5%)  burn 1.9M tok/min  ETA 152min   429s: 0
 BACKEND POD          CPU%  MEM%   share%  node
   key-1              63%   14%    9.4%    ...vmss-a1
   key-2              71%   15%   10.6%    ...vmss-a2
   ... (11 rows) ...          ⚠ hot: none
 FRONTEND (UI)        CPU%  MEM%   replicas 6/6 Ready
   ui-...-xk8mg       18%    9%
 PER-KEY TPM (Azure)  key-1 6.1M/30M(20%) key-2 5.8M/30M(19%) ...  ⚠ none >80%
 NODES pool16         a1 63% a2 71% ... | system a 22%
 ERRORS  : 503:12  empty-body:4  429:0
```

### Final summary
```
 TOTAL tokens: 118,400,000 in + 22,100,000 out = 140,500,000  (42.6% of 330M)
 TOTAL cost  : ~$31.10 (gpt-4o-mini)
 Peak concurrent VUs: 2500   Peak in-flight designs: 380
 Requests: 24,120  success 96.4%  429: 210 (0.9%, from 6 user_ids)
 Per-turn escalation: t1 55k → t3 98k → t6 210k tokens
 Per-key token spread: min 9.1M  max 14.8M  (balance ratio 1.6×)
 Ceiling found at: ~1800 concurrent (err crossed 5% / node CPU 88%)
 PASS/FAIL: err ✅  p95 ✅  hot-pod ✅  OOM ✅  429 ✅
```

---

## 5. Pass / fail thresholds ⚙️
| Check | Threshold |
|---|---|
| Overall error rate | < 5% |
| p95 latency | < 300 s (design is 5–10 LLM calls) |
| Sustained node CPU | < 85% |
| Pod memory | < 70% of 6 Gi (no OOM) |
| Hot-pod share | < 2× ideal (≈18%) |
| 429s | ≈ 0 under realistic profile; only under abuse/ladder |
| Per-key TPM | < 80% of 30M |
| Token burn | total ≪ 330M for the event |

---

## 6. Safety / stop conditions
- `--max-tokens` hard cap → auto-stop (proposed **200M**, ~$45 worst case).
- Auto-abort if err > 25% for > 3 min (cluster clearly falling over).
- Ladder auto-stops climbing once break predicate trips (don't waste spend past the ceiling).

---

## 7. Code changes I'll make on approval (so you know the blast radius)
| File | Change |
|---|---|
| `config.py` | `OPENAI_MODEL`→gpt-4o-mini; `TOKEN_QUOTA_TOTAL`→330M; add LADDER stages + thresholds |
| `metrics.py` | add `get_pod_cpu_pct` (vs node), UI-pod metrics, `get_azure_openai_token_usage` per resource, `RateLimitTracker`, in-flight gauge; fix `_DEFAULT_MODEL`; 429 in NGINX parse |
| `users.py` | add `FrontendUser` (login wave); split 429 into RateLimitTracker; inc/dec in-flight; keep sly_data fix |
| `shapes.py` | add `CapacityLadderShape` (2A) and `EventWaveShape` (2B) |
| `hackathon_soak.py` | per-pod CPU%+mem% table (backend+frontend), per-key TPM row, 429 line, p99, peak-concurrent, richer final summary; fix stale labels (10M, 5000, key-1-only, HPA patch) |
| new `frontend_test.py` | drives 2C (UI login wave) |

---

## 8. Decisions — LOCKED per your review
1. **Peak target** — 🔒 2,700 concurrent, HOLD, **no failure push**.
2. **Where does load run from** — 🔒 **Option A**: one big Azure VM in East US running distributed Locust (§9).
3. **Frontend login-wave test (2C)** — 🔒 INCLUDED.
4. **Rate limit** — 🔒 **1 request / 30 s per user** (`rate=2r/m`, burst 2, conn 2). Applied to config already. Same value in the test as in the event.
5. **Duration** — 🔒 **~30 min per phase at PRODUCTION settings** (no compression); extrapolate to the 90-min event (§2B box).
6. **Token budget cap** — ⏳ confirm: propose ladder 150M / soak 250M / frontend 10M (safety brakes; expected actual well under).
7. **Run order** — proposed: ladder (2A) → frontend (2C) → end-to-end soak (2B). ⏳ confirm.

---

## 9. Decision (b) explained — where the 2,700 "users" come from

**The question in plain terms:** something has to *pretend to be 2,700 people at once*.
That "something" is the load-testing tool (Locust), running on some computer. The
question is **which computer**, because the tool has limits too.

**Why you can't just run it on your laptop:**
- To simulate 2,700 users, Locust must hold **2,700 live connections open at the same
  time**, each waiting 2–4 minutes for a design to finish.
- One Locust process is limited by one CPU core (Python's GIL). In practice a single
  process tops out around a few hundred users — after that *the tool itself* is maxed,
  so it can't fire requests fast enough. You'd be measuring **your laptop's limit, not
  the cluster's.**
- Your laptop's network makes it worse: home/office Wi-Fi, limited bandwidth, and the
  OS cap on open file handles all choke long before 2,700. And if you're in India
  hitting a cluster in US-East, every request pays ~200 ms round-trip — you physically
  can't keep 2,700 streams flowing.

**The two ways to actually generate 2,700 (pick one):**

| Option | What it is | Verdict |
|---|---|---|
| **A. Distributed Locust on one Azure VM (recommended)** | Spin up **one large VM in East US** (same region as the cluster, e.g. 16–32 vCPU). Run Locust in **1 master + ~16 worker** processes on it; each worker drives ~170 users, together = 2,700. Same-region = ~1 ms latency, huge bandwidth, no Wi-Fi/NAT choke. Tear it down after (~a few $ for a few hours). | ✅ Real 2,700, clean numbers |
| **B. Laptop / single process** | Run as-is from your machine. | ❌ Caps at a few hundred; measures the laptop, not the cluster. Not a real 2,700 test. |

**My recommendation:** Option A. I'll give you the exact commands to create the VM,
install the suite, and launch master + workers. It's the only way the 2,700 number
means anything. Just confirm and I'll add the VM setup to the runbook.
