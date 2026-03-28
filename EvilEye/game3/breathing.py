"""
Breathing — Evil Eye Room Game 3
The room is alive. It breathes. Calm it. Or die trying.
No instructions. No voice. Only rhythm.
"""

import socket
import threading
import time
import math
import random
import queue
import os

try:
    import keyboard
except ImportError:
    keyboard = None

try:
    import winsound
except ImportError:
    winsound = None

# ─── Network ─────────────────────────────────────────────────────────────────
UDP_SEND_IP   = "255.255.255.255"
UDP_SEND_PORT = 4626
UDP_RECV_IP   = "0.0.0.0"
UDP_RECV_PORT = 7800

# ─── Game constants ───────────────────────────────────────────────────────────
BPM_START            = 20.0
BPM_MIN              = 4.0
BPM_MAX              = 60.0
CALM_START           = 50.0
PANIC_DEATH_SECS     = 30.0   # seconds at calm=0 before death
RHYTHM_TOLERANCE     = 0.15   # phase tolerance window (0-1 scale)

# ─── Hardware protocol ────────────────────────────────────────────────────────
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

def _cksum(data):   return PASSWORD_ARRAY[sum(data) & 0xFF]

def _cmd_pkt(data_id, msg_loc, payload, seq):
    r1, r2 = random.randint(0, 127), random.randint(0, 127)
    inner  = bytes([0x02, 0x00, 0x00,
                    (data_id >> 8) & 0xFF, data_id & 0xFF,
                    (msg_loc >> 8) & 0xFF, msg_loc & 0xFF,
                    (len(payload) >> 8) & 0xFF, len(payload) & 0xFF]) + payload
    hdr    = bytes([0x75, r1, r2, (len(inner) >> 8) & 0xFF, len(inner) & 0xFF])
    pkt    = bytearray(hdr + inner)
    pkt[10] = (seq >> 8) & 0xFF;  pkt[11] = seq & 0xFF
    pkt.append(_cksum(pkt));      return bytes(pkt)

def _start_pkt(seq):
    p = bytearray([0x75, random.randint(0,127), random.randint(0,127),
                   0x00, 0x08, 0x02, 0x00, 0x00, 0x33, 0x44,
                   (seq>>8)&0xFF, seq&0xFF, 0x00, 0x00])
    p.append(_cksum(p)); return bytes(p)

def _end_pkt(seq):
    p = bytearray([0x75, random.randint(0,127), random.randint(0,127),
                   0x00, 0x08, 0x02, 0x00, 0x00, 0x55, 0x66,
                   (seq>>8)&0xFF, seq&0xFF, 0x00, 0x00])
    p.append(_cksum(p)); return bytes(p)

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


