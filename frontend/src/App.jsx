import { useState, useEffect, useRef } from "react";
import {
  LineChart, Line, XAxis, YAxis, Tooltip,
  ResponsiveContainer, ReferenceLine, Area, AreaChart
} from "recharts";

const API = "http://localhost:8002";
const WS  = "ws://localhost:8002/ws/metrics";

const STATUS_COLOR = {
  healthy:    { bg: "#D1FAE5", border: "#059669", text: "#065F46" },
  degraded:   { bg: "#FEF3C7", border: "#D97706", text: "#92400E" },
  healing:    { bg: "#DBEAFE", border: "#2563EB", text: "#1E3A8A" },
  down:       { bg: "#FEE2E2", border: "#DC2626", text: "#7F1D1D" },
  warming_up: { bg: "#F3F4F6", border: "#9CA3AF", text: "#374151" },
};

const STATUS_ICON = {
  healthy: "●", degraded: "▲", healing: "↻", down: "✕", warming_up: "○"
};

function ServiceCard({ name, s }) {
  const c = STATUS_COLOR[s.status] || STATUS_COLOR.warming_up;
  return (
    <div style={{
      borderRadius: 12, border: `2px solid ${c.border}`,
      background: c.bg, padding: "1rem 1.2rem",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <span style={{ fontWeight: 700, fontSize: 14, color: "#111" }}>{name}</span>
        <span style={{
          fontSize: 11, fontWeight: 700, padding: "2px 10px",
          borderRadius: 20, background: c.border, color: "#fff", letterSpacing: 1
        }}>
          {STATUS_ICON[s.status]} {s.status.toUpperCase()}
        </span>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "4px 16px", fontSize: 12, color: "#555" }}>
        <span>CPU</span><span style={{ fontWeight: 600, color: "#111" }}>{(s.cpu * 100).toFixed(1)}%</span>
        <span>Memory</span><span style={{ fontWeight: 600, color: "#111" }}>{(s.memory * 100).toFixed(1)}%</span>
        <span>Latency</span><span style={{ fontWeight: 600, color: "#111" }}>{s.response_time.toFixed(0)} ms</span>
        <span>Errors</span><span style={{ fontWeight: 600, color: s.error_rate > 0.1 ? "#DC2626" : "#111" }}>{(s.error_rate * 100).toFixed(0)}%</span>
      </div>
    </div>
  );
}

