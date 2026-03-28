import socket
import threading
import time
import random
import queue
import os
import subprocess
import sys

try:
    import keyboard
except ImportError:
    print("Warning: 'keyboard' library not found. Auto-reset with 'R' will not work. Install with pip install keyboard.")
    keyboard = None

try:
    import winsound
except ImportError:
    winsound = None

UDP_SEND_IP = "255.255.255.255"
UDP_SEND_PORT = 4626
UDP_RECV_IP = "0.0.0.0"
UDP_RECV_PORT = 7800

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

def calc_checksum(data: bytes) -> int:
    return PASSWORD_ARRAY[sum(data) & 0xFF]

def build_command_packet(data_id: int, msg_loc: int, payload: bytes, seq: int) -> bytes:
    rand1 = random.randint(0, 127)
    rand2 = random.randint(0, 127)

    internal = bytes([
        0x02,
        0x00,                               
        0x00,                               
        (data_id >> 8) & 0xFF, data_id & 0xFF,
        (msg_loc >> 8) & 0xFF, msg_loc & 0xFF,
        (len(payload) >> 8) & 0xFF, len(payload) & 0xFF,
    ]) + payload

    hdr = bytes([
        0x75, rand1, rand2,
        (len(internal) >> 8) & 0xFF, len(internal) & 0xFF,
    ])
    pkt = bytearray(hdr + internal)
    pkt[10] = (seq >> 8) & 0xFF
    pkt[11] = seq & 0xFF
    pkt.append(calc_checksum(pkt))
    return bytes(pkt)

def build_start_packet(seq: int) -> bytes:
    pkt = bytearray([
        0x75,
        random.randint(0, 127), random.randint(0, 127),
        0x00, 0x08,
        0x02, 0x00, 0x00,
        0x33, 0x44,
        (seq >> 8) & 0xFF, seq & 0xFF,
        0x00, 0x00,
    ])
    pkt.append(calc_checksum(pkt))
    return bytes(pkt)

def build_end_packet(seq: int) -> bytes:
    pkt = bytearray([
        0x75,
        random.randint(0, 127), random.randint(0, 127),
        0x00, 0x08,
        0x02, 0x00, 0x00,
        0x55, 0x66,
        (seq >> 8) & 0xFF, seq & 0xFF,
        0x00, 0x00,
    ])
    pkt.append(calc_checksum(pkt))
    return bytes(pkt)

def build_fff0_packet(seq: int) -> bytes:
    payload = bytearray()
    for _ in range(4): # 4 channels
        payload += bytes([(11 >> 8) & 0xFF, 11 & 0xFF]) # 11 LEDs
    pkt = build_command_packet(0x8877, 0xFFF0, bytes(payload), seq)
    return pkt

def gen_frame(leds):
    frame = bytearray(132)
    for (ch, led), (r, g, b) in leds.items():
        idx = ch - 1
        frame[led * 12 + idx] = g
        frame[led * 12 + 4 + idx] = r
        frame[led * 12 + 8 + idx] = b
    return frame

class TTSManager:
    """Uses Windows PowerShell System.Speech for rock-solid TTS on any Windows machine."""
    def __init__(self):
        self.queue = queue.Queue()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        while True:
            text = self.queue.get()
            if text is None:
                break
            try:
                ps_script = (
                    "Add-Type -AssemblyName System.Speech; "
                    "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                    "$s.Rate = -2; "
                    f"$s.Speak([string]'{text}');"
                )
                subprocess.run(
                    ["powershell", "-NoProfile", "-Command", ps_script],
                    capture_output=True
                )
            except Exception as e:
                print(f"TTS Error: {e}")
            self.queue.task_done()

    def say(self, text):
        # Escape single quotes for PowerShell
        safe = text.replace("'", "''")
        self.queue.put(safe)

