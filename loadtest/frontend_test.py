"""
Frontend / login-wave load test — the Next.js UI tier (the ~6 UI pods).
========================================================================
The backend tests (hackathon_test.py) fire only 2 GETs at the UI, so the UI tier is
effectively untested. This drives the UI pods the way a real login STAMPEDE does:
each virtual browser pulls the app shell + the JS/CSS bundle + the on-load API routes
(/api/environment, /api/auth/session). See users.FrontendUser.

This is the test to answer: "when 2,500 people open the URL in the same 60 seconds,
does the UI tier stay up and fast?" — the arrival spike the 6 UI replicas exist for.

It is HTTP-level (not a headless browser): it fetches the shell + bundle bytes and the
Node API routes, which is what saturates the UI pods. It deliberately does NOT drive the
backend design workload (that's hackathon_test.py).

Reports every SNAPSHOT_INTERVAL seconds:
  - VUs, RPS, aggregate p50/p95/p99, error rate + breakdown
  - UI per-pod CPU% / MEM% (label app=ui-node) + replica count
  - system-node CPU/MEM

Usage (UI pods already deployed):
  # Stampede: 2,500 browsers arriving in 60s, hold 5 min
  python3 frontend_test.py --vus 2500 --ramp-min 1 --duration 6

  # Gentler wave (staggered arrival sanity)
  python3 frontend_test.py --vus 2500 --ramp-min 5 --duration 10
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from locust.env import Environment

from config import (
    FRONTEND_URL,
    UI_CPU_LIMIT,
    UI_MEM_LIMIT_MI,
)
from metrics import get_node_metrics, get_pod_usage
from users import FrontendUser

NAMESPACE         = "neuro-san-hackathon"
REPORTS_DIR       = Path(__file__).parent / "reports"
REPORTS_DIR.mkdir(exist_ok=True)
SNAPSHOT_INTERVAL = 30          # seconds between snapshots (UI events are fast)


def parse_args():
    p = argparse.ArgumentParser(description="Frontend / login-wave (UI tier) load test")
    p.add_argument("--vus",      default=2500, type=int, help="peak concurrent browsers")
    p.add_argument("--duration", default=6,    type=int, help="total minutes (incl. ramp)")
    p.add_argument("--ramp-min", default=1,    type=int, help="minutes to ramp 0→vus (1 = stampede)")
    p.add_argument("--host",     default=FRONTEND_URL)
    p.add_argument("--namespace", default=NAMESPACE)
    return p.parse_args()


def _fmt_pods(pods, top=12):
    return "\n".join(
        f"     {p['name'][-24:]:<24}  cpu {p['cpu_pct']:>5.1f}%   mem {p['mem_pct']:>5.1f}%"
        f"   ({p['cpu_m']}m)"
        for p in pods[:top]
    )


def snapshot(runner, args, elapsed_min):
    total = runner.stats.total
    p50 = total.get_current_response_time_percentile(0.5) or 0
    p95 = total.get_current_response_time_percentile(0.95) or 0
    p99 = total.get_current_response_time_percentile(0.99) or 0
    err = total.fail_ratio * 100
    rps = total.current_rps

    ui    = get_pod_usage(args.namespace, "app=ui-node", int(UI_CPU_LIMIT), UI_MEM_LIMIT_MI)
    nodes = get_node_metrics()

    fails: dict[str, int] = {}
    for e in runner.stats.errors.values():
        fails[f"{e.name}: {e.error}"] = fails.get(f"{e.name}: {e.error}", 0) + e.occurrences

    bar = "─" * 72
    print(f"\n{bar}")
    print(f"  t={elapsed_min:>4.1f}m   VUs {runner.user_count}/{args.vus}   RPS {rps:.0f}"
          f"   reqs {total.num_requests:,}   err {err:.1f}%")
    print(f"  p50 {p50:.0f}ms   p95 {p95:.0f}ms   p99 {p99:.0f}ms")
    print(bar)
    if ui:
        hot = max((p['cpu_pct'] for p in ui), default=0)
        print(f"  UI PODS  ({len(ui)} replicas, peak CPU {hot:.0f}%):")
        print(_fmt_pods(ui))
    else:
        print(f"  UI PODS  : n/a (kubectl unavailable — run from a machine with cluster access)")
    if nodes:
        print(f"  NODES    : " + "  |  ".join(
            f"{n['name'][-10:]} cpu {n['cpu_pct']}% mem {n['mem_pct']}%" for n in nodes))
    # per-endpoint latency (shell vs bundle vs API routes) is what pinpoints the tier
    print(f"  BY ENDPOINT (p95 ms / reqs):")
    for name, s in sorted(runner.stats.entries.items()):
        # entries key is (name, method); s is a StatsEntry
        nm = s.name
        if nm == "Aggregated":
            continue
        print(f"     {nm[:34]:<34} p95 {int(s.get_response_time_percentile(0.95) or 0):>6}   "
              f"n {s.num_requests:>7,}   fail {s.num_failures:>5,}")
    if fails and err > 0:
        print(f"  ERRORS   :")
        for reason, cnt in sorted(fails.items(), key=lambda x: -x[1])[:6]:
            print(f"     [{cnt:>5}] {reason[:88]}")


def main():
    args = parse_args()
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    spawn_rate = max(args.vus / max(args.ramp_min * 60, 1), 1)

    print(f"\n{'='*72}")
    print(f"  FRONTEND / LOGIN-WAVE LOAD TEST  (UI tier)")
    print(f"{'='*72}")
    print(f"  Host        : {args.host}")
    print(f"  Peak VUs    : {args.vus}   (ramp {args.ramp_min} min @ {spawn_rate:.0f}/s, then hold)")
    print(f"  Duration    : {args.duration} min")
    print(f"  Each VU     : GET / (shell) + /api/environment + /api/auth/session + JS/CSS bundle")
    print(f"{'='*72}")

    env = Environment(user_classes=[FrontendUser], host=args.host)
    runner = env.create_local_runner()
    runner.start(user_count=args.vus, spawn_rate=spawn_rate)

    start = time.monotonic()
    try:
        while time.monotonic() - start < args.duration * 60:
            time.sleep(SNAPSHOT_INTERVAL)
            snapshot(runner, args, (time.monotonic() - start) / 60)
    finally:
        runner.quit()
        time.sleep(3)

    total = runner.stats.total
    success_pct = (1 - total.fail_ratio) * 100
    print(f"\n{'='*72}")
    print(f"  RESULT — frontend / login-wave test")
    print(f"{'='*72}")
    print(f"  Total requests   : {total.num_requests:,}   success {success_pct:.2f}%"
          f"   ({total.num_failures:,} failed)")
    print(f"  Latency  p50 {total.median_response_time:.0f}ms"
          f"  p95 {(total.get_response_time_percentile(0.95) or 0):.0f}ms"
          f"  p99 {(total.get_response_time_percentile(0.99) or 0):.0f}ms")
    ui = get_pod_usage(args.namespace, "app=ui-node", int(UI_CPU_LIMIT), UI_MEM_LIMIT_MI)
    if ui:
        print(f"  UI pods peak CPU : {max((p['cpu_pct'] for p in ui), default=0):.0f}%"
              f"   ({len(ui)} replicas)")
    print(f"  PASS if: 5xx < 1%, no UI pod restarts, p95 shell < 3-5s, UI CPU not pinned")
    print(f"{'='*72}\n")

    out = {
        "test_type": "frontend_login_wave",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "config": {"vus": args.vus, "duration_min": args.duration, "ramp_min": args.ramp_min},
        "summary": {
            "total_requests": total.num_requests,
            "success_pct": round(success_pct, 2),
            "failures": total.num_failures,
            "p50_ms": total.median_response_time,
            "p95_ms": total.get_response_time_percentile(0.95),
            "p99_ms": total.get_response_time_percentile(0.99),
        },
    }
    path = REPORTS_DIR / f"frontend_test_{ts}.json"
    path.write_text(json.dumps(out, indent=2, default=str))
    print(f"  Report saved → {path}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
