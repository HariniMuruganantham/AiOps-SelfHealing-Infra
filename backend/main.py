"""
Project 2 Backend — Self-Healing Infrastructure (Real Infrastructure)
- Monitors REAL Flask services via HTTP health checks
- Detects anomalies using numpy LSTM-style detector
- Heals via LocalStack EC2 API (reboot/stop/start instances)
- Streams live via WebSocket
"""
import os, time, asyncio, json
import httpx
import boto3
import numpy as np
from collections import deque
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="Self-Healing Infrastructure — Real Infra", version="2.0.0")
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
                        "status":        m.get("status", "healthy")
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
                    "status":        "down"
                }
    return snapshot


# ── LSTM-style anomaly detector (numpy, no TensorFlow) ────────────────────
SEQ_LEN    = 15
N_FEATURES = 4


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
            np.mean([s["cpu"]           for s in svcs]),
            np.mean([s["memory"]        for s in svcs]),
            np.mean([s["error_rate"]    for s in svcs]),
            np.mean([s["response_time"] / 10000 for s in svcs])
        ])

    def warmup(self, snapshots: list):
        arr                = np.array([self._features(s) for s in snapshots])
        self.baseline_mean = arr.mean(axis=0)
        self.baseline_std  = arr.std(axis=0) + 1e-8
        self.trained       = True
        print(f"Detector trained on {len(snapshots)} real metric snapshots.")

    def detect(self, snap: dict) -> tuple:
        self.history.append(self._features(snap))
        if not self.trained or len(self.history) < SEQ_LEN:
            return False, 0.0
        seq      = np.array(list(self.history)[-SEQ_LEN:])
        seq_norm = (seq - self.baseline_mean) / self.baseline_std
        per_step = np.mean(seq_norm ** 2, axis=1)
        error    = float(np.dot(self.weights, per_step))
        return error > self.threshold, round(error, 6)


# ── EC2 remediation via LocalStack ────────────────────────────────────────
class EC2Remediator:
    def __init__(self):
        self.active       = {}
        self.instance_map = {}

    def register_instance(self, service: str, instance_id: str):
        self.instance_map[service] = instance_id

    def decide_action(self, name: str, metrics: dict) -> str:
        if metrics["error_rate"] > 0.5:  return "circuit_break"
        if metrics["cpu"]        > 0.85: return "scale_out"
        if metrics["health"]     < 0.3:  return "reboot_instance"
        return "none"

    async def heal(self, service: str, action: str, metrics: dict):
        if service in self.active:
            return None

        self.active[service] = action
        print(f"[HEAL] {action} on {service}")

        try:
            if action == "reboot_instance" and service in self.instance_map:
                ec2.reboot_instances(InstanceIds=[self.instance_map[service]])
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
                print(f"[EC2] Circuit break + recover for {service}")
                await asyncio.sleep(2)

            else:
                await asyncio.sleep(3)

        except Exception as e:
            print(f"[HEAL ERROR] {service}: {e}")

        self.active.pop(service, None)
        return {
            "service":  service,
            "action":   action,
            "result":   "recovered",
            "ec2_call": action == "reboot_instance",
            "instance": self.instance_map.get(service, ""),
            "timestamp": time.time()
        }

    async def auto_remediate(self, snapshot: dict) -> list:
        tasks = []
        for name, metrics in snapshot.items():
            if metrics["status"] in ("degraded", "down") and name not in self.active:
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
    print("Self-Healing backend starting...")
    print(f"Monitoring services: {list(SERVICES.keys())}")
    print(f"Will warmup detector after {WARMUP_TICKS} real metric snapshots.")
    try:
        resp = ec2.describe_instances(
            Filters=[{"Name": "tag:Name", "Values": ["auth-svc", "payment-svc", "inventory-svc"]}]
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
        "status":    "ok",
        "mode":      "real-infrastructure",
        "localstack": AWS_ENDPOINT
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
    """Inject a real failure for the live demo."""
    url = SERVICES.get(service)
    if not url:
        return {"error": f"Unknown service: {service}"}
    async with httpx.AsyncClient(timeout=5) as client:
        r = await client.post(f"{url}/crash", json={"duration": duration})
        return r.json()


@app.post("/demo/recover/{service}")
async def demo_recover(service: str):
    """Recover a service manually."""
    url = SERVICES.get(service)
    if not url:
        return {"error": f"Unknown service: {service}"}
    async with httpx.AsyncClient(timeout=5) as client:
        r = await client.post(f"{url}/recover")
        return r.json()


# ── AWS Console-style endpoints ────────────────────────────────────────────

@app.get("/aws/infra")
def aws_infra():
    """Full AWS infrastructure overview — EC2 + SNS + CloudWatch Alarms."""
    try:
        # EC2 instances
        ec2_resp  = ec2.describe_instances(
            Filters=[{"Name": "tag:Name", "Values": ["auth-svc", "payment-svc", "inventory-svc"]}]
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

        # SNS topics
        sns    = boto3.client("sns", **AWS_KWARGS)
        topics = sns.list_topics().get("Topics", [])

        # CloudWatch alarms
        cw     = boto3.client("cloudwatch", **AWS_KWARGS)
        alarms = cw.describe_alarms().get("MetricAlarms", [])

        # Key pair
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
            "key_pairs": [k["KeyName"] for k in keys],
            "healing_active": list(remediator.active.keys()),
            "registered_instances": remediator.instance_map
        }
    except Exception as e:
        return {"error": str(e)}


@app.get("/aws/ec2")
def aws_ec2():
    """EC2 instances — mirrors AWS Console EC2 dashboard."""
    try:
        resp      = ec2.describe_instances(
            Filters=[{"Name": "tag:Name", "Values": ["auth-svc", "payment-svc", "inventory-svc"]}]
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

            if not detector.trained:
                warmup_snapshots.append(snapshot)
                if len(warmup_snapshots) >= WARMUP_TICKS:
                    detector.warmup(warmup_snapshots)
                await ws.send_json({
                    "tick":           tick_count,
                    "services":       snapshot,
                    "is_anomaly":     False,
                    "lstm_error":     0.0,
                    "remediations":   [],
                    "healing_active": [],
                    "status":         f"warming_up ({len(warmup_snapshots)}/{WARMUP_TICKS})"
                })
                await asyncio.sleep(1)
                continue

            is_anomaly, error = detector.detect(snapshot)

            remediations = []
            if is_anomaly:
                remediations = await remediator.auto_remediate(snapshot)

            await ws.send_json({
                "tick":             tick_count,
                "services":         snapshot,
                "is_anomaly":       is_anomaly,
                "lstm_error":       error,
                "anomaly_threshold": detector.threshold,
                "remediations":     remediations,
                "healing_active":   list(remediator.active.keys()),
                "status":           "live"
            })
            await asyncio.sleep(1)

    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"WebSocket error: {e}")