class EvilEyeGame:
    def __init__(self):
        self.tts = TTSManager()
        
        self.send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Enable broadcast if using 255.255.255.255
        self.send_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        
        self.listen_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.listen_sock.bind((UDP_RECV_IP, UDP_RECV_PORT))
        
        self.state_lock = threading.Lock()
        self.prev_btn_state = {}
        
        # Mappings for audio hints
        self.wall_names = {
            1: "Left wall.",
            2: "Wall behind you.",
            3: "Right wall.",
            4: "Wall in front."
        }
        
        self.btn_names = {
            0: "",             # eye - not used as target
            1: "Row one, column one.",
            2: "Row one, column two.",
            3: "Row one, column three.",
            4: "Row one, column four.",
            5: "Row one, column five.",
            6: "Row two, column one.",
            7: "Row two, column two.",
            8: "Row two, column three.",
            9: "Row two, column four.",
            10: "Row two, column five."
        }

        self.reset_game(silent=True)
        
    def reset_game(self, silent=False):
        with self.state_lock:
            self.game_state = {
                "eyes": {(wall, btn): True for wall in range(1, 5) for btn in range(1, 11)},
                "current_targets": [],
                "round_level": 1,
                "mistakes": 0,
                "consecutive_correct": 0,
                "column_pulsing_red": False,
                "game_over": False,
                "victory": False,
                "flash_red_until": 0,
                "silence_until": 0,
                "victory_anim_start": 0
            }
        
        if not silent:
            self.tts.say("Round reset.")
        
        time.sleep(0.5)
        self.pick_new_target()

    def get_instruction_for_target(self, target):
        wall, btn = target
        return f"Wall {wall}, button {btn}."

    def pick_new_target(self):
        with self.state_lock:
            if self.game_state["game_over"] or self.game_state["victory"]:
                return

            level = self.game_state["round_level"]

            if level > 5:
                pass
            else:
                lit_eyes = [k for k, v in self.game_state["eyes"].items() if v]
                if len(lit_eyes) < level:
                    self.game_state["current_targets"] = []
                else:
                    random.shuffle(lit_eyes)
                    self.game_state["current_targets"] = lit_eyes[:level]

            silence_until = self.game_state["silence_until"]
            targets = list(self.game_state["current_targets"])
            is_victory = level > 5 or not targets

        if is_victory:
            self.trigger_victory()
            return

        if time.time() > silence_until:
            # Announce level, then read ALL targets in one go
            self.tts.say(f"Level {level}.")
            for i, t in enumerate(targets):
                instruction = self.get_instruction_for_target(t)
                if i > 0:
                    self.tts.say("Then.")
                self.tts.say(instruction)

    def trigger_victory(self):
        with self.state_lock:
            self.game_state["victory"] = True
            self.game_state["victory_anim_start"] = time.time()
        self.tts.say("It is done. You may leave.")
        
    def trigger_game_over(self):
        with self.state_lock:
            self.game_state["game_over"] = True
            self.game_state["eyes"] = {(w, b): False for w in range(1, 5) for b in range(11)} # Extinguish all conceptually for game over
        self.tts.say("You have failed.")
        if winsound:
            threading.Thread(target=lambda: winsound.Beep(500, 1500), daemon=True).start()
        
        # Auto-reset after 30 seconds
        threading.Timer(30.0, self.reset_game).start()

    def play_chime(self):
        if winsound:
            threading.Thread(target=lambda: [winsound.Beep(1200, 200), winsound.Beep(1500, 400)], daemon=True).start()

    def play_error_sound(self):
        if winsound:
            threading.Thread(target=lambda: [winsound.Beep(300, 300), winsound.Beep(250, 400)], daemon=True).start()

    def handle_button_press(self, wall, btn):
        if btn == 0:
            return # Ignore Big Eye logic completely
        
        with self.state_lock:
            if self.game_state["game_over"] or self.game_state["victory"] or not self.game_state["current_targets"]:
                return
            
            if not self.game_state["eyes"].get((wall, btn), False):
                return

            if self.game_state["current_targets"][0] == (wall, btn):
                # Correct!
                self.game_state["eyes"][(wall, btn)] = False
                self.game_state["current_targets"].pop(0)
                
                self.game_state["consecutive_correct"] += 1
                if self.game_state["consecutive_correct"] >= 5:
                    self.game_state["mistakes"] = 0
                    self.game_state["consecutive_correct"] = 0
                    self.game_state["column_pulsing_red"] = False
                    self.tts.say("Good. Keep going.")
                
                self.play_chime()
                
                # If round sequence is not completely finished
                if self.game_state["current_targets"]:
                    next_target = self.game_state["current_targets"][0]
                    instruction = self.get_instruction_for_target(next_target)
                    self.tts.say("Next.")
                    self.tts.say(instruction)
                else:
                    # Round sequence completed successfully! Move to next Round Level
                    self.game_state["round_level"] += 1
                    # Give them 2 secs before throwing the harder level at them
                    threading.Timer(2.0, self.pick_new_target).start()
            else:
                # Incorrect!
                self.game_state["mistakes"] += 1
                self.game_state["consecutive_correct"] = 0
                self.game_state["flash_red_until"] = time.time() + 1.0
                self.play_error_sound()
                
                m = self.game_state["mistakes"]
                if m == 1:
                    threading.Timer(1.1, self.repeat_instruction).start()
                elif m == 2:
                    self.game_state["silence_until"] = time.time() + 15.0
                    self.tts.say("Silence.")
                    threading.Timer(15.1, self.repeat_instruction).start()
                elif m == 3:
                    self.game_state["column_pulsing_red"] = True
                    threading.Timer(1.1, self.repeat_instruction).start()
                else:
                    self.trigger_game_over()

    def repeat_instruction(self):
        with self.state_lock:
            if self.game_state["game_over"] or self.game_state["victory"]: return
            target = self.game_state["current_targets"][0] if self.game_state["current_targets"] else None
            silenced = time.time() < self.game_state["silence_until"]
            
        if not silenced and target:
            self.tts.say("That was wrong. Try again.")
            self.tts.say(self.get_instruction_for_target(target))

    def listen_loop(self):
        while True:
            try:
                data, _ = self.listen_sock.recvfrom(2048)
                if len(data) == 687 and data[0] == 0x88:
                    for ch in range(1, 5):
                        base = 2 + (ch - 1) * 171
                        for led in range(11):
                            is_pressed = (data[base + 1 + led] == 0xCC)
                            prev = self.prev_btn_state.get((ch, led), False)
                            
                            if is_pressed and not prev:
                                self.handle_button_press(ch, led)
                            
                            self.prev_btn_state[(ch, led)] = is_pressed
            except Exception as e:
                pass

    def _send_udp_sequence(self, leds: dict):
        base_color_dict = leds
        
        self.seq = (getattr(self, 'seq', 0) + 1) & 0xFFFF
        seq = self.seq
        
        p1 = build_start_packet(seq)
        self.send_sock.sendto(p1, (UDP_SEND_IP, UDP_SEND_PORT))
        time.sleep(0.008)
        
        p2 = build_fff0_packet(seq)
        self.send_sock.sendto(p2, (UDP_SEND_IP, UDP_SEND_PORT))
        time.sleep(0.008)
        
        fdata = gen_frame(base_color_dict)
        p3 = build_command_packet(0x8877, 0x0000, fdata, seq)
        self.send_sock.sendto(p3, (UDP_SEND_IP, UDP_SEND_PORT))
        time.sleep(0.008)
        
        p4 = build_end_packet(seq)
        self.send_sock.sendto(p4, (UDP_SEND_IP, UDP_SEND_PORT))
        time.sleep(0.008)

    def generate_current_colors(self):
        # Default Lit Color
        LIT_COLOR = (200, 240, 255) # white-blueish glow
        OFF_COLOR = (0, 0, 0)
        RED_COLOR = (255, 0, 0)
        
        with self.state_lock:
            state = self.game_state.copy()
            
        colors = {}
        now = time.time()
        
        flashing_red = now < state["flash_red_until"]
        pulsing_red_idx = int(now * 2) % 2 if state["column_pulsing_red"] else 0
        
        if state["game_over"]:
            for w in range(1, 5):
                colors[(w, 0)] = RED_COLOR
                for b in range(1, 11):
                    colors[(w, b)] = RED_COLOR
            return colors
            
        if state["victory"]:
            elapsed = now - state["victory_anim_start"]
            if elapsed < 3.0:
                for w in range(1, 5):
                    colors[(w, 0)] = OFF_COLOR
                    for b in range(1, 11):
                        colors[(w, b)] = OFF_COLOR
            elif elapsed < 8.0:
                progress = (elapsed - 3.0) / 5.0
                r = 255
                g = int(255 * progress)
                b = int(255 * progress)
                for w in range(1, 5):
                    colors[(w, 0)] = (r, g, b)
                    for b_idx in range(1, 11):
                        colors[(w, b_idx)] = (r, g, b)
            else:
                for w in range(1, 5):
                    colors[(w, 0)] = (100, 100, 100)
                    for b in range(1, 11):
                        colors[(w, b)] = (100, 100, 100) # low white brightness
            return colors

        # Normal Game State
        for w in range(1, 5):
            # The big eye is always lit during normal play, or flashing red on error
            colors[(w, 0)] = RED_COLOR if flashing_red else LIT_COLOR
            
            for b in range(1, 11):
                if state["eyes"][(w, b)]:
                    if flashing_red:
                        colors[(w, b)] = RED_COLOR
                    else:
                        colors[(w, b)] = LIT_COLOR
                        if state["column_pulsing_red"] and pulsing_red_idx == 1:
                            colors[(w, b)] = (255, 100, 100)
                else:
                    colors[(w, b)] = OFF_COLOR
                    
        return colors

    def send_loop(self):
        while True:
            start = time.time()
            colors = self.generate_current_colors()
            self._send_udp_sequence(colors)
            # Ensure around 30-50ms tick rate
            elapsed = time.time() - start
            if elapsed < 0.05:
                time.sleep(0.05 - elapsed)

    def print_ui(self):
        with self.state_lock:
            state = self.game_state.copy()
            
        os.system('cls' if os.name == 'nt' else 'clear')
        print("="*50)
        print(" EVIL EYE ROOM - OPERATOR CONSOLE ".center(50, "="))
        print("="*50)
        
        if state["game_over"]:
            print("\n!!! GAME OVER !!!\nSystem resetting in ~30s.")
        elif state["victory"]:
            print("\n*** VICTORY !!! ***\nAll eyes extinguished or Sequence Mastered.")
        else:
            print(f"Mistakes: {state['mistakes']} | Consecutive Correct: {state['consecutive_correct']} | LEVEL: {state['round_level']}")
            if state["column_pulsing_red"]:
                print(">> PENALTY ACTIVE: Column Pulsing RED")
            if time.time() < state["silence_until"]:
                print(">> PENALTY ACTIVE: Total Silence (15s)")
            
            target = state["current_targets"][0] if state["current_targets"] else None
            sequence_len = len(state["current_targets"])
            
            if target:
                print(f"Current Target: Wall {target[0]}, Btn {target[1]} ({self.btn_names[target[1]]})")
                print(f"Remaining in sequence: {sequence_len}")
            
            print("\n--- Board State (O = ON, . = OFF, EYE = Ochi Central) ---")
            for w in range(1, 5):
                row_str = f"Wall {w}: [EYE] "
                for b in range(1, 11):
                    is_lit = state["eyes"][(w, b)]
                    target_mark = "*" if target == (w, b) else " "
                    eye_char = "O" if is_lit else "."
                    row_str += f"{eye_char}{target_mark} "
                print(row_str)
        
        print("\nPress 'R' to reset game.")

    def ui_loop(self):
        while True:
            self.print_ui()
            time.sleep(0.5)
            
    def input_loop(self):
        if keyboard is None:
            while True:
                cmd = input()
                if cmd.strip().lower() == 'r':
                    self.reset_game()
        else:
            # Requires admin privileges on windows
            while True:
                if keyboard.is_pressed('r'):
                    self.reset_game()
                    time.sleep(1) # debounce
                time.sleep(0.1)

    def run(self):
        print("Starting UDP receiver on 7800...")
        threading.Thread(target=self.listen_loop, daemon=True).start()
        print("Starting UDP sender to 4626...")
        threading.Thread(target=self.send_loop, daemon=True).start()
        print("Starting UI...")
        threading.Thread(target=self.ui_loop, daemon=True).start()
        threading.Thread(target=self.input_loop, daemon=True).start()
        
        time.sleep(1)
        # pick_new_target is already called inside reset_game(silent=True) above
        
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("Shutting down...")

if __name__ == "__main__":
    game = EvilEyeGame()
    game.run()
