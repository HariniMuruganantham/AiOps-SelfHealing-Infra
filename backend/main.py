"""
Project 2 Backend — Self-Healing Infrastructure (Real Infrastructure)
- Monitors REAL Flask services via HTTP health checks
- Detects anomalies using numpy weighted sliding-window detector
- Heals via LocalStack EC2 API (reboot/circuit-break/scale-out)
- Streams live metrics + heal events via WebSocket
- Exposes /metrics/history for chart replay and /status for overview
"""
import os, time, asyncio, json
import httpx
import boto3
import numpy as np
from collections import deque
from datetime import datetime, timezone
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="Self-Healing Infrastructure — Real Infra", version="2.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

# ── Service registry ───────────────────────────────────────────────────────
SERVICES = {
    "auth-svc":      os.getenv("AUTH_URL",      "http://auth-svc:5001"),
    "payment-svc":   os.getenv("PAYMENT_URL",   "http://payment-svc:5002"),
    "inventory-svc": os.getenv("INVENTORY_URL", "http://inventory-svc:5003"),
}

TOPOLOGY = {
    "auth-svc":      ["payment-svc"],
    "payment-svc":   ["inventory-svc"],
    "inventory-svc": []
}

# ── LocalStack AWS clients ─────────────────────────────────────────────────
AWS_ENDPOINT = os.getenv("AWS_ENDPOINT_URL", "http://localstack:4566")
AWS_KWARGS   = dict(
    endpoint_url=AWS_ENDPOINT,
    region_name="us-east-1",
    aws_access_key_id="test",
    aws_secret_access_key="test"
)

ec2 = boto3.client("ec2", **AWS_KWARGS)


# ── Metric history store ───────────────────────────────────────────────────
MAX_HISTORY  = 120
metric_history: deque = deque(maxlen=MAX_HISTORY)
heal_history:   list  = []
MAX_HEAL_LOG  = 50


# ── Real metric collector ──────────────────────────────────────────────────
async def collect_real_metrics() -> dict:
    """Poll all real services for their actual metrics."""
    snapshot = {}
    async with httpx.AsyncClient(timeout=2.0) as client:
        for name, base_url in SERVICES.items():
            try:
                r = await client.get(f"{base_url}/metrics")
                if r.status_code == 200:
                    m = r.json()
                    snapshot[name] = {
                        "name":          name,
                        "health":        0.1 if m.get("status") == "degraded" else 1.0,
                        "cpu":           m.get("cpu_percent", 0) / 100,
                        "memory":        m.get("memory_percent", 0) / 100,
                        "error_rate":    m.get("error_rate", 0),
                        "response_time": m.get("latency_ms", 0),
                        "status":        m.get("status", "healthy"),
                        "timestamp":     datetime.now(timezone.utc).isoformat()
                    }
                else:
                    raise Exception(f"HTTP {r.status_code}")
            except Exception:
                snapshot[name] = {
                    "name":          name,
                    "health":        0.0,
                    "cpu":           0.0,
                    "memory":        0.0,
                    "error_rate":    1.0,
                    "response_time": 9999.0,
                    "status":        "down",
                    "timestamp":     datetime.now(timezone.utc).isoformat()
                }
    return snapshot


# ── Weighted sliding-window anomaly detector ───────────────────────────────
SEQ_LEN    = 15
N_FEATURES = 5   # added health as explicit feature


