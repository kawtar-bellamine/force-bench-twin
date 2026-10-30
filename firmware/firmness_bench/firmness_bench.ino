// Firmness bench firmware: same protocol as the digital twin (src/bench.py), for the real hardware.
// Hardware: 5 kg load cell + HX711, NEMA 17 stepper + driver (STEP/DIR), lead screw, upper limit switch.
// Libraries: HX711 (bogde), AccelStepper.
// Not yet run on hardware: written to be the first thing tested once the bench is built.
//
// Serial commands (115200 baud):
//   HOME                 move up to the limit switch
//   TARE                 zero the load cell (no contact)
//   CAL <grams>          scale factor from a known mass on the cell
//   RUN <depth> <speed>  automatic contact detection, then penetration; streams t_ms,z_mm,force_n
#include <AccelStepper.h>
#include <HX711.h>

const int PIN_DOUT = 4, PIN_SCK = 5, PIN_STEP = 2, PIN_DIR = 3, PIN_LIMIT = 6;
const float STEPS_PER_MM = 1600.0;      // 200 steps x 16 microsteps / 2 mm lead
const float CONTACT_N = 0.02;           // force threshold that defines the surface
const float APPROACH_MM_S = 0.5;        // slow approach so that one sample = little travel
const float G = 9.80665;

HX711 cell;
AccelStepper axis(AccelStepper::DRIVER, PIN_STEP, PIN_DIR);
float scale = 1.0;                      // raw counts per newton, set by CAL

float forceN() { return cell.get_units(1); }
float posMm() { return -axis.currentPosition() / STEPS_PER_MM; }   // positive = downwards

void moveAtSpeed(float mm_s) { axis.setSpeed(-mm_s * STEPS_PER_MM); axis.runSpeed(); }

void home() {
  while (digitalRead(PIN_LIMIT) == HIGH) moveAtSpeed(-5.0);
  axis.setCurrentPosition(0);
  Serial.println("OK HOME");
}

bool approach(float max_mm) {            // go down until the force threshold is crossed
  float start = posMm();
  while (posMm() - start < max_mm) {
    moveAtSpeed(APPROACH_MM_S);
    if (cell.is_ready() && forceN() > CONTACT_N) return true;
  }
  return false;
}

void runTest(float depth, float speed) {
  if (!approach(60.0)) { Serial.println("ERR NO_CONTACT"); return; }
  float z0 = posMm();
  unsigned long t0 = millis();
  Serial.println("t_ms,z_mm,force_n");
  while (posMm() - z0 < depth) {
    moveAtSpeed(speed);
    if (cell.is_ready()) {
      Serial.print(millis() - t0); Serial.print(',');
      Serial.print(posMm() - z0, 4); Serial.print(',');
      Serial.println(forceN(), 4);
    }
  }
  Serial.println("END");
  while (posMm() > z0 - 5.0) moveAtSpeed(-5.0);   // retract 5 mm above the surface
}

void setup() {
  Serial.begin(115200);
  pinMode(PIN_LIMIT, INPUT_PULLUP);
  cell.begin(PIN_DOUT, PIN_SCK);          // HX711 RATE pin high = 80 samples/s (protocol P1)
  axis.setMaxSpeed(20 * STEPS_PER_MM);
  cell.set_scale(scale);
  Serial.println("READY");
}

void loop() {
  if (!Serial.available()) return;
  String cmd = Serial.readStringUntil('\n');
  cmd.trim();
  if (cmd == "HOME") home();
  else if (cmd == "TARE") { cell.tare(20); Serial.println("OK TARE"); }
  else if (cmd.startsWith("CAL")) {
    float grams = cmd.substring(4).toFloat();
    cell.set_scale(1.0);
    float raw = cell.get_units(20);
    scale = raw / (grams / 1000.0 * G);
    cell.set_scale(scale);
    Serial.print("OK SCALE "); Serial.println(scale, 3);
  } else if (cmd.startsWith("RUN")) {
    int sp = cmd.indexOf(' ', 4);
    runTest(cmd.substring(4, sp).toFloat(), cmd.substring(sp + 1).toFloat());
  } else Serial.println("ERR UNKNOWN");
}
