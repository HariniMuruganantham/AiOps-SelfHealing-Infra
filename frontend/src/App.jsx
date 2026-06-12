import { useState, useEffect, useRef } from "react";
import {
  AreaChart, Area, XAxis, YAxis, Tooltip,
  ResponsiveContainer, ReferenceLine, CartesianGrid,
  BarChart, Bar, Cell
} from "recharts";

const API = "http://localhost:8002";
const WS  = "ws://localhost:8002/ws/metrics";

const STATUS_META = {
  healthy:    { bg: "#0B1F17", border: "#166534", text: "#22C55E", dot: "#22C55E" },
  degraded:   { bg: "#1C1508", border: "#92400E", text: "#F59E0B", dot: "#F59E0B" },
  healing:    { bg: "#0D1A2E", border: "#1D4ED8", text: "#60A5FA", dot: "#60A5FA" },
  down:       { bg: "#1C0808", border: "#7F1D1D", text: "#EF4444", dot: "#EF4444" },
  warming_up: { bg: "#111827", border: "#374151", text: "#9CA3AF", dot: "#6B7280" },
};

const ACTION_COLOR = {
  circuit_break:   "#3B82F6",
  scale_out:       "#8B5CF6",
  reboot_instance: "#F59E0B",
};

function MetricBar({ label, value, max = 1, warn = 0.7, danger = 0.9 }) {
  const pct   = Math.min((value / max) * 100, 100);
  const color = value >= danger ? "#EF4444" : value >= warn ? "#F59E0B" : "#22C55E";
  return (
    <div style={{ marginBottom: 6 }}>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: "#6B7280", marginBottom: 2 }}>
        <span>{label}</span>
        <span style={{ color }}>{typeof value === "number" ? (value * 100).toFixed(1) + "%" : value}</span>
      </div>
      <div style={{ height: 4, background: "#1F2937", borderRadius: 2 }}>
        <div style={{ height: "100%", width: pct + "%", background: color, borderRadius: 2, transition: "width 0.5s, background 0.3s" }} />
      </div>
    </div>
  );
}

function ServiceCard({ name, s, healing }) {
  const m   = STATUS_META[healing ? "healing" : (s.status || "warming_up")];
  const short = name.replace("-svc", "");
  return (
    <div style={{
      background: m.bg, border: `1px solid ${m.border}`,
      borderRadius: 10, padding: "14px 16px",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
        <span style={{ fontWeight: 600, fontSize: 13, color: "#E5E7EB" }}>{short}</span>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          {healing && <span style={{ fontSize: 10, color: "#60A5FA", animation: "spin 1s linear infinite" }}>⟳</span>}
          <span style={{
            fontSize: 10, fontWeight: 700, padding: "2px 8px",
            borderRadius: 4, background: m.border + "55", color: m.text,
            letterSpacing: "0.05em"
          }}>
            <span style={{ display: "inline-block", width: 6, height: 6, borderRadius: "50%", background: m.dot, marginRight: 4 }} />
            {healing ? "HEALING" : s.status?.toUpperCase()}
          </span>
        </div>
      </div>
      <MetricBar label="CPU"       value={s.cpu}        warn={0.7}  danger={0.85} />
      <MetricBar label="Memory"    value={s.memory}     warn={0.75} danger={0.9} />
      <MetricBar label="Errors"    value={s.error_rate} warn={0.1}  danger={0.5} />
      <div style={{ fontSize: 11, color: "#6B7280", marginTop: 8, display: "flex", justifyContent: "space-between" }}>
        <span>Latency</span>
        <span style={{ color: s.response_time > 2000 ? "#EF4444" : s.response_time > 500 ? "#F59E0B" : "#22C55E" }}>
          {s.response_time?.toFixed(0)} ms
        </span>
      </div>
    </div>
  );
}

function FeatureScoreBar({ scores }) {
  if (!scores || Object.keys(scores).length === 0) return null;
  const entries = Object.entries(scores).sort((a, b) => b[1] - a[1]);
  const max     = Math.max(...Object.values(scores), 0.01);
  return (
    <div style={{ background: "#0B0D13", border: "1px solid #1F2937", borderRadius: 10, padding: "16px 20px", marginBottom: 16 }}>
      <div style={{ fontSize: 11, color: "#6B7280", textTransform: "uppercase", letterSpacing: "0.07em", marginBottom: 12 }}>
        Anomaly contribution — which features drove the score
      </div>
      {entries.map(([k, v]) => (
        <div key={k} style={{ marginBottom: 8 }}>
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: "#9CA3AF", marginBottom: 3 }}>
            <span>{k.replace("_", " ")}</span>
            <span>{v.toFixed(4)}</span>
          </div>
          <div style={{ height: 5, background: "#1F2937", borderRadius: 3 }}>
            <div style={{
              height: "100%",
              width: Math.min((v / max) * 100, 100) + "%",
              background: v > 0.5 ? "#EF4444" : v > 0.1 ? "#F59E0B" : "#3B82F6",
              borderRadius: 3, transition: "width 0.4s"
            }} />
          </div>
        </div>
      ))}
    </div>
  );
}