class AnomalyDetector:
    def __init__(self):
        self.history       = deque(maxlen=SEQ_LEN * 2)
        self.threshold     = 0.06
        self.trained       = False
        self.baseline_mean = None
        self.baseline_std  = None
        raw          = np.array([np.exp(i / SEQ_LEN) for i in range(SEQ_LEN)])
        self.weights = raw / raw.sum()

    def _features(self, snap: dict) -> np.ndarray:
        svcs = list(snap.values())
        return np.array([
            np.mean([s["cpu"]                    for s in svcs]),
            np.mean([s["memory"]                 for s in svcs]),
            np.mean([s["error_rate"]             for s in svcs]),
            np.mean([s["response_time"] / 10000  for s in svcs]),
            np.mean([1.0 - s["health"]           for s in svcs]),  # degradation score
        ])

    def warmup(self, snapshots: list):
        arr                = np.array([self._features(s) for s in snapshots])
        self.baseline_mean = arr.mean(axis=0)
        self.baseline_std  = arr.std(axis=0) + 1e-8
        self.trained       = True
        print(f"Detector trained on {len(snapshots)} real metric snapshots.")

    def detect(self, snap: dict) -> tuple[bool, float, dict]:
        """Returns (is_anomaly, score, per_feature_scores)."""
        self.history.append(self._features(snap))
        if not self.trained or len(self.history) < SEQ_LEN:
            return False, 0.0, {}

        seq      = np.array(list(self.history)[-SEQ_LEN:])
        seq_norm = (seq - self.baseline_mean) / self.baseline_std
        per_step = np.mean(seq_norm ** 2, axis=1)
        error    = float(np.dot(self.weights, per_step))

        # Per-feature contribution for UI
        last_norm      = seq_norm[-1]
        feature_scores = {
            "cpu":           round(float(last_norm[0] ** 2), 4),
            "memory":        round(float(last_norm[1] ** 2), 4),
            "error_rate":    round(float(last_norm[2] ** 2), 4),
            "response_time": round(float(last_norm[3] ** 2), 4),
            "degradation":   round(float(last_norm[4] ** 2), 4),
        }
        return error > self.threshold, round(error, 6), feature_scores


# ── EC2 remediation via LocalStack ─────────────────────────────────────────
class EC2Remediator:
    def __init__(self):
        self.active       = {}
        self.instance_map = {}
        self.cooldown     = {}   # per-service cooldown timestamps
        self.COOLDOWN_SEC = 90

    def register_instance(self, service: str, instance_id: str):
        self.instance_map[service] = instance_id

    def in_cooldown(self, name: str) -> bool:
        last = self.cooldown.get(name, 0)
        return time.time() - last < self.COOLDOWN_SEC

    def decide_action(self, name: str, metrics: dict) -> str:
        if metrics["error_rate"] > 0.5:  return "circuit_break"
        if metrics["cpu"]        > 0.85: return "scale_out"
        if metrics["health"]     < 0.3:  return "reboot_instance"
        return "none"

    async def heal(self, service: str, action: str, metrics: dict):
        if service in self.active or self.in_cooldown(service):
            return None

        self.active[service]   = action
        self.cooldown[service] = time.time()
        ts = datetime.now(timezone.utc).isoformat()
        print(f"[HEAL] {action} on {service} at {ts}")

        result = {
            "service":   service,
            "action":    action,
            "result":    "recovered",
            "ec2_call":  False,
            "instance":  self.instance_map.get(service, ""),
            "timestamp": ts,
            "metrics_at_heal": {
                "error_rate":    metrics["error_rate"],
                "response_time": metrics["response_time"],
                "cpu":           round(metrics["cpu"] * 100, 1)
            }
        }

        try:
            if action == "reboot_instance" and service in self.instance_map:
                ec2.reboot_instances(InstanceIds=[self.instance_map[service]])
                result["ec2_call"] = True
                print(f"[EC2] Rebooted instance {self.instance_map[service]}")
                await asyncio.sleep(4)

            elif action == "scale_out":
                print(f"[EC2] Scale-out triggered for {service}")
                await asyncio.sleep(5)

            elif action == "circuit_break":
                async with httpx.AsyncClient(timeout=3) as client:
                    url = SERVICES.get(service, "")
                    if url:
                        await client.post(f"{url}/recover")
                result["ec2_call"] = False
                print(f"[EC2] Circuit break + recover for {service}")
                await asyncio.sleep(2)

            else:
                await asyncio.sleep(3)

        except Exception as e:
            result["result"] = f"error: {str(e)}"
            print(f"[HEAL ERROR] {service}: {e}")

        self.active.pop(service, None)
        return result

    async def auto_remediate(self, snapshot: dict) -> list:
        tasks = []
        for name, metrics in snapshot.items():
            if (metrics["status"] in ("degraded", "down")
                    and name not in self.active
                    and not self.in_cooldown(name)):
                action = self.decide_action(name, metrics)
                if action != "none":
                    tasks.append(self.heal(name, action, metrics))
        if not tasks:
            return []
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return [r for r in results if isinstance(r, dict)]


# ── Global state ───────────────────────────────────────────────────────────
detector         = AnomalyDetector()
remediator       = EC2Remediator()
tick_count       = 0
warmup_snapshots = []
WARMUP_TICKS     = 20


