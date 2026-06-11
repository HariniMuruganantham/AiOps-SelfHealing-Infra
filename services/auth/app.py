"""
auth-service — real Flask microservice
Simulates an authentication service with:
- /health   → liveness check
- /metrics  → real CPU/memory via psutil
- /login    → endpoint that can be stressed
- /crash    → endpoint to simulate a failure (for demo)
"""
import os, time, random, logging, boto3, json, psutil
from flask import Flask, jsonify, request
from datetime import datetime, timezone

app = Flask(__name__)

# ── CloudWatch Logs setup ──────────────────────────────────────────────────
LOG_GROUP  = "/aiops/services"
LOG_STREAM = "auth-service"

cw = boto3.client(
    "logs",
    endpoint_url=os.getenv("AWS_ENDPOINT_URL", "http://localstack:4566"),
    region_name="us-east-1",
    aws_access_key_id="test",
    aws_secret_access_key="test"
)

def ensure_log_group():
    try:
        cw.create_log_group(logGroupName=LOG_GROUP)
    except cw.exceptions.ResourceAlreadyExistsException:
        pass
    try:
        cw.create_log_stream(logGroupName=LOG_GROUP, logStreamName=LOG_STREAM)
    except cw.exceptions.ResourceAlreadyExistsException:
        pass

def push_log(level: str, message: str, latency_ms: float = 0, status_code: int = 200):
    try:
        cw.put_log_events(
            logGroupName=LOG_GROUP,
            logStreamName=LOG_STREAM,
            logEvents=[{
                "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
                "message": json.dumps({
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "service":   "auth-service",
                    "level":     level,
                    "message":   message,
                    "latency_ms": round(latency_ms, 2),
                    "status_code": status_code,
                    "request_id": f"req-{random.randint(10000,99999)}"
                })
            }]
        )
    except Exception as e:
        app.logger.warning(f"CloudWatch push failed: {e}")

# State — can be toggled via /crash
SERVICE_STATE = {"degraded": False, "crash_until": 0}

def startup():
    ensure_log_group()
    push_log("INFO", "auth-service started", 0, 200)

with app.app_context():
    startup()

@app.route("/health")
def health():
    now = time.time()
    if SERVICE_STATE["degraded"] and now < SERVICE_STATE["crash_until"]:
        push_log("ERROR", "health check failed — service degraded", 5000, 500)
        return jsonify({"status": "degraded", "service": "auth-service"}), 500

    SERVICE_STATE["degraded"] = False
    latency = random.gauss(80, 15)
    push_log("INFO", "health check OK", latency, 200)
    return jsonify({"status": "healthy", "service": "auth-service",
                    "uptime": time.time()}), 200

@app.route("/metrics")
def metrics():
    cpu    = psutil.cpu_percent(interval=0.1)
    mem    = psutil.virtual_memory()
    latency = random.gauss(80, 15)
    if SERVICE_STATE["degraded"]:
        latency = random.uniform(2000, 6000)
        cpu = min(100, cpu + random.uniform(40, 60))

    return jsonify({
        "service":       "auth-service",
        "cpu_percent":   round(cpu, 2),
        "memory_percent": round(mem.percent, 2),
        "latency_ms":    round(abs(latency), 2),
        "error_rate":    0.8 if SERVICE_STATE["degraded"] else round(random.uniform(0, 0.05), 3),
        "status":        "degraded" if SERVICE_STATE["degraded"] else "healthy"
    })

@app.route("/login", methods=["POST"])
def login():
    start = time.time()
    if SERVICE_STATE["degraded"]:
        time.sleep(random.uniform(2, 5))
        push_log("ERROR", "login failed — high latency", (time.time()-start)*1000, 500)
        return jsonify({"error": "timeout"}), 500

    time.sleep(random.uniform(0.05, 0.15))
    latency = (time.time() - start) * 1000
    push_log("INFO", "login successful", latency, 200)
    return jsonify({"token": "jwt-mock-token", "latency_ms": latency}), 200

@app.route("/crash", methods=["POST"])
def crash():
    """Demo endpoint — degrades the service for 60 seconds"""
    duration = request.json.get("duration", 60) if request.is_json else 60
    SERVICE_STATE["degraded"]    = True
    SERVICE_STATE["crash_until"] = time.time() + duration
    push_log("ERROR", f"service crash injected for {duration}s", 9999, 500)
    return jsonify({"message": f"auth-service degraded for {duration}s"}), 200

@app.route("/recover", methods=["POST"])
def recover():
    SERVICE_STATE["degraded"]    = False
    SERVICE_STATE["crash_until"] = 0
    push_log("INFO", "service recovered", 50, 200)
    return jsonify({"message": "auth-service recovered"}), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=False)
