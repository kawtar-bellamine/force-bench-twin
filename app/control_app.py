"""Firmness bench control panel (Streamlit).

    streamlit run app/control_app.py

Drives the bench over its serial protocol: the simulated bench (digital twin) by default, the real Arduino bench by
choosing "Serial port". Live force/depth curve, firmness with its expanded uncertainty, 3 × 3 map of the jar,
CSV export and a one-page HTML report per test.
"""
from __future__ import annotations

import base64
import io
import json
import sys
from datetime import datetime
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd
import streamlit as st

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from analysis import GRID, SAMPLES  # noqa: E402
from bench_link import REFERENCE_GEL, BenchClient, SimulatedSerial, firmness  # noqa: E402

BLUE, RUST, GREEN, INK = "#2C7DA0", "#C4561D", "#3B8C5A", "#1C1F22"
POS_LABELS = ["top left", "top", "top right", "left", "centre", "right", "bottom left", "bottom", "bottom right"]
WINDOW_MM = 0.02

st.set_page_config(page_title="Firmness bench", page_icon="🔩", layout="wide")


def uncertainty_rel():
    """Relative expanded uncertainty (k = 2) of one test, from the latest qualification run."""
    try:
        s = json.loads((ROOT / "results" / "summary.json").read_text())
        return s["incertitude"]["U_rel_pct"] / 100, s["incertitude"]["U_moyenne_3_points_rel_pct"] / 100
    except (OSError, KeyError, ValueError):
        return None, None


U_REL, U3_REL = uncertainty_rel()
ss = st.session_state
ss.setdefault("client", None)
ss.setdefault("mode", None)
ss.setdefault("results", [])
ss.setdefault("curves", {})

# ------------------------------------------------------------------------------------------------ sidebar
with st.sidebar:
    st.header("Connection")
    mode = st.radio("Bench", ["Simulated bench (digital twin)", "Serial port (Arduino)"], label_visibility="collapsed")
    if mode.startswith("Simulated"):
        names = [s.name for s in SAMPLES] + [REFERENCE_GEL.name]
        sample_name = st.selectbox("Sample in the holder", names, index=4)
        temp = st.slider("Sample temperature (°C)", 17.0, 23.0, 20.0, 0.1)
        edge = st.checkbox("Firmer rim (surface drying, +4 % at the rim)", value=True)
        realtime = st.checkbox("Real acquisition pace", value=True)
        if ss.mode != "sim" or ss.client is None:
            ss.client, ss.mode = BenchClient(SimulatedSerial()), "sim"
            ss.client._readline()
        sim = ss.client.conn
        base = REFERENCE_GEL if sample_name == REFERENCE_GEL.name else next(s for s in SAMPLES if s.name == sample_name)
        from dataclasses import replace
        sim.sample = replace(base, edge_effect=0.04 if edge and base is not REFERENCE_GEL else 0.0)
        sim.temp_c, sim.realtime = temp, realtime
        st.caption("Simulated bench: same serial protocol as the Arduino firmware.")
    else:
        port = st.text_input("Port", "/dev/ttyACM0" if sys.platform != "win32" else "COM3")
        sample_name = st.text_input("Sample / batch reference", "BATCH-001")
        temp = st.number_input("Sample temperature measured (°C)", value=20.0, step=0.1)
        if st.button("Connect"):
            try:
                ss.client, ss.mode = BenchClient.open_serial(port), "serial"
                st.success(f"Connected to {port}")
            except Exception as e:  # noqa: BLE001  shown to the operator
                st.error(f"Cannot open {port}: {e}")
        if ss.mode != "serial":
            ss.client = None
    st.header("Protocol")
    depth = st.number_input("Penetration depth (mm)", 1.0, 15.0, 5.0, 0.5)
    speed = st.number_input("Speed (mm/s)", 0.25, 5.0, 2.0, 0.25)
    operator = st.text_input("Operator", "AB")
    pos_label = st.selectbox("Probe position in the jar", POS_LABELS, index=4)
    pos = GRID[POS_LABELS.index(pos_label)]
    if ss.mode == "sim":
        ss.client.conn.pos = pos

