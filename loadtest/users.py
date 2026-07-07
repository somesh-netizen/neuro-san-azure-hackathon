"""
User behaviour classes for the Neuro-San load test.

HealthCheckUser  (weight 1) — lightweight /readyz pinger only
BrowseUser       (weight 2) — /list + /connectivity, no chat
ChatUser         (weight 5) — full single-turn chat; primary load driver
PowerUser        (weight 2) — stateful multi-turn conversation
BurstUser        (weight 1) — rapid short-think-time requests; stress concurrency
"""

import json
import random
import time

import requests
from locust import HttpUser, between, task

from config import (
    CHAT_MESSAGES,
    CHAT_TIMEOUT,
    DEFAULT_AGENT,
    FAST_TIMEOUT,
    FRONTEND_URL,
    HACKATHON_DESIGN_PROMPTS,
    KNOWN_AGENTS,
    THINK_TIME_MAX,
    THINK_TIME_MIN,
)
from metrics import in_flight, rate_limit_tracker, token_tracker, turn_tracker


def _frontend_login(user) -> None:
    """Simulate a participant loading the UI (the login-wave hit) once per session.

    Fetches the app shell + /api/environment on the FRONTEND host and reports the
    timings into Locust's stats (names 'GET frontend shell' / 'GET /api/environment')
    so the UI tier shows up alongside the backend design load. Best-effort: a failure
    is recorded but never aborts the session.
    """
    for path, name in (("/", "GET frontend shell"),
                       ("/api/environment", "GET /api/environment")):
        t0 = time.monotonic()
        exc = None
        length = 0
        try:
            resp = requests.get(f"{FRONTEND_URL}{path}", timeout=FAST_TIMEOUT * 3)
            length = len(resp.content or b"")
            if resp.status_code >= 400:
                exc = Exception(f"{name} → {resp.status_code}")
        except Exception as e:      # noqa: BLE001 — report, don't crash the VU
            exc = e
        user.environment.events.request.fire(
            request_type="GET", name=name,
            response_time=(time.monotonic() - t0) * 1000.0,
            response_length=length, exception=exc, context={},
        )


def _read_stream(r) -> bytes:
    """Read a chunked streaming response, tolerating premature connection close.

    NGINX sometimes closes the TCP connection without sending the terminal
    chunked-encoding marker (0\\r\\n\\r\\n), causing requests to raise
    ChunkedEncodingError.  The JSON-lines body up to that point is still valid,
    so we collect chunks and swallow the truncation error.
    """
    chunks: list[bytes] = []
    try:
        for chunk in r.iter_content(chunk_size=65536):
            if chunk:
                chunks.append(chunk)
    except Exception:
        pass  # accept whatever we buffered before the connection dropped
    return b"".join(chunks)


def _extract_tokens(body: bytes) -> tuple[int, int]:
    """
    Extract token usage from neuro-san SSE frames.

    neuro-san 0.6.63 reports tokens in AGENT-type frames:
        {"response": {"type": "AGENT", "structure": {
            "prompt_tokens": N, "completion_tokens": M, ...}}}

    We sum across all AGENT frames (each sub-agent reports separately).
    The top-level AGENT_FRAMEWORK frame duplicates the total — skip it by
    checking for the caveats list containing 'External agent token usage'.
    Returns (prompt_tokens, completion_tokens) — (0, 0) if not found.
    """
    total_in = total_out = 0
    try:
        for ln in body.decode("utf-8", errors="ignore").splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                p = json.loads(ln)
                r = p.get("response", {})
                if r.get("type") != "AGENT":
                    continue
                struct = r.get("structure", {})
                # Skip the AGENT_FRAMEWORK rollup frame (double-counts sub-agents)
                caveats = struct.get("caveats", [])
                if any("External agent token usage is not included" in c for c in caveats):
                    continue
                inp = int(struct.get("prompt_tokens") or 0)
                out = int(struct.get("completion_tokens") or 0)
                if inp or out:
                    total_in  += inp
                    total_out += out
            except Exception:
                continue
    except Exception:
        pass
    return total_in, total_out


def _estimate_agent_depth(body: bytes) -> int:
    """
    Count distinct agent names in the SSE stream as a proxy for chain depth.
    Only works if the API echoes agent identifiers in intermediate events.
    """
    try:
        text = body.decode("utf-8", errors="ignore")
        lines = [
            ln[5:].strip()
            for ln in text.splitlines()
            if ln.startswith("data:")
        ]
        agent_names: set = set()
        for raw in lines:
            if not raw or raw == "[DONE]":
                continue
            try:
                p = json.loads(raw)
                for key in ("agent_name", "agent", "source_agent", "calling_agent"):
                    if isinstance(p.get(key), str):
                        agent_names.add(p[key])
            except Exception:
                pass
        return len(agent_names) if agent_names else 1
    except Exception:
        return 1


def _headers(user_id: str, *, accept_stream: bool = False) -> dict:
    return {
        "Content-Type": "application/json",
        "Accept": "application/json-lines" if accept_stream else "application/json",
        "user_id": user_id,
    }