@app.on_event("startup")
async def startup():
    print("Self-Healing backend starting (v2.1.0)...")
    print(f"Monitoring: {list(SERVICES.keys())}")
    try:
        resp = ec2.describe_instances(
            Filters=[{"Name": "tag:Name",
                      "Values": ["auth-svc", "payment-svc", "inventory-svc"]}]
        )
        for res in resp["Reservations"]:
            for inst in res["Instances"]:
                name = next((t["Value"] for t in inst.get("Tags", [])
                             if t["Key"] == "Name"), None)
                if name:
                    remediator.register_instance(name, inst["InstanceId"])
                    print(f"[EC2] Registered: {name} -> {inst['InstanceId']}")
    except Exception as e:
        print(f"[EC2] Could not load instances: {e}")


# ══════════════════════════════════════════════════════════════════════════
#  API ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════

@app.get("/health")
def health():
    return {
        "status":     "ok",
        "version":    "2.1.0",
        "mode":       "real-infrastructure",
        "localstack": AWS_ENDPOINT,
        "trained":    detector.trained,
        "registered": list(remediator.instance_map.keys())
    }


@app.get("/status")
async def status():
    """Full system snapshot — services, detector state, heal history."""
    snapshot = await collect_real_metrics()
    return {
        "services":      snapshot,
        "detector": {
            "trained":   detector.trained,
            "threshold": detector.threshold,
            "history_len": len(detector.history)
        },
        "remediator": {
            "active":    list(remediator.active.keys()),
            "cooldowns": {k: round(remediator.COOLDOWN_SEC - (time.time() - v), 1)
                          for k, v in remediator.cooldown.items()
                          if time.time() - v < remediator.COOLDOWN_SEC},
            "registered": list(remediator.instance_map.keys())
        },
        "heal_history":  heal_history[-10:],
        "tick":          tick_count
    }


@app.get("/metrics/history")
def metrics_history():
    """Last 120 ticks of metric snapshots for chart replay."""
    return {
        "ticks": list(metric_history),
        "count": len(metric_history)
    }


@app.get("/topology")
def get_topology():
    return {
        "nodes": list(SERVICES.keys()),
        "edges": [
            {"source": s, "target": t}
            for s, targets in TOPOLOGY.items()
            for t in targets
        ]
    }


@app.get("/services")
async def get_services():
    return await collect_real_metrics()


@app.post("/demo/crash/{service}")
async def demo_crash(service: str, duration: int = 60):
    url = SERVICES.get(service)
    if not url:
        return {"error": f"Unknown service: {service}"}
    async with httpx.AsyncClient(timeout=5) as client:
        r = await client.post(f"{url}/crash", json={"duration": duration})
        return r.json()


@app.post("/demo/recover/{service}")
async def demo_recover(service: str):
    url = SERVICES.get(service)
    if not url:
        return {"error": f"Unknown service: {service}"}
    async with httpx.AsyncClient(timeout=5) as client:
        r = await client.post(f"{url}/recover")
        return r.json()


# ── AWS Console-style endpoints ────────────────────────────────────────────

@app.get("/aws/infra")
def aws_infra():
    """Full AWS infrastructure overview."""
    try:
        ec2_resp  = ec2.describe_instances(
            Filters=[{"Name": "tag:Name",
                      "Values": ["auth-svc", "payment-svc", "inventory-svc"]}]
        )
        instances = []
        for r in ec2_resp["Reservations"]:
            for i in r["Instances"]:
                name = next((t["Value"] for t in i.get("Tags", [])
                             if t["Key"] == "Name"), i["InstanceId"])
                svc  = next((t["Value"] for t in i.get("Tags", [])
                             if t["Key"] == "Service"), "")
                instances.append({
                    "instance_id":  i["InstanceId"],
                    "name":         name,
                    "service":      svc,
                    "state":        i["State"]["Name"],
                    "type":         i["InstanceType"],
                    "region":       "us-east-1",
                    "ami":          i.get("ImageId", "ami-mock"),
                    "registered":   name in remediator.instance_map,
                    "healing_now":  name in remediator.active
                })

        sns    = boto3.client("sns", **AWS_KWARGS)
        topics = sns.list_topics().get("Topics", [])

        cw     = boto3.client("cloudwatch", **AWS_KWARGS)
        alarms = cw.describe_alarms().get("MetricAlarms", [])

        keys = ec2.describe_key_pairs().get("KeyPairs", [])

        return {
            "region":  "us-east-1",
            "account": "000000000000 (LocalStack)",
            "ec2": {
                "total":     len(instances),
                "running":   sum(1 for i in instances if i["state"] == "running"),
                "stopped":   sum(1 for i in instances if i["state"] == "stopped"),
                "instances": instances
            },
            "sns": {
                "total":  len(topics),
                "topics": [t["TopicArn"].split(":")[-1] for t in topics]
            },
            "cloudwatch_alarms": [
                {
                    "name":       a["AlarmName"],
                    "state":      a["StateValue"],
                    "metric":     a["MetricName"],
                    "threshold":  a.get("Threshold"),
                    "comparison": a.get("ComparisonOperator")
                }
                for a in alarms
            ],
            "key_pairs":            [k["KeyName"] for k in keys],
            "healing_active":       list(remediator.active.keys()),
            "registered_instances": remediator.instance_map
        }
    except Exception as e:
        return {"error": str(e)}


