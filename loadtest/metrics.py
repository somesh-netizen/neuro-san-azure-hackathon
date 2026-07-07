"""
Cluster, node, and service metrics collectors for capacity planning tests.

Imported by:
  - users.py       → token_tracker singleton (per-request token counts)
  - capacity_test.py → step/pod-level snapshots (nodes, NGINX, OpenAI, Blob)

All collectors are non-fatal — missing kubectl/az access returns empty dicts.
"""

import json
import os
import subprocess
import threading
from collections import defaultdict


# ═══════════════════════════════════════════════════════════════════════════════
# TOKEN TRACKING  (populated per-request in CapacityUser)
# ═══════════════════════════════════════════════════════════════════════════════

class TokenTracker:
    """Thread-safe accumulator for LLM token counts across concurrent users."""

    def __init__(self):
        self._lock   = threading.Lock()
        self._input  = 0
        self._output = 0
        self._calls  = 0

    def record(self, input_tokens: int, output_tokens: int) -> None:
        with self._lock:
            self._input  += input_tokens
            self._output += output_tokens
            self._calls  += 1

    def reset(self) -> None:
        with self._lock:
            self._input = self._output = self._calls = 0

    def snapshot(self) -> dict:
        with self._lock:
            calls = self._calls
            return {
                "total_input_tokens":  self._input,
                "total_output_tokens": self._output,
                "total_llm_calls":     calls,
                "avg_input_per_call":  round(self._input  / calls, 1) if calls else 0,
                "avg_output_per_call": round(self._output / calls, 1) if calls else 0,
            }


# Module-level singleton shared by all user greenlets
token_tracker = TokenTracker()


class TurnTracker:
    """
    Tracks per-turn token usage across all SessionUser instances.
    Turn 1 = first design request (fresh context).
    Turn N = Nth refinement (context contains N-1 previous exchanges).
    Reveals how token cost compounds as sessions age.
    """

    def __init__(self):
        self._lock  = threading.Lock()
        self._turns: dict[int, dict] = {}

    def record(self, turn_num: int, input_tokens: int, output_tokens: int) -> None:
        with self._lock:
            t = self._turns.setdefault(turn_num, {"count": 0, "input": 0, "output": 0})
            t["count"]  += 1
            t["input"]  += input_tokens
            t["output"] += output_tokens

    def snapshot(self) -> dict:
        with self._lock:
            return {
                turn: {
                    "count":      d["count"],
                    "avg_input":  round(d["input"]  / d["count"]) if d["count"] else 0,
                    "avg_output": round(d["output"] / d["count"]) if d["count"] else 0,
                    "avg_total":  round((d["input"] + d["output"]) / d["count"]) if d["count"] else 0,
                }
                for turn, d in sorted(self._turns.items())
            }

    def reset(self) -> None:
        with self._lock:
            self._turns = {}


turn_tracker = TurnTracker()


class RateLimitTracker:
    """Thread-safe counter for 429 (rate-limit) responses, keyed by user_id.

    Lets the snapshot show total 429s AND how many distinct users hit the limit —
    so you can confirm rate limiting is guarding against abuse (few users, many
    hits) rather than blocking normal participants (many users, few hits each).
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._total = 0
        self._by_user: dict[str, int] = defaultdict(int)

    def record(self, user_id: str) -> None:
        with self._lock:
            self._total += 1
            self._by_user[user_id] += 1

    def snapshot(self) -> dict:
        with self._lock:
            top = sorted(self._by_user.items(), key=lambda x: -x[1])[:5]
            return {
                "total_429":      self._total,
                "unique_users":   len(self._by_user),
                "top_offenders":  top,   # [(user_id, count), ...]
            }

    def reset(self) -> None:
        with self._lock:
            self._total = 0
            self._by_user = defaultdict(int)


rate_limit_tracker = RateLimitTracker()


class InFlightGauge:
    """Thread-safe gauge of requests currently in flight, plus the running peak.

    inc() on request start, dec() on completion. `current` = designs running RIGHT
    NOW; `peak` = the highest concurrency seen so far this run.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._current = 0
        self._peak = 0

    def inc(self) -> None:
        with self._lock:
            self._current += 1
            if self._current > self._peak:
                self._peak = self._current

    def dec(self) -> None:
        with self._lock:
            self._current = max(0, self._current - 1)

    @property
    def current(self) -> int:
        with self._lock:
            return self._current

    @property
    def peak(self) -> int:
        with self._lock:
            return self._peak


