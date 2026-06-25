#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
#  Neuro-San Load Test — convenience runner
#  Usage:  ./run.sh <command>
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Auto-activate virtual environment if present
if [[ -f "$DIR/.venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "$DIR/.venv/bin/activate"
fi
REPORTS="$DIR/reports"
HOST="${API_URL:-https://neurosanhackathon-api.eastus.cloudapp.azure.com}"
TS="$(date +%Y%m%d_%H%M%S)"

mkdir -p "$REPORTS"
cd "$DIR"

# ── Helpers ───────────────────────────────────────────────────────────────────
info()  { echo "  $*"; }
hr()    { echo "──────────────────────────────────────────────────────────────"; }

usage() {
  hr
  echo "  Neuro-San Load Test Runner"
  hr
  echo ""
  echo "  Commands:"
  echo "    install          Install Python dependencies"
  echo "    prewarm          Pre-warm pods   — hit every pod directly before hackathon opens"
  echo "    smoke            Smoke test      — 10 VUs / 2 min (sanity check)"
  echo "    load             Load test       — stepped ramp 50 → 200 → 500 → 1000 VUs"
  echo "    stress           Stress test     — ramp to 2000 VUs (find breaking point)"
  echo "    spike            Spike test      — sudden 10x burst then recover"
  echo "    soak             Soak test       — 200 VUs for 30 min (endurance)"
  echo "    capacity         Capacity plan   — 2/4/8/10 pods, stepped VU ramp"
  echo "    capacity-quick   Capacity plan   — quick version (45s hold)"
  echo "    deploy-10        Scale to 10 pods in Azure and verify"
  echo "    capacity-10      Test exactly 10 pods (skip auto-scale)"
  echo "    capacity-step    Step 1 pod to 10 pods — find per-pod breaking point"
  echo "    hackathon-sim    Stepped VU test on 10 pods — token budget analysis"
  echo "    hackathon-soak   90-min session soak — models real participant behaviour"
  echo "                       Tracks: context growth, sticky sessions, pod memory,"
  echo "                       Blob IOPS per turn, quota compounding"
  echo "    reset-hpa        Restore HPA to min=1 max=10 after testing"
  echo "    interactive      Locust web UI at http://localhost:8089"
  echo "    dashboard        Streamlit dashboard at http://localhost:8501"
  echo "    results          List all saved reports"
  echo ""
  echo "  Environment variables:"
  echo "    API_URL                Backend API URL (default: Azure hostname)"
  echo "    AGENT                  Agent to test (default: agent_network_designer)"
  echo "    BLOB_STORAGE_ACCOUNT   Azure storage account for Blob metrics"
  echo "    BLOB_RESOURCE_GROUP    Resource group for Blob metrics"
  echo ""
  hr
  exit 1
}

run_locust() {
  local shape_key="$1"
  local label="$2"
  shift 2

  info "Shape       : $shape_key"
  info "Label       : $label"
  info "Host        : $HOST"
  info "Report dir  : $REPORTS"
  echo ""

  LOCUST_SHAPE="$shape_key" locust -f locustfile.py \
    --host "$HOST" \
    --headless \
    --test-label "$label" \
    --csv "$REPORTS/${label}_${TS}" \
    --html "$REPORTS/${label}_${TS}.html" \
    --logfile "$REPORTS/${label}_${TS}.log" \
    "$@"

  echo ""
  info "HTML report : $REPORTS/${label}_${TS}.html"
  info "To view     : ./run.sh dashboard"
}

# ── Commands ──────────────────────────────────────────────────────────────────
CMD="${1:-help}"

case "$CMD" in

  install)
    hr
    info "Installing Python dependencies..."
    hr
    if [[ ! -f "$DIR/.venv/bin/activate" ]]; then
      python3 -m venv "$DIR/.venv"
    fi
    "$DIR/.venv/bin/pip" install -r "$DIR/requirements.txt" -q
    info "Done. Activate with: source loadtest/.venv/bin/activate"
    ;;

  prewarm)
    hr
    info "PRE-WARM — warming all 12 pods before hackathon opens"
    info "Sends readyz + list + 1 chat turn to every pod via direct port-forward."
    info "Run this 5-10 min before releasing the hackathon URL."
    hr
    python3 "$DIR/prewarm.py" "$@"
    ;;

  smoke)
    hr
    info "SMOKE TEST  (10 VUs / ~3 min)"
    hr
    run_locust smoke smoke
    ;;

  load)
    hr
    info "LOAD TEST  (50 → 200 → 500 → 1000 VUs / ~16 min)"
    hr
    run_locust load load
    ;;

  stress)
    hr
    info "STRESS TEST  (50 → 500 → 1000 → 2000 VUs / ~16 min)"
    hr
    run_locust stress stress
    ;;

  spike)
    hr
    info "SPIKE TEST  (sudden burst 50 → 600 → 50 VUs / ~13 min)"
    hr
    run_locust spike spike
    ;;

  soak)
    hr
    info "SOAK TEST  (200 VUs / 30 min)"
    hr
    run_locust soak soak
    ;;

  capacity)
    hr
    info "CAPACITY PLANNING TEST"
    info "Pods: 2 → 4 → 8 → 10"
    info "VUs:  50 → 1000 (step 50, 300s hold)"
    info "Est:  ~90 min with early-exit"
    hr
    python3 "$DIR/capacity_test.py" \
      --host "$HOST" \
      --pods "2,4,8,10" \
      --max-vus 1000 \
      --step 50 \
      --hold 300
    ;;

  capacity-quick)
    hr
    info "CAPACITY QUICK TEST (45s hold, 500 VU max)"
    hr
    python3 "$DIR/capacity_test.py" \
      --host "$HOST" \
      --pods "${PODS:-2,4,8,10}" \
      --max-vus "${MAX_VUS:-500}" \
      --step "${STEP:-50}" \
      --hold 45
    ;;

  deploy-10)
    hr
    info "DEPLOYING 10 PODS TO AZURE (hackathon-day config)"
    hr
    info "Patching HPA: min=10, max=10 (locked at 10 for hackathon)..."
    kubectl patch hpa neuro-san-hpa -n neuro-san-hackathon \
      --type=merge -p '{"spec":{"minReplicas":10,"maxReplicas":10}}'
    kubectl patch hpa neuro-san-hpa-1 -n neuro-san-hackathon \
      --type=merge -p '{"spec":{"minReplicas":10,"maxReplicas":10}}' 2>/dev/null || true
    echo ""
    info "Scaling deployment to 10 replicas..."
    kubectl scale deployment/neuro-san-key-1 --replicas=10 -n neuro-san-hackathon
    echo ""
    info "Waiting for all 10 pods to be ready..."
    kubectl rollout status deployment/neuro-san-key-1 -n neuro-san-hackathon --timeout=5m
    echo ""
    info "Pods:"
    kubectl get pods -n neuro-san-hackathon -l app=neuro-san
    echo ""
    info "Node resource usage:"
    kubectl top nodes
    echo ""
    info "10 pods deployed. Run: ./run.sh hackathon-soak"
    ;;

  capacity-10)
    hr
    info "CAPACITY TEST — 10 PODS (hackathon-day config, skip auto-scale)"
    info "VUs: 50 → 1000 (step 50, 300s hold)"
    hr
    python3 "$DIR/capacity_test.py" \
      --host "$HOST" \
      --pods "10" \
      --max-vus 1000 \
      --step 50 \
      --hold 300 \
      --skip-scale
    ;;

  capacity-step)
    hr
    info "CAPACITY STEP TEST — 1 pod to 10 pods (DESCENDING)"
    info "Agent   : agent_network_designer (2-5 min per design)"
    info "Pattern : 2500 → 1000 (step -250), then 900 → 100 (step -100)"
    info "Break   : >5% errors  OR  p95 >300s  OR  RPS <75% of VU count"
    info "Hold    : 300s per step — covers full LLM response cycle"
    info "Est     : ~4-5 hrs"
    hr
    info "Resetting HPA to min=1 so scaling can go below 10..."
    kubectl patch hpa neuro-san-hpa -n neuro-san-hackathon \
      --type=merge -p '{"spec":{"minReplicas":1,"maxReplicas":10}}' 2>/dev/null || true
    echo ""
    python3 "$DIR/capacity_test.py" \
      --host "$HOST" \
      --pods "1,2,3,4,5,6,7,8,9,10" \
      --max-vus 2500 \
      --step 100 \
      --hold 300
    ;;

  hackathon-sim)
    hr
    info "HACKATHON STEPPED SIMULATION (DESCENDING)"
    info "Agent   : agent_network_designer (2-5 min per design)"
    info "Pods    : 10  |  VUs: 2500→100 descending  |  Hold: 360s per step"
    info "Pattern : 2500→1000 (step -250), then 900→100 (step -100)"
    info "Break   : >5% errors  OR  p95 >300s  OR  RPS <75% of VU count"
    info "Tracks  : token quota burn rate, ETA to 10M exhaustion"
    info "Est     : ~3-4 hrs"
    info ""
    info "NOTE: Does NOT model context growth. For full session simulation run hackathon-soak."
    hr
    info "Scaling to 10 pods..."
    kubectl patch hpa neuro-san-hpa -n neuro-san-hackathon \
      --type=merge -p '{"spec":{"minReplicas":10,"maxReplicas":10}}' 2>/dev/null || true
    kubectl scale deployment/neuro-san-key-1 --replicas=10 -n neuro-san-hackathon
    kubectl rollout status deployment/neuro-san-key-1 -n neuro-san-hackathon --timeout=5m
    echo ""
    python3 "$DIR/capacity_test.py" \
      --host "$HOST" \
      --pods "10" \
      --max-vus 2500 \
      --step 100 \
      --hold 360 \
      --user-class hackathon \
      --skip-scale \
      --warmup 60
    ;;

  hackathon-soak)
    hr
    info "HACKATHON SOAK TEST — 90-min sessions, stateful context, agent_network_designer"
    hr
    info "Models the REAL hackathon bottlenecks:"
    info "  - NGINX sticky sessions + ewma: users pinned to ONE pod for 90 min"
    info "  - Context compounding: turn 5 costs ~5x turn 1 in tokens"
    info "  - Azure Blob: read+write EVERY turn (not just session start)"
    info "  - Pod memory pressure: grows as active sessions deepen"
    info "  - Token quota burns 5-10x faster than single-turn estimates"
    info ""
    info "Pods: 12 fixed deployments (HPA disabled, 1 pod per Azure OpenAI key)"
    info "Quota: 12 × 10M TPM = 120M TPM total"
    info "Snapshots every 5 min showing all bottlenecks + hackathon projection"
    info "Est: ~2 hrs (5000 VUs, 120 min duration)"
    hr
    python3 "$DIR/hackathon_soak.py" \
      --host "$HOST" \
      --pods 12 \
      --vus "${VUS:-5000}" \
      --duration "${DURATION:-120}" \
      --skip-scale
    ;;

  reset-hpa)
    hr
    info "Restoring HPA to normal: min=1, max=10"
    hr
    kubectl patch hpa neuro-san-hpa -n neuro-san-hackathon \
      --type=merge -p '{"spec":{"minReplicas":1,"maxReplicas":10}}'
    kubectl patch hpa neuro-san-hpa-1 -n neuro-san-hackathon \
      --type=merge -p '{"spec":{"minReplicas":1,"maxReplicas":10}}' 2>/dev/null || true
    info "Done. HPA will now auto-scale between 1 and 10 pods."
    kubectl get hpa -n neuro-san-hackathon
    ;;

  interactive)
    hr
    info "INTERACTIVE MODE — open http://localhost:8089"
    info "Host: $HOST"
    hr
    locust -f locustfile.py --host "$HOST"
    ;;

  dashboard)
    hr
    info "Launching Streamlit dashboard at http://localhost:8501"
    hr
    streamlit run "$DIR/dashboard.py" --server.port 8501
    ;;

  results)
    hr
    info "Saved reports in $REPORTS:"
    hr
    ls -lh "$REPORTS"/*.json 2>/dev/null || info "(none yet)"
    ;;

  help|--help|-h)
    usage
    ;;

  *)
    echo "Unknown command: $CMD"
    usage
    ;;

esac
