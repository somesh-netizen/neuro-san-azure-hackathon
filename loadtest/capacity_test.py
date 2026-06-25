"""
Neuro-San Capacity Planning Test
=================================
Tests the backend at multiple pod counts, stepping VUs from 2500→100 descending.
Phase 1: 2500 → 1000 in -250 steps.  Phase 2: 900 → 100 in -100 steps.
For each pod count it records where the system recovers and how many VUs it can
sustain at <5% error rate and ≥75% RPS efficiency.

Usage:
  python3 capacity_test.py                         # default: 2,4,8,10 pods / 3000 VUs
  python3 capacity_test.py --pods 4,8              # specific pod counts
  python3 capacity_test.py --max-vus 1000 --hold 45  # quick run
  python3 capacity_test.py --skip-scale            # if already scaled manually

Caveats (read before running):
  - All pods share ONE OpenAI key → shared rate limit. More pods does NOT give more
    AI throughput past that limit. To unlock true N×throughput you need N API keys.
  - Memory limit is 3000Mi per pod. Under heavy load pods can OOMKill mid-test.
    Watch with: kubectl get pods -n neuro-san-hackathon -w
  - HPA has cpu:<unknown> because no CPU request is set. Auto-scaling won't trigger
    during this test; pods are scaled manually by this script.
  - NGINX default worker_connections=1024. At 3000 VUs with SSE streams you may hit
    NGINX before hitting the pods. Symptom: 502s from the ingress, not 503s from pods.

Results: loadtest/reports/capacity_<timestamp>.json
Dashboard: streamlit run dashboard.py  →  http://localhost:8501
"""

import argparse
import json
import math
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from locust.env import Environment

from config import API_URL, TOKEN_QUOTA_TOTAL
from metrics import (
    collect_step_metrics,
    cost_summary,
    get_nginx_error_breakdown,
    get_node_instance_types,
    get_node_metrics,
    get_pod_vertical_config,
    token_tracker,
)

# ── Constants ─────────────────────────────────────────────────────────────────
NAMESPACE  = "neuro-san-hackathon"
DEPLOYMENT = "neuro-san-key-1"
REPORTS_DIR = Path(__file__).parent / "reports"
REPORTS_DIR.mkdir(exist_ok=True)

# ── Break conditions ──────────────────────────────────────────────────────────
# agent_network_designer: each user request spawns 5-10 internal LLM calls taking
# 2-5 minutes total.  Calibrate thresholds accordingly — p95=60s would fire
# immediately since even a single design takes longer than that.
BREAK_ERROR_PCT    = 5.0       # % error rate (hard stop)
BREAK_P95_MS       = 300_000   # 5 min p95 — normal for multi-step agent design
BREAK_RPS_PCT      = 75.0      # concurrent users must sustain ≥75% RPS (hard stop)
MIN_VUS_FOR_RPS    = 0         # apply RPS check from the very first step
CONSECUTIVE_BREAKS = 2         # consecutive breaking steps before we stop

# Descending VU ladder: 2500 → 1000 (step -LARGE_STEP), then 900 → 100 (step -args.step)
VU_STEP_CHANGE_AT  = 1000      # boundary: above → LARGE_STEP; at/below → args.step
LARGE_STEP         = 250       # step size above VU_STEP_CHANGE_AT (descending)

RAMP_RATE          = 50        # users/second ramp rate

# ── Token budget (Azure AI Foundry 10M quota) ─────────────────────────────────
QUOTA_TOTAL_TOKENS = TOKEN_QUOTA_TOTAL  # 10,000,000 — shared across all pods/keys
HACKATHON_PARTICIPANTS = 2000           # target participants to project quota for


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(
        description="Neuro-San capacity planning test",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--pods",       default="2,4,8,10",
                   help="Comma-separated pod counts to test (default: 2,4,8,10)")
    p.add_argument("--max-vus",    default=2500, type=int,
                   help="Starting VU count — test descends from here (default: 2500)")
    p.add_argument("--step",       default=100, type=int,
                   help="VU decrement per step below VU_STEP_CHANGE_AT=1000 (default: 100)")
    p.add_argument("--hold",       default=300, type=int,
                   help="Seconds to hold at each VU step (default: 300 — covers 2-5 min LLM responses)")
    p.add_argument("--user-class", default="capacity",
                   choices=["capacity", "hackathon"],
                   help="capacity=stress (0.5-1.5s think time), "
                        "hackathon=realistic (5-15 min think time)")
    p.add_argument("--host",       default=API_URL,
                   help="Backend API base URL")
    p.add_argument("--namespace",  default=NAMESPACE)
    p.add_argument("--deployment", default=DEPLOYMENT)
    p.add_argument("--skip-scale", action="store_true",
                   help="Skip kubectl scaling — use current pod count")
    p.add_argument("--warmup",     default=30, type=int,
                   help="Seconds to wait after scaling before starting load (default: 30)")
    return p.parse_args()


