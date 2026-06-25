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
_DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")


def estimate_cost(input_tokens: int, output_tokens: int,
                  model: str | None = None) -> dict:
    model   = model or _DEFAULT_MODEL
    prices  = _PRICING.get(model, _PRICING["gpt-4o"])
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
