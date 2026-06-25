"""
Hackathon Soak Test
===================
Simulates 2000 participants in 90-minute sessions using agent_network_designer.

Each SessionUser:
  - Holds a FIXED user_id for the session → NGINX sticky routes all turns to ONE pod.
  - Carries a growing chat_context → token cost compounds each turn.
  - Thinks 5-15 min between turns → realistic reading/discussion time.
  - Records per-turn tokens so the dashboard shows the compounding effect.

Bottlenecks surfaced every SNAPSHOT_INTERVAL seconds:
  1. Token quota burn rate + ETA to 10M exhaustion (compounds as sessions age)
  2. Per-turn token escalation (turn 1 vs turn 5 vs turn 10)
  3. Pod memory growth (growing context held in RAM per sticky session)
  4. NGINX hot-pod distribution (sticky imbalance → one pod saturates first)
  5. Azure Blob IOPS per interval (turns = read+write of growing context)
  6. p50/p95 latency drift over session lifetime

Usage:
  python3 hackathon_soak.py                         # defaults: 10 pods, 200 VUs, 120 min
  python3 hackathon_soak.py --vus 400 --duration 90
  python3 hackathon_soak.py --skip-scale --vus 200  # cluster already at 10 pods
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
    check_openai_headroom,
    cost_summary,
    estimate_cost,
    get_blob_metrics,
    get_node_metrics,
    get_pod_cpu_distribution,
    get_pod_metrics,
    get_pod_vertical_config,
    token_tracker,
    turn_tracker,
)
from users import SessionUser

# ── Constants ─────────────────────────────────────────────────────────────────
NAMESPACE        = "neuro-san-hackathon"
DEPLOYMENT       = "neuro-san-key-1"
REPORTS_DIR      = Path(__file__).parent / "reports"
REPORTS_DIR.mkdir(exist_ok=True)

SNAPSHOT_INTERVAL   = 300    # seconds between metric snapshots (5 min)
RAMP_RATE           = 10     # users per second — 5000 VUs in ~8 min (50 users every 5s;
                             # models leadership-call surge: MC announces, cohorts join in waves)
QUOTA_TOTAL_TOKENS  = TOKEN_QUOTA_TOTAL
HACKATHON_PARTICIPANTS = 5000


# ─────────────────────────────────────────────────────────────────────────────
# kubectl helpers
# ─────────────────────────────────────────────────────────────────────────────

def _kubectl(*args) -> subprocess.CompletedProcess:
    return subprocess.run(["kubectl", *args], capture_output=True, text=True)


def scale_to(pods: int, namespace: str, deployment: str):
    _kubectl("patch", "hpa", "neuro-san-hpa", f"-n={namespace}",
             "--type=merge", "-p", json.dumps({"spec": {"minReplicas": pods}}))
    _kubectl("patch", "hpa", "neuro-san-hpa-1", f"-n={namespace}",
             "--type=merge", "-p", json.dumps({"spec": {"minReplicas": pods}}))
    _kubectl("scale", f"deployment/{deployment}",
             f"--replicas={pods}", f"-n={namespace}")
    print(f"  Waiting for {pods} pods to be ready...")
    _kubectl("rollout", "status", f"deployment/{deployment}",
             f"-n={namespace}", "--timeout=5m")


def get_pod_memory_usage(namespace: str) -> list[dict]:
    """Per-pod memory in MiB and % of limit (3000Mi)."""
    pods = get_pod_metrics(namespace)
    result = []
    for p in pods:
        mem_str = p.get("mem", "0Mi")
        try:
            if mem_str.endswith("Mi"):
                mem_mi = float(mem_str[:-2])
            elif mem_str.endswith("Gi"):
                mem_mi = float(mem_str[:-2]) * 1024
            else:
                mem_mi = 0.0
        except ValueError:
            mem_mi = 0.0
        result.append({
            "name":       p["name"],
            "cpu":        p.get("cpu", "?"),
            "mem_mi":     round(mem_mi),
            "mem_limit_pct": round(mem_mi / 3072 * 100, 1),  # 3000Mi limit
        })
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Periodic snapshot
# ─────────────────────────────────────────────────────────────────────────────

def take_snapshot(runner, interval: int, elapsed_min: float,
                  cumulative_in: int, cumulative_out: int,
                  test_start: float, args) -> dict:
    total    = runner.stats.total
    p50      = total.median_response_time or 0
    p95      = total.get_response_time_percentile(0.95) or 0
    err      = total.fail_ratio * 100
    rps      = total.total_rps

    # Surface the top failure reasons from this interval
    fail_reasons: dict[str, int] = {}
    for entry in runner.stats.errors.values():
        key = f"{entry.method} {entry.name}: {entry.error}"
        fail_reasons[key] = fail_reasons.get(key, 0) + entry.occurrences

    # Token snapshot for this interval
    tok      = token_tracker.snapshot()
    token_tracker.reset()
    interval_in  = tok.get("total_input_tokens",  0)
    interval_out = tok.get("total_output_tokens", 0)

    # Cumulative quota
    cumulative_in  += interval_in
    cumulative_out += interval_out
    quota_used      = cumulative_in + cumulative_out
    quota_pct       = quota_used / QUOTA_TOTAL_TOKENS * 100
    burn_tpm        = quota_used / max(elapsed_min, 0.1)
    eta_min         = max(QUOTA_TOTAL_TOKENS - quota_used, 0) / max(burn_tpm, 1)

    # Per-turn token escalation
    turn_snap = turn_tracker.snapshot()

    # Pod memory + NGINX hot-pod
    pod_mem       = get_pod_memory_usage(NAMESPACE)
    pod_cpu_dist  = get_pod_cpu_distribution(NAMESPACE)
    node_snap     = get_node_metrics()
    blob          = get_blob_metrics(
                        os.getenv("BLOB_STORAGE_ACCOUNT", ""),
                        os.getenv("BLOB_RESOURCE_GROUP", ""))

    # ── Print snapshot ────────────────────────────────────────────────────────
    bar = "─" * 66
    print(f"\n{bar}")
    print(f"  t={elapsed_min:.0f}min  [{datetime.now().strftime('%H:%M:%S')}]  "
          f"VUs: {runner.user_count}/{args.vus}  "
          f"RPS: {rps:.1f}  p50: {p50/1000:.0f}s  p95: {p95/1000:.0f}s  err: {err:.1f}%")
    print(f"{bar}")

    # Token quota
    quota_flag = "  ⚠ WARNING" if quota_pct >= 80 else ""
    print(f"  QUOTA   : {quota_used:>12,} / {QUOTA_TOTAL_TOKENS:,} "
          f"({quota_pct:.1f}%)  burn: {burn_tpm:,.0f} tok/min  "
          f"ETA exhaustion: {eta_min:.0f} min{quota_flag}")

    # Per-turn token escalation — the key compounding insight
    if turn_snap:
        print(f"  TURNS   : ", end="")
        parts = []
        for t_num, t_data in sorted(turn_snap.items()):
            parts.append(f"t{t_num}: {t_data['avg_total']:,} tok "
                         f"({t_data['count']} completions)")
        print("  |  ".join(parts))
        if len(turn_snap) >= 2:
            t1_avg = turn_snap.get(1, {}).get("avg_total", 1)
            t_last = list(turn_snap.values())[-1].get("avg_total", 0)
            multiplier = t_last / max(t1_avg, 1)
            print(f"           ↳ context growth: turn {max(turn_snap.keys())} "
                  f"costs {multiplier:.1f}× turn 1 tokens")

    # Pod memory (OOM risk from growing session context)
    if pod_mem:
        max_mem = max(p["mem_limit_pct"] for p in pod_mem)
        oom_flag = "  ⚠ OOM RISK" if max_mem > 70 else ""
        print(f"  MEM     : ", end="")
        pod_mem_parts = [f"{p['name'][-8:]}: {p['mem_mi']}Mi ({p['mem_limit_pct']}%)"
                         for p in pod_mem[:5]]
        print("  ".join(pod_mem_parts) + oom_flag)

    # NGINX hot-pod detection (sticky session imbalance)
    if pod_cpu_dist:
        max_share = max(pod_cpu_dist.values())
        ideal_share = 100 // max(len(pod_cpu_dist), 1)
        hot_flag = f"  ⚠ HOT POD ({max_share}% vs ideal {ideal_share}%)" \
                   if max_share > ideal_share * 2 else ""
        print(f"  STICKY  : " +
              "  ".join(f"{name[-8:]}: {share}%" for name, share
                        in list(pod_cpu_dist.items())[:5]) + hot_flag)

    # Node CPU
    if node_snap:
        node_parts = [f"{n['name'][-12:]}: cpu {n['cpu_pct']}%  mem {n['mem_pct']}%"
                      for n in node_snap]
        print(f"  NODES   : " + "  |  ".join(node_parts))

    # Azure Blob
    if blob:
        print(f"  BLOB    : {blob.get('blob_transactions', '?')} IOPS  "
              f"{blob.get('blob_e2e_latency_ms', '?')} ms E2E latency"
              f"  (grows with active session depth)")

    # Failure breakdown — surfaces root cause of the error rate
    if fail_reasons and err > 0:
        print(f"  ERRORS  :")
        for reason, count in sorted(fail_reasons.items(), key=lambda x: -x[1])[:5]:
            print(f"    [{count:>4}] {reason[:100]}")

    runner.stats.reset_all()  # reset per-interval so next snapshot shows fresh data

    return {
        "interval":          interval,
        "elapsed_min":       round(elapsed_min, 1),
        "vu_count":          runner.user_count,
        "rps":               round(rps, 2),
        "p50_ms":            round(p50, 1),
        "p95_ms":            round(p95, 1),
        "error_rate_pct":    round(err, 2),
        "quota_used":        quota_used,
        "quota_pct":         round(quota_pct, 2),
        "burn_tpm":          round(burn_tpm, 0),
        "eta_exhaustion_min": round(eta_min, 0),
        "turn_stats":        turn_snap,
        "pod_memory":        pod_mem,
        "pod_cpu_dist":      pod_cpu_dist,
        "nodes":             node_snap,
        "blob":              blob,
        # carry forward cumulative totals
        "_cumulative_in":    cumulative_in,
        "_cumulative_out":   cumulative_out,
    }


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="Hackathon 90-min soak test")
    p.add_argument("--vus",        default=200, type=int,
                   help="Concurrent VUs — each = one active participant (default 200)")
    p.add_argument("--duration",   default=120, type=int,
                   help="Total test duration in minutes (default 120)")
    p.add_argument("--pods",       default=10, type=int,
                   help="Pod count to scale to before test (default 10)")
    p.add_argument("--host",       default=API_URL)
    p.add_argument("--namespace",  default=NAMESPACE)
    p.add_argument("--deployment", default=DEPLOYMENT)
    p.add_argument("--skip-scale", action="store_true",
                   help="Skip kubectl scaling — use current pod count")
    p.add_argument("--warmup",     default=60, type=int,
                   help="Seconds to wait after scaling before load starts (default 60)")
    p.add_argument("--max-tokens", default=0, type=int,
                   help="Hard token budget cap — stop test when exceeded (0 = no cap)")
    return p.parse_args()


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    args = parse_args()
    ts   = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    ramp_min  = math.ceil(args.vus / RAMP_RATE / 60)
    n_snaps   = args.duration * 60 // SNAPSHOT_INTERVAL

    print(f"\n{'='*66}")
    print(f"  HACKATHON SOAK TEST — 90-min sessions, agent_network_designer")
    print(f"{'='*66}")
    print(f"  Host        : {args.host}")
    print(f"  Pods        : {args.pods}")
    print(f"  VUs         : {args.vus} concurrent active participants")
    print(f"  Duration    : {args.duration} min ({n_snaps} snapshots every {SNAPSHOT_INTERVAL//60} min)")
    print(f"  Ramp        : {ramp_min} min at {RAMP_RATE} user/sec")
    print(f"  Think time  : 5-15 min between turns (realistic reading pace)")
    print(f"")
    print(f"  SESSION MODEL:")
    print(f"  • Each VU = 1 participant with a FIXED user_id for the full session")
    print(f"  • NGINX sticky cookie pins that user to ONE pod for 90 min")
    print(f"  • chat_context passed and grows each turn:")
    print(f"      Turn 1 (fresh design) → ~5k-15k tokens")
    print(f"      Turn 3 (refine)       → ~20k-40k tokens (history included)")
    print(f"      Turn 6 (deep refine)  → ~50k-80k tokens")
    print(f"  • Azure Blob: read + write on EVERY turn (not just session start)")
    print(f"")
    print(f"  BOTTLENECKS TRACKED:")
    print(f"  1. Token quota burn (compounds as sessions age) → ETA to 10M")
    print(f"  2. Per-turn token escalation (turn 1 vs 3 vs 6 cost)")
    print(f"  3. Pod memory growth (growing context in RAM per pinned session)")
    print(f"  4. NGINX hot-pod (sticky imbalance → one pod saturates first)")
    print(f"  5. Azure Blob IOPS (grows with session depth, not just count)")
    print(f"  6. p95 latency drift (slow as pod memory/queue fills)")
    print(f"{'='*66}\n")

    # Scale
    if not args.skip_scale:
        scale_to(args.pods, args.namespace, args.deployment)
        print(f"  Warming up for {args.warmup}s...")
        time.sleep(args.warmup)

    vert = get_pod_vertical_config(args.namespace, args.deployment)
    print(f"  Pod spec: cpu {vert['cpu_request']} req / {vert['cpu_limit']} lim  "
          f"| mem {vert['memory_request']} req / {vert['memory_limit']} lim")
    _slots_per_pod = 200  # AGENT_MAX_CONCURRENT_REQUESTS in values-azure-hackathon.yaml
    print(f"  LLM slots: {_slots_per_pod}/pod × {args.pods} pods = {_slots_per_pod * args.pods} total concurrent AI calls\n")

    # Locust environment
    env    = Environment(user_classes=[SessionUser], host=args.host)
    runner = env.create_local_runner()

    # Ramp up slowly (realistic hackathon arrival pattern)
    runner.start(user_count=args.vus, spawn_rate=RAMP_RATE)
    print(f"  Ramping to {args.vus} VUs at {RAMP_RATE} user/sec (~{ramp_min} min)...")

    snapshots      = []
    cumulative_in  = 0
    cumulative_out = 0
    test_start     = time.monotonic()

    budget_cap = args.max_tokens  # 0 = unlimited
    if budget_cap:
        print(f"\n  ⚠ TOKEN BUDGET CAP: {budget_cap:,} tokens  "
              f"(~₹{budget_cap/1e6*2.5*83.5:,.0f} at GPT-4o pricing)")
        print(f"    Test will stop automatically when cap is reached.\n")

    try:
        while True:
            elapsed = time.monotonic() - test_start
            if elapsed >= args.duration * 60:
                break
            time.sleep(SNAPSHOT_INTERVAL)

            elapsed_min   = (time.monotonic() - test_start) / 60
            interval_num  = len(snapshots) + 1
            snap = take_snapshot(
                runner, interval_num, elapsed_min,
                cumulative_in, cumulative_out, test_start, args
            )
            cumulative_in  = snap["_cumulative_in"]
            cumulative_out = snap["_cumulative_out"]
            snapshots.append(snap)

            # Hard budget cap — stop before burning more money
            if budget_cap and (cumulative_in + cumulative_out) >= budget_cap:
                print(f"\n  ⛔ TOKEN BUDGET CAP REACHED ({cumulative_in+cumulative_out:,} / {budget_cap:,})")
                print(f"     Stopping test to protect spend. Use --max-tokens to adjust.\n")
                break

    finally:
        runner.quit()
        time.sleep(3)

    # ── Final summary ─────────────────────────────────────────────────────────
    quota_used = cumulative_in + cumulative_out
    quota_pct  = quota_used / QUOTA_TOTAL_TOKENS * 100
    elapsed_min = (time.monotonic() - test_start) / 60
    burn_tpm   = quota_used / max(elapsed_min, 0.1)

    final_turn_snap = turn_tracker.snapshot()
    avg_turn1_tok = final_turn_snap.get(1, {}).get("avg_total", 0)
    avg_latest_tok = (list(final_turn_snap.values())[-1].get("avg_total", 0)
                      if final_turn_snap else 0)

    print(f"\n{'='*66}")
    print(f"  SOAK TEST COMPLETE")
    print(f"{'='*66}")
    print(f"  Duration     : {elapsed_min:.0f} min")
    print(f"  Peak VUs     : {args.vus}")
    print(f"  Tokens used  : {quota_used:,} ({quota_pct:.1f}% of 10M quota)")
    print(f"  Avg burn rate: {burn_tpm:,.0f} tok/min")

    if final_turn_snap:
        print(f"\n  PER-TURN TOKEN ESCALATION (context compounding effect):")
        for t_num, t_data in sorted(final_turn_snap.items()):
            multiplier = t_data["avg_total"] / max(avg_turn1_tok, 1)
            bar = "█" * min(int(multiplier * 5), 40)
            print(f"    Turn {t_num:>2} : {t_data['avg_total']:>7,} avg tokens  "
                  f"({multiplier:.1f}× turn 1)  {bar}  [{t_data['count']} samples]")

    # Hackathon projection
    print(f"\n  HACKATHON TOKEN BUDGET PROJECTION ({HACKATHON_PARTICIPANTS} participants):")
    if avg_turn1_tok > 0:
        for turns_per_person in [3, 5, 8, 10]:
            # Later turns cost more — use a weighted average
            # Approximation: turns follow avg_turn1_tok × turn_number growth
            weighted_avg = avg_turn1_tok * (1 + turns_per_person) / 2
            total_needed = HACKATHON_PARTICIPANTS * turns_per_person * weighted_avg
            multiplier   = total_needed / QUOTA_TOTAL_TOKENS
            ok_flag      = "✅" if multiplier <= 1 else f"⚠  need {math.ceil(multiplier)}× quota"
            print(f"    {turns_per_person} turns/person → {total_needed/1e6:.0f}M tokens  "
                  f"({multiplier:.0f}× current 10M)  {ok_flag}")
        recommended_m = math.ceil(HACKATHON_PARTICIPANTS * 5 * avg_turn1_tok * 3 / 1e6)
        print(f"\n  ★ REQUEST ≥{recommended_m}M TOKENS on Azure AI Foundry")
        print(f"    (2000 participants × 5 turns × compounding context, 20% headroom)")
    else:
        print(f"  Run longer with --duration 60+ to capture turn data.")

    openai_key = os.getenv("OPENAI_API_KEY", "")
    headroom   = check_openai_headroom(openai_key)
    if headroom:
        print(f"\n  OpenAI headroom: req {headroom.get('req_headroom_pct')}%  "
              f"tok {headroom.get('tok_headroom_pct')}%")

    # Save report
    output = {
        "test_type":  "hackathon_soak",
        "timestamp":  datetime.now(timezone.utc).isoformat(),
        "host":       args.host,
        "config": {
            "vus":              args.vus,
            "duration_min":     args.duration,
            "pods":             args.pods,
            "snapshot_interval": SNAPSHOT_INTERVAL,
        },
        "summary": {
            "quota_used_tokens":  quota_used,
            "quota_pct":          round(quota_pct, 2),
            "burn_tpm":           round(burn_tpm, 0),
            "elapsed_min":        round(elapsed_min, 1),
        },
        "turn_stats":  final_turn_snap,
        "snapshots":   snapshots,
    }
    path = REPORTS_DIR / f"hackathon_soak_{ts}.json"
    path.write_text(json.dumps(output, indent=2))
    print(f"\n  Report saved → {path}")
    print(f"  Dashboard   → streamlit run dashboard.py")
    print(f"{'='*66}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
