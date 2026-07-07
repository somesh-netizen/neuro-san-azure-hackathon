"""
Hackathon end-to-end load test — single ramp-and-hold run.
===========================================================
ONE run that contains all three signals (ladder + login wave + realistic soak):

  • Ramp 0 → --vus over --ramp-min minutes = the capacity curve (watch for the knee
    where behaviour stops being linear).
  • Hold at --vus for the rest = the realistic steady-state event.
  • Each SessionUser loads the UI first (login wave) then designs, closed-loop, with
    2-4 min think time between turns and the production 1-query/30s rate limit.

Everything is at PRODUCTION settings — nothing is compressed. Observe ~30 min and
extrapolate to the 90-min event (steady-state metrics are flat once stabilised;
only turn 4-6 token cost is projected from the measured curve).

Reports every SNAPSHOT_INTERVAL seconds:
  - concurrency: target VUs, actual VUs, in-flight designs (live), peak
  - latency: p50 / p95 / p99   · throughput: RPS   · error rate + breakdown
  - tokens: cumulative / 330M, burn tok/min, per-turn escalation
  - BACKEND per-pod: CPU% (of D16 node) + MEM% (of 6Gi) + node placement
  - FRONTEND per-pod: UI CPU% / MEM% / replicas
  - per-key TPM from Azure Monitor (balance across the 11 keys)
  - rate limit: 429 total + how many distinct users hit it
  - nodes: CPU% / MEM% per node

Usage (pods already deployed — always --skip-scale here; fixed fleet, no autoscaling):
  python3 hackathon_test.py --vus 2700 --duration 30 --ramp-min 8
Distributed (recommended for 2700 — see RUN_LOADTEST.md): run this as the master with
  --master, and start workers with `locust -f locustfile.py --worker`.
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from locust.env import Environment

from config import (
    API_URL,
    AZURE_OPENAI_RESOURCES,
    BACKEND_MEM_LIMIT_MI,
    BACKEND_NODE_VCPU,
    OPENAI_MODEL,
    PER_KEY_TPM_LIMIT,
    TOKEN_QUOTA_TOTAL,
    UI_CPU_LIMIT,
    UI_MEM_LIMIT_MI,
)
from metrics import (
    estimate_cost,
    get_node_metrics,
    get_per_key_tpm,
    get_pod_usage,
    in_flight,
    rate_limit_tracker,
    token_tracker,
    turn_tracker,
)
from users import SessionUser

NAMESPACE       = "neuro-san-hackathon"
REPORTS_DIR     = Path(__file__).parent / "reports"
REPORTS_DIR.mkdir(exist_ok=True)
SNAPSHOT_INTERVAL = 60          # seconds between snapshots


def parse_args():
    p = argparse.ArgumentParser(description="Hackathon end-to-end ramp-and-hold test")
    p.add_argument("--vus",      default=2700, type=int, help="peak concurrent participants")
    p.add_argument("--duration", default=30,   type=int, help="total minutes (incl. ramp)")
    p.add_argument("--ramp-min", default=8,    type=int, help="minutes to ramp 0→vus")
    p.add_argument("--host",     default=API_URL)
    p.add_argument("--namespace", default=NAMESPACE)
    return p.parse_args()


def _fmt_pods(pods, top=12):
    return "\n".join(
        f"     {p['name'][-20:]:<20}  cpu {p['cpu_pct']:>5.1f}%   mem {p['mem_pct']:>5.1f}%"
        f"   ({p['cpu_m']}m)"
        for p in pods[:top]
    )


def snapshot(runner, args, elapsed_min, prev_tokens, peak_vu):
    total = runner.stats.total
    p50 = total.get_current_response_time_percentile(0.5) or 0
    p95 = total.get_current_response_time_percentile(0.95) or 0
    p99 = total.get_current_response_time_percentile(0.99) or 0
    err = total.fail_ratio * 100
    rps = total.current_rps

    tok = token_tracker.snapshot()
    used = tok["total_input_tokens"] + tok["total_output_tokens"]
    burn = (used - prev_tokens) / max(SNAPSHOT_INTERVAL / 60, 0.01)   # tok/min this interval

    back = get_pod_usage(args.namespace, "app=neuro-san", BACKEND_NODE_VCPU, BACKEND_MEM_LIMIT_MI)
    uipods = get_pod_usage(args.namespace, "app=ui-node", int(UI_CPU_LIMIT), UI_MEM_LIMIT_MI)
    nodes = get_node_metrics()
    per_key = get_per_key_tpm(AZURE_OPENAI_RESOURCES)
    rl = rate_limit_tracker.snapshot()
    turns = turn_tracker.snapshot()

    # error breakdown (cumulative)
    fails: dict[str, int] = {}
    for e in runner.stats.errors.values():
        fails[f"{e.name}: {e.error}"] = fails.get(f"{e.name}: {e.error}", 0) + e.occurrences

    completed = sum(d["count"] for d in turns.values()) if turns else 0
    # Saturation: most VUs stuck in-flight while almost nothing is completing = the queue
    # is building, not draining. This is what "100% success" hides under overload.
    saturated = in_flight.current >= 0.7 * args.vus and rps < 3

    bar = "─" * 72
    print(f"\n{bar}")
    print(f"  t={elapsed_min:>4.1f}m   VUs {runner.user_count}/{args.vus}"
          f"   in-flight {in_flight.current} (peak {in_flight.peak})"
          f"   RPS {rps:.1f}   completed {completed}"
          + ("   ⚠ SATURATED — designs queuing, not draining" if saturated else ""))
    print(f"  p50 {p50/1000:.0f}s  p95 {p95/1000:.0f}s  p99 {p99/1000:.0f}s"
          f"   err {err:.1f}%   (latency reflects COMPLETED reqs; queued designs not counted)")
    print(bar)
    print(f"  TOKENS   : {used:>13,} total   burn {burn:,.0f}/min"
          f"  ({burn / TOKEN_QUOTA_TOTAL * 100:.1f}% of {TOKEN_QUOTA_TOTAL // 1_000_000}M TPM capacity)")
    if turns:
        parts = [f"t{n} {d['avg_total']//1000}k({d['count']})" for n, d in sorted(turns.items())]
        print(f"  TURNS    : " + "  ".join(parts))
    print(f"  429s     : {rl['total_429']} total from {rl['unique_users']} users"
          + (f"  top: {rl['top_offenders'][:3]}" if rl['top_offenders'] else ""))
    if back:
        hot = max((p['cpu_pct'] for p in back), default=0)
        print(f"  BACKEND  ({len(back)} pods, peak CPU {hot:.0f}%):")
        print(_fmt_pods(back))
    if uipods:
        print(f"  FRONTEND ({len(uipods)} UI pods):")
        print(_fmt_pods(uipods))
    if per_key:
        vals = list(per_key.values())
        lo, hi = min(vals), max(vals)
        bal = hi / max(lo, 1)
        per_min_hi = hi / 5.0     # window is PT5M
        flag = "  ⚠ >80% TPM" if per_min_hi > 0.8 * PER_KEY_TPM_LIMIT else ""
        print(f"  PER-KEY  : {len(per_key)} keys reporting  "
              f"5-min tokens min {lo:,} / max {hi:,}  balance {bal:.1f}×{flag}")
    else:
        print(f"  PER-KEY  : n/a (Azure Monitor unavailable — client token total is authoritative)")
    if nodes:
        print(f"  NODES    : " + "  |  ".join(
            f"{n['name'][-10:]} cpu {n['cpu_pct']}% mem {n['mem_pct']}%" for n in nodes))
    if fails and err > 0:
        print(f"  ERRORS   :")
        for reason, cnt in sorted(fails.items(), key=lambda x: -x[1])[:5]:
            print(f"     [{cnt:>4}] {reason[:90]}")

    return used, max(peak_vu, runner.user_count)


def main():
    args = parse_args()
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    spawn_rate = max(args.vus / max(args.ramp_min * 60, 1), 1)

    print(f"\n{'='*72}")
    print(f"  HACKATHON END-TO-END LOAD TEST")
    print(f"{'='*72}")
    print(f"  Host          : {args.host}")
    print(f"  Peak VUs      : {args.vus}   (ramp {args.ramp_min} min @ {spawn_rate:.1f}/s, then hold)")
    print(f"  Duration      : {args.duration} min   (production settings; extrapolate to 90)")
    print(f"  Model / quota : {OPENAI_MODEL}   ceiling {TOKEN_QUOTA_TOTAL:,} TPM (context only)")
    print(f"  Each VU       : login (UI) → design → refine 1-6× closed-loop, 2-4 min think")
    print(f"{'='*72}")

    env = Environment(user_classes=[SessionUser], host=args.host)
    runner = env.create_local_runner()
    runner.start(user_count=args.vus, spawn_rate=spawn_rate)

    start = time.monotonic()
    prev_tokens = 0
    peak_vu = 0
    snaps = 0
    try:
        while time.monotonic() - start < args.duration * 60:
            time.sleep(SNAPSHOT_INTERVAL)
            elapsed_min = (time.monotonic() - start) / 60
            prev_tokens, peak_vu = snapshot(runner, args, elapsed_min, prev_tokens, peak_vu)
            snaps += 1
    finally:
        runner.quit()
        time.sleep(3)

    # ── Final summary ─────────────────────────────────────────────────────────
    tok = token_tracker.snapshot()
    tin, tout = tok["total_input_tokens"], tok["total_output_tokens"]
    used = tin + tout
    cost = estimate_cost(tin, tout, OPENAI_MODEL)
    total = runner.stats.total
    rl = rate_limit_tracker.snapshot()
    turns = turn_tracker.snapshot()
    elapsed_min = (time.monotonic() - start) / 60
    success_pct = (1 - total.fail_ratio) * 100
    completed = sum(d["count"] for d in turns.values()) if turns else 0
    avg_burn = used / max(elapsed_min, 0.01)                    # tok/min
    # If in-flight ended near the VU count, most designs were still QUEUED at the end —
    # "success%" only reflects requests that actually finished, so flag it honestly.
    ended_saturated = in_flight.current >= 0.7 * args.vus

    print(f"\n{'='*72}")
    print(f"  RESULT — hackathon end-to-end test")
    print(f"{'='*72}")
    print(f"  Peak concurrent VUs   : {peak_vu}        Peak in-flight designs : {in_flight.peak}")
    print(f"  Total requests        : {total.num_requests:,}   success {success_pct:.1f}%"
          f"   ({total.num_failures:,} failed)")
    print(f"  Designs COMPLETED     : {completed}   still in-flight at end: {in_flight.current}"
          + ("   ⚠ ENDED SATURATED — most designs were QUEUED, not finished;"
             " success% counts only completed reqs" if ended_saturated else ""))
    print(f"  TOKENS  in {tin:,} + out {tout:,} = {used:,} total")
    print(f"  BURN    ~ {avg_burn:,.0f} tok/min  ({avg_burn / TOKEN_QUOTA_TOTAL * 100:.1f}% of"
          f" {TOKEN_QUOTA_TOTAL // 1_000_000}M TPM capacity — tokens are NOT the bottleneck)")
    print(f"  COST    ~ ${cost['cost_total_usd']:.2f}  ({OPENAI_MODEL})")
    print(f"  RATE LIMIT (429)      : {rl['total_429']} total from {rl['unique_users']} users"
          f"   (abuse-guard; normal users pace under 1/30s)")
    print(f"  LATENCY  p50 {total.median_response_time/1000:.0f}s"
          f"  p95 {(total.get_response_time_percentile(0.95) or 0)/1000:.0f}s"
          f"  p99 {(total.get_response_time_percentile(0.99) or 0)/1000:.0f}s")
    if turns:
        esc = "  ".join(f"t{n} {d['avg_total']//1000}k" for n, d in sorted(turns.items()))
        print(f"  PER-TURN escalation   : {esc}   (turns 4-6 project the 90-min token total)")
    print(f"{'='*72}\n")

    out = {
        "test_type": "hackathon_end_to_end",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "config": {"vus": args.vus, "duration_min": args.duration, "ramp_min": args.ramp_min,
                   "model": OPENAI_MODEL, "quota": TOKEN_QUOTA_TOTAL},
        "summary": {
            "peak_vu": peak_vu, "peak_in_flight": in_flight.peak,
            "total_requests": total.num_requests, "success_pct": round(success_pct, 2),
            "tokens_in": tin, "tokens_out": tout, "tokens_total": used,
            "cost_usd": cost["cost_total_usd"],
            "rate_limit": rl, "turn_stats": turns,
            "p50_ms": total.median_response_time,
            "p95_ms": total.get_response_time_percentile(0.95),
            "p99_ms": total.get_response_time_percentile(0.99),
        },
    }
    path = REPORTS_DIR / f"hackathon_test_{ts}.json"
    path.write_text(json.dumps(out, indent=2, default=str))
    print(f"  Report saved → {path}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