function HealLogEntry({ entry }) {
  const color = ACTION_COLOR[entry.action] || "#9CA3AF";
  const ts    = new Date(entry.timestamp).toLocaleTimeString("en-IN");
  return (
    <div style={{ display: "flex", gap: 10, padding: "8px 0", borderBottom: "1px solid #111827", alignItems: "flex-start" }}>
      <span style={{ fontSize: 10, color: "#374151", flexShrink: 0, marginTop: 2 }}>{ts}</span>
      <span style={{
        fontSize: 10, fontWeight: 700, padding: "2px 7px", borderRadius: 3,
        background: color + "22", color, border: `1px solid ${color}44`, flexShrink: 0
      }}>
        {entry.action?.replace("_", " ").toUpperCase()}
      </span>
      <span style={{ fontSize: 12, color: "#9CA3AF" }}>{entry.service}</span>
      {entry.ec2_call && (
        <span style={{ fontSize: 10, color: "#F59E0B", marginLeft: "auto", flexShrink: 0 }}>EC2 API ↓</span>
      )}
      <span style={{ fontSize: 10, color: entry.result === "recovered" ? "#22C55E" : "#EF4444", flexShrink: 0 }}>
        {entry.result}
      </span>
    </div>
  );
}

const CustomTooltip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null;
  return (
    <div style={{ background: "#0F1117", border: "1px solid #1F2937", borderRadius: 8, padding: "8px 12px", fontSize: 12 }}>
      <div style={{ color: "#6B7280", marginBottom: 4 }}>Tick {label}</div>
      <div style={{ color: "#60A5FA" }}>Score: {payload[0]?.value?.toFixed(5)}</div>
    </div>
  );
};

