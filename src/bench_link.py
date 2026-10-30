"""Link between the control software and the bench: same serial protocol for the simulated bench and the real one.

The firmware (firmware/firmness_bench/firmness_bench.ino) answers text commands over serial at 115200 baud:

    HOME                 -> OK HOME
    TARE                 -> OK TARE
    CAL <grams>          -> OK SCALE <counts per newton>
    RUN <depth> <speed>  -> t_ms,z_mm,force_n   (header), one CSV line per sample, then END
                            or ERR NO_CONTACT

`SimulatedSerial` speaks exactly this protocol on top of the digital twin, so the application written against it runs
unchanged on the real bench: only the connection object changes (`serial.Serial(port, 115200)` instead).
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from analysis import SAMPLES
from bench import Bench, Operator, Protocol, Sample

REFERENCE_GEL = Sample("gel_reference", 1.0, heterogeneity=0.005)


@dataclass
class SimulatedSerial:
    """File-like object with the subset of the pyserial API the client uses: write, readline, close."""
    sample: Sample | None = field(default_factory=lambda: SAMPLES[4])
    temp_c: float = 20.0
    pos: tuple | None = None          # probe position in the jar (normalised), None = centre
    realtime: bool = False            # stream samples at the real acquisition pace
    rate_sps: int = 80
    seed: int | None = None

    def __post_init__(self):
        self.rng = np.random.default_rng(self.seed)
        self.bench = Bench()
        self.bench.calibrate(rng=self.rng)
        self._out: deque[tuple[float, str]] = deque()
        self._buf = ""
        self.homed = False
        self._say("READY")

    # ---------------------------------------------------------------- pyserial-like API
    def write(self, data: bytes):
        self._buf += data.decode()
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self._handle(line.strip())
        return len(data)

    def readline(self) -> bytes:
        if not self._out:
            return b""
        due, line = self._out.popleft()
        if self.realtime:
            wait = due - time.monotonic()
            if wait > 0:
                time.sleep(wait)
        return (line + "\n").encode()

    @property
    def in_waiting(self):
        return len(self._out)

    def reset_input_buffer(self):
        self._out.clear()

    def close(self):
        self._out.clear()

    # ---------------------------------------------------------------- firmware behaviour
    def _say(self, line, delay=0.0):
        last = self._out[-1][0] if self._out else time.monotonic()
        self._out.append((max(last, time.monotonic()) + delay, line))

    def _handle(self, cmd):
        if cmd == "HOME":
            self.homed = True
            self._say("OK HOME", 0.5)
        elif cmd == "TARE":
            self._say("OK TARE", 0.3)
        elif cmd.startswith("CAL"):
            try:
                float(cmd.split()[1])
            except (IndexError, ValueError):
                self._say("ERR UNKNOWN")
                return
            self.bench.calibrate(rng=self.rng)
            counts_per_n = 21_500.0 / self.bench.cal_gain     # order of magnitude of a 5 kg cell on an HX711
            self._say(f"OK SCALE {counts_per_n:.3f}", 0.5)
        elif cmd.startswith("RUN"):
            try:
                _, depth, speed = cmd.split()
                depth, speed = float(depth), float(speed)
            except ValueError:
                self._say("ERR UNKNOWN")
                return
            if self.sample is None:
                self._say("ERR NO_CONTACT", 2.0)
                return
            prot = Protocol(contact="auto", depth_mm=depth, speed_mm_s=speed, rate_sps=self.rate_sps,
                            averaging_window_mm=0.02, temp_control=True)
            _, z, f = self.bench.measure(self.sample, prot, Operator("auto"), self.rng, temp_c=self.temp_c,
                                         return_curve=True, pos=self.pos)
            self._say("t_ms,z_mm,force_n", 0.6)            # slow approach until the contact threshold
            dt = 1.0 / self.rate_sps
            for zi, fi in zip(z, f):
                self._say(f"{int(1000 * zi / speed)},{zi:.4f},{fi:.4f}", dt)
            self._say("END", 0.4)
        else:
            self._say("ERR UNKNOWN")


class BenchClient:
    """Sends commands and parses answers; works with SimulatedSerial or a pyserial `Serial` object."""

    def __init__(self, conn, timeout_s=60.0):
        self.conn = conn
        self.timeout_s = timeout_s
        self.log: list[str] = []

    @classmethod
    def open_serial(cls, port, baud=115200):
        import serial  # pyserial, only needed for the real bench
        conn = serial.Serial(port, baud, timeout=1)
        time.sleep(2.0)                                     # Arduino resets when the port opens
        client = cls(conn)
        client._drain()
        return client

    def _readline(self, quiet=False):
        t0 = time.monotonic()
        while time.monotonic() - t0 < self.timeout_s:
            raw = self.conn.readline()
            if raw:
                line = raw.decode(errors="replace").strip()
                if not quiet:
                    self.log.append(f"< {line}")
                return line
            time.sleep(0.01)
        raise TimeoutError("no answer from the bench")

    def _drain(self):
        while getattr(self.conn, "in_waiting", 0):
            self.log.append(f"< {self.conn.readline().decode(errors='replace').strip()}")

    def command(self, cmd):
        """HOME, TARE or CAL <g>: returns the answer line."""
        self._drain()
        self.log.append(f"> {cmd}")
        self.conn.write((cmd + "\n").encode())
        return self._readline()

    def run(self, depth_mm, speed_mm_s, on_sample=None):
        """Runs one test; yields nothing, calls on_sample(t_ms, z_mm, force_n) for live display, returns arrays."""
        self._drain()
        cmd = f"RUN {depth_mm:g} {speed_mm_s:g}"
        self.log.append(f"> {cmd}")
        self.conn.write((cmd + "\n").encode())
        head = self._readline()
        if head.startswith("ERR"):
            raise RuntimeError(head)
        t, z, f = [], [], []
        while True:
            line = self._readline(quiet=True)
            if line == "END":
                self.log.append(f"< ({len(t)} samples) END")
                break
            a, b, c = line.split(",")
            t.append(int(a))
            z.append(float(b))
            f.append(float(c))
            if on_sample:
                on_sample(t[-1], z[-1], f[-1])
        return np.array(t), np.array(z), np.array(f)


def firmness(z, f, depth_mm, window_mm=0.02):
    """Firmness value = mean force over the last `window_mm` of travel (same definition as the digital twin)."""
    m = z >= depth_mm - window_mm
    return float(np.mean(f[m])) if m.any() else float("nan")