export default function App() {
  const [metrics, setMetrics]     = useState(null);
  const [errorHist, setErrorHist] = useState([]);
  const [healLog, setHealLog]     = useState([]);
  const [connected, setConnected] = useState(false);
  const [anomalyCount, setAnomalyCount] = useState(0);
  const wsRef = useRef(null);

  useEffect(() => {
    const connect = () => {
      const ws = new WebSocket(WS);
      wsRef.current = ws;
      ws.onopen  = () => setConnected(true);
      ws.onclose = () => { setConnected(false); setTimeout(connect, 3000); };
      ws.onerror = () => ws.close();
      ws.onmessage = (e) => {
        const data = JSON.parse(e.data);
        setMetrics(data);
        if (data.is_anomaly) setAnomalyCount(prev => prev + 1);
        setErrorHist(prev => [...prev.slice(-79),
          { tick: data.tick, error: data.lstm_error ?? 0, anomaly: data.is_anomaly }
        ]);
        if (data.remediations?.length > 0) {
          const ts = new Date().toLocaleTimeString();
          setHealLog(prev => [
            ...data.remediations.map(r =>
              `[${ts}] ${r.action?.toUpperCase()} → ${r.service} ${r.ec2_call ? "· EC2 API" : ""} → ${r.result}`),
            ...prev
          ].slice(0, 200));
        }
      };
    };
    connect();
    return () => wsRef.current?.close();
  }, []);

  const injectCrash = async (svc) => {
    await fetch(`${API}/demo/crash/${svc}`, { method: "POST" });
  };

  const isAnomaly  = metrics?.is_anomaly;
  const anyDegraded = metrics?.services &&
    Object.values(metrics.services).some(s => s.status !== "healthy" && s.status !== "warming_up");
  const isWarming  = metrics?.status === "warming_up";

  return (
    <div style={{ minHeight: "100vh", background: "#F8FAFC", fontFamily: "'Inter', system-ui, sans-serif" }}>

      {/* Header */}
      <div style={{
        background: "linear-gradient(135deg, #0F172A 0%, #1E3A5F 100%)",
        padding: "1.5rem 2rem", color: "#fff",
        display: "flex", justifyContent: "space-between", alignItems: "center"
      }}>
        <div>
          <div style={{ fontSize: 11, letterSpacing: 2, color: "#94A3B8", marginBottom: 4, textTransform: "uppercase" }}>
            AIOps Project 2
          </div>
          <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700 }}>Self-Healing Infrastructure</h1>
          <div style={{ fontSize: 12, color: "#94A3B8", marginTop: 4 }}>
            LocalStack EC2 · LSTM Anomaly Detection · Async Auto-Remediation
          </div>
        </div>
        <div style={{
          display: "flex", alignItems: "center", gap: 8,
          background: connected ? "#065F46" : "#7F1D1D",
          padding: "6px 16px", borderRadius: 20, fontSize: 12, fontWeight: 600
        }}>
          <span style={{ width: 8, height: 8, borderRadius: "50%",
            background: connected ? "#34D399" : "#F87171",
            boxShadow: connected ? "0 0 6px #34D399" : "none" }}/>
          {connected ? "WebSocket Connected" : "Reconnecting..."}
        </div>
        {anomalyCount > 0 && (
          <div style={{
            background: "#7F1D1D", padding: "6px 16px", borderRadius: 20,
            fontSize: 12, fontWeight: 600, color: "#FCA5A5", marginLeft: 8
          }}>
            🚨 {anomalyCount} anomalies fired
          </div>
        )}
        <div style={{ display: "none" }}>
        </div>
      </div>

      <div style={{ maxWidth: 1100, margin: "0 auto", padding: "1.5rem 2rem" }}>

        {/* Status Banner */}
        {metrics && (
          <div style={{
            borderRadius: 10, padding: "12px 20px", marginBottom: 20,
            display: "flex", justifyContent: "space-between", alignItems: "center",
            background: isWarming ? "#F1F5F9"
                      : isAnomaly ? "#FEF2F2" : "#F0FDF4",
            border: `1px solid ${isWarming ? "#CBD5E1" : isAnomaly ? "#FECACA" : "#BBF7D0"}`,
          }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <span style={{ fontSize: 20 }}>
                {isWarming ? "⏳" : isAnomaly ? "🚨" : "✅"}
              </span>
              <div>
                <div style={{ fontWeight: 700, fontSize: 14,
                  color: isWarming ? "#475569" : isAnomaly ? "#DC2626" : "#15803D" }}>
                  {isWarming ? "Warming up — collecting baseline metrics"
                   : isAnomaly && anyDegraded ? "Anomaly Detected — Auto-remediation triggered"
                   : isAnomaly ? "Elevated LSTM error — monitoring closely"
                   : "All Systems Healthy"}
                </div>
                {metrics.healing_active?.length > 0 && (
                  <div style={{ fontSize: 12, color: "#2563EB", marginTop: 2 }}>
                    Healing: {metrics.healing_active.join(", ")}
                  </div>
                )}
              </div>
            </div>
            <div style={{ fontSize: 12, color: "#64748B", textAlign: "right" }}>
              <div>Tick <strong>{metrics.tick ?? "—"}</strong></div>
              <div>LSTM Error <strong>{metrics.lstm_error?.toFixed(5) ?? "—"}</strong></div>
            </div>
          </div>
        )}

        {/* Crash injection */}
        <div style={{ display: "flex", gap: 10, marginBottom: 20, flexWrap: "wrap", alignItems: "center" }}>
          <span style={{ fontSize: 12, color: "#64748B", fontWeight: 600 }}>INJECT FAILURE:</span>
          {["auth-svc","payment-svc","inventory-svc"].map(svc => (
            <button key={svc} onClick={() => injectCrash(svc)} style={{
              padding: "7px 16px", background: "#DC2626", color: "#fff",
              border: "none", borderRadius: 6, cursor: "pointer", fontSize: 12,
              fontWeight: 600, letterSpacing: 0.5
            }}>
              ✕ {svc.replace("-svc", "").toUpperCase()}
            </button>
          ))}
        </div>

        {/* Service Grid */}
        {metrics?.services && (
          <>
            <h3 style={{ fontSize: 13, fontWeight: 700, color: "#475569", letterSpacing: 1,
                         textTransform: "uppercase", marginBottom: 10 }}>Live Service Health</h3>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 12, marginBottom: 24 }}>
              {Object.entries(metrics.services).map(([name, s]) => (
                <ServiceCard key={name} name={name} s={s} />
              ))}
            </div>
          </>
        )}

        {/* LSTM Chart */}
        {errorHist.length > 1 && (
          <>
            <h3 style={{ fontSize: 13, fontWeight: 700, color: "#475569", letterSpacing: 1,
                         textTransform: "uppercase", marginBottom: 10 }}>
              Anomaly Score — Real-time LSTM Reconstruction Error
            </h3>
            <div style={{ background: "#fff", borderRadius: 10, padding: "1rem",
                          border: "1px solid #E2E8F0", marginBottom: 24 }}>
              <ResponsiveContainer width="100%" height={200}>
                <AreaChart data={errorHist} margin={{ top: 8, right: 20, left: 0, bottom: 0 }}>
                  <defs>
                    <linearGradient id="errGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#3B82F6" stopOpacity={0.2}/>
                      <stop offset="95%" stopColor="#3B82F6" stopOpacity={0}/>
                    </linearGradient>
                  </defs>
                  <XAxis dataKey="tick" tick={{ fontSize: 10 }} tickLine={false}/>
                  <YAxis domain={["auto", "auto"]} tick={{ fontSize: 10 }} tickLine={false}/>
                  <Tooltip formatter={(v) => typeof v === "number" ? v.toFixed(3) : v} labelFormatter={(l) => `Tick ${l}`}/>
                  {metrics?.anomaly_threshold && (
                    <ReferenceLine y={metrics.anomaly_threshold} stroke="#DC2626" strokeDasharray="4 2"
                      label={{ value: `threshold (${metrics.anomaly_threshold?.toFixed(1)})`, position: "insideTopLeft", fontSize: 10, fill: "#DC2626" }}/>
                  )}
                  <Area type="monotone" dataKey="error" stroke="#3B82F6"
                    fill="url(#errGrad)" strokeWidth={2} dot={false}/>
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </>
        )}

        {/* Remediation Log */}
        <h3 style={{ fontSize: 13, fontWeight: 700, color: "#475569", letterSpacing: 1,
                     textTransform: "uppercase", marginBottom: 10 }}>
          Remediation Log — EC2 API Calls
        </h3>
        <div style={{
          height: 220, overflowY: "auto", background: "#0F172A",
          color: "#E2E8F0", fontFamily: "'Fira Code', 'Courier New', monospace",
          fontSize: 12, padding: "12px 16px", borderRadius: 10,
          border: "1px solid #1E293B"
        }}>
          {healLog.length === 0 ? (
            <span style={{ color: "#475569" }}>
              {'>'} Watching for anomalies... inject a crash above to trigger auto-remediation.
            </span>
          ) : healLog.map((e, i) => (
            <div key={i} style={{
              marginBottom: 3,
              color: e.includes("RESTART") ? "#34D399"
                   : e.includes("STOP") ? "#F87171"
                   : "#CBD5E1"
            }}>{e}</div>
          ))}
        </div>
      </div>
    </div>
  );
}