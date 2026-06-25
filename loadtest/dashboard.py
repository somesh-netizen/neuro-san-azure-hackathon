"""
Neuro-San Load Test — Analysis Dashboard
=========================================
Post-test Streamlit app.  Reads JSON summaries and Locust CSV history files
produced by each test run and renders interactive charts.

Launch:
  streamlit run dashboard.py
  # opens at http://localhost:8501
"""

import json
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Neuro-San Load Test Dashboard",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded",
)

REPORTS_DIR = Path(__file__).parent / "reports"

# ── Helpers ───────────────────────────────────────────────────────────────────

def load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def find_history_csv(label: str, ts: str) -> Path | None:
    """Match stats_history CSV produced with --csv reports/<label>_<ts>."""
    candidates = sorted(REPORTS_DIR.glob(f"{label}_{ts}*_stats_history.csv"))
    if candidates:
        return candidates[0]
    # Fallback: any history file matching timestamp prefix
    candidates = sorted(REPORTS_DIR.glob(f"*{ts}*_stats_history.csv"))
    return candidates[0] if candidates else None


def load_history(path: Path) -> pd.DataFrame | None:
    try:
        df = pd.read_csv(path)
        # Timestamp column is Unix epoch in Locust CSVs
        df["Timestamp"] = pd.to_datetime(df["Timestamp"], unit="s", utc=True)
        return df
    except Exception:
        return None


def colour_threshold(passed: bool) -> str:
    return "normal" if passed else "inverse"


# ── Sidebar ───────────────────────────────────────────────────────────────────
st.sidebar.image(
    "https://img.shields.io/badge/Neuro--San-Load%20Test-blue?style=for-the-badge",
    use_column_width=True,
)
st.sidebar.title("🧠 Neuro-San Load Test")

summary_files  = sorted(REPORTS_DIR.glob("summary_*.json"),  reverse=True)
capacity_files = sorted(REPORTS_DIR.glob("capacity_*.json"), reverse=True)

if not summary_files and not capacity_files:
    st.title("🧠 Neuro-San Load Test Dashboard")
    st.info(
        "No reports found yet.  Run a test first:\n\n"
        "```bash\n./run.sh smoke              # quick 2-min sanity check\n"
        "./run.sh load               # stepped ramp to 1000 VUs\n"
        "./run.sh stress             # ramp to 2000 VUs\n"
        "python3 capacity_test.py   # capacity planning (2/4/8/10 pods)\n```"
    )
    st.stop()

# Which top-level page to show
page = st.sidebar.radio(
    "View",
    ["Load Test Results", "Capacity Planning"],
    disabled=(not summary_files, not capacity_files).count(False) < 2,
)

def _label(f: Path) -> str:
    stem  = f.stem.replace("summary_", "").replace("capacity_", "")
    parts = stem.split("_")
    if len(parts) >= 3:
        kind = parts[0]
        date = parts[-2][:8]
        t    = parts[-1][:6]
        return f"{kind}  {date[:4]}-{date[4:6]}-{date[6:]}  {t[:2]}:{t[2:4]}"
    return stem

st.sidebar.markdown("---")
st.sidebar.caption(f"Reports dir: `{REPORTS_DIR}`")
st.sidebar.caption("Run `./run.sh <cmd>` or `python3 capacity_test.py` to generate reports.")

# ═══════════════════════════════════════════════════════════════════════════════
# PAGE: LOAD TEST RESULTS
# ═══════════════════════════════════════════════════════════════════════════════
if page == "Load Test Results" or not capacity_files:

    if not summary_files:
        st.info("No load test reports found. Run `./run.sh smoke` first.")
        st.stop()

    file_labels = [_label(f) for f in summary_files]
    selection   = st.sidebar.selectbox("Select test run", file_labels)
    sel_idx     = file_labels.index(selection)
    sel_file    = summary_files[sel_idx]
    data        = load_json(sel_file)
    compare_enabled = st.sidebar.checkbox("Compare all runs", value=False)

# ── Header ────────────────────────────────────────────────────────────────────
label_text = data.get("test_label", "unknown").upper()
st.title(f"🧠 Neuro-San Load Test — {label_text}")
st.caption(f"Ran at {data['timestamp']}  |  Duration: {data['duration_seconds']:.0f}s")

# ── KPI cards ─────────────────────────────────────────────────────────────────
st.subheader("📊 Summary")
k1, k2, k3, k4, k5, k6 = st.columns(6)
k1.metric("Total Requests",  f"{data['total_requests']:,}")
k2.metric("Total Failures",  f"{data['total_failures']:,}")
k3.metric("Error Rate",      f"{data['error_rate_pct']:.1f}%",
          delta=f"limit 5%", delta_color="normal" if data['error_rate_pct'] <= 5 else "inverse")
