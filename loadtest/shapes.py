"""
Load test shapes — select at runtime with the --shape-class flag.

Usage (headless):
  locust -f locustfile.py --shape-class SmokeShape  --headless --csv reports/smoke
  locust -f locustfile.py --shape-class LoadShape   --headless --csv reports/load
  locust -f locustfile.py --shape-class StressShape --headless --csv reports/stress
  locust -f locustfile.py --shape-class SpikeShape  --headless --csv reports/spike
  locust -f locustfile.py --shape-class SoakShape   --headless --csv reports/soak

Or use run.sh for pre-built commands.
"""

from locust import LoadTestShape


class _StagedShape(LoadTestShape):
    """
    Base class: drives VU count through a list of stages.
    Each stage dict: {"duration": seconds, "users": int, "spawn_rate": int}.
    """

    stages: list[dict] = []

    def tick(self):
        t = self.get_run_time()
        for stage in self.stages:
            if t < stage["duration"]:
                return stage["users"], stage["spawn_rate"]
        return None  # signal Locust to stop


# ─────────────────────────────────────────────────────────────────────────────
class SmokeShape(_StagedShape):
    """
    10 VUs for 2 minutes.
    Goal: verify every endpoint responds correctly before a real load test.
    Expected runtime: ~3 min.
    """
    stages = [
        {"duration":  30, "users":  5, "spawn_rate":  5},   # warm-up
        {"duration": 150, "users": 10, "spawn_rate":  5},   # hold
        {"duration": 180, "users":  0, "spawn_rate": 10},   # ramp down
    ]


# ─────────────────────────────────────────────────────────────────────────────
class LoadShape(_StagedShape):
    """
    Stepped ramp: 50 → 200 → 500 → 1000 VUs, 3 min per step.
    Goal: confirm the app handles expected production traffic.
    Expected runtime: ~16 min.
    """
    stages = [
        {"duration":  60,  "users":    50, "spawn_rate":  10},  # step 1
        {"duration": 240,  "users":   200, "spawn_rate":  15},  # step 2
        {"duration": 420,  "users":   500, "spawn_rate":  25},  # step 3
        {"duration": 600,  "users":  1000, "spawn_rate":  50},  # step 4
        {"duration": 780,  "users":  1000, "spawn_rate":   5},  # hold
        {"duration": 840,  "users":     0, "spawn_rate": 100},  # ramp down
    ]


# ─────────────────────────────────────────────────────────────────────────────
class StressShape(_StagedShape):
    """
    Aggressive ramp: 50 → 500 → 1000 → 2000 VUs.
    Goal: find the breaking point (error rate > 5% or p95 > 30 s).
    Expected runtime: ~16 min.
    """
    stages = [
        {"duration":  60,  "users":    50, "spawn_rate":  10},
        {"duration": 240,  "users":   500, "spawn_rate":  30},
        {"duration": 420,  "users":  1000, "spawn_rate":  60},
        {"duration": 600,  "users":  2000, "spawn_rate": 100},
        {"duration": 780,  "users":  2000, "spawn_rate":   5},  # hold at max
        {"duration": 840,  "users":     0, "spawn_rate": 200},  # ramp down
    ]


# ─────────────────────────────────────────────────────────────────────────────
class SpikeShape(_StagedShape):
    """
    Baseline → sudden 10x spike → recover → repeat smaller spike.
    Goal: measure how fast the system absorbs and drains sudden surges
          (relevant for HPA scale-up latency).
    Expected runtime: ~13 min.
    """
    stages = [
        {"duration":  60,  "users":  50, "spawn_rate":  10},  # baseline
        {"duration": 120,  "users":  50, "spawn_rate":   5},  # hold baseline
        # Spike 1 — massive
        {"duration": 150,  "users": 600, "spawn_rate": 500},  # spike UP
        {"duration": 270,  "users": 600, "spawn_rate":   5},  # hold spike
        {"duration": 300,  "users":  50, "spawn_rate": 300},  # drop back
        {"duration": 420,  "users":  50, "spawn_rate":   5},  # hold recovered
        # Spike 2 — moderate (recovery validation)
        {"duration": 450,  "users": 300, "spawn_rate": 200},  # second spike
        {"duration": 570,  "users": 300, "spawn_rate":   5},  # hold
        {"duration": 600,  "users":  50, "spawn_rate": 150},  # recover
        {"duration": 720,  "users":  50, "spawn_rate":   5},  # hold
        {"duration": 750,  "users":   0, "spawn_rate": 100},  # stop
    ]


# ─────────────────────────────────────────────────────────────────────────────
class SoakShape(_StagedShape):
    """
    200 VUs sustained for 30 minutes.
    Goal: detect memory leaks, connection pool exhaustion, gradual degradation,
          and pod restart-loops that only appear under prolonged load.
    Expected runtime: ~33 min.
    """
    _HOLD = 30 * 60  # 30 minutes in seconds

    stages = [
        {"duration":     60,           "users": 200, "spawn_rate": 20},  # ramp up
        {"duration":     60 + _HOLD,   "users": 200, "spawn_rate":  5},  # hold
        {"duration":     60 + _HOLD + 60, "users":  0, "spawn_rate": 50},  # ramp down
    ]