def _chat_body(message: str, context: dict | None = None,
               sly_data: dict | None = None) -> str:
    # neuro-san 0.6.63: user_message must be {"text": "..."}, not a plain string.
    body: dict = {
        "user_message": {"text": message},
        "chat_context": context or {},
        "chat_filter": {"chat_filter_type": "MAXIMAL"},
    }
    # sly_data carries inter-turn state (e.g. agent_network_name for turn 2+).
    # Only include when present to match the official client's behaviour.
    if sly_data:
        body["sly_data"] = sly_data
    return json.dumps(body)


def _uid(prefix: str) -> str:
    return f"{prefix}-{random.randint(100_000, 999_999)}"


# ─────────────────────────────────────────────────────────────────────────────
class HealthCheckUser(HttpUser):
    """Pings /readyz every 2-5 s. No chat. Simulates monitoring probes."""
    weight    = 1
    wait_time = between(2, 5)

    @task
    def health_probe(self):
        with self.client.get(
            "/readyz",
            headers={"Accept": "application/json"},
            timeout=FAST_TIMEOUT,
            name="GET /readyz",
            catch_response=True,
        ) as r:
            if r.status_code == 200:
                r.success()
            else:
                r.failure(f"readyz → {r.status_code}")


# ─────────────────────────────────────────────────────────────────────────────
class BrowseUser(HttpUser):
    """Browses available agents and checks connectivity but never sends chat."""
    weight    = 2
    wait_time = between(1, 4)

    @task(3)
    def list_agents(self):
        with self.client.get(
            "/api/v1/list",
            headers=_headers(_uid("browse")),
            timeout=FAST_TIMEOUT,
            name="GET /api/v1/list",
            catch_response=True,
        ) as r:
            if r.status_code == 200 and r.text:
                r.success()
            elif r.status_code == 429:
                r.failure("rate limited (429)")
            else:
                r.failure(f"list → {r.status_code}")

    @task(2)
    def check_connectivity(self):
        agent = random.choice(KNOWN_AGENTS)
        with self.client.get(
            f"/api/v1/{agent}/connectivity",
            headers=_headers(_uid("conn")),
            timeout=FAST_TIMEOUT,
            name="GET /api/v1/{agent}/connectivity",
            catch_response=True,
        ) as r:
            if r.status_code == 200:
                r.success()
            elif r.status_code == 429:
                r.failure("rate limited (429)")
            else:
                r.failure(f"connectivity → {r.status_code}")


# ─────────────────────────────────────────────────────────────────────────────
class ChatUser(HttpUser):
    """
    Primary load driver. Runs the full user journey:
      1. 30% chance: list agents first  (mirrors typical UI navigation)
      2. Send a single chat message and wait for the full streamed response
    Think time 5-15 s matches real human behaviour.
    """
    weight    = 5
    wait_time = between(5, 15)

    @task
    def chat_session(self):
        uid   = _uid("chat")
        agent = DEFAULT_AGENT

        # 30% of users browse before chatting
        if random.random() < 0.3:
            self.client.get(
                "/api/v1/list",
                headers=_headers(uid),
                timeout=FAST_TIMEOUT,
                name="GET /api/v1/list",
            )

        with self.client.post(
            f"/api/v1/{agent}/streaming_chat",
            data=_chat_body(random.choice(CHAT_MESSAGES)),
            headers=_headers(uid, accept_stream=True),
            timeout=CHAT_TIMEOUT,
            name="POST /api/v1/{agent}/streaming_chat",
            catch_response=True,
            stream=True,
        ) as r:
            if r.status_code == 200:
                body = _read_stream(r)  # read full SSE stream; tolerates premature close
                if body:
                    r.success()
                else:
                    r.failure("empty stream body")
            elif r.status_code == 429:
                r.failure("rate limited (429)")
            elif r.status_code == 503:
                r.failure("service unavailable (503)")
            else:
                r.failure(f"chat → {r.status_code}: {r.text[:120]}")


