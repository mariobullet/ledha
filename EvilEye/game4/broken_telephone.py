"""
Telefonul Stricat — Evil Eye Room Game 4
Simon Says with 40 physical LED buttons across 4 walls.
Watch the sequence. Reproduce it. Don't break the chain.
No voice. No instructions. Only light and memory.
"""

import socket
import threading
import time
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
    r1, r2 = random.randint(0,127), random.randint(0,127)
    inner  = bytes([0x02,0x00,0x00,
                    (data_id>>8)&0xFF,  data_id&0xFF,
                    (msg_loc>>8)&0xFF,  msg_loc&0xFF,
                    (len(payload)>>8)&0xFF, len(payload)&0xFF]) + payload
    hdr    = bytes([0x75,r1,r2,(len(inner)>>8)&0xFF,len(inner)&0xFF])
    pkt    = bytearray(hdr + inner)
    pkt[10] = (seq>>8)&0xFF;  pkt[11] = seq&0xFF
    pkt.append(_cksum(pkt));  return bytes(pkt)

def _start_pkt(seq):
    p = bytearray([0x75,random.randint(0,127),random.randint(0,127),
                   0x00,0x08,0x02,0x00,0x00,0x33,0x44,
                   (seq>>8)&0xFF,seq&0xFF,0x00,0x00])
    p.append(_cksum(p)); return bytes(p)

def _end_pkt(seq):
    p = bytearray([0x75,random.randint(0,127),random.randint(0,127),
                   0x00,0x08,0x02,0x00,0x00,0x55,0x66,
                   (seq>>8)&0xFF,seq&0xFF,0x00,0x00])
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