in_flight = InFlightGauge()


# ═══════════════════════════════════════════════════════════════════════════════
# COST ESTIMATION
# ═══════════════════════════════════════════════════════════════════════════════

_PRICING = {
    "gpt-4o":            {"input":  5.00, "output":  15.00},
    "gpt-4o-mini":       {"input":  0.15, "output":   0.60},
    "gpt-4-turbo":       {"input": 10.00, "output":  30.00},
    "gpt-4":             {"input": 30.00, "output":  60.00},
    "gpt-3.5-turbo":     {"input":  0.50, "output":   1.50},
    # Azure AI Foundry — GPT-4.5 (verify pricing in your Azure billing dashboard)
    "gpt-4.5":           {"input": 75.00, "output": 150.00},
    "claude-3-5-sonnet": {"input":  3.00, "output":  15.00},
    "claude-3-haiku":    {"input":  0.25, "output":   1.25},
}
# The live deployment resolves to gpt-4o-mini — default to it so cost isn't
# silently computed at gpt-4o ($5/$15) rates (~25-33× overstatement).
_DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")


def estimate_cost(input_tokens: int, output_tokens: int,
                  model: str | None = None) -> dict:
    model   = model or _DEFAULT_MODEL
    prices  = _PRICING.get(model, _PRICING["gpt-4o-mini"])
    c_in    = input_tokens  / 1_000_000 * prices["input"]
    c_out   = output_tokens / 1_000_000 * prices["output"]
    return {
        "model":           model,
        "cost_input_usd":  round(c_in,        6),
        "cost_output_usd": round(c_out,        6),
        "cost_total_usd":  round(c_in + c_out, 6),
    }