k4.metric("Avg RPS",         f"{data['avg_rps']:.1f}")
k5.metric("p95 Latency",     f"{data['p95_ms']:.0f} ms",
          delta=f"limit 30 000 ms", delta_color="normal" if data['p95_ms'] <= 30_000 else "inverse")
k6.metric("p99 Latency",     f"{data['p99_ms']:.0f} ms")

# ── Threshold results ─────────────────────────────────────────────────────────
st.subheader("✅ Pass / Fail Thresholds")
thresholds = data.get("thresholds", {})
thr_cols   = st.columns(max(len(thresholds), 1))
for col, (name, result) in zip(thr_cols, thresholds.items()):
    icon  = "✅" if result["passed"] else "❌"
    label = name.replace("_", " ").title()
    col.metric(
        label=f"{icon} {label}",
        value=result["actual"],
        delta=f"limit: {result['limit']}",
        delta_color=colour_threshold(result["passed"]),
    )

st.divider()

# ── Try to load time-series CSV (produced by --csv flag) ──────────────────────
stem     = sel_file.stem.replace("summary_", "")   # label_YYYYMMDD_HHMMSS
parts    = stem.split("_")
ts_suffix = "_".join(parts[-2:]) if len(parts) >= 2 else stem
history_path = find_history_csv(parts[0], ts_suffix)
history_df   = load_history(history_path) if history_path else None

if history_df is not None:
    st.subheader("📈 Time-Series Metrics")
    agg = history_df[history_df["Name"] == "Aggregated"].copy()

    if not agg.empty:
        tab1, tab2, tab3, tab4 = st.tabs(
            ["Response Times", "Request Rate", "Error Rate", "Virtual Users"]
        )

        with tab1:
            fig = go.Figure()
            for pct, colour in [("50%", "#2196F3"), ("95%", "#FF9800"), ("99%", "#F44336")]:
                if pct in agg.columns:
                    fig.add_trace(go.Scatter(
                        x=agg["Timestamp"], y=agg[pct].fillna(0),
                        mode="lines", name=f"p{pct.rstrip('%')}",
                        line=dict(color=colour),
                    ))
            fig.update_layout(
                title="Response Time Percentiles (ms)",
                xaxis_title="Time", yaxis_title="Latency (ms)",
                height=380, template="plotly_white",
            )
            st.plotly_chart(fig, use_container_width=True)

        with tab2:
            fig = px.area(
                agg, x="Timestamp", y="Requests/s",
                title="Request Rate (RPS)",
                template="plotly_white", height=380,
            )
            st.plotly_chart(fig, use_container_width=True)

        with tab3:
            agg = agg.copy()
            safe_rps = agg["Requests/s"].replace(0, float("nan"))
            agg["Error Rate %"] = (agg["Failures/s"] / safe_rps * 100).fillna(0)
            fig = px.line(
                agg, x="Timestamp", y="Error Rate %",
                title="Error Rate (%)",
                template="plotly_white", height=380,
            )
            fig.add_hline(y=5, line_dash="dash", line_color="red",
                          annotation_text="5% threshold")
            st.plotly_chart(fig, use_container_width=True)

        with tab4:
            fig = px.area(
                agg, x="Timestamp", y="User count",
                title="Concurrent Virtual Users",
                template="plotly_white", height=380,
            )
            st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("CSV loaded but no Aggregated row found.")
else:
    st.info(
        "No stats-history CSV found for this run.  "
        "Pass `--csv reports/<label>` when running Locust to enable time-series charts."
    )

st.divider()

# ── Per-endpoint breakdown ────────────────────────────────────────────────────
endpoints = data.get("endpoints", {})
if endpoints:
    st.subheader("🔍 Per-Endpoint Breakdown")

    rows = []
    for key, ep in endpoints.items():
        rows.append({
            "Endpoint":    key,
            "Requests":    ep["requests"],
            "Failures":    ep["failures"],
            "Error %":     ep["error_pct"],
            "Median (ms)": ep["median_ms"],
            "Avg (ms)":    ep["avg_ms"],
            "p95 (ms)":    ep["p95_ms"],
            "p99 (ms)":    ep["p99_ms"],
            "Min (ms)":    ep["min_ms"],
            "Max (ms)":    ep["max_ms"],
            "RPS":         ep["rps"],
        })

    df_ep = pd.DataFrame(rows)

    # Highlight rows with high error rate
    def highlight_errors(row):
        colour = "background-color: #ffcccc" if row["Error %"] > 5 else ""
        return [colour] * len(row)

    st.dataframe(
        df_ep.style.apply(highlight_errors, axis=1),
        use_container_width=True,
        hide_index=True,
    )

    col_a, col_b = st.columns(2)
    with col_a:
        fig = px.bar(
            df_ep, x="Endpoint", y="p95 (ms)",
            title="p95 Latency by Endpoint",
            template="plotly_white", height=350,
        )
        st.plotly_chart(fig, use_container_width=True)
    with col_b:
        fig = px.bar(
            df_ep, x="Endpoint", y="Error %",
            title="Error Rate by Endpoint (%)",
            color="Error %",
            color_continuous_scale=["green", "yellow", "red"],
            template="plotly_white", height=350,
        )
        fig.add_hline(y=5, line_dash="dash", line_color="red")
        st.plotly_chart(fig, use_container_width=True)