# ─── Game ─────────────────────────────────────────────────────────────────────
class BrokenTelephone:
    # 4 walls × 10 buttons (btn 0 = big eye indicator, never a target)
    ALL_KEYS = [(w, b) for w in range(1, 5) for b in range(1, 11)]

    # Wall rotation: demo shown on W, player presses on WALL_SHIFT[W]
    WALL_SHIFT = {1: 2, 2: 3, 3: 4, 4: 1}

    # Demo display speed per round depth
    SPEED_TABLE = [
        (5,  0.80),   # rounds 1-4:  slow
        (10, 0.55),   # rounds 5-9:  medium
        (15, 0.30),   # rounds 10-14: fast
        (999, 0.15),  # rounds 15+:  very fast
    ]

    # Input timeout per button (generous)
    INPUT_TIMEOUT = 10.0

    def __init__(self):
        self._seq        = 0
        self._prev_btn   = {}
        self._lock       = threading.Lock()

        # LED state dict — what the send thread renders
        self._leds       = {}   # (w,b) -> (r,g,b)
        self._col_color  = (80, 80, 80)   # big eye column color
        self._leds_lock  = threading.Lock()

        # Input queue — filled by recv thread, drained by game thread
        self._input_q    = queue.Queue()

        # Game state (read by UI)
        self.sequence    = []    # demo sequence (what's shown)
        self.expected    = []    # shifted sequence (what player must press)
        self.round       = 0
        self.phase       = "IDLE"
        self.best        = 0
        self.alive       = False
        self.reset_flag  = False

        self._send_sock  = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._send_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

        self._recv_sock  = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._recv_sock.bind((UDP_RECV_IP, UDP_RECV_PORT))

        self._clear_all()

    # ── LED helpers ───────────────────────────────────────────────────────
    def _clear_all(self, color=(0, 0, 0)):
        with self._leds_lock:
            for w in range(1, 5):
                for b in range(11):
                    self._leds[(w, b)] = color

    def _set_led(self, wall, btn, color):
        with self._leds_lock:
            self._leds[(wall, btn)] = color

    def _set_col(self, color):
        with self._leds_lock:
            for w in range(1, 5):
                self._leds[(w, 0)] = color

    def _pulse(self, wall, btn, color, duration):
        """Light one LED for `duration` seconds then off."""
        self._set_led(wall, btn, color)
        time.sleep(duration)
        self._set_led(wall, btn, (0, 0, 0))

    def _flush_input_queue(self, wait_secs=0.6):
        """Drain all pending presses AND wait so no stale presses leak into INPUT."""
        deadline = time.time() + wait_secs
        while time.time() < deadline:
            try:
                self._input_q.get_nowait()
            except queue.Empty:
                time.sleep(0.05)

    # ── Speed ─────────────────────────────────────────────────────────────
    def _demo_speed(self, depth):
        for threshold, speed in self.SPEED_TABLE:
            if depth < threshold:
                return speed
        return 0.14

    # ── Sounds ────────────────────────────────────────────────────────────
    @staticmethod
    def _beep(freq, ms):
        if winsound:
            threading.Thread(target=lambda: winsound.Beep(max(37, freq), ms),
                             daemon=True).start()

    def _sound_show(self, step_idx):
        """Rising pitch as sequence progresses during demo."""
        freq = 400 + step_idx * 40
        self._beep(min(2000, freq), 80)

    def _sound_correct(self):
        self._beep(1200, 60)

    def _sound_wrong(self):
        self._beep(150, 500)

    def _sound_level_up(self):
        self._beep(880, 80)
        time.sleep(0.1)
        self._beep(1100, 80)
        time.sleep(0.1)
        self._beep(1400, 200)

    def _sound_victory(self):
        for f in [600, 800, 1000, 1300, 1600]:
            self._beep(f, 120)
            time.sleep(0.13)

    # ── UDP send ──────────────────────────────────────────────────────────
    def _build_frame(self):
        with self._leds_lock:
            return dict(self._leds)

    def _send_colors(self, colors):
        self._seq = (self._seq + 1) & 0xFFFF
        s  = self._seq
        ep = (UDP_SEND_IP, UDP_SEND_PORT)
        try:
            self._send_sock.sendto(_start_pkt(s),  ep); time.sleep(0.008)
            self._send_sock.sendto(_fff0_pkt(s),   ep); time.sleep(0.008)
            self._send_sock.sendto(_cmd_pkt(0x8877, 0x0000, _gen_frame(colors), s), ep); time.sleep(0.008)
            self._send_sock.sendto(_end_pkt(s),    ep); time.sleep(0.008)
        except Exception as e:
            print(f"Send error: {e}")

    def _send_loop(self):
        while True:
            t = time.time()
            self._send_colors(self._build_frame())
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
                            if pressed and not prev and led != 0:
                                self._input_q.put((ch, led))
                            self._prev_btn[(ch, led)] = pressed
            except Exception:
                pass

    # ── Core game loop ────────────────────────────────────────────────────
    def _game_loop(self):
        self.alive    = True
        self.sequence = []
        self.round    = 0

        # Brief intro — all white flash
        self._clear_all((255, 255, 255))
        self._set_col((255, 255, 255))
        time.sleep(0.6)
        self._clear_all()
        time.sleep(0.8)

        while self.alive:
            if self.reset_flag:
                break

            # ── Add one step to demo sequence ────────────────────────
            new_key = random.choice(self.ALL_KEYS)
            self.sequence.append(new_key)
            # Expected = same button, next wall
            w, b = new_key
            self.expected = [(self.WALL_SHIFT[sw], sb) for sw, sb in self.sequence]
            self.round    = len(self.sequence)
            speed         = self._demo_speed(self.round)

            # ── PHASE: DEMO ───────────────────────────────────────────
            self.phase = "DEMO"
            self._set_col((0, 80, 255))   # blue col = watch
            self._clear_all()
            time.sleep(0.4)

            for i, (w, b) in enumerate(self.sequence):
                if self.reset_flag: return
                self._set_led(w, b, (255, 200, 0))  # yellow = demo
                self._sound_show(i)
                time.sleep(speed * 0.55)
                self._set_led(w, b, (0, 0, 0))
                time.sleep(speed * 0.45)

            time.sleep(0.4)

            # ── PHASE: INPUT ──────────────────────────────────────────
            self.phase = "INPUT"
            self._set_col((80, 0, 255))   # purple col = your turn
            self._clear_all()

            # Flush stale presses + wait so no ghost input leaks in
            self._flush_input_queue(wait_secs=0.6)

            success = True
            for expected_key in self.expected:
                if self.reset_flag: return

                # Wait for press
                try:
                    pressed = self._input_q.get(timeout=self.INPUT_TIMEOUT)
                except queue.Empty:
                    success = False
                    self._set_led(*expected_key, (255, 0, 0))
                    break

                if pressed == expected_key:
                    # ✅ Correct — light green on pressed wall
                    self._set_led(*pressed, (0, 255, 80))
                    self._sound_correct()
                    time.sleep(0.18)
                    self._set_led(*pressed, (0, 0, 0))
                else:
                    # ❌ Wrong
                    self._set_led(*pressed, (255, 0, 0))
                    self._sound_wrong()
                    success = False
                    break

            # ── PHASE: FEEDBACK ───────────────────────────────────────
            if success:
                self.phase = "SUCCESS"
                self.best  = max(self.best, self.round)
                self._success_anim()
            else:
                self.phase = "FAIL"
                self._fail_anim()
                self.sequence = []
                self.expected = []
                self.round    = 0

            time.sleep(0.8)

    # ── Animations ────────────────────────────────────────────────────────
    def _success_anim(self):
        """Green ripple outward from each wall."""
        self._sound_level_up()
        self._set_col((0, 255, 80))
        # Light each wall sequentially
        for w in range(1, 5):
            for b in range(1, 11):
                self._set_led(w, b, (0, 255, 80))
            time.sleep(0.08)
        time.sleep(0.4)
        self._clear_all()
        self._set_col((0, 0, 0))
        time.sleep(0.3)

    def _fail_anim(self):
        """All LEDs flash red 3 times."""
        self._sound_wrong()
        for _ in range(3):
            self._clear_all((200, 0, 0))
            self._set_col((200, 0, 0))
            time.sleep(0.2)
            self._clear_all()
            self._set_col((0, 0, 0))
            time.sleep(0.15)

    # ── Terminal UI ───────────────────────────────────────────────────────
    def _ui_loop(self):
        while True:
            os.system("cls" if os.name == "nt" else "clear")
            print("=" * 58)
            print(" TELEFONUL STRICAT — OPERATOR CONSOLE ".center(58, "="))
            print("=" * 58)
            seq  = list(self.sequence)
            rnd  = self.round
            ph   = self.phase
            best = self.best

            print(f"  Round    : {self.round}")
            print(f"  Best     : {self.best}")
            print(f"  Phase    : {self.phase}")
            print(f"  Sequence : {len(self.sequence)} steps")
            print(f"  Wall shift: W1→W2  W2→W3  W3→W4  W4→W1")

            seq = list(self.sequence)
            exp = list(self.expected) if hasattr(self, 'expected') else []
            if seq:
                shown = seq[-6:]
                exps  = exp[-6:]
                demo_str = "  → ".join(f"W{w}B{b}" for w, b in shown)
                exp_str  = "  → ".join(f"W{w}B{b}" for w, b in exps)
                prefix   = "..." if len(seq) > 6 else "   "
                print(f"  Demo     : {prefix} {demo_str}")
                print(f"  Expect   : {prefix} {exp_str}")

            if self.round > 0:
                spd = self._demo_speed(self.round)
                print(f"  Speed    : {spd:.2f}s/step")

            print("\n  Press R to reset.")
            time.sleep(0.5)

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
        self.alive      = False
        self.reset_flag = True
        self.phase      = "IDLE"
        # Drain input queue
        while not self._input_q.empty():
            try: self._input_q.get_nowait()
            except: pass
        self._clear_all()
        self._set_col((0, 0, 0))
        time.sleep(0.3)
        self.reset_flag = False
        threading.Thread(target=self._game_loop, daemon=True).start()

    # ── Entry point ───────────────────────────────────────────────────────
    def run(self):
        print("Telefonul Stricat — initializing...")
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
    BrokenTelephone().run()