def cost_summary(tok: dict, model: str | None = None,
                 hold_seconds: int = 1) -> dict:
    """Enrich a token snapshot with cost and throughput figures."""
    calls = tok.get("total_llm_calls", 0)
    inp   = tok.get("total_input_tokens",  0)
    out   = tok.get("total_output_tokens", 0)
    cost  = estimate_cost(inp, out, model)
    return {
        **tok,
        **cost,
        "cost_per_request_usd":   round(cost["cost_total_usd"] / calls, 6) if calls else None,
        "token_throughput_per_s": round((inp + out) / hold_seconds, 1)     if hold_seconds else None,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# kubectl HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _kube(*args, timeout: int = 20) -> str:
    try:
        r = subprocess.run(
            ["kubectl", *args], capture_output=True, text=True, timeout=timeout
        )
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


# ── Node metrics (kubectl top nodes) ─────────────────────────────────────────

def get_node_metrics() -> list[dict]:
    """CPU/memory usage per node. Requires metrics-server (default on AKS)."""
    out = _kube("top", "nodes", "--no-headers")
    nodes = []
    for line in out.strip().splitlines():
        parts = line.split()
        if len(parts) >= 5:
            try:
                nodes.append({
                    "name":    parts[0],
                    "cpu":     parts[1],
                    "cpu_pct": int(parts[2].rstrip("%")),
                    "mem":     parts[3],
                    "mem_pct": int(parts[4].rstrip("%")),
                })
            except (ValueError, IndexError):
                pass
    return nodes


# ── Pod CPU distribution — detects NGINX sticky-session hot pods ─────────────

def get_pod_cpu_distribution(namespace: str) -> dict:
    """
    Returns per-pod CPU usage to detect NGINX sticky-session imbalance.
    If one pod has significantly higher CPU it is absorbing most sessions.
    Returns {"pod_name": cpu_pct, ...} sorted descending.
    """
    out = _kube("top", "pods", f"-n={namespace}", "-l=app=neuro-san", "--no-headers")
    pods: dict[str, int] = {}
    for line in out.strip().splitlines():
        parts = line.split()
        if len(parts) >= 3:
            try:
                name    = parts[0]
                cpu_pct = int(parts[2].rstrip("%"))
                pods[name] = cpu_pct
            except (ValueError, IndexError):
                pass
    if not pods:
        return {}
    total = sum(pods.values()) or 1
    # Return share (%) of total pod CPU each pod is consuming
    return {name: round(cpu / total * 100) for name, cpu in
            sorted(pods.items(), key=lambda x: -x[1])}


# ── Pod metrics (kubectl top pods) ───────────────────────────────────────────

def get_pod_metrics(namespace: str) -> list[dict]:
    out = _kube("top", "pods", f"-n={namespace}", "--no-headers")
    pods = []
    for line in out.strip().splitlines():
        parts = line.split()
        if len(parts) >= 3:
            pods.append({"name": parts[0], "cpu": parts[1], "mem": parts[2]})
    return pods


# ── Per-pod usage with % (CPU vs node vCPU, MEM vs limit) ────────────────────

def _cpu_to_millicores(s: str) -> float:
    s = s.strip()
    try:
        if s.endswith("m"):
            return float(s[:-1])
        return float(s) * 1000.0        # bare cores → millicores
    except ValueError:
        return 0.0


def _mem_to_mi(s: str) -> float:
    s = s.strip()
    try:
        if s.endswith("Mi"):
            return float(s[:-2])
        if s.endswith("Gi"):
            return float(s[:-2]) * 1024.0
        if s.endswith("Ki"):
            return float(s[:-2]) / 1024.0
        return 0.0
    except ValueError:
        return 0.0


def get_pod_usage(namespace: str, label: str,
                  node_vcpu: int, mem_limit_mi: int) -> list[dict]:
    """Per-pod CPU% and MEM% for pods matching `label`.

    CPU% is against the whole node's vCPU (backend runs 1 pod per D16s_v3 node, so
    node vCPU is the pod's effective budget). MEM% is against the pod's mem limit.
    Returns [{name, cpu_m, cpu_pct, mem_mi, mem_pct}, ...] sorted by CPU desc.
    """
    out = _kube("top", "pods", f"-n={namespace}", f"-l={label}", "--no-headers")
    node_m = max(node_vcpu * 1000, 1)
    pods = []
    for line in out.strip().splitlines():
        parts = line.split()
        if len(parts) >= 3:
            cpu_m  = _cpu_to_millicores(parts[1])
            mem_mi = _mem_to_mi(parts[2])
            pods.append({
                "name":    parts[0],
                "cpu_m":   round(cpu_m),
                "cpu_pct": round(cpu_m / node_m * 100, 1),
                "mem_mi":  round(mem_mi),
                "mem_pct": round(mem_mi / max(mem_limit_mi, 1) * 100, 1),
            })
    return sorted(pods, key=lambda p: -p["cpu_pct"])


# ── Per-key token usage from Azure Monitor (per Azure OpenAI resource) ───────

def _az_metric_total(resource_id: str, metric: str, timespan: str) -> float | None:
    try:
        r = subprocess.run(
            ["az", "monitor", "metrics", "list", "--resource", resource_id,
             "--metric", metric, "--interval", "PT1M", "--aggregation", "Total",
             "--timespan", timespan, "--output", "json"],
            capture_output=True, text=True, timeout=25,
        )
    except Exception:          # az missing, timeout, etc. — non-fatal
        return None
    if r.returncode != 0:
        return None
    try:
        data = json.loads(r.stdout)
        vals = [pt.get("total") for ts in data.get("value", [])
                for pt in ts.get("timeseries", [{}])[0].get("data", [])
                if pt.get("total") is not None]
        return sum(vals) if vals else 0.0
    except Exception:
        return None


def get_per_key_tpm(resources: list, timespan: str = "PT5M") -> dict:
    """Best-effort per-key token usage from Azure Monitor (prompt + generated).

    `resources` = [(key_id, resource_name, resource_group), ...]. Returns
    {key_id: total_tokens_in_window} or {} if az is unavailable. Queried in
    parallel (one thread per resource). Non-fatal — the snapshot degrades to
    "n/a" if this returns empty (e.g. metric name differs or no az login).
    """
    if not resources:
        return {}
    try:
        sub = subprocess.run(["az", "account", "show", "--query", "id", "-o", "tsv"],
                             capture_output=True, text=True, timeout=10)
    except Exception:          # az not installed (e.g. in-pod) — degrade to n/a
        return {}
    if sub.returncode != 0:
        return {}
    sub_id = sub.stdout.strip()

    def _one(entry):
        key_id, name, rg = entry
        rid = (f"/subscriptions/{sub_id}/resourceGroups/{rg}"
               f"/providers/Microsoft.CognitiveServices/accounts/{name}")
        prompt = _az_metric_total(rid, "ProcessedPromptTokens", timespan)
        gen    = _az_metric_total(rid, "GeneratedTokens", timespan)
        if prompt is None and gen is None:
            return key_id, None
        return key_id, round((prompt or 0) + (gen or 0))

    from concurrent.futures import ThreadPoolExecutor
    result: dict = {}
    with ThreadPoolExecutor(max_workers=min(len(resources), 11)) as pool:
        for key_id, val in pool.map(_one, resources):
            if val is not None:
                result[key_id] = val
    return result


# ── Deployment vertical config (CPU/memory requests + limits) ────────────────

def get_pod_vertical_config(namespace: str, deployment: str) -> dict:
    """Read resource requests/limits from the deployment spec."""
    raw = _kube(
        "get", "deployment", deployment, f"-n={namespace}",
        "-o=jsonpath={.spec.template.spec.containers[0].resources}",
    )
    try:
        res = json.loads(raw) if raw.strip() else {}
    except Exception:
        res = {}
    return {
        "cpu_request":    res.get("requests", {}).get("cpu",    "—"),
        "memory_request": res.get("requests", {}).get("memory", "—"),
        "cpu_limit":      res.get("limits",   {}).get("cpu",    "—"),
        "memory_limit":   res.get("limits",   {}).get("memory", "—"),
    }


# ── Node instance types (Azure VM SKU + allocatable resources) ───────────────

def get_node_instance_types() -> list[dict]:
    raw = _kube(
        "get", "nodes",
        "-o=jsonpath={range .items[*]}"
        "{.metadata.name}|"
        "{.metadata.labels.node\\.kubernetes\\.io/instance-type}|"
        "{.status.allocatable.cpu}|"
        "{.status.allocatable.memory}"
        " {end}",
    )
    result = []
    for entry in raw.strip().split():
        parts = entry.split("|")
        if len(parts) == 4:
            result.append({
                "node":          parts[0],
                "instance_type": parts[1],
                "alloc_cpu":     parts[2],
                "alloc_mem":     parts[3],
            })
    return result


# ── NGINX upstream error breakdown ───────────────────────────────────────────

def get_nginx_error_breakdown(nginx_ns: str = "ingress-nginx",
                               tail: int = 2000) -> dict:
    """
    Parse the last N lines of ingress-nginx logs for upstream HTTP error codes.
    Returns {code: count}, e.g. {"502": 8, "503": 2}.
    """
    pod = _kube(
        "get", "pods", f"-n={nginx_ns}",
        "-l=app.kubernetes.io/name=ingress-nginx",
        "-o=jsonpath={.items[0].metadata.name}",
    ).strip()
    if not pod:
        return {}

    logs = _kube("logs", pod, f"-n={nginx_ns}", f"--tail={tail}")
    counts: dict[str, int] = defaultdict(int)
    for line in logs.splitlines():
        for code in ("400", "499", "502", "503", "504"):
            if f" {code} " in line:
                counts[code] += 1
    return {k: v for k, v in sorted(counts.items()) if v > 0}


# ── Network I/O per node ─────────────────────────────────────────────────────

def get_node_network_io(namespace: str = "kube-system") -> list[dict]:
    """
    Best-effort: read rx/tx bytes from /proc/net/dev inside each node via a
    hostNetwork debug pod.  Falls back to empty list if not permitted.
    This is a lightweight probe — spawns nothing, just reads from the
    node-exporter DaemonSet metrics if available.
    """
    # Try to read from node-exporter if deployed
    raw = _kube(
        "get", "pods", f"-n={namespace}",
        "-l=app=node-exporter",
        "-o=jsonpath={range .items[*]}{.metadata.name}={.status.podIP} {end}",
    )
    # node-exporter not required — return empty gracefully
    return []


# ── OpenAI rate-limit headroom ────────────────────────────────────────────────

def check_openai_headroom(api_key: str | None = None) -> dict:
    """
    Probe OpenAI's /v1/models endpoint (no tokens consumed) and read
    rate-limit headers. Returns headroom as percentages.
    Needs OPENAI_API_KEY env var or the api_key argument.
    """
    key = api_key or os.getenv("OPENAI_API_KEY", "")
    if not key:
        return {}
    try:
        import requests as _req
        resp = _req.get(
            "https://api.openai.com/v1/models",
            headers={"Authorization": f"Bearer {key}"},
            timeout=5,
        )
        h = resp.headers

        def _pct(rem, lim):
            try:
                return round(int(h[rem]) / int(h[lim]) * 100, 1)
            except (KeyError, ValueError, ZeroDivisionError):
                return None

        return {
            "req_remaining":    h.get("x-ratelimit-remaining-requests"),
            "req_limit":        h.get("x-ratelimit-limit-requests"),
            "tok_remaining":    h.get("x-ratelimit-remaining-tokens"),
            "tok_limit":        h.get("x-ratelimit-limit-tokens"),
            "req_headroom_pct": _pct("x-ratelimit-remaining-requests",
                                      "x-ratelimit-limit-requests"),
            "tok_headroom_pct": _pct("x-ratelimit-remaining-tokens",
                                      "x-ratelimit-limit-tokens"),
        }
    except Exception:
        return {}


# ── Azure Blob IOPS + end-to-end latency ─────────────────────────────────────

def get_blob_metrics(storage_account: str = "",
                     resource_group:  str = "",
                     timespan:        str = "PT5M") -> dict:
    """
    Pull Azure Blob Storage transaction rate and E2E latency from Azure Monitor
    via the az CLI.  Requires `az login` and contributor access to the storage
    account.  Set BLOB_STORAGE_ACCOUNT and BLOB_RESOURCE_GROUP env vars.
    Returns {} silently if az CLI is unavailable or unconfigured.
    """
    acct = storage_account or os.getenv("BLOB_STORAGE_ACCOUNT", "")
    rg   = resource_group  or os.getenv("BLOB_RESOURCE_GROUP",  "")
    if not acct or not rg:
        return {}

    # Resolve subscription ID
    sub = subprocess.run(
        ["az", "account", "show", "--query", "id", "-o", "tsv"],
        capture_output=True, text=True, timeout=10,
    )
    if sub.returncode != 0:
        return {}
    sub_id = sub.stdout.strip()

    resource_id = (
        f"/subscriptions/{sub_id}/resourceGroups/{rg}"
        f"/providers/Microsoft.Storage/storageAccounts/{acct}"
        f"/blobServices/default"
    )

    def _metric(name: str, aggregation: str = "Average") -> float | None:
        r = subprocess.run(
            [
                "az", "monitor", "metrics", "list",
                "--resource", resource_id,
                "--metric", name,
                "--interval", "PT1M",
                "--aggregation", aggregation,
                "--timespan", timespan,
                "--output", "json",
            ],
            capture_output=True, text=True, timeout=20,
        )
        if r.returncode != 0:
            return None
        try:
            data = json.loads(r.stdout)
            vals = [
                pt[aggregation.lower()]
                for ts in data.get("value", [])
                for pt in ts.get("timeseries", [{}])[0].get("data", [])
                if pt.get(aggregation.lower()) is not None
            ]
            return round(sum(vals) / len(vals), 2) if vals else None
        except Exception:
            return None

    iops    = _metric("Transactions", "Total")
    latency = _metric("SuccessE2ELatency", "Average")
    if iops is None and latency is None:
        return {}
    return {
        "blob_transactions":    iops,
        "blob_e2e_latency_ms":  latency,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# FULL STEP SNAPSHOT  (called by capacity_test.py)
# ═══════════════════════════════════════════════════════════════════════════════

def collect_step_metrics(namespace:     str,
                          nginx_ns:      str = "ingress-nginx",
                          openai_key:    str | None = None,
                          blob_acct:     str = "",
                          blob_rg:       str = "") -> dict:
    """Collect all external metrics in one call. Non-fatal."""
    return {
        "nodes":           get_node_metrics(),
        "pods":            get_pod_metrics(namespace),
        "nginx_errors":    get_nginx_error_breakdown(nginx_ns),
        "openai_headroom": check_openai_headroom(openai_key),
        "blob":            get_blob_metrics(blob_acct, blob_rg),
    }