# ─────────────────────────────────────────────────────────────────────────────
# kubectl helpers
# ─────────────────────────────────────────────────────────────────────────────

def _kubectl(*args) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["kubectl", *args],
        capture_output=True, text=True,
    )


def _patch_hpa_min(count: int, namespace: str) -> None:
    """
    Set HPA minReplicas to match the desired pod count so the HPA controller
    does not override our manual kubectl scale during the test hold window.
    Non-fatal — if no HPA exists the patch silently fails.
    """
    _kubectl(
        "patch", "hpa", "neuro-san-hpa",
        f"-n={namespace}",
        "--type=merge",
        "-p", json.dumps({"spec": {"minReplicas": count}}),
    )
    # Also try the Helm-managed HPA name
    _kubectl(
        "patch", "hpa", "neuro-san-hpa-1",
        f"-n={namespace}",
        "--type=merge",
        "-p", json.dumps({"spec": {"minReplicas": count}}),
    )


def scale_deployment(count: int, namespace: str, deployment: str) -> bool:
    # Patch HPA first so it doesn't fight the scale
    _patch_hpa_min(count, namespace)
    r = _kubectl("scale", f"deployment/{deployment}",
                 f"--replicas={count}", f"-n={namespace}")
    if r.returncode != 0:
        print(f"  ⚠  scale failed: {r.stderr.strip()}")
        return False
    return True


def wait_for_rollout(count: int, namespace: str, deployment: str,
                     timeout: int = 180) -> bool:
    r = _kubectl("rollout", "status", f"deployment/{deployment}",
                 f"-n={namespace}", f"--timeout={timeout}s")
    if r.returncode != 0:
        print(f"  ⚠  rollout timeout: {r.stderr.strip()}")
        return False
    return True


def get_ready_replicas(namespace: str, deployment: str) -> int:
    r = _kubectl("get", "deployment", deployment, f"-n={namespace}",
                 "-o=jsonpath={.status.readyReplicas}")
    try:
        return int(r.stdout.strip()) if r.stdout.strip() else 0
    except ValueError:
        return 0


def get_pod_restarts(namespace: str) -> dict[str, int]:
    """Return {pod_name: restart_count} for all backend pods."""
    r = _kubectl("get", "pods", f"-n={namespace}",
                 "-l=app=neuro-san",
                 "-o=jsonpath={range .items[*]}{.metadata.name}:{.status.containerStatuses[0].restartCount} {end}")
    result: dict[str, int] = {}
    for entry in r.stdout.strip().split():
        if ":" in entry:
            name, count = entry.split(":", 1)
            try:
                result[name] = int(count)
            except ValueError:
                result[name] = 0
    return result


def get_node_pressure(namespace: str) -> str:
    """Quick check for any DiskPressure/MemoryPressure on nodes."""
    r = _kubectl("get", "nodes",
                 "-o=jsonpath={range .items[*]}{.metadata.name}={.status.conditions[-1].type} {end}")
    return r.stdout.strip()


# ─────────────────────────────────────────────────────────────────────────────
# Single VU step measurement
# ─────────────────────────────────────────────────────────────────────────────