# ------------------------------------------------------------------------------------------------ header
st.title("Firmness bench, control panel")
c1, c2, c3 = st.columns(3)
c1.metric("Protocol", f"{depth:g} mm at {speed:g} mm/s")
c1.caption("automatic contact detection, 0.02 N threshold")
c2.metric("Expanded uncertainty (k = 2)", f"± {100 * U_REL:.1f} % per test" if U_REL else "run src/run_all.py")
if U3_REL:
    c2.caption(f"± {100 * U3_REL:.1f} % on the mean of 3 spots (GUM budget, results/qualification_banc.xlsx)")
n_tests = c3.empty()

client: BenchClient | None = ss.client
if client is None:
    st.info("Connect the bench in the sidebar.")
    st.stop()

b1, b2, b3, b4, b5 = st.columns([1, 1, 1.4, 1.2, 1.6])
if b1.button("HOME", width="stretch"):
    st.toast(client.command("HOME"))
if b2.button("TARE", width="stretch"):
    st.toast(client.command("TARE"))
cal_g = b3.number_input("Mass (g)", 10, 5000, 1000, 100, label_visibility="collapsed")
if b4.button("CAL", width="stretch"):
    st.toast(client.command(f"CAL {cal_g}"))
run = b5.button("▶ RUN TEST", type="primary", width="stretch")
run_map = st.button("Run 3 × 3 map of the jar (9 tests)", disabled=ss.mode != "sim")

if ss.mode != "sim":
    st.caption("On the real bench, place the jar on the indexed holder, choose the position in the sidebar and run each test.")
live_col, res_col = st.columns([1.5, 1])
chart = live_col.empty()
value_box = res_col.empty()


def one_test(position_label, position):
    pts = []

    def on_sample(t, z, f):
        pts.append((z, f))
        if len(pts) % 25 == 0:
            chart.line_chart(pd.DataFrame(pts, columns=["depth_mm", "force_n"]).set_index("depth_mm"), color=BLUE,
                             x_label="Depth (mm)", y_label="Force (N)")

    if ss.mode == "sim":
        client.conn.pos = position
    try:
        t, z, f = client.run(depth, speed, on_sample)
    except (RuntimeError, TimeoutError) as e:
        st.error(str(e))
        return None
    chart.line_chart(pd.DataFrame({"depth_mm": z, "force_n": f}).set_index("depth_mm"), color=BLUE,
                     x_label="Depth (mm)", y_label="Force (N)")
    val = firmness(z, f, depth, WINDOW_MM)
    test_id = f"T{len(ss.results) + 1:03d}"
    ss.results.append({"test": test_id, "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "sample": sample_name,
                       "position": position_label, "x": position[0], "y": position[1], "operator": operator,
                       "temp_c": round(float(temp), 2), "depth_mm": depth, "speed_mm_s": speed,
                       "firmness_n": round(val, 4), "U_n_k2": round(val * U_REL, 4) if U_REL else None,
                       "test_time_s": round(t[-1] / 1000, 2)})
    ss.curves[test_id] = pd.DataFrame({"t_ms": t, "z_mm": z, "force_n": f})
    return val


if run:
    one_test(pos_label, pos)
if run_map:
    bar = st.progress(0.0, "3 × 3 map")
    for i, (lab, p) in enumerate(zip(POS_LABELS, GRID)):
        one_test(lab, p)
        bar.progress((i + 1) / 9, f"3 × 3 map: {lab}")
    bar.empty()

if ss.results:
    last = ss.results[-1]
    u = f" ± {last['U_n_k2']:.2f} N" if last["U_n_k2"] else ""
    with value_box.container():
        st.metric(f"Firmness, {last['test']} ({last['sample']}, {last['position']})", f"{last['firmness_n']:.2f} N{u}")
        st.caption(f"penetration {last['test_time_s']:.1f} s, sample at {last['temp_c']} °C, operator {last['operator']}")
    if not run and not run_map and last["test"] in ss.curves:
        cv = ss.curves[last["test"]]
        chart.line_chart(cv.set_index("z_mm")["force_n"], color=BLUE, x_label="Depth (mm)", y_label="Force (N)")

