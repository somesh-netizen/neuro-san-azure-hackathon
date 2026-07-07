# Hackathon Event-Day Plan — Smooth Onboarding for 2,500 Participants

**Goal:** get 2,500 people onto Neuro-San without the start-of-event error spike.
**Core principle:** compute is **fixed and always-on** (11 pods, one per D16s_v3
node, all running the whole event — no autoscaling, no scale-on-surge). We smooth
the **arrival of people**, not the number of pods. A wall of 2,500 simultaneous
logins is flattened into waves so the constant-size backend never gets hit by a
thundering herd.

Why this is the #1 lever: your soak test collapsed at 94% errors from a *burst*,
not from sustained load. Spread the same 2,500 people over ~40 minutes and peak
concurrency drops ~5×, which the fixed 11×D16 fleet handles comfortably.

---

## A. The always-on fleet (prerequisite — must be true before anyone arrives)

- 11 backend pods (key-9 dropped), each on its own dedicated D16s_v3 node.
- Node pool `pool16` is **fixed at 12 nodes** (11 + 1 hot spare), **no autoscaler**.
- `hpa.enabled: false` — pods never scale on request surge.
- All pods **pre-warmed** (Azure OpenAI TCP connection + routing table warm) so the
  first real user hits a hot pod, not a cold one.

---

## B. T-minus timeline (run this the morning of the event)

| When | Action | Command / check |
|---|---|---|
| **T-60 min** | Cluster running, all nodes Ready | `az aks show ... --query powerState.code` → `Running`; `kubectl get nodes -l agentpool=pool16` → 12 Ready |
| **T-45 min** | All 11 backend pods Ready + UI 6/6 | `kubectl -n neuro-san-hackathon get pods` |
| **T-30 min** | **Pre-warm every pod** | `python3 loadtest/prewarm.py` → "All pods are warm" |
| **T-20 min** | Sanity smoke test one design | curl the streaming endpoint (see redeploy runbook §4) |
| **T-15 min** | Open monitoring dashboards | `watch -n5 'kubectl top nodes -l agentpool=pool16'` + error-rate view |
| **T-0** | **Release Wave 1 URL** | send to first group only |
| **+10 / +20 / +30 / +40 min** | Release Waves 2–5 | one group per interval |

> Re-run `prewarm.py` if more than ~15 min passes between warm-up and Wave 1 — the
> Azure OpenAI keep-alive connections can idle out.

---

## C. Wave schedule (2,500 people → 5 waves of 500, 10 min apart)

| Wave | Headcount | Release time | Assignment method |
|---|---|---|---|
| 1 | 500 | 10:00 | Teams/tracks A–E (or surnames A–E) |
| 2 | 500 | 10:10 | Tracks F–J |
| 3 | 500 | 10:20 | Tracks K–O |
| 4 | 500 | 10:30 | Tracks P–T |
| 5 | 500 | 10:40 | Tracks U–Z |

Peak concurrency math: 500 arriving per wave, ~30% actively designing at once
(others reading/typing) ≈ **~150 concurrent designs at the busiest moment**, spread
across 11 pods ≈ **~14 designs/pod** on a dedicated 16-vCPU node — comfortable.
Contrast: 2,500 at once ≈ ~750 concurrent designs = the collapse you measured.

**Simpler alternative if 5 waves is too much coordination:** 2 waves 15 min apart
(1,250 each) still halves the peak, or just **open the URL 30 min early** and let
natural arrival spread do the smoothing (people don't all click at the same second
when there's no countdown). Any of these beats a single synchronized start.

---

## D. Participant comms (drop-in templates)

**Pre-event (day before):**
> Your hackathon workspace opens tomorrow. You'll receive your personal start link
> at your assigned time — **please wait for it** rather than trying early, so
> everyone gets a fast experience. Designs take ~2–4 minutes each; that's normal.

**Wave release (per group):**
> 🚀 Track {A–E}, you're live! Open {URL}. First design taking a couple of minutes
> is expected — it's building a full multi-agent network for you.

Setting the "~2–4 min is normal" expectation up front prevents the retry-hammering
that turns a slow moment into an error storm.

---

## E. Live monitoring during the event (what to watch)

```bash
# primary signal — backend node CPU (this is the real ceiling)
watch -n5 'kubectl top nodes -l agentpool=pool16'

# pod health / restarts
watch -n10 'kubectl -n neuro-san-hackathon get pods -o wide | grep -E "key|ui"'

# ingress + backend error rate (tail NGINX logs for 5xx)
kubectl -n ingress-nginx-backend logs deploy/ingress-nginx-backend-controller --tail=50 -f | grep -E " 50[0-9] "
```

Green = node CPU < ~75%, no pod restarts, 5xx near zero. If a wave pushes CPU toward
90%+, **delay the next wave by 5–10 min** — the fixed fleet is the fixed fleet;
pacing arrivals is your live control knob.

---

## F. If it still spikes (contingency, no pod scaling)

Since we deliberately don't autoscale, the release valve is **arrival pacing**:
1. Pause the next wave until node CPU drops.
2. Re-warm pods if any restarted (`prewarm.py`).
3. Only if a specific pod is unhealthy: `kubectl -n neuro-san-hackathon rollout restart deploy/neuro-san-key-N` (it reschedules onto the hot-spare node).

Optional future hardening (not built — ask if you want it): a real **virtual
waiting room** (Azure Front Door queue / APIM) that admits users in batches
automatically, enforcing the waves technically instead of by comms.