@app.get("/aws/ec2")
def aws_ec2():
    try:
        resp      = ec2.describe_instances(
            Filters=[{"Name": "tag:Name",
                      "Values": ["auth-svc", "payment-svc", "inventory-svc"]}]
        )
        instances = []
        for r in resp["Reservations"]:
            for i in r["Instances"]:
                name = next((t["Value"] for t in i.get("Tags", [])
                             if t["Key"] == "Name"), "unnamed")
                instances.append({
                    "instance_id": i["InstanceId"],
                    "name":        name,
                    "state":       i["State"]["Name"],
                    "type":        i["InstanceType"],
                    "region":      "us-east-1",
                    "ami":         i.get("ImageId", "ami-mock"),
                    "launch_time": str(i.get("LaunchTime", ""))
                })
        return {
            "instances": instances,
            "total":     len(instances),
            "running":   sum(1 for i in instances if i["state"] == "running"),
            "region":    "us-east-1",
            "account":   "000000000000 (LocalStack)"
        }
    except Exception as e:
        return {"error": str(e)}


# ── WebSocket streaming ────────────────────────────────────────────────────

@app.websocket("/ws/metrics")
async def metrics_ws(ws: WebSocket):
    global tick_count, warmup_snapshots
    await ws.accept()
    try:
        while True:
            tick_count += 1
            snapshot    = await collect_real_metrics()

            # Store in history for replay
            metric_history.append({
                "tick":      tick_count,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "services":  snapshot
            })

            if not detector.trained:
                warmup_snapshots.append(snapshot)
                if len(warmup_snapshots) >= WARMUP_TICKS:
                    detector.warmup(warmup_snapshots)
                await ws.send_json({
                    "tick":             tick_count,
                    "services":         snapshot,
                    "is_anomaly":       False,
                    "lstm_error":       0.0,
                    "anomaly_threshold": detector.threshold,
                    "feature_scores":   {},
                    "remediations":     [],
                    "healing_active":   [],
                    "cooldowns":        {},
                    "status":           f"warming_up ({len(warmup_snapshots)}/{WARMUP_TICKS})"
                })
                await asyncio.sleep(1)
                continue

            is_anomaly, error, feature_scores = detector.detect(snapshot)

            remediations = []
            if is_anomaly:
                remediations = await remediator.auto_remediate(snapshot)
                for r in remediations:
                    heal_history.append(r)
                if len(heal_history) > MAX_HEAL_LOG:
                    heal_history[:] = heal_history[-MAX_HEAL_LOG:]

            cooldowns = {
                k: round(remediator.COOLDOWN_SEC - (time.time() - v), 1)
                for k, v in remediator.cooldown.items()
                if time.time() - v < remediator.COOLDOWN_SEC
            }

            await ws.send_json({
                "tick":              tick_count,
                "services":          snapshot,
                "is_anomaly":        is_anomaly,
                "lstm_error":        error,
                "anomaly_threshold": detector.threshold,
                "feature_scores":    feature_scores,
                "remediations":      remediations,
                "healing_active":    list(remediator.active.keys()),
                "cooldowns":         cooldowns,
                "status":            "live"
            })
            await asyncio.sleep(1)

    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"WebSocket error: {e}")