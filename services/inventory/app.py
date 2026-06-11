import os, time, random, json, psutil, boto3
from flask import Flask, jsonify, request
from datetime import datetime, timezone

app = Flask(__name__)

LOG_GROUP  = "/aiops/services"
LOG_STREAM = "inventory-api"

cw = boto3.client(
    "logs",
    endpoint_url=os.getenv("AWS_ENDPOINT_URL", "http://localstack:4566"),
    region_name="us-east-1",
    aws_access_key_id="test",
    aws_secret_access_key="test"
)

def ensure_log_group():
    try: cw.create_log_group(logGroupName=LOG_GROUP)
    except: pass
    try: cw.create_log_stream(logGroupName=LOG_GROUP, logStreamName=LOG_STREAM)
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
                    "service":     "inventory-api",
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
        push_log("ERROR", "inventory health check failed", 6000, 500)
        return jsonify({"status": "degraded", "service": "inventory-api"}), 500
    SERVICE_STATE["degraded"] = False
    push_log("INFO", "inventory health OK", random.gauss(90, 10), 200)
    return jsonify({"status": "healthy", "service": "inventory-api"}), 200

@app.route("/metrics")
def metrics():
    cpu = psutil.cpu_percent(interval=0.1)
    mem = psutil.virtual_memory()
    latency = random.gauss(90, 10)
    if SERVICE_STATE["degraded"]:
        latency = random.uniform(2500, 7000)
        cpu = min(100, cpu + random.uniform(45, 65))
    return jsonify({
        "service":        "inventory-api",
        "cpu_percent":    round(cpu, 2),
        "memory_percent": round(mem.percent, 2),
        "latency_ms":     round(abs(latency), 2),
        "error_rate":     0.85 if SERVICE_STATE["degraded"] else round(random.uniform(0, 0.04), 3),
        "status":         "degraded" if SERVICE_STATE["degraded"] else "healthy"
    })

@app.route("/stock/<item_id>")
def get_stock(item_id):
    start = time.time()
    if SERVICE_STATE["degraded"]:
        time.sleep(random.uniform(2, 5))
        push_log("ERROR", f"stock lookup failed for {item_id}", (time.time()-start)*1000, 500)
        return jsonify({"error": "database timeout"}), 500
    time.sleep(random.uniform(0.05, 0.12))
    latency = (time.time() - start) * 1000
    push_log("INFO", f"stock lookup OK for {item_id}", latency, 200)
    return jsonify({"item_id": item_id, "stock": random.randint(0, 500), "latency_ms": latency}), 200

@app.route("/crash", methods=["POST"])
def crash():
    duration = request.json.get("duration", 60) if request.is_json else 60
    SERVICE_STATE["degraded"]    = True
    SERVICE_STATE["crash_until"] = time.time() + duration
    push_log("ERROR", f"inventory-api crash injected for {duration}s", 9999, 500)
    return jsonify({"message": f"inventory-api degraded for {duration}s"}), 200

@app.route("/recover", methods=["POST"])
def recover():
    SERVICE_STATE["degraded"]    = False
    SERVICE_STATE["crash_until"] = 0
    push_log("INFO", "inventory-api recovered", 70, 200)
    return jsonify({"message": "inventory-api recovered"}), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5003, debug=False)
