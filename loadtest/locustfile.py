"""
Neuro-San Hackathon — Python Load Test Suite (Locust)
======================================================

Quick start:
  pip install -r requirements.txt

  # Interactive mode (real-time dashboard at http://localhost:8089)
  locust -f locustfile.py --host https://neurosanhackathon-api.eastus.cloudapp.azure.com

  # Headless smoke test (2 min, 10 VUs)
  LOCUST_SHAPE=smoke locust -f locustfile.py --headless \\
      --csv reports/smoke --html reports/smoke.html

  # Full stress test to 2000 VUs
  LOCUST_SHAPE=stress locust -f locustfile.py --headless \\
      --csv reports/stress --html reports/stress.html

  # Post-run analysis dashboard
  streamlit run dashboard.py

Or use the convenience wrapper:  ./run.sh <smoke|load|stress|spike|soak|interactive|dashboard>
"""

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from locust import events
from locust.env import Environment

# ── Users (Locust discovers all HttpUser subclasses in module namespace) ───────
from users import BrowseUser, BurstUser, ChatUser, HealthCheckUser, PowerUser

# ── Shape — ONE LoadTestShape subclass selected via LOCUST_SHAPE env var ───────
# Locust 2.x auto-uses the single LoadTestShape it finds; having multiple causes
# it to pick arbitrarily.  We expose exactly one by importing dynamically.
import importlib as _il
import os as _os

_SHAPE_MAP = {
    "smoke":  "SmokeShape",
    "load":   "LoadShape",
    "stress": "StressShape",
    "spike":  "SpikeShape",
    "soak":   "SoakShape",
}
_shape_key = _os.getenv("LOCUST_SHAPE", "load")
ActiveShape = getattr(_il.import_module("shapes"), _SHAPE_MAP.get(_shape_key, "LoadShape"))

# ── Config ────────────────────────────────────────────────────────────────────
from config import API_URL, THRESHOLDS

__all__ = [
    "HealthCheckUser", "BrowseUser", "ChatUser", "PowerUser", "BurstUser",
    "ActiveShape",
]

log = logging.getLogger("neuro-san-loadtest")

REPORTS_DIR = Path(__file__).parent / "reports"
REPORTS_DIR.mkdir(exist_ok=True)

_test_start: float = 0.0
_test_label: str   = "run"


@events.init_command_line_parser.add_listener
def add_custom_args(parser, **kwargs):
    parser.add_argument(
        "--test-label",
        default="run",
        help="Short label for this test run (used in saved report filename).",
    )


@events.test_start.add_listener
def on_test_start(environment: Environment, **kwargs):
    global _test_start, _test_label
    _test_start = time.monotonic()
    _test_label = getattr(environment.parsed_options, "test_label", "run")
    log.info("Load test started — host: %s  label: %s", API_URL, _test_label)
    log.info("Thresholds: %s", THRESHOLDS)


@events.test_stop.add_listener
def on_test_stop(environment: Environment, **kwargs):
    duration = time.monotonic() - _test_start
    stats    = environment.runner.stats
    total    = stats.total

    # ── Build per-endpoint breakdown ──────────────────────────────────────────
    endpoints: dict = {}
    for (method, name), entry in stats.entries.items():
        if not entry.num_requests:
            continue
        p95 = entry.get_response_time_percentile(0.95) or 0
        p99 = entry.get_response_time_percentile(0.99) or 0
        endpoints[f"{method} {name}"] = {
            "method":      method,
            "name":        name,
            "requests":    entry.num_requests,
            "failures":    entry.num_failures,
            "error_pct":   round(entry.fail_ratio * 100, 2),
            "median_ms":   round(entry.median_response_time, 1),
            "avg_ms":      round(entry.avg_response_time, 1),
            "p95_ms":      round(p95, 1),
            "p99_ms":      round(p99, 1),
            "min_ms":      round(entry.min_response_time or 0, 1),
            "max_ms":      round(entry.max_response_time or 0, 1),
            "rps":         round(entry.total_rps, 2),
        }

    # ── Overall percentiles ───────────────────────────────────────────────────
    p95_total = total.get_response_time_percentile(0.95) or 0
    p99_total = total.get_response_time_percentile(0.99) or 0

    summary = {
        "test_label":         _test_label,
        "timestamp":          datetime.now(timezone.utc).isoformat(),
        "duration_seconds":   round(duration, 1),
        "total_requests":     total.num_requests,
        "total_failures":     total.num_failures,
        "error_rate_pct":     round(total.fail_ratio * 100, 2),
        "avg_rps":            round(total.total_rps, 2),
        "median_ms":          round(total.median_response_time, 1),
        "avg_ms":             round(total.avg_response_time, 1),
        "p95_ms":             round(p95_total, 1),
        "p99_ms":             round(p99_total, 1),
        "endpoints":          endpoints,
        "thresholds":         _evaluate_thresholds(total, p95_total),
    }

    ts   = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = REPORTS_DIR / f"summary_{_test_label}_{ts}.json"
    path.write_text(json.dumps(summary, indent=2))

    # ── Console output ────────────────────────────────────────────────────────
    passed = sum(1 for v in summary["thresholds"].values() if v["passed"])
    total_thr = len(summary["thresholds"])
    print(f"\n{'='*62}")
    print(f"  LOAD TEST COMPLETE — {_test_label.upper()}")
    print(f"{'='*62}")
    print(f"  Duration      : {duration/60:.1f} min")
    print(f"  Requests      : {total.num_requests:,}")
    print(f"  Failures      : {total.num_failures:,}  ({summary['error_rate_pct']:.1f}%)")
    print(f"  Avg RPS       : {summary['avg_rps']:.1f}")
    print(f"  Median latency: {summary['median_ms']:.0f} ms")
    print(f"  p95 latency   : {summary['p95_ms']:.0f} ms")
    print(f"  p99 latency   : {summary['p99_ms']:.0f} ms")
    print(f"\n  Thresholds: {passed}/{total_thr} passed")
    for name, result in summary["thresholds"].items():
        icon = "✅" if result["passed"] else "❌"
        print(f"    {icon} {name:35s}  {result['actual']:>10s}  (limit: {result['limit']})")
    print(f"\n  Report saved → {path}")
    print(f"  Dashboard    → streamlit run dashboard.py")
    print(f"{'='*62}\n")


def _evaluate_thresholds(total, p95_ms: float) -> dict:
    return {
        "error_rate_lt_5pct": {
            "passed": total.fail_ratio * 100 <= THRESHOLDS["error_rate_pct"],
            "actual": f"{total.fail_ratio * 100:.1f}%",
            "limit":  f"{THRESHOLDS['error_rate_pct']:.0f}%",
        },
        "p95_latency_lt_30s": {
            "passed": p95_ms <= THRESHOLDS["p95_latency_ms"],
            "actual": f"{p95_ms / 1000:.1f}s",
            "limit":  f"{THRESHOLDS['p95_latency_ms'] / 1000:.0f}s",
        },
        "min_1000_requests": {
            "passed": total.num_requests >= 1_000,
            "actual": f"{total.num_requests:,}",
            "limit":  "≥ 1,000",
        },
    }