n_tests.metric("Tests this session", len(ss.results))

# ------------------------------------------------------------------------------------------------ session results
df = pd.DataFrame(ss.results)
if not df.empty:
    st.subheader("Session results")
    t1, t2 = st.columns([1.6, 1])
    t1.dataframe(df.drop(columns=["x", "y"]), hide_index=True, width="stretch")
    cur = df[df["sample"] == sample_name]
    if cur["position"].nunique() >= 3:
        m = cur.groupby(["y", "x"])["firmness_n"].mean().unstack().reindex(index=[0.6, 0.0, -0.6], columns=[-0.6, 0.0, 0.6])
        rel = 100 * (m / cur["firmness_n"].mean() - 1)
        fig, ax = plt.subplots(figsize=(3.6, 3.2))
        im = ax.imshow(rel.values, cmap="RdBu_r", vmin=-3, vmax=3)
        for (i, j), v in np.ndenumerate(rel.values):
            if not np.isnan(v):
                ax.text(j, i, f"{m.values[i, j]:.2f} N\n{v:+.1f} %", ha="center", va="center", fontsize=7, color="white" if abs(v) > 2 else INK)
        ax.set_xticks([])
        ax.set_yticks([])
        ax.set_title(f"{sample_name}: map, mean {cur['firmness_n'].mean():.2f} N", fontsize=9)
        fig.colorbar(im, ax=ax, fraction=0.046)
        t2.pyplot(fig)
        plt.close(fig)
    else:
        t2.caption("Test at least 3 positions of the same jar to see its map.")

    d1, d2, d3 = st.columns(3)
    d1.download_button("Results (CSV)", df.to_csv(index=False).encode(), "firmness_results.csv", "text/csv")
    curves = pd.concat([c.assign(test=k) for k, c in ss.curves.items()])
    d2.download_button("Raw curves (CSV)", curves.to_csv(index=False).encode(), "firmness_curves.csv", "text/csv")

    last = ss.results[-1]
    cv = ss.curves[last["test"]]
    fig, ax = plt.subplots(figsize=(6, 3))
    ax.plot(cv.z_mm, cv.force_n, color=BLUE)
    ax.axvspan(depth - WINDOW_MM, depth, color=RUST, alpha=0.3)
    ax.set_xlabel("Depth (mm)")
    ax.set_ylabel("Force (N)")
    ax.spines[["top", "right"]].set_visible(False)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130, bbox_inches="tight")
    plt.close(fig)
    img = base64.b64encode(buf.getvalue()).decode()
    rows = "".join(f"<tr><th>{k}</th><td>{v}</td></tr>" for k, v in last.items() if k not in ("x", "y"))
    report = (f"<html><head><meta charset='utf-8'><title>Firmness test {last['test']}</title><style>"
              f"body{{font-family:sans-serif;max-width:720px;margin:24px auto;color:{INK}}}"
              f"th{{text-align:left;padding:3px 14px 3px 0;color:#555}}h1{{font-size:20px}}</style></head><body>"
              f"<h1>Firmness test {last['test']}: {last['firmness_n']:.2f} N"
              f"{' ± %.2f N (k = 2)' % last['U_n_k2'] if last['U_n_k2'] else ''}</h1>"
              f"<p>Bench: {'digital twin (simulated)' if ss.mode == 'sim' else 'real bench'}. Firmness = mean force over "
              f"the last {WINDOW_MM} mm of penetration, contact detected automatically at 0.02 N.</p>"
              f"<table>{rows}</table><img style='width:100%' src='data:image/png;base64,{img}'></body></html>")
    d3.download_button("Report of the last test (HTML)", report.encode(), f"report_{last['test']}.html", "text/html")

with st.expander("Serial log"):
    st.code("\n".join(client.log[-60:]) or "(empty)")