def run_step(runner, vu_count: int, hold_seconds: int) -> dict:
    """
    Ramp to vu_count, wait for stabilisation, reset stats, hold for
    hold_seconds, then snapshot and return per-step metrics.
    """
    # Set target VU count — Locust spawns users if going up, stops if going down
    runner.start(user_count=vu_count, spawn_rate=RAMP_RATE)

    # Wait until actual user count reaches target (handles both ramp-up and ramp-down)
    deadline = time.monotonic() + 60
    while abs(runner.user_count - vu_count) > 5 and time.monotonic() < deadline:
        time.sleep(0.5)

    # Discard ramp-up noise — reset both Locust stats and token counter
    runner.stats.reset_all()
    token_tracker.reset()

    # Measurement window
    time.sleep(hold_seconds)

    total = runner.stats.total
    p50   = total.median_response_time or 0
    p95   = total.get_response_time_percentile(0.95) or 0
    p99   = total.get_response_time_percentile(0.99) or 0
    err   = total.fail_ratio * 100
    rps   = total.total_rps
    rps_pct = (rps / vu_count * 100) if vu_count > 0 else 100.0

    # Token + cost snapshot for this measurement window
    tok  = token_tracker.snapshot()
    cost = cost_summary(tok, hold_seconds=hold_seconds)

    # ── HTTP error code breakdown from Locust failure records ─────────────────
    error_counts: dict[str, int] = {}
    for (_, _, description), stat_err in runner.stats.errors.items():
        for code in ("400", "404", "429", "499", "502", "503", "504"):
            if code in description:
                error_counts[code] = error_counts.get(code, 0) + stat_err.occurrences

    # ── Count successful chat (streaming_chat) completions this step ──────────
    # Used to calculate avg tokens per design for quota projection.
    chat_completions = 0
    for (name, method), stat in runner.stats.entries.items():
        if "streaming_chat" in name:
            chat_completions += max(0, stat.num_requests - stat.num_failures)

    # ── Break conditions ──────────────────────────────────────────────────────
    # NOTE: for agent_network_designer (2-5 min response), RPS per VU ≈ 0.003-0.008.
    # rps_pct = RPS / VU_count × 100.  At 100 VUs and 0.5 RPS → rps_pct ≈ 0.5%.
    # The 75% threshold will always trigger for LLM agents unless the agent
    # responds in < 1.3s.  Watch the actual rps_pct values to calibrate.
    if err >= BREAK_ERROR_PCT:
        break_reason = "high_errors"
    elif p95 > BREAK_P95_MS:
        break_reason = "p95_latency"
    elif vu_count >= MIN_VUS_FOR_RPS and rps_pct < BREAK_RPS_PCT:
        break_reason = "low_rps"
    else:
        break_reason = None

    rps_warning = False  # subsumed into break_reason = "low_rps"

    return {
        "vu_count":          vu_count,
        "actual_users":      runner.user_count,
        "requests":          total.num_requests,
        "failures":          total.num_failures,
        "chat_completions":  chat_completions,
        "error_rate_pct":    round(err, 2),
        "error_counts":      error_counts,
        "rps":               round(rps, 2),
        "rps_pct":           round(rps_pct, 1),
        "rps_warning":       rps_warning,
        "median_ms":         round(p50, 1),
        "avg_ms":            round(total.avg_response_time, 1),
        "p95_ms":            round(p95, 1),
        "p99_ms":            round(p99, 1),
        "min_ms":            round(total.min_response_time or 0, 1),
        "max_ms":            round(total.max_response_time or 0, 1),
        "tokens":            cost,
        "breaking":          break_reason is not None,
        "break_reason":      break_reason,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Full ladder for one pod count
# ─────────────────────────────────────────────────────────────────────────────

def run_pod_config(pod_count: int, args) -> dict:
    print(f"\n{'─'*66}")
    print(f"  POD CONFIG: {pod_count} pods  [descending {args.max_vus}→{args.step} VUs]")
    print(f"{'─'*66}")

    # ── Scale ─────────────────────────────────────────────────────────────────
    if not args.skip_scale:
        ok = scale_deployment(pod_count, args.namespace, args.deployment)
        if not ok:
            return {"pod_count": pod_count, "error": "scale failed", "steps": [],
                    "max_ok_vus": 0, "breaking_vu": None, "vus_per_pod": 0}
        print(f"  Waiting for {pod_count} pods to be ready...")
        wait_for_rollout(pod_count, args.namespace, args.deployment)

    actual = get_ready_replicas(args.namespace, args.deployment)
    restarts_before = get_pod_restarts(args.namespace)

    # ── Show pod vertical config + node info ──────────────────────────────────
    vert = get_pod_vertical_config(args.namespace, args.deployment)
    print(f"  Ready: {actual} pods  |  Warm-up: {args.warmup}s")
    print(f"  Pod spec : cpu {vert['cpu_request']} req / {vert['cpu_limit']} limit  |  "
          f"mem {vert['memory_request']} req / {vert['memory_limit']} limit")
    nodes = get_node_instance_types()
    if nodes:
        types = {n["instance_type"] for n in nodes}
        print(f"  Nodes    : {len(nodes)} × {', '.join(sorted(types))}  "
              f"(alloc cpu={nodes[0]['alloc_cpu']}  mem={nodes[0]['alloc_mem']})")
    time.sleep(args.warmup)

    # ── Locust environment ─────────────────────────────────────────────────────
    if getattr(args, "user_class", "capacity") == "hackathon":
        from users import HackathonUser
        env = Environment(user_classes=[HackathonUser], host=args.host)
    else:
        from users import CapacityUser
        env = Environment(user_classes=[CapacityUser], host=args.host)
    runner = env.create_local_runner()

    steps              = []
    break_count        = 0
    breaking_vu        = None
    # Token quota tracking across steps in this pod config
    quota_start_time   = time.monotonic()
    cumulative_in      = 0
    cumulative_out     = 0
    cumulative_designs = 0

    hdr = (f"  {'VUs':>6}  {'RPS':>8}  {'RPS%':>6}  {'p50(ms)':>9}  {'p95(ms)':>9}"
           f"  {'p99(ms)':>9}  {'Err%':>7}  {'Status':<10}  Reason / Notes")
    print(f"\n{hdr}")
    print(f"  {'─'*6}  {'─'*8}  {'─'*6}  {'─'*9}  {'─'*9}  {'─'*9}  {'─'*7}  {'─'*10}  {'─'*24}")

    # Descending VU ladder — start at max load, step down to minimum.
    # Phase 1: max_vus → VU_STEP_CHANGE_AT  (step -LARGE_STEP = -250)
    #   e.g.  2500, 2250, 2000, 1750, 1500, 1250, 1000
    # Phase 2: VU_STEP_CHANGE_AT-step → args.step  (step -args.step = -100)
    #   e.g.  900, 800, 700, 600, 500, 400, 300, 200, 100
    _phase1 = list(range(args.max_vus, VU_STEP_CHANGE_AT - 1, -LARGE_STEP))
    _phase2 = list(range(VU_STEP_CHANGE_AT - args.step, args.step - 1, -args.step))
    vu_ladder = _phase1 + _phase2

    try:
        for vu in vu_ladder:
            step = run_step(runner, vu, args.hold)
            steps.append(step)

            # Detect OOMKills mid-step
            restarts_now = get_pod_restarts(args.namespace)
            new_restarts = sum(
                restarts_now.get(p, 0) - restarts_before.get(p, 0)
                for p in restarts_now
            )
            notes = f"⚠ {new_restarts} pod restart(s)" if new_restarts else ""

            status = "❌ BREAK" if step["breaking"] else "✅ OK"

            # Build notes: break reason, rps warning, error code counts, restarts
            note_parts = []
            if step.get("break_reason"):
                note_parts.append(step["break_reason"])
            if step.get("rps_warning"):
                note_parts.append("⚠ rps<75%")
            ec = step.get("error_counts", {})
            if ec:
                note_parts.append("errs:" + " ".join(f"{k}={v}" for k, v in ec.items()))
            if new_restarts:
                note_parts.append(f"⚠ {new_restarts} restart(s)")
            notes_str = "  ".join(note_parts)

            print(
                f"  {vu:>6}  {step['rps']:>8.1f}  {step['rps_pct']:>5.0f}%"
                f"  {step['median_ms']:>9.0f}  {step['p95_ms']:>9.0f}"
                f"  {step['p99_ms']:>9.0f}  {step['error_rate_pct']:>6.1f}%"
                f"  {status:<10}  {notes_str}"
            )

            # ── Inline metrics: tokens / cost / node pressure / quota ─────────
            tok = step.get("tokens", {})
            cumulative_in      += tok.get("total_input_tokens",  0)
            cumulative_out     += tok.get("total_output_tokens", 0)
            cumulative_designs += step.get("chat_completions",   0)

            node_snap = get_node_metrics()
            max_cpu   = max((n["cpu_pct"] for n in node_snap), default=0)
            max_mem   = max((n["mem_pct"] for n in node_snap), default=0)
            nginx_err = get_nginx_error_breakdown("ingress-nginx-backend")
            nginx_errs_str = " ".join(f"{k}:{v}" for k, v in nginx_err.items()) or "none"

            if tok.get("total_llm_calls"):
                print(
                    f"         tokens: {tok['total_input_tokens']:,}in/"
                    f"{tok['total_output_tokens']:,}out  "
                    f"tok/s: {tok.get('token_throughput_per_s', 0):,.0f}  "
                    f"cost/req: ${tok.get('cost_per_request_usd') or 0:.4f}  "
                    f"node cpu:{max_cpu}%  mem:{max_mem}%  "
                    f"nginx errs:{nginx_errs_str}"
                )
                step["cluster_snapshot"] = {
                    "nodes": node_snap, "nginx_errors": nginx_err
                }

            # Quota status line — always show once we have token data
            quota_used  = cumulative_in + cumulative_out
            quota_pct   = quota_used / QUOTA_TOTAL_TOKENS * 100
            elapsed_min = max((time.monotonic() - quota_start_time) / 60, 0.1)
            burn_tpm    = quota_used / elapsed_min
            eta_min     = max(QUOTA_TOTAL_TOKENS - quota_used, 0) / max(burn_tpm, 1)
            designs_done = cumulative_designs
            quota_flag  = "  ⚠ QUOTA WARN" if quota_pct >= 80 else ""
            if quota_used > 0:
                print(
                    f"         quota: {quota_used:,}/{QUOTA_TOTAL_TOKENS:,} "
                    f"({quota_pct:.1f}%)  burn: {burn_tpm:,.0f} tok/min  "
                    f"ETA exhaustion: {eta_min:.0f} min  "
                    f"designs done: {designs_done}{quota_flag}"
                )

            step["pod_restarts_during_step"] = new_restarts

            if step["breaking"]:
                break_count += 1
                if break_count >= CONSECUTIVE_BREAKS:
                    breaking_vu = steps[-(CONSECUTIVE_BREAKS)]["vu_count"]
                    print(f"\n  *** BREAKING POINT: {breaking_vu} VUs "
                          f"— reason: {step.get('break_reason', 'unknown')}"
                          f"  ({step['error_rate_pct']:.1f}% errors,"
                          f" p95={step['p95_ms']:.0f}ms,"
                          f" rps={step['rps']:.0f} [{step['rps_pct']:.0f}% of VUs]) ***")
                    break
            else:
                break_count = 0

    finally:
        runner.quit()
        time.sleep(3)  # let greenlets drain

    # ── Derive summary metrics ─────────────────────────────────────────────────
    ok_steps  = [s for s in steps if s["error_rate_pct"] < 5.0]
    max_ok    = max((s["vu_count"] for s in ok_steps), default=0)
    peak_rps  = max((s["rps"] for s in ok_steps), default=0)
    restarts_after = get_pod_restarts(args.namespace)
    total_restarts = sum(
        restarts_after.get(p, 0) - restarts_before.get(p, 0)
        for p in restarts_after
    )

    # ── Aggregate token totals + design counts across all steps ──────────────
    total_input   = sum(s.get("tokens", {}).get("total_input_tokens",  0) for s in ok_steps)
    total_output  = sum(s.get("tokens", {}).get("total_output_tokens", 0) for s in ok_steps)
    total_calls   = sum(s.get("tokens", {}).get("total_llm_calls",     0) for s in ok_steps)
    total_designs = sum(s.get("chat_completions", 0) for s in ok_steps)

    # ── Post-run external metrics ─────────────────────────────────────────────
    from config import API_URL as _API_URL
    openai_key   = os.getenv("OPENAI_API_KEY", "")
    blob_acct    = os.getenv("BLOB_STORAGE_ACCOUNT", "")
    blob_rg      = os.getenv("BLOB_RESOURCE_GROUP",  "")

    from metrics import check_openai_headroom, get_blob_metrics
    headroom = check_openai_headroom(openai_key)
    blob     = get_blob_metrics(blob_acct, blob_rg)

    # ── Print per-pod-config metrics summary ──────────────────────────────────
    print(f"\n  ── Metrics summary for {pod_count} pods ──")
    if total_calls:
        from metrics import estimate_cost
        cost = estimate_cost(total_input, total_output)
        print(f"  Tokens   : {total_input:,} input + {total_output:,} output"
              f"  ({total_calls:,} LLM calls)")
        print(f"  Cost     : ${cost['cost_total_usd']:.4f} total  |  "
              f"${cost['cost_total_usd']/total_calls:.5f} per request  "
              f"({cost['model']})")
    if headroom:
        rh = headroom.get("req_headroom_pct")
        th = headroom.get("tok_headroom_pct")
        print(f"  OpenAI   : req headroom {rh}%  |  token headroom {th}%"
              f"  ({headroom.get('req_remaining')}/{headroom.get('req_limit')} reqs)")
    if blob:
        print(f"  Blob     : {blob.get('blob_transactions')} IOPS  |  "
              f"{blob.get('blob_e2e_latency_ms')} ms E2E latency")
    node_snap = get_node_metrics()
    if node_snap:
        for n in node_snap:
            print(f"  Node     : {n['name']:<40}  cpu {n['cpu_pct']:>3}%  mem {n['mem_pct']:>3}%")

    return {
        "pod_count":          pod_count,
        "actual_pods":        actual,
        "pod_vertical_config": vert,
        "breaking_vu":        breaking_vu,
        "max_ok_vus":         max_ok,
        "peak_rps":           round(peak_rps, 2),
        "vus_per_pod":        round(max_ok / max(actual, 1), 1),
        "total_pod_restarts": total_restarts,
        "token_totals": {
            "input_tokens":    total_input,
            "output_tokens":   total_output,
            "llm_calls":       total_calls,
            "designs_done":    total_designs,
        },
        "openai_headroom": headroom,
        "blob_metrics":    blob,
        "steps":           steps,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    args       = parse_args()
    pod_counts = [int(x.strip()) for x in args.pods.split(",")]
    ts         = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    # Phase 1: 2500→1000 inclusive = (2500-1000)/250 + 1 steps
    # Phase 2: 900→100 inclusive   = (1000-100)/100 steps
    n_steps_p1 = max(0, args.max_vus - VU_STEP_CHANGE_AT) // LARGE_STEP + 1
    n_steps_p2 = (VU_STEP_CHANGE_AT - args.step) // args.step
    n_steps    = n_steps_p1 + n_steps_p2
    est_per_config = n_steps * (args.hold + 5) / 60  # +5s for user count transition
    est_total  = len(pod_counts) * est_per_config

    print(f"\n{'='*66}")
    print(f"  NEURO-SAN CAPACITY PLANNING TEST")
    print(f"{'='*66}")
    print(f"  Host           : {args.host}")
    print(f"  Pod configs    : {pod_counts}")
    print(f"  VU range       : {args.max_vus}→{VU_STEP_CHANGE_AT} (step -{LARGE_STEP})  "
          f"then {VU_STEP_CHANGE_AT - args.step}→{args.step} (step -{args.step})  "
          f"[{n_steps} steps per pod config]")
    print(f"  Hold per step  : {args.hold}s")
    print(f"  Break condition: >{BREAK_ERROR_PCT:.0f}% errors  "
          f"OR  p95 >{BREAK_P95_MS/1000:.0f}s  "
          f"OR  RPS <{BREAK_RPS_PCT:.0f}% of VU count  "
          f"for {CONSECUTIVE_BREAKS} consecutive steps")
    print(f"  Est. max time  : ~{est_total:.0f} min (early-exit usually cuts this in half)")
    print(f"\n  ⚠  TOKEN BUDGET ALERT:")
    print(f"  agent_network_designer makes 5-10 internal LLM calls per design.")
    print(f"  Estimated tokens per design: 20,000 – 50,000  (avg ~35,000)")
    print(f"  10M quota → ~200-500 complete designs possible in total.")
    print(f"  2000 participants × 1 design = 70M tokens needed (7× current quota).")
    print(f"  Recommendation: request ≥100M tokens on Azure AI Foundry before hackathon.")
    print(f"\n  BOTTLENECK PRIORITY ORDER:")
    print(f"  1. Token quota (10M) — exhausts after ~300 designs  ← CRITICAL")
    print(f"  2. TPM rate limit (Azure AI Foundry per-minute cap)  ← HIGH")
    print(f"  3. Pod LLM slots (50/pod × {max(pod_counts)} pods = {50*max(pod_counts)} concurrent) ← MEDIUM")
    print(f"  4. NGINX proxy timeout (600s — fine for 5-min requests)  ← LOW")
    print(f"  5. Azure Blob (60s check period, session state)           ← LOW")
    print(f"  6. Node compute (mostly I/O-bound — CPU headroom is good) ← LOW")
    print(f"\n  CAVEATS:")
    print(f"  • HPA minReplicas is patched to match each pod count during the test")
    print(f"    and restored to 1 after. The HPA will not fight manual scaling.")
    print(f"  • Watch for OOMKills: kubectl get pods -n {args.namespace} -w")
    print(f"  • Hold={args.hold}s per step. agent_network_designer avg response: 2-5 min.")
    print(f"    Steps shorter than 3 min may show incomplete statistics.")
    print(f"{'='*66}\n")

    # ── Print full pod spec so it can be verified before the test runs ────────
    from metrics import get_pod_vertical_config, get_node_instance_types
    vert  = get_pod_vertical_config(args.namespace, args.deployment)
    nodes = get_node_instance_types()
    print(f"  POD SPEC (live from cluster):")
    print(f"  {'─'*60}")
    print(f"  Container image : neurosanhackathonacr.azurecr.io/neuro-san/neuro-san-studio:0.0.1")
    print(f"  CPU request     : {vert['cpu_request']}    (guaranteed slice per pod)")
    print(f"  CPU limit       : {vert['cpu_limit']}   (burst ceiling — throttled if exceeded)")
    print(f"  Memory request  : {vert['memory_request']} (scheduler uses this for placement)")
    print(f"  Memory limit    : {vert['memory_limit']} (OOMKill if exceeded)")
    print(f"  Concurrent AI   : 50 slots/pod × {max(pod_counts)} pods = {50*max(pod_counts)} total AI slots")
    print(f"  OpenAI keys     : 1 (shared across all pods)")
    print(f"  Azure Blob      : neurosanhackathonsa / neuro-san-reservations  (session state)")
    print(f"  Blob check      : every 60s")
    if nodes:
        sku = nodes[0]['instance_type'] if nodes else 'unknown'
        print(f"  Node VM SKU     : {sku} ({len(nodes)} nodes)")
        print(f"  Node capacity   : {nodes[0]['alloc_cpu']} CPU / {nodes[0]['alloc_mem']} memory each")
        total_cpu_m = len(nodes) * 3860
        used_cpu_m  = max(pod_counts) * 500
        print(f"  CPU headroom    : {max(pod_counts)} pods × 500m = {used_cpu_m}m of {total_cpu_m}m available ({used_cpu_m*100//total_cpu_m}% committed)")
    print(f"  {'─'*60}\n")

    # Check kubectl connectivity
    r = _kubectl("get", "deployment", args.deployment, f"-n={args.namespace}")
    if r.returncode != 0:
        print(f"ERROR: cannot reach cluster. Run:\n"
              f"  az aks get-credentials -g neuro-san-studio-marketplace-rg "
              f"-n neuro-san-hackathon-aks --overwrite-existing")
        return 1

    all_results = []

    for pod_count in pod_counts:
        result = run_pod_config(pod_count, args)
        all_results.append(result)

    # Restore to minimum pod count when done
    if not args.skip_scale:
        print(f"\n  Scaling back to 1 pod (restore baseline)...")
        scale_deployment(1, args.namespace, args.deployment)

    # ── Save ──────────────────────────────────────────────────────────────────
    output = {
        "test_type":  "capacity",
        "timestamp":  datetime.now(timezone.utc).isoformat(),
        "host":       args.host,
        "config": {
            "pod_counts":       pod_counts,
            "max_vus":          args.max_vus,
            "vu_step":          args.step,
            "hold_seconds":     args.hold,
            "break_error_pct":  BREAK_ERROR_PCT,
            "break_p95_ms":     BREAK_P95_MS,
        },
        "results": all_results,
    }
    path = REPORTS_DIR / f"capacity_{ts}.json"
    path.write_text(json.dumps(output, indent=2))

    # ── Summary table ─────────────────────────────────────────────────────────
    print(f"\n{'='*66}")
    print(f"  CAPACITY PLANNING RESULTS")
    print(f"{'='*66}")
    print(f"  {'Pods':>5}  {'Max OK VUs':>12}  {'VUs/pod':>9}  {'Peak RPS':>9}  "
          f"{'Break at':>10}  {'Restarts':>9}")
    print(f"  {'─'*5}  {'─'*12}  {'─'*9}  {'─'*9}  {'─'*10}  {'─'*9}")
    for r in all_results:
        if "error" in r:
            print(f"  {r['pod_count']:>5}  FAILED ({r['error']})")
            continue
        brk = f"{r['breaking_vu']} VUs" if r["breaking_vu"] else ">max"
        rts = r.get("total_pod_restarts", 0)
        rst = f"⚠ {rts}" if rts else "0"
        print(f"  {r['pod_count']:>5}  {r['max_ok_vus']:>12,}  {r['vus_per_pod']:>9.0f}"
              f"  {r['peak_rps']:>9.1f}  {brk:>10}  {rst:>9}")

    # Capacity recommendation
    print(f"\n  RECOMMENDATION — pods needed for concurrent active users (< 5% errors):")
    for target_vus in [50, 100, 200, 300, 500, 750, 1_000]:
        qualifying = [r for r in all_results
                      if "error" not in r and r["max_ok_vus"] >= target_vus]
        if qualifying:
            best = min(qualifying, key=lambda r: r["pod_count"])
            print(f"    {target_vus:>5} concurrent VUs  →  {best['pod_count']} pods")
        else:
            print(f"    {target_vus:>5} concurrent VUs  →  needs > {pod_counts[-1]} pods (or more OpenAI keys)")

    # ── Hackathon token budget projection ─────────────────────────────────────
    total_designs = sum(
        r.get("token_totals", {}).get("designs_done", 0)
        for r in all_results if "error" not in r
    )
    total_tokens_used = sum(
        r.get("token_totals", {}).get("input_tokens", 0) +
        r.get("token_totals", {}).get("output_tokens", 0)
        for r in all_results if "error" not in r
    )

    print(f"\n{'─'*66}")
    print(f"  HACKATHON TOKEN BUDGET ANALYSIS")
    print(f"{'─'*66}")
    print(f"  Azure AI Foundry quota  : {QUOTA_TOTAL_TOKENS:,} tokens (10M)")
    if total_designs > 0 and total_tokens_used > 0:
        avg_tok = total_tokens_used / total_designs
        designs_possible = int(QUOTA_TOTAL_TOKENS / avg_tok)
        print(f"  Measured avg/design     : {avg_tok:,.0f} tokens "
              f"({total_designs} designs, {total_tokens_used:,} total tokens in test)")
        print(f"  Designs on 10M quota    : ~{designs_possible} complete designs")
        print(f"")
        print(f"  With {HACKATHON_PARTICIPANTS} hackathon participants:")
        for d in [1, 2, 3, 5]:
            needed     = int(HACKATHON_PARTICIPANTS * d * avg_tok)
            multiplier = needed / QUOTA_TOTAL_TOKENS
            suffixes   = ["✅ OK" if multiplier <= 1.0 else f"⚠ need {math.ceil(multiplier)}× more tokens"]
            print(f"    × {d} design(s) each  → {needed:,} tokens needed  "
                  f"({multiplier:.1f}× quota)  {suffixes[0]}")
        recommended_m = math.ceil(HACKATHON_PARTICIPANTS * 2 * avg_tok / 1_000_000)
        print(f"")
        print(f"  ★ REQUEST AT LEAST {recommended_m}M TOKENS on Azure AI Foundry")
        print(f"    (covers {HACKATHON_PARTICIPANTS} participants × 2 designs with 20% headroom)")
    else:
        print(f"  No designs completed during test — run with longer --hold to capture token data.")
        print(f"  Estimated (30k avg): {int(QUOTA_TOTAL_TOKENS/30_000)} designs on 10M quota")
        print(f"  2000 participants × 2 designs = need ~120M tokens (12× current quota)")

    print(f"\n  Full results → {path}")
    print(f"  Dashboard    → streamlit run dashboard.py")
    print(f"{'='*66}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
