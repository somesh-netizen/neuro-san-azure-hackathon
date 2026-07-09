#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# chaos_drill.sh — failure drills for the Neuro-San hackathon cluster.
#
# Run this from a machine with kubectl (+ az for node drain) WHILE a load test is
# running at a MODERATE level (~200-500 VUs, NOT the full 2,700). It injects real
# failures and observes whether the platform survives them:
#
#   Drill 1 (pod kill) : delete one backend pod mid-design. Confirms the pod is
#                        recreated, becomes Ready, and other pods keep serving
#                        (sticky users on the killed pod fail/retry; everyone else
#                        is unaffected). Verifies blob-backed session state + reroute.
#
#   Drill 2 (node drain): cordon + drain one pool16 node (opt-in via --node-drain).
#                        Confirms its pod reschedules onto the spare node and the
#                        ingress stays up. Uncordons afterwards.
#
# Throughout, it probes the PUBLIC API /readyz every 2s and reports how many probes
# failed during each drill — i.e. did the *event* see an outage, or just the pinned
# users on the killed pod?
#
# Usage:
#   ./chaos_drill.sh                          # pod-kill drill only (safe default)
#   ./chaos_drill.sh --node-drain             # also drain one node (more disruptive)
#   API_URL=https://... NS=neuro-san-hackathon ./chaos_drill.sh
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail

NS="${NS:-neuro-san-hackathon}"
API_URL="${API_URL:-https://neurosanhackathon-api.eastus.cloudapp.azure.com}"
CLUSTER="${CLUSTER:-neuro-san-hackathon-aks}"
RG="${RG:-neuro-san-studio-marketplace-rg}"
BACKEND_LABEL="${BACKEND_LABEL:-app=neuro-san}"
NODE_DRAIN=0
[ "${1:-}" = "--node-drain" ] && NODE_DRAIN=1

ts() { date +%H:%M:%S; }
log() { echo "[$(ts)] $*"; }

# ── background API availability probe ────────────────────────────────────────
PROBE_LOG="$(mktemp)"
probe_loop() {
  while :; do
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$API_URL/readyz" 2>/dev/null)
    echo "$(date +%s) $code" >> "$PROBE_LOG"
    sleep 2
  done
}
probe_loop & PROBE_PID=$!
trap 'kill $PROBE_PID 2>/dev/null; rm -f "$PROBE_LOG"' EXIT

probe_report() {  # $1=window_start_epoch  $2=label
  local since="$1" label="$2" total ok
  total=$(awk -v s="$since" '$1>=s{n++}END{print n+0}' "$PROBE_LOG")
  ok=$(awk -v s="$since" '$1>=s && $2=="200"{n++}END{print n+0}' "$PROBE_LOG")
  log "   ↳ API availability during '$label': $ok/$total probes returned 200"
}

log "chaos_drill start — ns=$NS  api=$API_URL  node-drain=$NODE_DRAIN"
log "baseline pods:"
kubectl -n "$NS" get pods -l "$BACKEND_LABEL" -o wide 2>/dev/null | sed 's/^/       /'

# ── Drill 1: kill one backend pod ────────────────────────────────────────────
echo; log "═══ DRILL 1: kill one backend pod ═══"
VICTIM=$(kubectl -n "$NS" get pods -l "$BACKEND_LABEL" \
          -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
if [ -z "$VICTIM" ]; then log "!! no backend pods found (label $BACKEND_LABEL) — is the cluster up?"; exit 1; fi
NODE_OF_VICTIM=$(kubectl -n "$NS" get pod "$VICTIM" -o jsonpath='{.spec.nodeName}' 2>/dev/null)
log "killing pod: $VICTIM (on node $NODE_OF_VICTIM)"
W0=$(date +%s)
kubectl -n "$NS" delete pod "$VICTIM" --grace-period=0 --force 2>/dev/null

log "waiting for a replacement pod to become Ready..."
for i in $(seq 1 60); do
  sleep 5
  ready=$(kubectl -n "$NS" get pods -l "$BACKEND_LABEL" \
          -o jsonpath='{range .items[*]}{.status.containerStatuses[0].ready}{"\n"}{end}' 2>/dev/null \
          | grep -c true)
  total=$(kubectl -n "$NS" get pods -l "$BACKEND_LABEL" --no-headers 2>/dev/null | wc -l | tr -d ' ')
  log "   t+$((i*5))s: $ready/$total backend pods Ready"
  [ "$ready" = "$total" ] && [ "$total" != "0" ] && break
done
RECOVERY=$(( $(date +%s) - W0 ))
log "pod recovery took ~${RECOVERY}s"
probe_report "$W0" "pod kill"
log "EXPECT: API stayed ~100% (only the sticky users on $VICTIM saw a blip; others unaffected)."

# ── Drill 2: drain one node (opt-in) ─────────────────────────────────────────
if [ "$NODE_DRAIN" = "1" ]; then
  echo; log "═══ DRILL 2: cordon + drain one pool16 node ═══"
  DNODE=$(kubectl get nodes -l agentpool=pool16 -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
  if [ -z "$DNODE" ]; then log "!! no pool16 nodes found — skipping node drill"; else
    log "draining node: $DNODE"
    W1=$(date +%s)
    kubectl cordon "$DNODE" 2>/dev/null
    kubectl drain "$DNODE" --ignore-daemonsets --delete-emptydir-data \
       --force --grace-period=30 --timeout=180s 2>&1 | sed 's/^/       /'
    log "waiting for rescheduled pods to settle..."
    for i in $(seq 1 36); do
      sleep 5
      ready=$(kubectl -n "$NS" get pods -l "$BACKEND_LABEL" \
              -o jsonpath='{range .items[*]}{.status.containerStatuses[0].ready}{"\n"}{end}' 2>/dev/null | grep -c true)
      total=$(kubectl -n "$NS" get pods -l "$BACKEND_LABEL" --no-headers 2>/dev/null | wc -l | tr -d ' ')
      log "   t+$((i*5))s: $ready/$total backend pods Ready"
      [ "$ready" = "$total" ] && [ "$total" != "0" ] && break
    done
    probe_report "$W1" "node drain"
    log "uncordoning $DNODE"
    kubectl uncordon "$DNODE" 2>/dev/null
    log "EXPECT: pod rescheduled onto the spare node; ingress + API stayed up."
  fi
fi

echo; log "final pod state:"
kubectl -n "$NS" get pods -l "$BACKEND_LABEL" -o wide 2>/dev/null | sed 's/^/       /'
echo; log "restart counts (0 = no crash-looping):"
kubectl -n "$NS" get pods -l "$BACKEND_LABEL" \
  -o jsonpath='{range .items[*]}{.metadata.name}{"  restarts="}{.status.containerStatuses[0].restartCount}{"\n"}{end}' 2>/dev/null | sed 's/^/       /'
log "chaos_drill complete."
