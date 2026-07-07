#!/usr/bin/env python3
"""
Pre-warm every running neuro-san backend pod before opening the hackathon.

Pod count is discovered dynamically (label app=neuro-san), so this warms whatever
is live — currently 11 pods (key-9 was dropped: its Azure resource had no model
deployment). No edit needed when the pod set changes.

Run at T-30 min, before participants get the URL (see EVENT_DAY_PLAN.md):
    python3 loadtest/prewarm.py

What it does for each pod:
  1. Opens a direct kubectl port-forward to pod:8080 (bypasses NGINX — hits every
     pod exactly once regardless of NGINX load-balancing state)
  2. GET  /readyz               — confirms pod is alive
  3. GET  /api/v1/list          — warms the agent routing table + DNS
  4. POST streaming_chat        — sends a cheap LLM turn, forcing Python to
                                  open and keep alive the TCP connection to Azure
                                  OpenAI; subsequent real requests reuse this conn
  5. Reports readyz / list / chat status, token count, and elapsed time per pod

All discovered pods are warmed in parallel (~2-4 min total depending on LLM latency).
"""

import argparse
import json
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import requests
except ImportError:
    print("ERROR: requests not installed. Run: pip install requests")
    sys.exit(1)

NAMESPACE    = "neuro-san-hackathon"
PORT_BASE    = 18080            # pods use ports 18080..18091 (won't clash with app port 8080)
WARM_MESSAGE = "Hello, what can you help me with?"  # cheap 1-turn warm-up
TIMEOUT_CHAT = 180              # 3 min — first LLM call can be slow on a cold pod


# ── Port-forward helper ───────────────────────────────────────────────────────