# ── Compare all runs (load test) ─────────────────────────────────────────────
if compare_enabled and len(summary_files) > 1:
    st.divider()
    st.subheader("📊 Compare All Runs")

    rows = []
    for f in summary_files:
        s = load_json(f)
        rows.append({
            "Run":          f.stem.replace("summary_", ""),
            "Label":        s.get("test_label", "?"),
            "Requests":     s["total_requests"],
            "Failures":     s["total_failures"],
            "Error %":      s["error_rate_pct"],
            "Avg RPS":      s["avg_rps"],
            "Median (ms)":  s["median_ms"],
            "p95 (ms)":     s["p95_ms"],
            "p99 (ms)":     s["p99_ms"],
            "Duration (s)": s["duration_seconds"],
            "Passed":       sum(1 for v in s.get("thresholds", {}).values() if v.get("passed")),
        })

    df_cmp = pd.DataFrame(rows)
    st.dataframe(df_cmp, use_container_width=True, hide_index=True)

    c1, c2 = st.columns(2)
    with c1:
        fig = px.bar(df_cmp, x="Run", y="p95 (ms)",
                     color="Label", title="p95 Latency Across Runs",
                     template="plotly_white", height=350)
        st.plotly_chart(fig, use_container_width=True)
    with c2:
        fig = px.bar(df_cmp, x="Run", y="Error %",
                     color="Label", title="Error Rate Across Runs (%)",
                     template="plotly_white", height=350)
        fig.add_hline(y=5, line_dash="dash", line_color="red",
                      annotation_text="5% limit")
        st.plotly_chart(fig, use_container_width=True)

    fig = px.line(df_cmp, x="Run", y=["Median (ms)", "p95 (ms)", "p99 (ms)"],
                  title="Latency Percentiles Across All Runs",
                  template="plotly_white", height=380)
    st.plotly_chart(fig, use_container_width=True)