export default function App() {
  const [metrics, setMetrics]       = useState(null);
  const [errorHist, setErrorHist]   = useState([]);
  const [healLog, setHealLog]       = useState([]);
  const [connected, setConnected]   = useState(false);
  const [anomalyCount, setAnomalyCount] = useState(0);
  const [healCount, setHealCount]   = useState(0);
  const [featureScores, setFeatureScores] = useState({});
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
        if (data.feature_scores && Object.keys(data.feature_scores).length > 0) {
          setFeatureScores(data.feature_scores);
        }
        if (data.is_anomaly) setAnomalyCount(prev => prev + 1);
        setErrorHist(prev => [...prev.slice(-99), {
          tick:    data.tick,
          error:   data.lstm_error ?? 0,
          anomaly: data.is_anomaly
        }]);
        if (data.remediations?.length > 0) {
          setHealCount(prev => prev + data.remediations.length);
          setHealLog(prev => [...data.remediations, ...prev].slice(0, 50));
        }
      };
    };
    connect();
    return () => wsRef.current?.close();
  }, []);

  const injectCrash   = async (svc) => fetch(`${API}/demo/crash/${svc}`,   { method: "POST" });
  const recoverManual = async (svc) => fetch(`${API}/demo/recover/${svc}`,  { method: "POST" });

  const isAnomaly   = metrics?.is_anomaly;
  const isWarming   = metrics?.status?.startsWith("warming_up");
  const healActive  = metrics?.healing_active || [];
  const cooldowns   = metrics?.cooldowns || {};

  return (
    <div style={{ minHeight: "100vh", background: "#080A0F", color: "#E5E7EB", fontFamily: "'Inter', system-ui, sans-serif" }}>

      {/* Nav */}
      <div style={{ background: "#0B0D13", borderBottom: "1px solid #111827", padding: "0 28px", height: 52, display: "flex", alignItems: "center", gap: 12 }}>
        <span style={{ fontSize: 16, color: "#3B82F6" }}>◈</span>
        <span style={{ fontSize: 14, fontWeight: 700, color: "#F9FAFB", letterSpacing: "-0.01em" }}>Self-Healing Infrastructure</span>
        <span style={{ fontSize: 10, padding: "2px 7px", borderRadius: 3, background: "rgba(139,92,246,0.12)", color: "#A78BFA", border: "1px solid rgba(139,92,246,0.2)", fontWeight: 600 }}>AIOps</span>
        <div style={{ flex: 1 }} />
        {anomalyCount > 0 && (
          <span style={{ fontSize: 11, color: "#EF4444", background: "rgba(239,68,68,0.1)", border: "1px solid rgba(239,68,68,0.2)", padding: "3px 10px", borderRadius: 4 }}>
            {anomalyCount} anomalies
          </span>
        )}
        {healCount > 0 && (
          <span style={{ fontSize: 11, color: "#22C55E", background: "rgba(34,197,94,0.1)", border: "1px solid rgba(34,197,94,0.2)", padding: "3px 10px", borderRadius: 4 }}>
            {healCount} heals
          </span>
        )}
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ width: 7, height: 7, borderRadius: "50%", background: connected ? "#22C55E" : "#EF4444", boxShadow: connected ? "0 0 6px #22C55E" : "none" }} />
          <span style={{ fontSize: 11, color: "#6B7280" }}>{connected ? "WebSocket live" : "reconnecting..."}</span>
        </div>
        <span style={{ fontSize: 11, color: "#374151" }}>us-east-1 · LocalStack · :4567</span>
      </div>

      <div style={{ padding: "22px 28px", maxWidth: 1200, margin: "0 auto" }}>

        {/* Status banner */}
        {metrics && (
          <div style={{
            borderRadius: 10, padding: "12px 18px", marginBottom: 20,
            background: isWarming ? "#0F1117" : isAnomaly ? "rgba(239,68,68,0.08)" : "rgba(34,197,94,0.06)",
            border: `1px solid ${isWarming ? "#1F2937" : isAnomaly ? "rgba(239,68,68,0.25)" : "rgba(34,197,94,0.2)"}`,
            display: "flex", justifyContent: "space-between", alignItems: "center"
          }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <span style={{ fontSize: 18 }}>{isWarming ? "⏳" : isAnomaly ? "🚨" : "✅"}</span>
              <div>
                <div style={{ fontWeight: 600, fontSize: 14, color: isWarming ? "#6B7280" : isAnomaly ? "#EF4444" : "#22C55E" }}>
                  {isWarming ? `Warming up — ${metrics.status}`
                   : isAnomaly ? "Anomaly detected — auto-remediation active"
                   : "All systems healthy"}
                </div>
                {healActive.length > 0 && (
                  <div style={{ fontSize: 12, color: "#60A5FA", marginTop: 2 }}>
                    Healing: {healActive.join(", ")}
                  </div>
                )}
                {Object.keys(cooldowns).length > 0 && (
                  <div style={{ fontSize: 11, color: "#F59E0B", marginTop: 2 }}>
                    Cooldown: {Object.entries(cooldowns).map(([k, v]) => `${k.replace("-svc", "")} (${v}s)`).join(", ")}
                  </div>
                )}
              </div>
            </div>
            <div style={{ textAlign: "right", fontSize: 11, color: "#4B5563" }}>
              <div>Tick <strong style={{ color: "#9CA3AF" }}>{metrics.tick}</strong></div>
              <div>Score <strong style={{ color: isAnomaly ? "#EF4444" : "#9CA3AF" }}>{metrics.lstm_error?.toFixed(5)}</strong> / {metrics.anomaly_threshold}</div>
            </div>
          </div>
        )}

        {/* Controls */}
        <div style={{ background: "#0B0D13", border: "1px solid #1F2937", borderRadius: 10, padding: "14px 18px", marginBottom: 20 }}>
          <div style={{ fontSize: 11, color: "#6B7280", textTransform: "uppercase", letterSpacing: "0.07em", marginBottom: 10 }}>
            Chaos controls
          </div>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
            {["auth-svc", "payment-svc", "inventory-svc"].map(svc => (
              <div key={svc} style={{ display: "flex", gap: 6 }}>
                <button onClick={() => injectCrash(svc)} style={{
                  padding: "7px 14px", background: "rgba(239,68,68,0.1)", color: "#EF4444",
                  border: "1px solid rgba(239,68,68,0.25)", borderRadius: 6,
                  cursor: "pointer", fontSize: 12, fontWeight: 600
                }}>
                  ✕ {svc.replace("-svc", "")}
                </button>
                <button onClick={() => recoverManual(svc)} style={{
                  padding: "7px 14px", background: "rgba(34,197,94,0.08)", color: "#22C55E",
                  border: "1px solid rgba(34,197,94,0.2)", borderRadius: 6,
                  cursor: "pointer", fontSize: 12, fontWeight: 600
                }}>
                  ↺
                </button>
              </div>
            ))}
          </div>
        </div>

        {/* Service cards */}
        {metrics?.services && (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 12, marginBottom: 20 }}>
            {Object.entries(metrics.services).map(([name, s]) => (
              <ServiceCard key={name} name={name} s={s} healing={healActive.includes(name)} />
            ))}
          </div>
        )}

        {/* LSTM score chart */}
        {errorHist.length > 1 && (
          <div style={{ background: "#0B0D13", border: "1px solid #1F2937", borderRadius: 10, padding: "18px 20px", marginBottom: 16 }}>
            <div style={{ fontSize: 11, color: "#6B7280", textTransform: "uppercase", letterSpacing: "0.07em", marginBottom: 14 }}>
              Anomaly score — sliding window reconstruction error
            </div>
            <ResponsiveContainer width="100%" height={180}>
              <AreaChart data={errorHist} margin={{ top: 8, right: 10, left: 0, bottom: 0 }}>
                <defs>
                  <linearGradient id="grad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%"  stopColor="#3B82F6" stopOpacity={0.25} />
                    <stop offset="95%" stopColor="#3B82F6" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#111827" vertical={false} />
                <XAxis dataKey="tick" tick={{ fill: "#6B7280", fontSize: 10 }} tickLine={false} axisLine={false} />
                <YAxis tick={{ fill: "#6B7280", fontSize: 10 }} tickLine={false} axisLine={false} domain={[0, "auto"]} />
                <Tooltip content={<CustomTooltip />} cursor={{ stroke: "#1F2937" }} />
                {metrics?.anomaly_threshold && (
                  <ReferenceLine y={metrics.anomaly_threshold} stroke="#EF4444" strokeDasharray="4 2"
                    label={{ value: `threshold ${metrics.anomaly_threshold}`, position: "insideTopLeft", fontSize: 10, fill: "#EF4444" }} />
                )}
                <Area type="monotone" dataKey="error" stroke="#3B82F6" fill="url(#grad)" strokeWidth={1.5} dot={false} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        )}

        {/* Feature score breakdown */}
        {metrics?.is_anomaly && <FeatureScoreBar scores={featureScores} />}

        {/* Heal log */}
        <div style={{ background: "#0B0D13", border: "1px solid #1F2937", borderRadius: 10, padding: "18px 20px" }}>
          <div style={{ fontSize: 11, color: "#6B7280", textTransform: "uppercase", letterSpacing: "0.07em", marginBottom: 12, display: "flex", justifyContent: "space-between" }}>
            <span>Remediation log</span>
            <span style={{ color: "#374151" }}>{healLog.length} events</span>
          </div>
          <div style={{ maxHeight: 260, overflowY: "auto" }}>
            {healLog.length === 0 ? (
              <div style={{ fontSize: 12, color: "#374151", fontFamily: "monospace", padding: "8px 0" }}>
                Watching for anomalies... inject a crash to trigger auto-remediation.
              </div>
            ) : healLog.map((e, i) => (
              <HealLogEntry key={i} entry={e} />
            ))}
          </div>
        </div>
      </div>

      <style>{`
        * { box-sizing: border-box; margin: 0; padding: 0; }
        @keyframes spin { from { transform: rotate(0deg) } to { transform: rotate(360deg) } }
        ::-webkit-scrollbar { width: 4px; }
        ::-webkit-scrollbar-track { background: #0B0D13; }
        ::-webkit-scrollbar-thumb { background: #1F2937; border-radius: 2px; }
      `}</style>
    </div>
  );
}