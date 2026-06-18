#!/usr/bin/env python3
"""
Log generator — simulates Elasticsearch log documents in the real payload format.
Usage: python log_generator.py [--interval SECONDS] [--count N]
"""

import argparse
import json
import random
import time
import uuid
from datetime import datetime, timezone

SERVICES = [
    "security-accounts-service",
    "payments-service",
    "parking-session-service",
    "notification-service",
    "api-gateway-service",
]

NAMESPACES = [
    "streetsmart-global-uat",
    "streetsmart-global-prod",
    "streetsmart-global-staging",
]

MESSAGES = {
    # Bunyan level 20 = debug
    20: [
        "cache miss for key {key}",
        "processing request id={id}",
        "db query executed in {ms}ms",
        "token validation started",
    ],
    # Bunyan level 30 = info
    30: [
        "prometheus - metrics requested",
        "health check passed",
        "user session created for id={id}",
        "request processed successfully",
        "service started on port {port}",
    ],
    # Bunyan level 40 = warn
    40: [
        "slow query detected: {ms}ms on table accounts",
        "retry attempt {n}/3 for downstream call",
        "rate limit approaching for client {id}",
        "deprecated endpoint called: /api/v1/sessions",
        "memory usage above 80%",
    ],
    # Bunyan level 50 = error
    50: [
        "unhandled exception in payment processor",
        "database connection timeout after {ms}ms",
        "downstream service unavailable: {service}",
        "failed to parse request body",
        "authentication token expired for user {id}",
    ],
    # Bunyan level 60 = fatal
    60: [
        "out of memory — process terminating",
        "critical: database pool exhausted",
        "fatal: unable to reach secrets manager",
    ],
}

LEVEL_WEIGHTS = [20, 30, 30, 40, 40, 40, 50, 50, 60]


def random_message(level: int) -> str:
    template = random.choice(MESSAGES[level])
    return template.format(
        key=f"session:{uuid.uuid4().hex[:8]}",
        id=str(random.randint(1000, 9999)),
        ms=random.randint(50, 5000),
        port=random.choice([3000, 8080, 8443]),
        n=random.randint(1, 3),
        service=random.choice(SERVICES),
    )


def generate_log() -> dict:
    service = random.choice(SERVICES)
    namespace = random.choice(NAMESPACES)
    level = random.choice(LEVEL_WEIGHTS)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    pod_hash = uuid.uuid4().hex[:10]
    pod_suffix = uuid.uuid4().hex[:5]
    pod_name = f"{service}-{pod_hash}-{pod_suffix}"
    container_id = uuid.uuid4().hex + uuid.uuid4().hex
    date_index = datetime.now(timezone.utc).strftime("%Y.%m.%d")
    index_suffix = str(random.randint(100000, 999999))
    index = f"fd-{namespace}-applicative-{date_index}-{index_suffix}"
    doc_id = uuid.uuid4().hex[:32]
    msg = random_message(level)

    return {
        "@timestamp": [now],
        "docker.container_id": [container_id],
        "docker.container_id.keyword": [container_id],
        "functionalName": [service],
        "functionalName.keyword": [service],
        "hostname": [pod_name],
        "hostname.keyword": [pod_name],
        "instanceId": [pod_name],
        "instanceId.keyword": [pod_name],
        "kubernetes.container_name": [service],
        "kubernetes.container_name.keyword": [service],
        "kubernetes.host": [f"k8s-hub-flowbird-eu-d0c42b-prod-worker-{random.randint(1, 50)}"],
        "kubernetes.namespace_name": [namespace],
        "kubernetes.namespace_name.keyword": [namespace],
        "kubernetes.pod_id": [str(uuid.uuid4())],
        "kubernetes.pod_name": [pod_name],
        "kubernetes.pod_name.keyword": [pod_name],
        "level": [level],
        "msg": [msg],
        "msg.keyword": [msg],
        "time": [now],
        "type": ["applicative"],
        "type.keyword": ["applicative"],
        "_id": doc_id,
        "_index": index,
        "_score": None,
    }


def main():
    parser = argparse.ArgumentParser(description="Elasticsearch log generator")
    parser.add_argument("--interval", type=float, default=2.0, help="Seconds between logs (default: 2)")
    parser.add_argument("--count", type=int, default=0, help="Number of logs to generate (0 = infinite)")
    args = parser.parse_args()

    LEVEL_LABELS = {20: "DEBUG", 30: "INFO", 40: "WARN", 50: "ERROR", 60: "FATAL"}

    i = 0
    try:
        while True:
            log = generate_log()
            level = log["level"][0]
            label = LEVEL_LABELS.get(level, str(level))
            print(f"[{label:5}] {log['functionalName'][0]} | {log['msg'][0]}")
            print(json.dumps(log))
            print()

            i += 1
            if args.count and i >= args.count:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
