"""
Last Light — Evil Eye Room Game 2
No instructions. No oracle. 40 eyes start lit white.
Each eye starts dying one by one — it flickers, then goes dark.
Players must find and press the flickering eye to save it.
"""

import socket
import threading
import time
import random
import queue
import os
import subprocess

try:
    import keyboard
except ImportError:
    keyboard = None

try:
    import winsound
except ImportError:
    winsound = None

# ─── Network constants ───────────────────────────────────────────────────────
UDP_SEND_IP   = "255.255.255.255"
UDP_SEND_PORT = 4626
UDP_RECV_IP   = "0.0.0.0"
UDP_RECV_PORT = 7800

# ─── Game phases ─────────────────────────────────────────────────────────────
PHASES = {
    1: {"threshold": 15,  "countdown": 20.0, "simultaneous": 1},
    2: {"threshold": 30,  "countdown": 12.0, "simultaneous": 2},
    3: {"threshold": 999, "countdown":  7.0, "simultaneous": 3},
}

# ─── Hardware protocol ───────────────────────────────────────────────────────
PASSWORD_ARRAY = [
    35,63,187,69,107,178,92,76,39,69,205,37,223,255,165,231,16,220,99,61,
    25,203,203,155,107,30,92,144,218,194,226,88,196,190,67,195,159,185,209,24,
    163,65,25,172,126,63,224,61,160,80,125,91,239,144,25,141,183,204,171,188,
    255,162,104,225,186,91,232,3,100,208,49,211,37,192,20,99,27,92,147,152,
    86,177,53,153,94,177,200,33,175,195,15,228,247,18,244,150,165,229,212,96,
    84,200,168,191,38,112,171,116,121,186,147,203,30,118,115,159,238,139,60,
    57,235,213,159,198,160,50,97,201,253,242,240,77,102,12,183,235,243,247,75,
    90,13,236,56,133,150,128,138,190,140,13,213,18,7,117,255,45,69,214,179,
    50,28,66,123,239,190,73,142,218,253,5,212,174,152,75,226,226,172,78,35,
    93,250,238,19,32,247,223,89,123,86,138,150,146,214,192,93,152,156,211,67,
    51,195,165,66,10,10,31,1,198,234,135,34,128,208,200,213,169,238,74,221,
    208,104,170,166,36,76,177,196,3,141,167,127,56,177,203,45,107,46,82,217,
    139,168,45,198,6,43,11,57,88,182,84,189,29,35,143,138,171
]

def _checksum(data): return PASSWORD_ARRAY[sum(data) & 0xFF]

def _cmd_pkt(data_id, msg_loc, payload, seq):
    r1, r2 = random.randint(0,127), random.randint(0,127)
    inner = bytes([0x02,0x00,0x00,
                   (data_id>>8)&0xFF, data_id&0xFF,
                   (msg_loc>>8)&0xFF, msg_loc&0xFF,
                   (len(payload)>>8)&0xFF, len(payload)&0xFF]) + payload
    hdr = bytes([0x75, r1, r2, (len(inner)>>8)&0xFF, len(inner)&0xFF])
    pkt = bytearray(hdr + inner)
    pkt[10] = (seq>>8)&0xFF; pkt[11] = seq&0xFF
    pkt.append(_checksum(pkt))
    return bytes(pkt)

def _start_pkt(seq):
    p = bytearray([0x75,random.randint(0,127),random.randint(0,127),
                   0x00,0x08,0x02,0x00,0x00,0x33,0x44,
                   (seq>>8)&0xFF,seq&0xFF,0x00,0x00])
    p.append(_checksum(p)); return bytes(p)

def _end_pkt(seq):
    p = bytearray([0x75,random.randint(0,127),random.randint(0,127),
                   0x00,0x08,0x02,0x00,0x00,0x55,0x66,
                   (seq>>8)&0xFF,seq&0xFF,0x00,0x00])
    p.append(_checksum(p)); return bytes(p)