# ─────────────────────────────────────────────────────────────────────────────
class PowerUser(HttpUser):
    """
    Maintains chat context across up to 5 turns, then resets.
    Simulates a user who has an extended back-and-forth conversation.
    Shorter think time (3-8 s) because power users type faster.
    """
    weight    = 2
    wait_time = between(3, 8)

    def on_start(self):
        self._context: dict = {}
        self._sly_data: dict | None = None
        self._uid    = _uid("power")
        self._turn   = 0

    @task
    def multi_turn_chat(self):
        agent   = DEFAULT_AGENT
        message = random.choice(CHAT_MESSAGES)

        with self.client.post(
            f"/api/v1/{agent}/streaming_chat",
            data=_chat_body(message, self._context, self._sly_data),
            headers=_headers(self._uid, accept_stream=True),
            timeout=CHAT_TIMEOUT,
            name="POST /api/v1/{agent}/streaming_chat (multi-turn)",
            catch_response=True,
            stream=True,
        ) as r:
            if r.status_code == 200:
                body = _read_stream(r)
                if body:
                    self._update_context(body)
                    self._turn += 1
                    r.success()
                else:
                    r.failure("empty response")
            elif r.status_code == 429:
                r.failure("rate limited (429)")
            else:
                r.failure(f"multi-turn chat → {r.status_code}")

        # Reset conversation after 5 turns
        if self._turn >= 5:
            self._context  = {}
            self._sly_data = None
            self._turn     = 0

    def _update_context(self, body: bytes):
        """Extract chat_context and sly_data from the AGENT_FRAMEWORK SSE frame."""
        try:
            for ln in reversed(body.decode("utf-8", errors="ignore").splitlines()):
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    payload = json.loads(ln)
                    r = payload.get("response", {})
                    if r.get("type") != "AGENT_FRAMEWORK":
                        continue
                    ctx = r.get("chat_context")
                    if ctx:
                        self._context = ctx
                        sd = r.get("sly_data")
                        if sd:
                            self._sly_data = sd
                        return
                except Exception:
                    continue
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
class BurstUser(HttpUser):
    """
    Short think time, rapid requests. Used in stress and spike shapes to quickly
    saturate AGENT_MAX_CONCURRENT_REQUESTS=50 and reveal concurrency ceilings.
    """
    weight    = 1
    wait_time = between(0.5, 2)

    @task(2)
    def rapid_list(self):
        self.client.get(
            "/api/v1/list",
            headers=_headers(_uid("burst")),
            timeout=FAST_TIMEOUT,
            name="GET /api/v1/list",
        )

    @task(3)
    def rapid_chat(self):
        uid   = _uid("burst")
        agent = random.choice(KNOWN_AGENTS)
        with self.client.post(
            f"/api/v1/{agent}/streaming_chat",
            data=_chat_body(random.choice(CHAT_MESSAGES)),
            headers=_headers(uid, accept_stream=True),
            timeout=CHAT_TIMEOUT,
            name="POST /api/v1/{agent}/streaming_chat",
            catch_response=True,
            stream=True,
        ) as r:
            if r.status_code not in (200, 429, 503):
                r.failure(f"unexpected {r.status_code}")


# ─────────────────────────────────────────────────────────────────────────────
class CapacityUser(HttpUser):
    """
    Used exclusively by capacity_test.py.

    Minimal think time (0.5–1.5 s) to maximise throughput.  Mixes fast
    read-only calls (health, list) with chat at a 5:3:2 ratio so the load
    driver stays busy between LLM responses without bottlenecking on think
    time.

    With LLM responses averaging ~5 s this yields ≈25–30 % RPS/VU, roughly
    3× the ChatUser baseline.  60 % RPS/VU is impossible for an LLM service
    because each VU is blocked waiting for the model for most of its cycle.

    weight=0 keeps this class out of normal Locust shape runs.
    """
    weight    = 1  # >0 required by Locust; capacity_test.py passes this class explicitly
    wait_time = between(0.5, 1.5)

    @task(5)
    def chat(self):
        uid = _uid("cap")
        with self.client.post(
            f"/api/v1/{DEFAULT_AGENT}/streaming_chat",
            data=_chat_body(random.choice(CHAT_MESSAGES)),
            headers=_headers(uid, accept_stream=True),
            timeout=CHAT_TIMEOUT,
            name="POST /api/v1/{agent}/streaming_chat",
            catch_response=True,
            stream=True,
        ) as r:
            if r.status_code == 200:
                body = _read_stream(r)  # drain full SSE stream; tolerates premature close
                if body:
                    inp, out = _extract_tokens(body)
                    if inp or out:
                        token_tracker.record(inp, out)
                    r.success()
                else:
                    r.failure("empty stream body")
            elif r.status_code == 429:
                r.failure("rate limited (429)")
            elif r.status_code == 503:
                r.failure("service unavailable (503)")
            else:
                r.failure(f"chat → {r.status_code}: {r.text[:120]}")

    @task(3)
    def list_agents(self):
        with self.client.get(
            "/api/v1/list",
            headers=_headers(_uid("cap")),
            timeout=FAST_TIMEOUT,
            name="GET /api/v1/list",
            catch_response=True,
        ) as r:
            if r.status_code == 200:
                r.success()
            else:
                r.failure(f"list → {r.status_code}")

    @task(2)
    def health(self):
        with self.client.get(
            "/readyz",
            headers={"Accept": "application/json"},
            timeout=FAST_TIMEOUT,
            name="GET /readyz",
            catch_response=True,
        ) as r:
            if r.status_code == 200:
                r.success()
            else:
                r.failure(f"readyz → {r.status_code}")


