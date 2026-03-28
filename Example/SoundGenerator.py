import wave
import math
import struct
import random
import os

SFX_DIR = "_sfx"

def save_wav(filename, data, sample_rate=44100):
    if not os.path.exists(SFX_DIR):
        os.makedirs(SFX_DIR)
        
    path = os.path.join(SFX_DIR, filename)
    with wave.open(path, 'w') as f:
        f.setnchannels(1)
        f.setsampwidth(1) # 8-bit audio
        f.setframerate(sample_rate)
        f.writeframes(data)
    print(f"Generated {path}")

def generate_tone(freq, duration, vol=0.5, type='sine', slide=0):
    sample_rate = 44100
    n_samples = int(sample_rate * duration)
    data = bytearray()
    
    for i in range(n_samples):
        t = i / sample_rate
        cur_freq = freq + slide * t
        
        if type == 'sine':
            val = math.sin(2 * math.pi * cur_freq * t)
        elif type == 'square':
            val = 1.0 if math.sin(2 * math.pi * cur_freq * t) > 0 else -1.0
        elif type == 'saw':
            val = 2.0 * (t * cur_freq - math.floor(0.5 + t * cur_freq))
        elif type == 'noise':
            val = random.uniform(-1, 1)
            
        # Convert -1.0...1.0 to 0...255
        scaled = int((val * vol + 1.0) * 127.5)
        scaled = max(0, min(255, scaled))
        data.append(scaled)
        
    return data

def mix(data1, data2):
    # Mix two bytearrays of the same length
    length = min(len(data1), len(data2))
    mixed = bytearray()
    for i in range(length):
        val1 = data1[i] - 128
        val2 = data2[i] - 128
        m = val1 + val2
        m = max(-128, min(127, m))
        mixed.append(m + 128)
    return mixed

def generate_all():
    if not os.path.exists(SFX_DIR):
        os.makedirs(SFX_DIR)

    # 1. Move (Short Blip)
    move = generate_tone(400, 0.05, vol=0.3, type='square')
    save_wav("move.wav", move)

    # 2. Rotate (Rising chirp)
    rotate = generate_tone(300, 0.1, vol=0.3, type='square', slide=2000)
    save_wav("rotate.wav", rotate)

    # 3. Drop (Low thud)
    drop = generate_tone(100, 0.15, vol=0.5, type='saw', slide=-500)
    save_wav("drop.wav", drop)

    # 4. Line Clear (Happy Arpeggio-ish)
    # Simple major chord: C, E, G
    note1 = generate_tone(523.25, 0.1, vol=0.3, type='square') # C5
    note2 = generate_tone(659.25, 0.1, vol=0.3, type='square') # E5
    note3 = generate_tone(783.99, 0.4, vol=0.3, type='square', slide=-100) # G5
    line = note1 + note2 + note3
    save_wav("line.wav", line)

    # 5. Game Over (Descending slide)
    go = generate_tone(800, 1.0, vol=0.4, type='saw', slide=-700)
    save_wav("gameover.wav", go)

    # 6. BGM (Simple Loop)
    # A generic 120bpm bassline/melody loop
    bpm = 120
    beat_dur = 60 / bpm
    
    # 8 beats
    melody_notes = [
        (220, 0.25), (0, 0.25), (220, 0.25), (0, 0.25), # A3
        (261, 0.25), (0, 0.25), (293, 0.25), (0, 0.25), # C4, D4
        (329, 0.5), (293, 0.5), (261, 0.5), (220, 0.5)  # E4, D4, C4, A3
    ]
    
    bgm_data = bytearray()
    for freq, dur_beats in melody_notes:
        dur_sec = dur_beats * beat_dur * 2 # Speed up slightly
        if freq == 0:
            audio = bytearray([128] * int(44100 * dur_sec))
        else:
            audio = generate_tone(freq, dur_sec, vol=0.2, type='square')
        bgm_data += audio
        
    save_wav("bgm.wav", bgm_data)

if __name__ == "__main__":
    generate_all()