def _fff0_pkt(seq):
    payload = bytearray()
    for _ in range(4): payload += bytes([0x00, 11])
    return _cmd_pkt(0x8877, 0xFFF0, bytes(payload), seq)

def _gen_frame(leds):
    frame = bytearray(132)
    for (ch, led), (r, g, b) in leds.items():
        i = ch - 1
        frame[led*12+i] = g; frame[led*12+4+i] = r; frame[led*12+8+i] = b
    return frame


# ─── TTS (PowerShell System.Speech — always works on Windows) ────────────────
class TTS:
    def __init__(self):
        self._q = queue.Queue()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while True:
            text = self._q.get()
            if text is None: break
            try:
                ps = (
                    "Add-Type -AssemblyName System.Speech; "
                    "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                    "$s.Rate = -4; "
                    f"$s.Speak([string]'{text}');"
                )
                subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                               capture_output=True)
            except Exception as e:
                print(f"TTS error: {e}")
            self._q.task_done()

    def say(self, text):
        self._q.put(text.replace("'", "''"))


# ─── Main Game ───────────────────────────────────────────────────────────────
class LastLight:
    # 4 walls × 10 buttons (btn 0 = big eye, always lit, not a game button)
    ALL_KEYS = [(w, b) for w in range(1, 5) for b in range(1, 11)]
    TOTAL    = len(ALL_KEYS)  # 40

    def __init__(self):
        self.tts  = TTS()
        self._seq = 0
        self._prev_btn = {}

        self._send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._send_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

        self._recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._recv_sock.bind((UDP_RECV_IP, UDP_RECV_PORT))

        # Shared column color (set by column-pulse thread or ending sequence)
        self._col_color = (0, 0, 0)
        self._col_lock  = threading.Lock()
        self._col_stop  = threading.Event()

        self._lock = threading.Lock()
        self._reset()

    # ── State ─────────────────────────────────────────────────────────────
    def _reset(self):
        with self._lock:
            self.eyes = {
                k: {
                    "lit":            True,
                    "dying":          False,
                    "time_remaining": None,
                    "countdown":      20.0,
                    "saved":          None,   # None=active, True=saved, False=lost
                }
                for k in self.ALL_KEYS
            }
            self.active       = []   # keys currently dying
            self.saved_count  = 0
            self.lost_count   = 0
            self.resolved     = 0
            self.started      = False
            self.game_over    = False
        self._col_stop.set()          # stop old column pulse thread if any
        self._col_stop = threading.Event()
        self._last_hurry  = 0.0
        self._last_whisper= 0.0

    # ── Phase logic ───────────────────────────────────────────────────────
    def _phase(self):
        r = self.resolved
        return 1 if r < 15 else (2 if r < 30 else 3)

    def _phase_cfg(self):
        return PHASES[self._phase()]

    # ── Flicker color ─────────────────────────────────────────────────────
    @staticmethod
    def _flicker(tr, countdown):
        ratio = tr / countdown
        t     = time.time()
        if ratio > 0.75:
            return (255, 255, 255)                         # solid white
        elif ratio > 0.50:
            pulse = (t % 2.0) < 0.10                      # 85%, 0.5Hz
            return (217, 217, 217) if pulse else (255, 255, 255)
        elif ratio > 0.25:
            pulse = (t % 1.0) < 0.15                      # 60%, 1Hz
            return (153, 153, 153) if pulse else (255, 255, 255)
        else:
            pulse = (t % 0.33) < 0.10                     # 30%, 3Hz
            return (76, 76, 76) if pulse else (255, 255, 255)

    # ── Column ────────────────────────────────────────────────────────────
    def _set_col(self, r, g, b):
        with self._col_lock: self._col_color = (r, g, b)

    def _column_pulse_thread(self, stop_event):
        """White pulse whose speed grows as eyes are lost."""
        while not stop_event.is_set():
            lost  = self.lost_count
            speed = max(0.25, 4.0 - lost * 0.085)
            self._set_col(255, 255, 255)
            time.sleep(0.08)
            self._set_col(0, 0, 0)
            stop_event.wait(speed)

    # ── UDP send ──────────────────────────────────────────────────────────
    def _build_frame(self):
        colors = {}
        with self._col_lock:  col = self._col_color
        with self._lock:
            eyes_snap    = {k: dict(v) for k, v in self.eyes.items()}
            game_over_snap = getattr(self, '_instant_fail_flag', False)

        for w in range(1, 5):
            colors[(w, 0)] = (255, 0, 0) if game_over_snap else col

        for (w, b), eye in eyes_snap.items():
            if game_over_snap:
                colors[(w, b)] = (255, 0, 0)   # all red on instant fail
            elif not eye["lit"]:
                colors[(w, b)] = (0, 0, 0)
            elif eye["dying"] and eye["time_remaining"] is not None:
                colors[(w, b)] = self._flicker(eye["time_remaining"], eye["countdown"])
            else:
                colors[(w, b)] = (255, 255, 255)
        return colors

    def _send_frame(self, colors):
        self._seq = (self._seq + 1) & 0xFFFF
        s  = self._seq
        ep = (UDP_SEND_IP, UDP_SEND_PORT)
        try:
            self._send_sock.sendto(_start_pkt(s), ep);         time.sleep(0.008)
            self._send_sock.sendto(_fff0_pkt(s), ep);          time.sleep(0.008)
            self._send_sock.sendto(_cmd_pkt(0x8877, 0x0000, _gen_frame(colors), s), ep); time.sleep(0.008)
            self._send_sock.sendto(_end_pkt(s), ep);           time.sleep(0.008)
        except Exception as e:
            print(f"Send error: {e}")

    def _send_loop(self):
        while True:
            t = time.time()
            self._send_frame(self._build_frame())
            elapsed = time.time() - t
            if elapsed < 0.05: time.sleep(0.05 - elapsed)

    # ── UDP receive ───────────────────────────────────────────────────────
    def _recv_loop(self):
        while True:
            try:
                data, _ = self._recv_sock.recvfrom(2048)
                if len(data) == 687 and data[0] == 0x88:
                    for ch in range(1, 5):
                        base = 2 + (ch-1)*171
                        for led in range(11):
                            pressed = (data[base+1+led] == 0xCC)
                            prev    = self._prev_btn.get((ch, led), False)
                            if pressed and not prev and self.started:
                                self._on_press(ch, led)
                            self._prev_btn[(ch, led)] = pressed
            except Exception:
                pass

    # ── Button press ──────────────────────────────────────────────────────
    def _on_press(self, wall, btn):
        if btn == 0: return
        saved_it = False
        with self._lock:
            if self.game_over: return
            eye = self.eyes.get((wall, btn))
            if eye and eye["dying"] and eye["lit"]:
                eye["lit"] = eye["dying"] = False
                eye["saved"] = True
                if (wall, btn) in self.active: self.active.remove((wall, btn))
                self.saved_count += 1
                self.resolved    += 1
                saved_it = True
            # silently ignore presses on non-dying eyes

        if saved_it:
            if winsound:
                threading.Thread(target=lambda: [winsound.Beep(1200,150), winsound.Beep(1600,300)], daemon=True).start()
            # brief green column flash
            self._set_col(0, 255, 0)
            threading.Timer(0.35, lambda: self._set_col(0, 0, 0)).start()
            if random.random() < 0.35:
                threading.Thread(target=lambda: self.tts.say("Good."), daemon=True).start()

    # ── Core game loop ────────────────────────────────────────────────────
    def _game_loop(self):
        TICK = 0.05

        # 3s intro darkness
        self._set_col(0, 0, 0)
        with self._lock:
            for eye in self.eyes.values(): eye["lit"] = False
        time.sleep(3.0)

        # All eyes snap on together
        with self._lock:
            for eye in self.eyes.values(): eye["lit"] = True
            self.started = True

        # Start column pulse
        threading.Thread(target=self._column_pulse_thread,
                         args=(self._col_stop,), daemon=True).start()

        while True:
            now = time.time()

            with self._lock:
                if self.game_over: break

                cfg     = self._phase_cfg()
                max_sim = cfg["simultaneous"]
                cd      = cfg["countdown"]

                # Pick new dying eyes up to simultaneous limit
                idle_lit = [k for k, v in self.eyes.items()
                            if v["lit"] and not v["dying"]]
                while len(self.active) < max_sim and idle_lit:
                    key = random.choice(idle_lit)
                    idle_lit.remove(key)
                    self.eyes[key]["dying"]          = True
                    self.eyes[key]["time_remaining"] = cd
                    self.eyes[key]["countdown"]      = cd
                    self.active.append(key)

                # Tick each dying eye
                dead_this_tick = []
                for key in list(self.active):
                    eye = self.eyes[key]
                    eye["time_remaining"] -= TICK
                    if eye["time_remaining"] <= 0:
                        eye["lit"] = eye["dying"] = False
                        eye["saved"] = False
                        self.active.remove(key)
                        self.lost_count += 1
                        self.resolved   += 1
                        dead_this_tick.append(key)

                # 3 losses = instant fail
                if self.lost_count >= 3 and not self.game_over:
                    self.game_over = True
                    instant_fail   = True
                else:
                    instant_fail   = False

                all_done = self.resolved >= self.TOTAL or instant_fail
                in_crisis = (len(self.active) >= 3 and
                             any(self.eyes[k]["time_remaining"] < self.eyes[k]["countdown"] * 0.25
                                 for k in self.active))

            # Sound / speech outside the lock
            for _ in dead_this_tick:
                if winsound:
                    threading.Thread(target=lambda: winsound.Beep(180, 700), daemon=True).start()
                self._set_col(255, 0, 0)
                threading.Timer(0.35, lambda: self._set_col(0, 0, 0)).start()
                threading.Thread(target=lambda: self.tts.say("Gone."), daemon=True).start()

            if instant_fail:
                self._instant_fail_flag = True
                break

            # Whisper count every 30s
            if now - self._last_whisper > 30.0:
                with self._lock: lit = sum(1 for v in self.eyes.values() if v["lit"])
                threading.Thread(target=lambda n=lit: self.tts.say(str(n)), daemon=True).start()
                self._last_whisper = now

            # Hurry when 3 critical simultaneously
            if in_crisis and now - self._last_hurry > 6.0:
                threading.Thread(target=lambda: self.tts.say("Hurry."), daemon=True).start()
                self._last_hurry = now

            if all_done:
                with self._lock: self.game_over = True
                break

            time.sleep(TICK)

        self._ending(instant_fail=getattr(self, '_instant_fail_flag', False))

    def _set_instant_fail(self):
        self._instant_fail_flag = True

    # ── Ending sequence ───────────────────────────────────────────────────
    def _ending(self, instant_fail=False):
        self._col_stop.set()  # stop pulse thread
        saved = self.saved_count
        time.sleep(1.0)

        if instant_fail:
            # All lights go solid red immediately
            with self._lock:
                for eye in self.eyes.values():
                    eye["lit"]   = False
                    eye["dying"] = False
            self._set_col(255, 0, 0)
            if winsound:
                threading.Thread(target=lambda: winsound.Beep(150, 2000), daemon=True).start()
            time.sleep(1.5)
            self.tts.say("The dark has won.")
            time.sleep(4.0)
            self._set_col(30, 0, 0)  # dimly red — frozen
            return

        if saved >= 35:
            for _ in range(4):
                self._set_col(0, 255, 0); time.sleep(0.5)
                self._set_col(0,   0, 0); time.sleep(0.3)
            self.tts.say("You protected the light.")
        elif saved >= 20:
            for _ in range(3):
                self._set_col(255, 200, 0); time.sleep(0.6)
                self._set_col(0, 0, 0);     time.sleep(0.4)
            self.tts.say("Some light remains.")
        else:
            self._set_col(255, 0, 0)
            time.sleep(5.0)
            self._set_col(0, 0, 0)
            self.tts.say("The dark has won.")

        time.sleep(3.0)

        # Relight saved eyes (score display)
        with self._lock:
            for key, eye in self.eyes.items():
                if eye["saved"] is True:
                    eye["lit"] = True   # green score
        # Set column to dim white to indicate "frozen / waiting"
        self._set_col(30, 30, 30)

    # ── Terminal UI ───────────────────────────────────────────────────────
    def _ui_loop(self):
        while True:
            with self._lock:
                saved    = self.saved_count
                lost     = self.lost_count
                resolved = self.resolved
                phase    = self._phase()
                active   = list(self.active)
                snap     = {k: dict(v) for k, v in self.eyes.items()}
                over     = self.game_over
                started  = self.started

            os.system("cls" if os.name == "nt" else "clear")
            print("=" * 58)
            print(" LAST LIGHT — OPERATOR CONSOLE ".center(58, "="))
            print("=" * 58)

            if not started:
                print("  Intro darkness... starting soon.")
            else:
                pct = (saved / max(1, resolved)) * 100 if resolved else 0
                print(f"  Phase: {phase}  |  Saved: {saved}  Lost: {lost}  "
                      f"Resolved: {resolved}/{self.TOTAL}  ({pct:.0f}% saved)")
                print(f"  Dying now: {len(active)}")
                for key in active:
                    eye = snap.get(key, {})
                    tr  = eye.get("time_remaining", 0) or 0
                    cd  = eye.get("countdown", 1) or 1
                    bar = int((tr / cd) * 24)
                    print(f"    W{key[0]} B{key[1]:2d}  [{'█'*bar}{'░'*(24-bar)}]  {tr:.1f}s")

                print("\n  Board  (O=lit  *=dying  .=off  E=eye-col):")
                for w in range(1, 5):
                    row = f"  W{w}: [E] "
                    for b in range(1, 11):
                        e = snap.get((w, b), {})
                        if e.get("dying"):   row += "* "
                        elif e.get("lit"):   row += "O "
                        else:                row += ". "
                    print(row)

            if over:
                print(f"\n  ═══ FINAL: {saved}/{self.TOTAL} saved ═══")
            print("\n  Press R to reset.")
            time.sleep(0.25)

    # ── Input ─────────────────────────────────────────────────────────────
    def _input_loop(self):
        if keyboard:
            while True:
                if keyboard.is_pressed("r"):
                    self._do_reset(); time.sleep(1.0)
                time.sleep(0.1)
        else:
            while True:
                cmd = input()
                if cmd.strip().lower() == "r":
                    self._do_reset()

    def _do_reset(self):
        print("Resetting game...")
        self._reset()
        threading.Thread(target=self._game_loop, daemon=True).start()

    # ── Entry point ───────────────────────────────────────────────────────
    def run(self):
        print("Last Light — initializing...")
        threading.Thread(target=self._recv_loop, daemon=True).start()
        threading.Thread(target=self._send_loop, daemon=True).start()
        threading.Thread(target=self._ui_loop,   daemon=True).start()
        threading.Thread(target=self._input_loop, daemon=True).start()
        time.sleep(0.5)
        threading.Thread(target=self._game_loop, daemon=True).start()
        try:
            while True: time.sleep(1)
        except KeyboardInterrupt:
            print("Shutting down.")


if __name__ == "__main__":
    LastLight().run()