# ─────────────────────────────────────────────────────────────────────────────
class HackathonUser(HttpUser):
    """
    Simulates a real hackathon participant using agent_network_designer.

    Key differences from CapacityUser:
    - Realistic think time: 5-15 min between submissions (participant reads and
      discusses the generated agent network before requesting another).
    - Only submits agent network design requests (the actual hackathon task).
    - Captures token usage for quota projection.

    Use with: ./run.sh hackathon-sim
    At 500 VUs with 10-min avg think time and 3-min LLM response:
      duty cycle = 3/(3+10) = 23% → ~115 concurrent LLM calls → within 10-pod limit.
    The real constraint is the 10M token quota, not concurrent slot capacity.
    """
    weight    = 1  # must be > 0 — see CapacityUser comment
    wait_time = between(300, 900)  # 5-15 min realistic think time

    @task
    def design_agent_network(self):
        uid    = _uid("hack")
        prompt = random.choice(HACKATHON_DESIGN_PROMPTS)
        with self.client.post(
            f"/api/v1/agent_network_designer/streaming_chat",
            data=_chat_body(prompt),
            headers=_headers(uid, accept_stream=True),
            timeout=CHAT_TIMEOUT,
            name="POST /api/v1/agent_network_designer/streaming_chat",
            catch_response=True,
            stream=True,
        ) as r:
            if r.status_code == 200:
                body = _read_stream(r)
                if body:
                    inp, out = _extract_tokens(body)
                    if inp or out:
                        token_tracker.record(inp, out)
                    r.success()
                else:
                    r.failure("empty stream body")
            elif r.status_code == 429:
                r.failure("rate limited (429) — TPM/RPM quota hit on Azure AI Foundry")
            elif r.status_code == 503:
                r.failure("service unavailable (503) — all 50 LLM slots occupied")
            elif r.status_code == 504:
                r.failure("gateway timeout (504) — response exceeded NGINX 600s limit")
            else:
                r.failure(f"design → {r.status_code}: {r.text[:120]}")


