import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from bench_link import BenchClient, SimulatedSerial, firmness  # noqa: E402


def client(**kw):
    c = BenchClient(SimulatedSerial(seed=1, **kw))
    assert c._readline() == "READY"
    return c


def test_commands_follow_the_firmware_protocol():
    c = client()
    assert c.command("HOME") == "OK HOME"
    assert c.command("TARE") == "OK TARE"
    assert c.command("CAL 1000").startswith("OK SCALE")
    assert c.command("FOO") == "ERR UNKNOWN"


def test_run_streams_a_curve_and_gives_the_firmness():
    c = client()
    seen = []
    t, z, f = c.run(5, 2, on_sample=lambda *a: seen.append(a))
    assert len(seen) == len(z) > 100
    assert np.all(np.diff(z) >= 0) and z[-1] == pytest.approx(5, abs=0.01)
    assert 10 < firmness(z, f, 5) < 15          # sample S05


def test_no_sample_gives_no_contact():
    c = client(sample=None)
    with pytest.raises(RuntimeError, match="NO_CONTACT"):
        c.run(5, 2)
