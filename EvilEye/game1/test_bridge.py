import asyncio
import socket
import json
import websockets

UDP_RECV_PORT = 4626
UDP_SEND_IP = "127.0.0.1" # Send presses to local
UDP_SEND_PORT = 7800
WS_PORT = 8081

clients = set()

# Initialize UDP Recv (listen to evil_eye_game.py broadcasting on 4626)
sock_recv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
# We need to reuse the address on windows to prevent conflicts if simulator/engine both run
try:
    sock_recv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
except AttributeError:
    pass
sock_recv.bind(("0.0.0.0", UDP_RECV_PORT))
sock_recv.setblocking(False)

# Initialize UDP Send (send to evil_eye_game.py listening on 7800)
sock_send = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

async def udp_listener():
    loop = asyncio.get_event_loop()
    while True:
        try:
            # Receive up to 2048 bytes
            data, addr = await loop.sock_recvfrom(sock_recv, 2048)
            
            # Check if it's the 0x8877 Frame packet from our Python server
            # Format: p3[0]=0x88, p3[1]=0x77, followed by 132 bytes
            if len(data) >= 134 and data[0] == 0x88 and data[1] == 0x77:
                frames = data[2:134]
                # Parse frames
                # Frame is 11 LEDs * 12 bytes = 132 bytes
                # 4 channels per LED, 3 colors per channel
                # offset: led * 12 + idx = g, +4+idx = r, +8+idx = b (where idx = ch - 1)
                
                state = {}
                for ch in range(1, 5):
                    state[ch] = []
                    idx = ch - 1
                    for led in range(11):
                        base = led * 12
                        g = frames[base + idx]
                        r = frames[base + 4 + idx]
                        b = frames[base + 8 + idx]
                        state[ch].append(f"rgb({r},{g},{b})")
                
                # Broadcast JSON to all websocket clients
                if clients:
                    msg = json.dumps({"type": "colors", "state": state})
                    for client in list(clients):
                        try:
                            await client.send(msg)
                        except:
                            pass

        except Exception as e:
            # Handle non-blocking wait correctly
            if isinstance(e, BlockingIOError):
                await asyncio.sleep(0.01)
            else:
                await asyncio.sleep(0.1)


async def handle_ws(websocket):
    clients.add(websocket)
    try:
        async for message in websocket:
            data = json.loads(message)
            if data.get("action") == "press":
                ch = data["wall"]
                led = data["btn"]
                # Form the 687 byte packet expected by evil_eye_game
                pkt = bytearray(687)
                pkt[0] = 0x88
                pkt[1] = 0x01
                # Base is 2 + (ch - 1) * 171
                offset = 2 + (ch - 1) * 171 + 1 + led
                pkt[offset] = 0xCC
                pkt[-1] = sum(pkt[:-1]) & 0xFF
                sock_send.sendto(pkt, (UDP_SEND_IP, UDP_SEND_PORT))
                
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        clients.remove(websocket)

async def main():
    asyncio.create_task(udp_listener())
    print(f"WS Bridge running on ws://localhost:{WS_PORT}")
    print(f"Listening to UDP {UDP_RECV_PORT}, forwarding to UDP {UDP_SEND_PORT}")
    async with websockets.serve(handle_ws, "localhost", WS_PORT):
        await asyncio.Future()  # run forever

if __name__ == "__main__":
    asyncio.run(main())