# ─── Sound engine (winsound sine approximation via beeps) ────────────────────
class SoundEngine:
    """Non-blocking ambient breathing sounds using winsound Beep."""
    def __init__(self):
        self._lock = threading.Lock()
        self._busy = False

    def _beep_async(self, freq, dur_ms):
        def _do():
            with self._lock:
                self._busy = True
            try:
                if winsound:
                    winsound.Beep(max(37, min(32767, freq)), dur_ms)
            except Exception:
                pass
            with self._lock:
                self._busy = False
        threading.Thread(target=_do, daemon=True).start()

    def inhale(self, bpm):
        """Rising tone — synchronized to inhale duration."""
        cycle_ms = int(60000 / bpm)
        half_ms  = max(100, cycle_ms // 2)
        self._beep_async(180, half_ms)

    def exhale(self, bpm):
        """Falling tone — synchronized to exhale duration."""
        cycle_ms = int(60000 / bpm)
        half_ms  = max(100, cycle_ms // 2)
        self._beep_async(120, half_ms)

    def sync_click(self):
        """Short pleasant tone — in-rhythm press."""
        self._beep_async(880, 60)

    def disturbance(self):
        """Short low buzz — out-of-rhythm press."""
        self._beep_async(90, 120)

    def panic_rumble(self):
        """Long low buzz — panic death start."""
        self._beep_async(60, 2000)

    def is_busy(self):
        with self._lock: return self._busy


# ─── Main Game ───────────────────────────────────────────────────────────────
class Breathing:
    # All interactive eyes: 4 walls × 10 buttons (btn 0 = col indicator only)
    ALL_KEYS = [(w, b) for w in range(1, 5) for b in range(1, 11)]

    def __init__(self):
        self._seq        = 0
        self._prev_btn   = {}
        self._lock       = threading.Lock()
        self.sound       = SoundEngine()

        self._send_sock  = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._send_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

        self._recv_sock  = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._recv_sock.bind((UDP_RECV_IP, UDP_RECV_PORT))

        # Per-eye override colors (flash effects), cleared after duration
        self._eye_override = {}   # (w,b) -> (r,g,b, expire_time)
        self._col_override = None  # (r,g,b, expire_time) or None

        self._init_state()

    # ── State ─────────────────────────────────────────────────────────────
    def _init_state(self):
        with self._lock:
            self.bpm          = BPM_START
            self.calm         = CALM_START
            self.panic_timer  = 0.0
            self.sleeping     = False
            self.dead         = False
            self.started      = False
            self._eye_override.clear()
            self._col_override = None
        self._last_inhale_phase = -1.0   # tracks phase transitions for sound

    # ── Math helpers ──────────────────────────────────────────────────────
    @staticmethod
    def _brightness(t, bpm):
        cycle = 60.0 / bpm
        phase = (t % cycle) / cycle
        return 0.15 + 0.85 * (math.sin(phase * math.pi * 2 - math.pi / 2) + 1) / 2

    @staticmethod
    def _phase(t, bpm):
        cycle = 60.0 / bpm
        return (t % cycle) / cycle

    @staticmethod
    def _eye_color(brightness, calm):
        b = brightness
        if calm > 70:
            return (int(30*b), int(80*b), int(255*b))    # cool blue
        elif calm > 40:
            return (int(150*b), int(100*b), int(200*b))  # violet neutral
        else:
            # Panic: add random shimmer to make it feel unstable
            shake = random.uniform(0.85, 1.0)
            return (int(255*b*shake), int(20*b*shake), int(20*b*shake))  # red

    @staticmethod
    def _is_in_rhythm(t, bpm):
        cycle = 60.0 / bpm
        phase = (t % cycle) / cycle
        return (abs(phase - 0.25) < RHYTHM_TOLERANCE or
                abs(phase - 0.75) < RHYTHM_TOLERANCE)

    def _update_bpm(self):
        target = BPM_MAX - (self.calm / 100.0) * (BPM_MAX - BPM_MIN)
        self.bpm += (target - self.bpm) * 0.05
        self.bpm  = max(BPM_MIN, min(BPM_MAX, self.bpm))

    # ── Button press ──────────────────────────────────────────────────────
    def _on_press(self, wall, btn):
        if btn == 0: return
        now = time.time()
        with self._lock:
            if self.sleeping or self.dead or not self.started: return
            bpm  = self.bpm
            calm = self.calm
            in_r = self._is_in_rhythm(now, bpm)
            if in_r:
                self.calm = min(100.0, calm + 4.0)
                self._eye_override[(wall, btn)] = (255, 255, 255, now + 0.2)
            else:
                self.calm = max(0.0, calm - 8.0)
                self._eye_override[(wall, btn)] = (255, 0, 0, now + 0.2)

        if in_r:
            self.sound.sync_click()
        else:
            self.sound.disturbance()

    # ── UDP send ──────────────────────────────────────────────────────────
    def _build_colors(self, now, bpm, calm):
        brightness = self._brightness(now, bpm)
        base_color = self._eye_color(brightness, calm)
        colors     = {}
        # Column = btn 0 on each wall
        for w in range(1, 5):
            co = self._col_override
            if co and now < co[3]:
                colors[(w, 0)] = co[:3]
            else:
                colors[(w, 0)] = base_color

        for (w, b) in self.ALL_KEYS:
            ov = self._eye_override.get((w, b))
            if ov and now < ov[3]:
                colors[(w, b)] = ov[:3]
            else:
                colors[(w, b)] = base_color
        return colors

    def _send_frame(self, colors):
        self._seq = (self._seq + 1) & 0xFFFF
        s   = self._seq
        ep  = (UDP_SEND_IP, UDP_SEND_PORT)
        try:
            self._send_sock.sendto(_start_pkt(s),  ep);  time.sleep(0.008)
            self._send_sock.sendto(_fff0_pkt(s),   ep);  time.sleep(0.008)
            self._send_sock.sendto(_cmd_pkt(0x8877, 0x0000, _gen_frame(colors), s), ep); time.sleep(0.008)
            self._send_sock.sendto(_end_pkt(s),    ep);  time.sleep(0.008)
        except Exception as e:
            print(f"Send error: {e}")

    def _send_loop(self):
        while True:
            t = time.time()
            with self._lock:
                bpm  = self.bpm
                calm = self.calm
            colors = self._build_colors(t, bpm, calm)
            self._send_frame(colors)
            elapsed = time.time() - t
            if elapsed < 0.05: time.sleep(0.05 - elapsed)

    # ── UDP recv ──────────────────────────────────────────────────────────
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
                            if pressed and not prev:
                                self._on_press(ch, led)
                            self._prev_btn[(ch, led)] = pressed
            except Exception:
                pass

    # ── Breathing sound sync ──────────────────────────────────────────────
    def _maybe_play_breath(self, now):
        """Trigger inhale/exhale sound once per half-cycle."""
        with self._lock:
            bpm = self.bpm
        ph = self._phase(now, bpm)
        # Inhale peak around phase 0.25, exhale around 0.75
        if ph < 0.05 and self._last_inhale_phase > 0.9:
            self.sound.inhale(bpm)
            self._last_inhale_phase = ph
        elif 0.48 < ph < 0.52 and self._last_inhale_phase < 0.3:
            self.sound.exhale(bpm)
            self._last_inhale_phase = ph
        else:
            self._last_inhale_phase = ph

    # ── Core game loop ────────────────────────────────────────────────────
    def _game_loop(self):
        with self._lock: self.started = True
        last_t = time.time()

        while True:
            now = time.time()
            dt  = now - last_t
            last_t = now

            with self._lock:
                if self.sleeping or self.dead: break

                self._update_bpm()

                # Panic timer
                if self.calm <= 0:
                    self.panic_timer += dt
                    if self.panic_timer >= PANIC_DEATH_SECS:
                        self.dead = True
                        break
                else:
                    self.panic_timer = max(0.0, self.panic_timer - dt * 0.5)

                # Victory
                if self.calm >= 100.0:
                    self.sleeping = True
                    break

            self._maybe_play_breath(now)
            time.sleep(0.05)

        with self._lock:
            sleeping = self.sleeping
            dead     = self.dead

        if sleeping:
            self._sleep_sequence()
        elif dead:
            self._death_sequence()

    # ── Sleep sequence (Victory) ──────────────────────────────────────────
    def _sleep_sequence(self):
        print("\n\n  ★  THE ROOM IS ASLEEP  ★\n")
        keys = list(self.ALL_KEYS)
        random.shuffle(keys)

        # Eyes go dark one by one
        for (w, b) in keys:
            expire = time.time() + 0.25
            with self._lock:
                self._eye_override[(w, b)] = (0, 0, 0, expire + 9999)
            time.sleep(0.3)

        time.sleep(1.0)

        # Column goes pure white — still, silent
        with self._lock:
            self._col_override = (255, 255, 255, time.time() + 9999)
        # Final exhale beep
        if winsound:
            winsound.Beep(100, 1200)

        time.sleep(5.0)
        # Frozen — wait for R reset

    # ── Death sequence (Panic) ────────────────────────────────────────────
    def _death_sequence(self):
        print("\n\n  ✖  THE ROOM DIED  ✖\n")

        # 5 seconds of chaotic red flickering
        deadline = time.time() + 5.0
        while time.time() < deadline:
            with self._lock:
                for (w, b) in self.ALL_KEYS:
                    v = random.randint(100, 255)
                    self._eye_override[(w, b)] = (v, 0, 0, time.time() + 0.06)
                self._col_override = (random.randint(150, 255), 0, 0, time.time() + 0.06)
            if winsound:
                threading.Thread(target=lambda: winsound.Beep(random.randint(60, 200), 60),
                                  daemon=True).start()
            time.sleep(0.06)

        # All dark
        with self._lock:
            for (w, b) in self.ALL_KEYS:
                self._eye_override[(w, b)] = (0, 0, 0, time.time() + 9999)
            self._col_override = (0, 0, 0, time.time() + 9999)

        time.sleep(10.0)

        # Auto-restart
        print("  Restarting...")
        self._init_state()
        threading.Thread(target=self._game_loop, daemon=True).start()

    # ── Terminal UI ───────────────────────────────────────────────────────
    def _ui_loop(self):
        BAR = 40
        while True:
            with self._lock:
                bpm         = self.bpm
                calm        = self.calm
                panic_t     = self.panic_timer
                sleeping    = self.sleeping
                dead        = self.dead
                started     = self.started

            os.system("cls" if os.name == "nt" else "clear")
            print("=" * 58)
            print(" BREATHING — OPERATOR CONSOLE ".center(58, "="))
            print("=" * 58)

            if not started:
                print("  Initializing...")
            elif sleeping:
                print("  ★  The room is asleep. Press R to reset.  ★")
            elif dead:
                print("  ✖  The room died. Restarting...")
            else:
                # BPM bar
                bpm_pct = (bpm - BPM_MIN) / (BPM_MAX - BPM_MIN)
                bpm_bar = int(bpm_pct * BAR)
                print(f"  BPM   : {bpm:5.1f}  [{'█'*bpm_bar}{'░'*(BAR-bpm_bar)}]")

                # Calm bar
                calm_pct = calm / 100.0
                calm_bar = int(calm_pct * BAR)
                print(f"  CALM  : {calm:5.1f}  [{'█'*calm_bar}{'░'*(BAR-calm_bar)}]")

                # Panic timer
                if panic_t > 0:
                    p_bar = int((panic_t / PANIC_DEATH_SECS) * BAR)
                    print(f"  PANIC : {panic_t:5.1f}s [{'█'*p_bar}{'░'*(BAR-p_bar)}] (dies at {PANIC_DEATH_SECS}s)")
                else:
                    print(f"  PANIC : safe")

                # State label
                if calm > 70:   state = "CALM  (blue)"
                elif calm > 40: state = "NEUTRAL (violet)"
                else:           state = "PANIC (red)"
                print(f"  STATE : {state}")

            print("\n  Press R to reset.")
            time.sleep(0.2)

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
        print("Resetting...")
        self._init_state()
        threading.Thread(target=self._game_loop, daemon=True).start()

    # ── Entry point ───────────────────────────────────────────────────────
    def run(self):
        print("Breathing — initializing...")
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
    Breathing().run()
