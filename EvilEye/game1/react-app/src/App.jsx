import { useEffect, useState } from 'react';
import './App.css';

function App() {
  const [status, setStatus] = useState('status-disconnected');
  const [statusMsg, setStatusMsg] = useState('Connecting to Bridge...');
  const [colors, setColors] = useState({});
  const [socket, setSocket] = useState(null);

  useEffect(() => {
    let ws;
    let reconnectTimer;

    const connectWS = () => {
      ws = new WebSocket('ws://localhost:8081');

      ws.onopen = () => {
        setStatusMsg('Connected to Bridge');
        setStatus('status-connected');
      };

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          if (msg.type === 'colors') {
            setColors(msg.state);
          }
        } catch (e) {
             // Handle malformed
        }
      };

      ws.onclose = () => {
        setStatusMsg('Bridge Disconnected. Retrying in 2s...');
        setStatus('status-disconnected');
        reconnectTimer = setTimeout(connectWS, 2000);
      };

      ws.onerror = () => {
         // handled stringently by onclose
      };

      setSocket(ws);
    };

    connectWS();

    return () => {
      if (ws) {
        ws.onclose = null;
        ws.close();
      }
      clearTimeout(reconnectTimer);
    };
  }, []);

  const handlePress = (w, btn) => {
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({
        action: 'press',
        wall: w,
        btn: btn
      }));
    }
  };

  const getGlowStyle = (w, btn) => {
    if (!colors[w] || !colors[w][btn]) return { backgroundColor: 'transparent', boxShadow: 'none' };
    const colorStr = colors[w][btn];
    const isOff = colorStr === 'rgb(0,0,0)' || colorStr === 'rgb(0, 0, 0)';
    if (isOff) {
      return { backgroundColor: 'transparent', boxShadow: 'none' };
    }
    return {
      backgroundColor: colorStr,
      boxShadow: `0 0 20px 5px ${colorStr}`
    };
  };

  return (
    <div className="app-container">
      <header>
        <h1>EVIL EYE <span className="tag">SIMULATOR</span></h1>
        <div id="status" className={status}>{statusMsg}</div>
        {status === 'status-disconnected' && (
           <div className="hint">Make sure to manually run <code>python test_bridge.py</code> in your original environment!</div>
        )}
      </header>

      <main className="room">
        {[1, 2, 3, 4].map(w => (
          <div key={`wall-${w}`} className="wall">
            <div className="wall-header">WALL {w}</div>
            <div className="eyes-grid">
              {[...Array(11)].map((_, btn) => (
                <button 
                  key={`btn-${btn}`} 
                  className="btn-eye" 
                  onClick={() => handlePress(w, btn)}
                >
                  <div className="glow" style={getGlowStyle(w, btn)}></div>
                </button>
              ))}
            </div>
          </div>
        ))}
      </main>
    </div>
  );
}

export default App;
