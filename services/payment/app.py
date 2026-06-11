import os, time, random, json, psutil, boto3
from flask import Flask, jsonify, request
from datetime import datetime, timezone

app = Flask(__name__)

LOG_GROUP  = "/aiops/services"
LOG_STREAM = "payment-service"

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
    except: pass
    try:
        cw.create_log_stream(logGroupName=LOG_GROUP, logStreamName=LOG_STREAM)
    except: pass

def push_log(level, message, latency_ms=0, status_code=200):
    try:
        cw.put_log_events(
            logGroupName=LOG_GROUP,
            logStreamName=LOG_STREAM,
            logEvents=[{
                "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
                "message": json.dumps({
                    "timestamp":   datetime.now(timezone.utc).isoformat(),
                    "service":     "payment-service",
                    "level":       level,
                    "message":     message,
                    "latency_ms":  round(latency_ms, 2),
                    "status_code": status_code,
                    "request_id":  f"req-{random.randint(10000,99999)}"
                })
            }]
        )
    except: pass

SERVICE_STATE = {"degraded": False, "crash_until": 0}

def startup():
    ensure_log_group()
    push_log("INFO", "auth-service started", 0, 200)

with app.app_context():
    startup()

@app.route("/health")
def health():
    if SERVICE_STATE["degraded"] and time.time() < SERVICE_STATE["crash_until"]:
        push_log("ERROR", "payment health check failed", 8000, 500)
        return jsonify({"status": "degraded", "service": "payment-service"}), 500
    SERVICE_STATE["degraded"] = False
    push_log("INFO", "payment health OK", random.gauss(100, 20), 200)
    return jsonify({"status": "healthy", "service": "payment-service"}), 200

@app.route("/metrics")
def metrics():
    cpu = psutil.cpu_percent(interval=0.1)
    mem = psutil.virtual_memory()
    latency = random.gauss(100, 20)
    if SERVICE_STATE["degraded"]:
        latency = random.uniform(3000, 8000)
        cpu = min(100, cpu + random.uniform(50, 70))
    return jsonify({
        "service":        "payment-service",
        "cpu_percent":    round(cpu, 2),
        "memory_percent": round(mem.percent, 2),
        "latency_ms":     round(abs(latency), 2),
        "error_rate":     0.9 if SERVICE_STATE["degraded"] else round(random.uniform(0, 0.03), 3),
        "status":         "degraded" if SERVICE_STATE["degraded"] else "healthy"
    })

@app.route("/charge", methods=["POST"])
def charge():
    start = time.time()
    if SERVICE_STATE["degraded"]:
        time.sleep(random.uniform(3, 6))
        push_log("ERROR", "charge failed — timeout", (time.time()-start)*1000, 500)
        return jsonify({"error": "payment timeout"}), 500
    time.sleep(random.uniform(0.08, 0.2))
    latency = (time.time() - start) * 1000
    push_log("INFO", "charge processed", latency, 200)
    return jsonify({"charge_id": f"ch_{random.randint(100000,999999)}", "latency_ms": latency}), 200

@app.route("/crash", methods=["POST"])
def crash():
    duration = request.json.get("duration", 60) if request.is_json else 60
    SERVICE_STATE["degraded"]    = True
    SERVICE_STATE["crash_until"] = time.time() + duration
    push_log("ERROR", f"payment-service crash injected for {duration}s", 9999, 500)
    return jsonify({"message": f"payment-service degraded for {duration}s"}), 200

@app.route("/recover", methods=["POST"])
def recover():
    SERVICE_STATE["degraded"]    = False
    SERVICE_STATE["crash_until"] = 0
    push_log("INFO", "payment-service recovered", 80, 200)
    return jsonify({"message": "payment-service recovered"}), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5002, debug=False)
