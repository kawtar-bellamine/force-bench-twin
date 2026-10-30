import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import analysis as A  # noqa: E402
from bench import Bench, Protocol  # noqa: E402


def synthetic(sd_part, sd_op, sd_rep, p=10, o=3, r=3, seed=0):
    rng = np.random.default_rng(seed)
    parts, ops = rng.normal(0, sd_part, p), rng.normal(0, sd_op, o)
    rows = [{"sample": f"P{i}", "operator": f"O{j}", "trial": t, "firmness_n": 10 + parts[i] + ops[j] + rng.normal(0, sd_rep)}
            for i in range(p) for j in range(o) for t in range(r)]
    return pd.DataFrame(rows)


def test_grr_recovers_known_variance_components():
    df = synthetic(2.0, 0.0, 0.2, p=60, o=3, r=5)
    res = A.gage_rr(df)
    expected = 100 * 0.2 / np.sqrt(0.2 ** 2 + 2.0 ** 2)
    assert abs(res["grr_pct"] - expected) < 2.5
    assert res["reproductibilite_pct"] < 3


def test_operator_bias_shows_up_as_reproducibility():
    res = A.gage_rr(synthetic(2.0, 0.5, 0.1, p=30, o=6, r=3))
    assert res["reproductibilite_pct"] > res["repetabilite_pct"]


def test_perfect_gauge_has_high_ndc():
    assert A.gage_rr(synthetic(2.0, 0.0, 0.01))["ndc"] > 50


def test_calibration_corrects_gain_error():
    b = Bench()
    b.cell.gain_error = 0.03
    cal = b.calibrate(rng=np.random.default_rng(1))
    assert cal["linearity_err_pct_fs"] < 0.2


def test_auto_contact_removes_operator_effect():
    rng = np.random.default_rng(3)
    b = Bench()
    b.calibrate(rng=rng)
    man = A.gage_rr(A.run_study(b, Protocol(contact="manual", temp_control=True), rng))
    auto = A.gage_rr(A.run_study(b, Protocol(contact="auto", temp_control=True), rng))
    assert auto["reproductibilite_pct"] < man["reproductibilite_pct"]
    assert auto["grr_pct"] < man["grr_pct"]


def test_drift_is_detected_after_it_starts():
    rng = np.random.default_rng(5)
    b = Bench()
    b.calibrate(rng=rng)
    _, s = A.drift_monitoring(b, Protocol(contact="auto", temp_control=True), rng)
    assert s["alarme_jour"] is not None and s["alarme_jour"] >= s["debut_derive_jour"]


def test_mapping_finds_the_rim_pattern_and_better_sampling_plans():
    rng = np.random.default_rng(11)
    b = Bench()
    b.calibrate(rng=rng)
    prot = Protocol(contact="auto", temp_control=True, speed_mm_s=2.0, averaging_window_mm=0.02)
    _, comp, plans = A.mapping_study(b, prot, rng, edge_effect=0.06)
    assert comp["pattern_p_value"] < 0.05
    p = plans.set_index("plan")
    assert p.loc["centre, 1 essai", "bias_pct"] < -1          # centre misses the firmer rim
    assert abs(p.loc["grille complète, 9 points", "bias_pct"]) < 0.5
    assert p.loc["grille complète, 9 points", "rms_error_pct"] < p.loc["centre, 3 essais", "rms_error_pct"]


def test_uncertainty_budget_agrees_with_monte_carlo():
    import uncertainty as U
    rng = np.random.default_rng(13)
    b = Bench()
    b.calibrate(rng=rng)
    prot = Protocol(contact="auto", temp_control=True, speed_mm_s=2.0, averaging_window_mm=0.02)
    bud = U.budget(b, prot, rng, drift_rel_at_alarm=0.0045)
    assert abs(sum(r["contribution_pct"] for r in bud["rows"]) - 100) < 1e-6
    mc = U.monte_carlo(b, prot, rng, bud, n=1500)
    assert 0.7 < mc["half_width_95_n"] / bud["U_n"] < 1.3     # GUM and Monte Carlo within 30 %