def _port_forward(pod: str, local_port: int, ready: threading.Event, stop: threading.Event):
    """Run kubectl port-forward in a daemon thread until stop is set."""
    proc = subprocess.Popen(
        ["kubectl", "port-forward", "-n", NAMESPACE, pod, f"{local_port}:8080"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(1.2)     # give kubectl time to bind the local socket
    ready.set()
    stop.wait()
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()


# ── Per-pod warm-up ───────────────────────────────────────────────────────────

def _warm_pod(pod: str, local_port: int, agent: str) -> dict:
    """Warm a single pod through its own isolated port-forward.

    Returns a result dict with keys:
        pod, readyz, list, chat, tokens_in, tokens_out, elapsed_s, error
    """
    result: dict = {"pod": pod, "error": None}
    stop  = threading.Event()
    ready = threading.Event()

    fwd = threading.Thread(
        target=_port_forward,
        args=(pod, local_port, ready, stop),
        daemon=True,
    )
    fwd.start()
    ready.wait(timeout=6)

    base    = f"http://localhost:{local_port}"
    headers = {
        "Content-Type": "application/json",
        "Accept":       "application/json",
        "user_id":      f"prewarm-{pod}",
    }
    t0 = time.monotonic()

    try:
        # ── Step 1: liveness / readiness ──────────────────────────────────────
        r = requests.get(f"{base}/readyz", timeout=10,
                         headers={"Accept": "application/json"})
        result["readyz"] = r.status_code
        if r.status_code != 200:
            result["error"] = f"/readyz returned {r.status_code} — pod not ready"
            return result

        # ── Step 2: agent list — warms routing table ──────────────────────────
        r = requests.get(f"{base}/api/v1/list", timeout=10, headers=headers)
        result["list"] = r.status_code

        # ── Step 3: single cheap chat — establishes Azure OpenAI TCP conn ─────
        body = json.dumps({
            "user_message": {"text": WARM_MESSAGE},
            "chat_context": {},
            "chat_filter":  {"chat_filter_type": "MAXIMAL"},
        })
        r = requests.post(
            f"{base}/api/v1/{agent}/streaming_chat",
            data=body,
            headers={**headers, "Accept": "application/json-lines"},
            timeout=TIMEOUT_CHAT,
            stream=True,
        )
        result["chat"] = r.status_code

        # Drain the SSE stream and extract token counts
        chunks: list[bytes] = []
        if r.status_code == 200:
            try:
                for chunk in r.iter_content(chunk_size=65536):
                    if chunk:
                        chunks.append(chunk)
            except Exception:
                pass   # partial stream is still a warm-up win

        inp = out = 0
        for ln in b"".join(chunks).decode("utf-8", errors="ignore").splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                p = json.loads(ln)
                resp   = p.get("response", {})
                if resp.get("type") != "AGENT":
                    continue
                struct = resp.get("structure", {})
                # Skip the AGENT_FRAMEWORK rollup (double-counts sub-agents)
                if any("External agent token usage" in c
                       for c in struct.get("caveats", [])):
                    continue
                inp += int(struct.get("prompt_tokens")    or 0)
                out += int(struct.get("completion_tokens") or 0)
            except Exception:
                continue
        result["tokens_in"]  = inp
        result["tokens_out"] = out

    except requests.exceptions.ConnectionError as e:
        result["error"] = f"port-forward connection failed: {e}"
    except requests.exceptions.Timeout:
        result["error"] = f"chat timed out after {TIMEOUT_CHAT}s"
    except Exception as e:
        result["error"] = str(e)
    finally:
        result["elapsed_s"] = round(time.monotonic() - t0, 1)
        stop.set()

    return result


# ── Pod discovery ─────────────────────────────────────────────────────────────

def _get_running_pods() -> list[str]:
    out = subprocess.check_output(
        [
            "kubectl", "get", "pods", "-n", NAMESPACE,
            "-l", "app=neuro-san",
            "--no-headers",
            "-o", "custom-columns=NAME:.metadata.name,PHASE:.status.phase",
        ],
        text=True,
    )
    pods = []
    for line in out.strip().splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "Running":
            pods.append(parts[0])
    return sorted(pods)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="Pre-warm all neuro-san pods before the hackathon"
    )
    ap.add_argument(
        "--agent", default="agent_network_designer",
        help="Agent to use for the warm-up chat request (default: agent_network_designer)",
    )
    args = ap.parse_args()

    print("Discovering running pods…")
    try:
        pods = _get_running_pods()
    except subprocess.CalledProcessError as e:
        print(f"ERROR: kubectl failed — are you logged in?\n{e}")
        sys.exit(1)

    if not pods:
        print(f"ERROR: No Running neuro-san pods found in namespace '{NAMESPACE}'")
        sys.exit(1)

    print(f"Found {len(pods)} pods — warming all in parallel (agent: {args.agent})")
    print(f"Using local ports {PORT_BASE}–{PORT_BASE + len(pods) - 1}")
    print()

    ok = fail = 0
    with ThreadPoolExecutor(max_workers=len(pods)) as pool:
        futures = {
            pool.submit(_warm_pod, pod, PORT_BASE + i, args.agent): pod
            for i, pod in enumerate(pods)
        }
        for fut in as_completed(futures):
            r   = fut.result()
            pod = r["pod"]
            t   = r.get("elapsed_s", "?")
            err = r.get("error")

            if err or r.get("chat") != 200:
                line = (
                    f"  FAIL  {pod:<42}"
                    f"  readyz={r.get('readyz','?')}  list={r.get('list','?')}  "
                    f"chat={r.get('chat','?')}  [{t}s]  {err or ''}"
                )
                fail += 1
            else:
                inp = r.get("tokens_in", 0)
                out = r.get("tokens_out", 0)
                line = (
                    f"  OK    {pod:<42}"
                    f"  in={inp:>6,}  out={out:>5,} tokens  [{t}s]"
                )
                ok += 1

            print(line)

    print()
    print(f"{'─' * 70}")
    print(f"Pre-warm complete: {ok}/{len(pods)} pods warmed, {fail} failed.")

    if fail:
        print()
        print("Pods marked FAIL may still be partially warmed (readyz + list may have")
        print("succeeded even if the chat step failed). Check pod logs before opening.")
        sys.exit(1)
    else:
        print("All pods are warm. Safe to open the hackathon URL.")


if __name__ == "__main__":
    main()