# ─────────────────────────────────────────────────────────────────────────────
# Refinement prompts for SessionUser turns 2+
# Each prompt is deliberately broad and additive so it applies after ANY domain design.
# Designed to maximise token consumption:
#   - Forces addition of entirely new agents (more HOCON to generate)
#   - Demands enterprise tool integrations (new tool-definition sections)
#   - Requires compliance framework overlays (re-validates the entire network)
#   - Changes fundamental architecture (triggers full re-design loop)
_REFINEMENTS = [
    # ── Architecture-breaking changes (forces full re-design) ─────────────────
    (
        "Completely redesign the data flow so that every agent communicates exclusively through "
        "a central Apache Kafka event-bus. Each agent must produce and consume from named topics. "
        "Add a schema-registry-agent that enforces Avro schemas on every topic, a dead-letter-queue-handler-agent "
        "for malformed messages, and a lag-alerting-agent that pages PagerDuty when consumer lag exceeds 10,000 events. "
        "No agent should call another agent directly — all communication must be asynchronous and event-driven."
    ),
    (
        "Refactor the entire network for multi-region active-active deployment across US-East, EU-West, and APAC-Southeast. "
        "Add a global-traffic-manager-agent that routes requests using latency-based routing via Azure Front Door, "
        "a data-residency-enforcer-agent that blocks PII from crossing GDPR/PIPL jurisdictional boundaries, "
        "a cross-region-conflict-resolver-agent that handles split-brain write conflicts using vector clocks, "
        "and a regional-failover-orchestrator-agent that executes region-evacuation runbooks within 30 seconds of an availability-zone outage. "
        "Ensure RPO ≤ 5 minutes and RTO ≤ 2 minutes for each agent."
    ),
    (
        "Migrate the network from a synchronous request-response pattern to a fully asynchronous CQRS architecture. "
        "Add a command-handler-agent that writes to an event-sourced store in Azure Cosmos DB, "
        "a query-side-projector-agent that builds read-model views optimised for each consumer persona, "
        "a saga-orchestrator-agent that coordinates multi-step workflows with compensating transactions, "
        "and a event-replay-and-audit-agent that can reconstruct system state at any past point in time. "
        "Every agent decision must be an immutable event. No in-place updates anywhere in the network."
    ),
    # ── Compliance and regulatory overlays ────────────────────────────────────
    (
        "Add full EU AI Act Article 6 and Annex III compliance to the entire network. "
        "Insert a conformity-assessment-agent that generates a technical documentation package per Article 11, "
        "a human-oversight-dashboard-agent that surfaces every automated decision to a human reviewer with a 4-hour override window, "
        "a fundamental-rights-impact-assessor-agent that runs quarterly FRIAs per Article 27, "
        "a transparency-log-publisher-agent that writes every high-risk decision to a publicly accessible EU AI Act register, "
        "and a notified-body-liaison-agent that packages audit evidence for third-party conformity assessment. "
        "All agents must halt and escalate when operating outside their registered confidence bounds."
    ),
    (
        "Retrofit the entire network to meet PCI-DSS v4.0 Requirements 6, 7, 8, and 10 end-to-end. "
        "Add a secrets-vault-agent that fetches all credentials dynamically from HashiCorp Vault with a 1-hour TTL, "
        "a network-segmentation-enforcer-agent that validates every inter-agent call routes only through a designated CDE network segment, "
        "a privileged-access-workstation-policy-agent that ensures no agent calls external internet endpoints not on an approved allow-list, "
        "a file-integrity-monitoring-agent that detects config drift in agent definitions within 60 seconds, "
        "and a PCI-log-consolidation-agent that ships all agent audit events to a Splunk SIEM with 12-month retention. "
        "Quarterly ASV scan results must be embedded in every agent's compliance metadata."
    ),
    (
        "Implement a comprehensive GDPR Article 5 data-minimisation and right-to-erasure framework across every agent. "
        "Add a personal-data-inventory-agent that classifies and tags every data field flowing through the network using Microsoft Purview, "
        "a consent-gate-agent that blocks processing when a valid GDPR consent record cannot be verified in OneTrust, "
        "a data-retention-enforcer-agent that automatically schedules deletion jobs when retention periods expire, "
        "a subject-access-request-fulfiller-agent that assembles a complete data export within 72 hours, "
        "and a cross-border-transfer-compliance-agent that verifies SCCs or BCRs before any data leaves the EEA. "
        "All agents must log a GDPR-compliant processing record to a dedicated DPA audit trail."
    ),
    (
        "Add SOX Section 302 and 404 internal-control attestation capability to the entire network. "
        "Insert a control-objective-mapper-agent that links every automated agent decision to a COSO 2013 control objective, "
        "a control-testing-evidence-collector-agent that samples 25 automated decisions per quarter and stores proof in SharePoint, "
        "a management-review-control-agent that requires a named executive sign-off before any agent modifies financial records, "
        "a segregation-of-duties-enforcer-agent that prevents any single agent chain from initiating and approving the same transaction, "
        "and a external-auditor-package-assembler-agent that compiles PCAOB-compliant ICFR evidence packs on demand. "
        "Design all controls so they can be tested independently without requiring access to the underlying LLM weights."
    ),
    # ── New agent additions that massively expand the network ─────────────────
    (
        "Extend the network with a complete real-time observability stack. Add: "
        "a distributed-trace-collector-agent implementing OpenTelemetry SDK with W3C trace-context propagation across all agent calls, "
        "a RED-metrics-dashboard-agent computing rate, errors, and duration histograms per agent in Prometheus with 15-second scrape, "
        "a log-anomaly-detector-agent running LLM-based log analysis on Datadog Logs to surface novel error signatures, "
        "a SLO-burn-rate-calculator-agent computing multi-window burn rates per SLO and triggering alert suppression after 5 consecutive pages, "
        "a chaos-engineering-scheduler-agent running weekly Gremlin fault-injection experiments and measuring blast radius, "
        "a cost-attribution-agent computing per-agent LLM token spend and mapping to business-unit chargebacks in Azure Cost Management, "
        "and a auto-scaling-decision-agent adjusting Kubernetes HPA min/max replicas based on p99 latency vs cost trade-off models."
    ),
    (
        "Add a complete multi-tier human-in-the-loop escalation and approval workflow to the network. "
        "Create a risk-classification-gate-agent that scores every agent output on a 0-100 confidence scale before releasing it, "
        "a tier-1-automated-resolution-agent that handles cases above 85 confidence with no human involvement, "
        "a tier-2-expert-review-router-agent that sends 50-85 confidence cases to a named domain expert via MS Teams Adaptive Card, "
        "a tier-3-committee-escalation-agent that convenes a 3-person virtual committee for sub-50 confidence decisions via Zoom API, "
        "a decision-override-audit-agent that records every human override with rationale and feeds it back as labelled training data, "
        "and a escalation-SLA-monitor-agent that pages the responsible VP when any tier-2 case exceeds a 2-hour response window. "
        "The system must never auto-close a tier-3 case without human sign-off."
    ),
    (
        "Integrate the network with SAP S/4HANA as the system of record for all financial and operational data. "
        "Add a SAP-BAPI-gateway-agent that translates agent requests into RFC function-module calls with credential rotation via SAP OAuth 2.0, "
        "a SAP-IDoc-consumer-agent that processes inbound IDocs from SAP via Azure Logic Apps and maps to internal event schema, "
        "a SAP-change-document-monitor-agent that subscribes to SAP change documents via SAP Business Events and triggers downstream workflows, "
        "a SAP-master-data-integrity-checker-agent that validates material master, vendor master, and customer master consistency before every write, "
        "and a SAP-transport-release-gatekeeper-agent that blocks any config change to production SAP without a CAB-approved RFC in ServiceNow. "
        "All SAP integration must use SAP Certified integration scenarios and pass SAP Integration Suite health checks."
    ),
    (
        "Add a complete Salesforce CRM integration layer to the network. "
        "Insert a Salesforce-record-sync-agent that maintains bi-directional sync using Salesforce Platform Events and Change Data Capture, "
        "a Salesforce-flow-trigger-agent that fires Salesforce Flows and Process Builder actions in response to agent decisions via REST Composite API, "
        "a Salesforce-Einstein-signal-ingestion-agent that reads Einstein Lead Scoring and Opportunity Insights and uses them as agent inputs, "
        "a Salesforce-data-cloud-segment-publisher-agent that writes agent-generated audience segments back into Salesforce Data Cloud, "
        "and a Salesforce-field-level-encryption-enforcer-agent that validates Salesforce Shield encryption is active on all PII fields before reading them. "
        "Agent credentials must rotate every 24 hours via Salesforce Connected App with JWT bearer flow."
    ),
    (
        "Retrofit the network to support real-time streaming inference at 100,000 events per second. "
        "Add a Flink-stream-processor-agent that applies stateful CEP rules to detect patterns across 60-second tumbling windows, "
        "a feature-store-writer-agent that materialises derived features to Feast in under 50ms for online serving, "
        "a model-serving-load-balancer-agent that distributes inference requests across 10 Azure ML Online Endpoints with latency-aware routing, "
        "a backpressure-governor-agent that sheds low-priority inference requests when p99 latency exceeds 200ms, "
        "and a streaming-output-deduplication-agent that maintains a 5-minute bloom filter to suppress duplicate downstream actions. "
        "The full pipeline from event ingestion to agent action must complete in under 500ms at p99."
    ),
    # ── Security hardening ────────────────────────────────────────────────────
    (
        "Perform a complete zero-trust security hardening of the entire agent network. "
        "Add a mutual-TLS-enforcer-agent that validates client certificates on every inter-agent API call using Azure API Management, "
        "a JWT-token-validator-agent that checks iss, aud, iat, exp, and a custom jti allow-list on every request, "
        "a OAuth-2.0-scope-guardian-agent that enforces principle of least privilege — each agent may only request exactly the scopes it needs, "
        "a secrets-rotation-orchestrator-agent that rotates all API keys, certificates, and passwords every 90 days with zero downtime, "
        "a anomalous-agent-call-pattern-detector-agent that flags and blocks unusual inter-agent communication patterns deviating from the baseline graph, "
        "and a penetration-test-evidence-compiler-agent that packages OWASP ASVS L3 test results for quarterly sign-off by the CISO. "
        "No agent may communicate over plaintext HTTP. All secrets must be stored in Azure Key Vault with HSM backing."
    ),
    (
        "Add a comprehensive prompt-injection and adversarial-input defence layer to every agent. "
        "Insert a input-sanitisation-agent that strips control characters, Unicode homoglyphs, and known jailbreak patterns from all user inputs, "
        "a indirect-prompt-injection-detector-agent that scans retrieved documents and API responses for embedded instructions before passing them to LLMs, "
        "a output-validation-agent that checks every LLM response against a domain-specific schema and refuses malformed outputs with a structured error, "
        "a rate-limiting-and-quota-enforcer-agent that caps per-user token consumption and blocks burst patterns indicative of automated attacks, "
        "and a red-team-scenario-replayer-agent that daily runs a library of 500 known adversarial prompts and alerts when any bypasses validation. "
        "All prompt templates must be stored in version control and changes require security-team review."
    ),
    # ── Performance and cost ──────────────────────────────────────────────────
    (
        "Optimise the network for a 10× reduction in LLM token consumption without degrading output quality. "
        "Add a semantic-cache-agent that uses cosine similarity search in Redis to return cached responses for queries within 0.95 similarity, "
        "a prompt-compression-agent that applies LLMLingua-2 selective token pruning to reduce prompt length by 40% before sending to the LLM, "
        "a routing-classifier-agent that directs simple queries to a fine-tuned 7B-parameter model and complex queries to the frontier model, "
        "a chain-of-thought-length-controller-agent that limits reasoning traces to a maximum token budget per agent tier, "
        "and a speculative-decoding-agent that pre-generates likely next tokens using a draft model to reduce TTFT by 35%. "
        "Instrument every agent with per-call token counters and publish to a cost-attribution dashboard in Grafana."
    ),
    (
        "Add a comprehensive A/B testing and continuous-improvement framework to the agent network. "
        "Insert a experiment-registry-agent that maintains a catalogue of active A/B tests with traffic-split percentages in LaunchDarkly, "
        "a assignment-and-tracking-agent that assigns users to treatment arms and logs exposure events to a Snowflake experiment table, "
        "a statistical-significance-evaluator-agent that runs sequential probability ratio tests and declares winners when p < 0.01, "
        "a guardrail-breach-detector-agent that auto-pauses experiments when a negative metric deteriorates by more than 2%, "
        "a feature-flag-rollout-orchestrator-agent that gradually increases treatment traffic from 1% to 100% over 7 days, "
        "and a experiment-results-narrator-agent that writes a natural-language post-mortem report for each concluded experiment. "
        "All experiments must pre-register their primary metric and sample-size calculation before launch."
    ),
    # ── Data quality and governance ───────────────────────────────────────────
    (
        "Build a full data-quality and lineage-tracking layer across every agent in the network. "
        "Add a great-expectations-test-runner-agent that validates every incoming dataset against 150 expectation suites before processing, "
        "a data-lineage-recorder-agent that writes OPEN Lineage JSON events to Apache Atlas for every agent transformation, "
        "a schema-drift-detector-agent that alerts when upstream API schemas change by comparing current vs registered OpenAPI specs, "
        "a referential-integrity-enforcer-agent that validates foreign-key relationships across distributed agent data stores, "
        "a data-quality-SLA-monitor-agent that halts downstream agents when completeness falls below 99% or accuracy below 97%, "
        "and a data-quality-incident-postmortem-agent that generates a DMAR (Data Quality Incident Report) and routes it to the Data Steward in Collibra. "
        "Every agent must declare its input and output data contracts in a shared schema registry."
    ),
    (
        "Add a real-time ServiceNow ITSM integration so every agent action that affects production systems creates a governed change record. "
        "Insert a change-request-auto-creator-agent that drafts RFC records in ServiceNow with risk score, implementation plan, and rollback procedure, "
        "a CAB-approval-poller-agent that blocks execution until ServiceNow returns an approved state with named approver, "
        "a configuration-item-updater-agent that updates CMDB relationship records after every agent-initiated infrastructure change, "
        "a incident-auto-linker-agent that associates generated incidents to parent problems and known errors in ServiceNow, "
        "and a post-implementation-review-scheduler-agent that creates a PIR task 48 hours after every major change and populates it with agent telemetry. "
        "Change success rate per agent must be tracked and any agent with > 5% failed changes auto-suspended."
    ),
    # ── AI ethics and fairness ────────────────────────────────────────────────
    (
        "Add a complete AI fairness and algorithmic accountability layer to the network. "
        "Add a demographic-parity-monitor-agent that computes approval rates, recall, and precision broken down by age, gender, and ethnicity weekly, "
        "a counterfactual-fairness-tester-agent that generates synthetic counterfactual inputs and checks for discriminatory output flips, "
        "a protected-attribute-leakage-detector-agent that uses mutual information to flag proxy variables encoding protected characteristics, "
        "a model-card-publisher-agent that auto-generates and posts model cards to an internal ML Hub after every model update, "
        "a algorithmic-impact-assessment-coordinator-agent that runs a full AIA per ISO/IEC 42001 Annex B before any model goes to production, "
        "and a public-facing-explanation-page-generator-agent that creates plain-language explanations of every automated decision for affected individuals. "
        "All agents in the network must log the features that contributed most to each decision for post-hoc audit."
    ),
    (
        "Completely redesign the error-recovery and circuit-breaker architecture for production hardening. "
        "Add a bulkhead-isolation-agent that partitions agent thread pools so a failure in one agent cannot starve another of resources, "
        "a circuit-breaker-state-machine-agent that transitions each downstream dependency through closed, open, and half-open states with configurable thresholds, "
        "a retry-with-jitter-agent that applies exponential backoff with full jitter for transient failures and logs each retry attempt, "
        "a timeout-cascade-preventer-agent that enforces per-agent deadline propagation using distributed context deadlines, "
        "a degraded-mode-fallback-agent that serves a cached or simplified response when the primary path is unavailable rather than returning an error, "
        "and a failure-budget-ledger-agent that tracks cumulative downtime per agent and locks deployments when the 30-day error budget is exhausted. "
        "Every agent must declare its SLO, SLI definition, and error budget in a machine-readable YAML manifest."
    ),
    (
        "Add a complete multi-language and accessibility layer to the network so it serves a global enterprise with 50 nationalities. "
        "Insert a language-detection-agent that identifies the input language using Azure AI Language and routes to the appropriate locale pipeline, "
        "a culturally-sensitive-translation-agent that uses DeepL Advanced with domain glossaries rather than raw machine translation, "
        "a right-to-left-and-complex-script-formatter-agent that applies Unicode BIDI algorithm and proper rendering for Arabic, Hebrew, and Devanagari outputs, "
        "a accessibility-compliance-agent that checks all structured outputs against WCAG 2.2 AA and Section 508 before delivery, "
        "a legal-disclaimer-localiser-agent that appends jurisdiction-specific regulatory warnings in the user's language, "
        "and a translation-quality-evaluator-agent that uses COMET and BLEURT scores to reject translations below 0.85 quality threshold. "
        "The entire network must achieve ISO 9241-171 accessibility conformance for all 50 supported locales."
    ),
    (
        "Extend the network with a comprehensive vendor and third-party API resilience layer. "
        "Add a API-health-sentinel-agent that probes every external API endpoint every 60 seconds and publishes status to a dependency dashboard, "
        "a vendor-SLA-tracker-agent that logs every external API call latency and computes monthly uptime against contracted SLA, "
        "a automatic-vendor-failover-agent that switches to a secondary vendor API within 3 seconds when the primary returns 3 consecutive errors, "
        "a response-cache-warming-agent that pre-fetches and caches common external API responses during off-peak hours to reduce live dependency, "
        "a vendor-contract-expiry-alerter-agent that warns 90, 60, and 30 days before any API key or vendor contract expires, "
        "and a cost-anomaly-detector-agent that pages the engineering manager when any vendor API cost increases by more than 20% week-on-week. "
        "Vendor selection decisions must be documented with security and compliance review evidence before onboarding."
    ),
    (
        "Add a complete disaster-recovery and business-continuity plan to the agent network with automated DR testing. "
        "Insert a RPO-and-RTO-tracker-agent that continuously measures actual recovery metrics against declared targets, "
        "a backup-verification-agent that restores a sample dataset daily from Azure Blob geo-redundant storage and verifies integrity, "
        "a DR-runbook-executor-agent that automates the failover sequence to a paired Azure region using Terraform and runs it in DR-test mode weekly, "
        "a business-impact-calculator-agent that computes revenue and regulatory risk per minute of downtime for each agent function, "
        "a crisis-communication-dispatcher-agent that sends structured downtime notifications to internal stakeholders and regulators within 15 minutes, "
        "and a DR-test-evidence-packager-agent that compiles test results, gaps, and remediation actions into ISO 22301 evidence packs. "
        "The DR plan must cover loss of a complete Azure region, loss of a third-party LLM provider, and simultaneous loss of both."
    ),
]


