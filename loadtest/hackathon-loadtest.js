import http from "k6/http";
import { check, sleep } from "k6";
import { Counter, Rate, Trend } from "k6/metrics";

// ── Custom metrics ──────────────────────────────────────────────────────────
const listSuccess       = new Rate("list_success");
const connectSuccess    = new Rate("connect_success");
const chatSuccess       = new Rate("chat_success");
const chatDuration      = new Trend("chat_duration_ms", true);
const rateLimitErrors   = new Counter("rate_limit_429");
const serverErrors      = new Counter("server_errors_5xx");

// ── Config ───────────────────────────────────────────────────────────────────
const BASE_URL  = "https://neurosanhackathon-api.eastus.cloudapp.azure.com";
const AGENT     = "agent_network_designer";   // change if needed

// ── Load profile — ramp up to 50 VUs, hold, ramp down ───────────────────────
export const options = {
    stages: [
        { duration: "30s", target: 10  },   // warm up
        { duration: "60s", target: 30  },   // ramp to 30 users
        { duration: "60s", target: 50  },   // ramp to 50 users (pod limit)
        { duration: "60s", target: 80  },   // push past limit — see what breaks
        { duration: "30s", target: 0   },   // ramp down
    ],
    thresholds: {
        "http_req_duration":   ["p(95)<5000"],  // 95% of requests under 5s
        "list_success":        ["rate>0.95"],   // 95%+ list calls succeed
        "connect_success":     ["rate>0.90"],   // 90%+ connectivity calls succeed
        "chat_success":        ["rate>0.80"],   // 80%+ chat calls succeed
        "rate_limit_429":      ["count<50"],    // fewer than 50 rate limit hits
    },
    // Real-time output for dashboard
    summaryTrendStats: ["min", "med", "avg", "p(90)", "p(95)", "p(99)", "max"],
};

// ── Helpers ───────────────────────────────────────────────────────────────────
function headers(userId) {
    return {
        "Content-Type":  "application/json",
        "Accept":        "application/json",
        "user_id":       userId,
    };
}

// ── Main VU scenario ──────────────────────────────────────────────────────────
export default function () {
    const userId = `loadtest-user-${__VU}-${__ITER}`;
    const h      = headers(userId);

    // ── Step 1: List available agent networks ─────────────────────────────────
    const listRes = http.get(`${BASE_URL}/api/v1/list`, { headers: h, tags: { name: "list" } });
    const listOk  = check(listRes, {
        "list: status 200":     (r) => r.status === 200,
        "list: has agents":     (r) => r.body && r.body.length > 0,
    });
    listSuccess.add(listOk);
    if (listRes.status === 429) rateLimitErrors.add(1);
    if (listRes.status >= 500)  serverErrors.add(1);

    sleep(0.5);

    // ── Step 2: Connectivity check ────────────────────────────────────────────
    const connRes = http.get(
        `${BASE_URL}/api/v1/${AGENT}/connectivity`,
        { headers: h, tags: { name: "connectivity" } }
    );
    const connOk = check(connRes, {
        "connectivity: status 200": (r) => r.status === 200,
        "connectivity: has info":   (r) => r.body && r.body.includes("connectivity_info"),
    });
    connectSuccess.add(connOk);
    if (connRes.status === 429) rateLimitErrors.add(1);
    if (connRes.status >= 500)  serverErrors.add(1);

    sleep(0.5);

    // ── Step 3: Send a real chat message to agent_network_designer ────────────
    const payload = JSON.stringify({
        user_message: "Create a simple agent network with one agent that answers questions about Azure.",
        chat_context: {},
        chat_filter: { chat_filter_type: "MAXIMAL" },
    });

    const chatStart = Date.now();
    const chatRes   = http.post(
        `${BASE_URL}/api/v1/${AGENT}/streaming_chat`,
        payload,
        {
            headers: { ...h, "Accept": "text/event-stream" },
            tags:    { name: "streaming_chat" },
            timeout: "120s",
        }
    );
    const chatElapsed = Date.now() - chatStart;
    chatDuration.add(chatElapsed);

    const chatOk = check(chatRes, {
        "chat: status 200":       (r) => r.status === 200,
        "chat: has response":     (r) => r.body && r.body.length > 0,
        "chat: no error in body": (r) => !r.body.includes('"error"'),
    });
    chatSuccess.add(chatOk);
    if (chatRes.status === 429) rateLimitErrors.add(1);
    if (chatRes.status >= 500)  serverErrors.add(1);

    // Think time between iterations
    sleep(Math.random() * 3 + 2);   // 2-5 second pause between users
}

// ── End-of-test summary ───────────────────────────────────────────────────────
export function handleSummary(data) {
    return {
        "stdout": textSummary(data, { indent: " ", enableColors: true }),
        "/tmp/loadtest-results.json": JSON.stringify(data, null, 2),
    };
}

// inline textSummary (k6 built-in)
function textSummary(data, opts) {
    const indent = opts.indent || "";
    let out = "\n" + indent + "=== LOAD TEST SUMMARY ===\n\n";

    // Thresholds
    out += indent + "Thresholds:\n";
    for (const [name, result] of Object.entries(data.metrics)) {
        if (result.thresholds) {
            for (const [thr, passed] of Object.entries(result.thresholds)) {
                const icon = passed.ok ? "✅" : "❌";
                out += `${indent}  ${icon} ${name}: ${thr}\n`;
            }
        }
    }

    out += "\n" + indent + "Key Metrics:\n";
    const show = [
        "http_req_duration",
        "chat_duration_ms",
        "list_success",
        "connect_success",
        "chat_success",
        "rate_limit_429",
        "server_errors_5xx",
        "http_reqs",
        "http_req_failed",
    ];
    for (const name of show) {
        const m = data.metrics[name];
        if (!m) continue;
        if (m.type === "rate")    out += `${indent}  ${name}: ${(m.values.rate * 100).toFixed(1)}%\n`;
        if (m.type === "counter") out += `${indent}  ${name}: ${m.values.count}\n`;
        if (m.type === "trend") {
            out += `${indent}  ${name}: avg=${m.values.avg.toFixed(0)}ms  p95=${m.values["p(95)"].toFixed(0)}ms  max=${m.values.max.toFixed(0)}ms\n`;
        }
    }
    return out;
}