# ═══════════════════════════════════════════════════════════════════════════════
# PAGE: CAPACITY PLANNING
# ═══════════════════════════════════════════════════════════════════════════════
elif page == "Capacity Planning":

    if not capacity_files:
        st.title("📐 Capacity Planning")
        st.info(
            "No capacity reports found yet. Run:\n\n"
            "```bash\npython3 capacity_test.py\n```"
        )
        st.stop()

    cap_labels = [_label(f) for f in capacity_files]
    cap_sel    = st.sidebar.selectbox("Select capacity run", cap_labels)
    cap_idx    = cap_labels.index(cap_sel)
    cap_data   = load_json(capacity_files[cap_idx])

    cfg = cap_data.get("config", {})
    st.title("📐 Capacity Planning Results")
    st.caption(
        f"Ran at {cap_data['timestamp']}  |  "
        f"VU range: {cfg.get('vu_step',100)}–{cfg.get('max_vus',3000)} "
        f"(step {cfg.get('vu_step',100)})  |  "
        f"Hold: {cfg.get('hold_seconds',90)}s per step"
    )

    results = cap_data.get("results", [])
    if not results:
        st.warning("No result data in this file.")
        st.stop()

    # ── Summary table ─────────────────────────────────────────────────────────
    st.subheader("📊 Pod Configuration Summary")
    sum_rows = []
    for r in results:
        if "error" in r:
            continue
        sum_rows.append({
            "Pods":          r["pod_count"],
            "Max OK VUs":    r["max_ok_vus"],
            "VUs / Pod":     r["vus_per_pod"],
            "Peak RPS":      r.get("peak_rps", 0),
            "Break at VUs":  r["breaking_vu"] or ">max",
            "Pod Restarts":  r.get("total_pod_restarts", 0),
        })
    if sum_rows:
        df_sum = pd.DataFrame(sum_rows)
        st.dataframe(df_sum, use_container_width=True, hide_index=True)

    st.divider()

    # ── Flatten all steps for cross-pod charts ────────────────────────────────
    all_steps = []
    for r in results:
        if "error" in r:
            continue
        for s in r.get("steps", []):
            all_steps.append({
                "pods":        r["pod_count"],
                "vu_count":    s["vu_count"],
                "error_pct":   s["error_rate_pct"],
                "rps":         s["rps"],
                "median_ms":   s["median_ms"],
                "p95_ms":      s["p95_ms"],
                "p99_ms":      s["p99_ms"],
                "breaking":    s.get("breaking", False),
            })

    if not all_steps:
        st.info("No step data to visualise.")
        st.stop()

    df_steps = pd.DataFrame(all_steps)
    df_steps["pods_label"] = df_steps["pods"].apply(lambda n: f"{n} pods")

    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "Error Rate", "Throughput (RPS)", "p95 Latency", "p99 Latency", "Heatmap"
    ])

    with tab1:
        fig = px.line(
            df_steps, x="vu_count", y="error_pct", color="pods_label",
            title="Error Rate vs VU Count — by Pod Configuration",
            labels={"vu_count": "Virtual Users", "error_pct": "Error Rate (%)",
                    "pods_label": "Pods"},
            template="plotly_white", height=440,
            markers=True,
        )
        fig.add_hline(y=5,  line_dash="dash", line_color="orange",
                      annotation_text="5% threshold")
        fig.add_hline(y=15, line_dash="dash", line_color="red",
                      annotation_text="15% break threshold")
        st.plotly_chart(fig, use_container_width=True)

    with tab2:
        fig = px.line(
            df_steps, x="vu_count", y="rps", color="pods_label",
            title="Throughput (RPS) vs VU Count",
            labels={"vu_count": "Virtual Users", "rps": "Requests / Second",
                    "pods_label": "Pods"},
            template="plotly_white", height=440,
            markers=True,
        )
        st.plotly_chart(fig, use_container_width=True)

    with tab3:
        fig = px.line(
            df_steps, x="vu_count", y="p95_ms", color="pods_label",
            title="p95 Response Time vs VU Count",
            labels={"vu_count": "Virtual Users", "p95_ms": "p95 Latency (ms)",
                    "pods_label": "Pods"},
            template="plotly_white", height=440,
            markers=True,
        )
        fig.add_hline(y=30_000, line_dash="dash", line_color="orange",
                      annotation_text="30 s threshold")
        fig.add_hline(y=60_000, line_dash="dash", line_color="red",
                      annotation_text="60 s break threshold")
        st.plotly_chart(fig, use_container_width=True)

    with tab4:
        fig = px.line(
            df_steps, x="vu_count", y="p99_ms", color="pods_label",
            title="p99 Response Time vs VU Count",
            labels={"vu_count": "Virtual Users", "p99_ms": "p99 Latency (ms)",
                    "pods_label": "Pods"},
            template="plotly_white", height=440,
            markers=True,
        )
        st.plotly_chart(fig, use_container_width=True)

    with tab5:
        # Heatmap: pods (Y) × VU count (X) → error rate (colour)
        df_heat = df_steps.pivot_table(
            index="pods", columns="vu_count", values="error_pct", aggfunc="mean"
        )
        fig = px.imshow(
            df_heat,
            labels={"x": "Virtual Users", "y": "Pods", "color": "Error %"},
            title="Error Rate Heatmap (pods × VUs)",
            color_continuous_scale=[(0, "green"), (0.05, "yellow"),
                                     (0.15, "orange"), (1.0, "red")],
            aspect="auto",
            height=350,
        )
        st.plotly_chart(fig, use_container_width=True)

    # ── Capacity recommendation ────────────────────────────────────────────────
    st.subheader("🎯 Pod Recommendation")
    st.caption("Minimum pods needed to sustain each VU target at <5% error rate")

    ok_df = df_steps[df_steps["error_pct"] < 5.0]
    max_ok_per_pods = ok_df.groupby("pods")["vu_count"].max().reset_index()
    max_ok_per_pods.columns = ["Pods", "Max Sustained VUs (<5% err)"]

    targets = [100, 200, 500, 1_000, 1_500, 2_000, 3_000]
    rec_rows = []
    for t in targets:
        qualifying = max_ok_per_pods[max_ok_per_pods["Max Sustained VUs (<5% err)"] >= t]
        if not qualifying.empty:
            min_pods = int(qualifying["Pods"].min())
            rec_rows.append({"Target VUs": t, "Min Pods Needed": min_pods,
                              "Verdict": "✅ achievable"})
        else:
            max_pods = int(max_ok_per_pods["Pods"].max()) if not max_ok_per_pods.empty else "?"
            rec_rows.append({"Target VUs": t, "Min Pods Needed": f"> {max_pods}",
                              "Verdict": "❌ needs more pods or API keys"})

    st.dataframe(pd.DataFrame(rec_rows), use_container_width=True, hide_index=True)

    st.info(
        "**OpenAI rate limit note:** All pods share one API key, so adding pods beyond "
        "the key's RPM/TPM ceiling won't increase throughput further. "
        "To unlock higher VU counts, add more API keys in `values-azure-hackathon.yaml` → `openaiKeys`."
    )