class SessionUser(HttpUser):
    """
    Simulates one hackathon participant in a 90-minute session.

    What this models that HackathonUser does NOT:
    ─────────────────────────────────────────────
    • Fixed user_id for entire session → NGINX sticky cookie pins user to ONE pod.
      That pod holds all session state for 90 min; if it saturates, the user waits
      even if other pods are free.

    • chat_context grows each turn → token cost compounds.
      Turn 1 ≈ 5k tokens.  Turn 5 ≈ 25k tokens.  Turn 10 ≈ 50k tokens.
      The 10M quota exhausts 5-10× faster than single-turn estimates suggest.

    • Azure Blob read + write every turn → IOPS scales with active session depth,
      not just active session count.

    • Pod memory pressure → each pod accumulates session context for all pinned
      users. At 50 concurrent sessions × 90 min × growing context ≈ OOM risk.

    • turn_tracker records per-turn token cost so the dashboard can show the
      compounding effect clearly.
    """
    weight    = 1
    # Closed-loop: think time fires AFTER the answer arrives, before the next turn.
    wait_time = between(THINK_TIME_MIN, THINK_TIME_MAX)   # 2-4 min (config)

    def on_start(self):
        self._uid      = _uid("sess")   # FIXED for entire session → NGINX sticky
        self._context: dict = {}        # grows each turn (chat_context)
        self._sly_data: dict | None = None  # carries agent_network_name for turn 2+
        self._turn     = 0              # 0-indexed: 0 = first design, 1+ = refinements
        # Model the login wave: each participant loads the UI once before designing.
        _frontend_login(self)

    @task
    def session_turn(self):
        # Turn 0: fresh design request. Turns 1+: refinements on the same session.
        message = (
            random.choice(HACKATHON_DESIGN_PROMPTS) if self._turn == 0
            else random.choice(_REFINEMENTS)
        )
        turn_name = f"POST /api/v1/agent_network_designer/streaming_chat (turn {min(self._turn + 1, 5)}+)"

        # One retry with 20s backoff for transient 503/502/empty-stream errors.
        # Real participants wait a moment and try again — this models that behaviour
        # and avoids session death from a brief pod restart or Azure rate-limit blip.
        for attempt in range(2):
            in_flight.inc()   # this design is now running (concurrency gauge)
            try:
                with self.client.post(
                    "/api/v1/agent_network_designer/streaming_chat",
                    data=_chat_body(message, self._context, self._sly_data),
                    headers=_headers(self._uid, accept_stream=True),
                    timeout=CHAT_TIMEOUT,
                    name=turn_name,
                    catch_response=True,
                    stream=True,
                ) as r:
                    if r.status_code == 200:
                        body = _read_stream(r)
                        if body:
                            inp, out = _extract_tokens(body)
                            if inp or out:
                                token_tracker.record(inp, out)
                                turn_tracker.record(self._turn + 1, inp, out)
                            self._update_context(body)
                            self._turn += 1
                            r.success()
                            return  # done — think time fires next
                        else:
                            r.failure("empty stream body")
                    elif r.status_code == 429:
                        # Our NGINX per-user limit (1 query/30s), NOT an Azure quota hit.
                        rate_limit_tracker.record(self._uid)
                        r.failure("rate limited (429) — per-user 1 query/30s limit")
                        return  # rate limit is intentional — don't retry-hammer it
                    elif r.status_code == 503:
                        r.failure("service unavailable (503) — LLM slot capacity exceeded")
                    elif r.status_code == 504:
                        r.failure("gateway timeout (504) — response exceeded NGINX timeout")
                        return  # timeout already waited long enough — don't double-wait
                    else:
                        r.failure(f"turn {self._turn + 1} → {r.status_code}: {r.text[:120]}")
                        return  # non-retriable (unexpected status)
            finally:
                in_flight.dec()

            if attempt == 0:
                time.sleep(20)  # wait 20s before retry (pod recovers, Azure clears)

    def _update_context(self, body: bytes):
        """Extract chat_context AND sly_data from the AGENT_FRAMEWORK SSE frame.

        sly_data carries inter-turn state like agent_network_name that the
        agent_network_editor sub-agent needs on turn 2+.  Without it, every
        refinement turn fails with 'agent_network_name missing from sly_data'.
        """
        try:
            for ln in reversed(body.decode("utf-8", errors="ignore").splitlines()):
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    payload = json.loads(ln)
                    r = payload.get("response", {})
                    if r.get("type") != "AGENT_FRAMEWORK":
                        continue
                    ctx = r.get("chat_context")
                    if ctx:
                        self._context = ctx
                        # sly_data is alongside chat_context in the AGENT_FRAMEWORK frame
                        sd = r.get("sly_data")
                        if sd:
                            self._sly_data = sd
                        return
                except Exception:
                    continue
        except Exception:
            pass
