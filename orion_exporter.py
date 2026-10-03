import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

import requests
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    generate_latest,
)

EXPORTER_VERSION = "1.0.0"

# ------------------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------------------
ORION_BASE_URL = os.getenv("ORION_BASE_URL", "http://orion:1026").rstrip("/")
EXPORTER_BIND = os.getenv("EXPORTER_BIND", "0.0.0.0")
EXPORTER_PORT = int(os.getenv("EXPORTER_PORT", "7001"))
SCRAPE_INTERVAL_SECS = float(os.getenv("SCRAPE_INTERVAL_SECS", "30"))
REQUEST_TIMEOUT_SECS = float(os.getenv("REQUEST_TIMEOUT_SECS", "4"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

URL_STATISTICS = f"{ORION_BASE_URL}/statistics"
URL_ADMIN_METRICS = f"{ORION_BASE_URL}/admin/metrics"

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("orion-exporter")

REGISTRY = CollectorRegistry()

# ------------------------------------------------------------------------------
# Exporter / scrape health
# ------------------------------------------------------------------------------

ORION_UP = Gauge(
    "orion_up",
    "Whether both Orion monitoring endpoints succeeded in the latest collection cycle.",
    registry=REGISTRY,
)

ENDPOINT_UP = Gauge(
    "orion_exporter_endpoint_up",
    "Whether the latest scrape of an Orion endpoint succeeded.",
    ["endpoint"],
    registry=REGISTRY,
)

SCRAPE_ERRORS = Counter(
    "orion_exporter_scrape_errors_total",
    "Number of failed Orion endpoint scrapes.",
    ["endpoint"],
    registry=REGISTRY,
)

SCRAPE_DURATION = Gauge(
    "orion_exporter_scrape_duration_seconds",
    "Duration of the latest Orion endpoint scrape.",
    ["endpoint"],
    registry=REGISTRY,
)

LAST_SUCCESS = Gauge(
    "orion_exporter_last_success_unixtime",
    "Unix timestamp of the latest successful Orion endpoint scrape.",
    ["endpoint"],
    registry=REGISTRY,
)

BUILD_INFO = Gauge(
    "orion_exporter_build_info",
    "Static exporter build information.",
    ["version"],
    registry=REGISTRY,
)
BUILD_INFO.labels(version=EXPORTER_VERSION).set(1)

# ------------------------------------------------------------------------------
# /admin/metrics
# ------------------------------------------------------------------------------

INCOMING_TRANSACTIONS = Counter(
    "orion_incoming_transactions_total",
    "Incoming transactions consumed by Orion.",
    ["service", "service_path"],
    registry=REGISTRY,
)

INCOMING_REQ_SIZE = Counter(
    "orion_incoming_transaction_request_size_bytes_total",
    "Total request bytes of incoming Orion transactions.",
    ["service", "service_path"],
    registry=REGISTRY,
)

INCOMING_RESP_SIZE = Counter(
    "orion_incoming_transaction_response_size_bytes_total",
    "Total response bytes of incoming Orion transactions.",
    ["service", "service_path"],
    registry=REGISTRY,
)

INCOMING_ERRORS = Counter(
    "orion_incoming_transaction_errors_total",
    "Incoming Orion transactions resulting in an error.",
    ["service", "service_path"],
    registry=REGISTRY,
)

SERVICE_TIME = Gauge(
    "orion_service_time_seconds",
    "Average Orion service time reported by /admin/metrics.",
    ["service", "service_path"],
    registry=REGISTRY,
)

OUTGOING_TRANSACTIONS = Counter(
    "orion_outgoing_transactions_total",
    "Outgoing transactions sent by Orion.",
    ["service", "service_path"],
    registry=REGISTRY,
)

OUTGOING_REQ_SIZE = Counter(
    "orion_outgoing_transaction_request_size_bytes_total",
    "Total request bytes of outgoing Orion transactions.",
    ["service", "service_path"],
    registry=REGISTRY,
)

OUTGOING_RESP_SIZE = Counter(
    "orion_outgoing_transaction_response_size_bytes_total",
    "Total response bytes of outgoing Orion transactions.",
    ["service", "service_path"],
    registry=REGISTRY,
)

OUTGOING_ERRORS = Counter(
    "orion_outgoing_transaction_errors_total",
    "Outgoing Orion transactions resulting in an error.",
    ["service", "service_path"],
    registry=REGISTRY,
)

# ------------------------------------------------------------------------------
# /statistics
# ------------------------------------------------------------------------------

UPTIME = Gauge(
    "orion_uptime_seconds",
    "Orion uptime in seconds.",
    registry=REGISTRY,
)

MEASURING_INTERVAL = Gauge(
    "orion_measuring_interval_seconds",
    "Current Orion statistics measuring interval in seconds.",
    registry=REGISTRY,
)

GLOBAL_COUNTERS = Counter(
    "orion_global_requests_total",
    "Selected global Orion counters from /statistics.",
    ["type"],
    registry=REGISTRY,
)

HTTP_REQUESTS = Counter(
    "orion_http_requests_total",
    "Orion HTTP requests by path and method.",
    ["path", "method"],
    registry=REGISTRY,
)

SEM_WAIT = Counter(
    "orion_semaphore_wait_seconds_total",
    "Accumulated time waiting on Orion internal semaphores.",
    ["semaphore"],
    registry=REGISTRY,
)

TIMING_ACCUMULATED = Counter(
    "orion_timing_accumulated_seconds_total",
    "Accumulated Orion processing time by internal module.",
    ["module"],
    registry=REGISTRY,
)

TIMING_LAST = Gauge(
    "orion_timing_last_seconds",
    "Processing time of the last Orion transaction by internal module.",
    ["module"],
    registry=REGISTRY,
)

NOTIF_QUEUE_EVENTS = Counter(
    "orion_notification_queue_events_total",
    "Notification queue events.",
    ["action"],
    registry=REGISTRY,
)

NOTIF_QUEUE_SIZE = Gauge(
    "orion_notification_queue_size",
    "Current number of notifications in the Orion notification queue.",
    registry=REGISTRY,
)

NOTIF_QUEUE_TIME = Counter(
    "orion_notification_queue_time_seconds_total",
    "Accumulated time notifications spent waiting in the Orion notification queue.",
    registry=REGISTRY,
)

NOTIF_QUEUE_AVG_TIME = Gauge(
    "orion_notification_queue_average_time_seconds",
    "Average time a notification waits in the Orion notification queue.",
    registry=REGISTRY,
)

# ------------------------------------------------------------------------------
# Counter synchronization
# ------------------------------------------------------------------------------

LAST_SEEN_COUNTERS: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def update_counter(metric: Counter, labels: dict[str, str], current_value: Any) -> None:
    """
    Mirror an Orion source counter into a Prometheus Counter.

    Orion counters can reset when Orion restarts or its statistics/metrics are
    explicitly reset. Prometheus Counters must only increase, so after a source
    reset we add the new source value instead of decreasing the exported metric.
    """
    current = _number(current_value)
    if current is None or current < 0:
        return

    normalized_labels = tuple(sorted((str(k), str(v)) for k, v in labels.items()))
    state_key = (metric._name, normalized_labels)
    previous = LAST_SEEN_COUNTERS.get(state_key)

    child = metric.labels(**labels) if labels else metric

    if previous is None:
        if current > 0:
            child.inc(current)
    elif current >= previous:
        delta = current - previous
        if delta > 0:
            child.inc(delta)
    else:
        # Orion source counter reset/restart.
        if current > 0:
            child.inc(current)

    LAST_SEEN_COUNTERS[state_key] = current


def set_gauge(metric: Gauge, labels: dict[str, str], value: Any) -> None:
    numeric = _number(value)
    if numeric is None:
        return
    child = metric.labels(**labels) if labels else metric
    child.set(numeric)


# ------------------------------------------------------------------------------
# Parsers
# ------------------------------------------------------------------------------

def normalize_service_path(subservice: str) -> str:
    if subservice == "root-subserv":
        return "/"
    if subservice.startswith("/"):
        return subservice
    return f"/{subservice}"


def parse_admin_metrics_endpoint(data: dict[str, Any]) -> None:
    """
    Parse GET /admin/metrics.

    Structure:
      services -> <service> -> subservs -> <subservice> -> metrics
    """
    services = data.get("services", {})
    if not isinstance(services, dict):
        return

    for service_name, service_data in services.items():
        if not isinstance(service_data, dict):
            continue

        subservices = service_data.get("subservs", {})
        if not isinstance(subservices, dict):
            continue

        for subservice_name, metrics in subservices.items():
            if not isinstance(metrics, dict):
                continue

            labels = {
                "service": str(service_name),
                "service_path": normalize_service_path(str(subservice_name)),
            }

            source_counters = (
                ("incomingTransactions", INCOMING_TRANSACTIONS),
                ("incomingTransactionRequestSize", INCOMING_REQ_SIZE),
                ("incomingTransactionResponseSize", INCOMING_RESP_SIZE),
                ("incomingTransactionErrors", INCOMING_ERRORS),
                ("outgoingTransactions", OUTGOING_TRANSACTIONS),
                ("outgoingTransactionRequestSize", OUTGOING_REQ_SIZE),
                ("outgoingTransactionResponseSize", OUTGOING_RESP_SIZE),
                ("outgoingTransactionErrors", OUTGOING_ERRORS),
            )

            for field, metric in source_counters:
                if field in metrics:
                    update_counter(metric, labels, metrics[field])

            if "serviceTime" in metrics:
                set_gauge(SERVICE_TIME, labels, metrics["serviceTime"])


def parse_statistics_endpoint(data: dict[str, Any]) -> None:
    """Parse GET /statistics."""

    if "uptime_in_secs" in data:
        set_gauge(UPTIME, {}, data["uptime_in_secs"])

    if "measuring_interval_in_secs" in data:
        set_gauge(MEASURING_INTERVAL, {}, data["measuring_interval_in_secs"])

    counters = data.get("counters", {})
    if isinstance(counters, dict):
        for source_name in (
            "jsonRequests",
            "noPayloadRequests",
            "invalidRequests",
            "notificationsSent",
        ):
            if source_name in counters:
                update_counter(
                    GLOBAL_COUNTERS,
                    {"type": source_name},
                    counters[source_name],
                )

        requests_block = counters.get("requests", {})
        if isinstance(requests_block, dict):
            for path, methods in requests_block.items():
                if not isinstance(methods, dict):
                    continue
                for method, count in methods.items():
                    update_counter(
                        HTTP_REQUESTS,
                        {"path": str(path), "method": str(method).upper()},
                        count,
                    )

    sem_wait = data.get("semWait", {})
    if isinstance(sem_wait, dict):
        for semaphore, value in sem_wait.items():
            update_counter(
                SEM_WAIT,
                {"semaphore": str(semaphore)},
                value,
            )

    timing = data.get("timing", {})
    if isinstance(timing, dict):
        accumulated = timing.get("accumulated", {})
        if isinstance(accumulated, dict):
            for module, value in accumulated.items():
                update_counter(
                    TIMING_ACCUMULATED,
                    {"module": str(module)},
                    value,
                )

        last = timing.get("last", {})
        if isinstance(last, dict):
            for module, value in last.items():
                set_gauge(
                    TIMING_LAST,
                    {"module": str(module)},
                    value,
                )

    notif_queue = data.get("notifQueue", {})
    if isinstance(notif_queue, dict):
        for action in ("in", "out", "reject", "sentOk", "sentError"):
            if action in notif_queue:
                update_counter(
                    NOTIF_QUEUE_EVENTS,
                    {"action": action},
                    notif_queue[action],
                )

        if "size" in notif_queue:
            set_gauge(NOTIF_QUEUE_SIZE, {}, notif_queue["size"])

        if "timeInQueue" in notif_queue:
            update_counter(
                NOTIF_QUEUE_TIME,
                {},
                notif_queue["timeInQueue"],
            )

        if "avgTimeInQueue" in notif_queue:
            set_gauge(
                NOTIF_QUEUE_AVG_TIME,
                {},
                notif_queue["avgTimeInQueue"],
            )


# ------------------------------------------------------------------------------
# Orion collection
# ------------------------------------------------------------------------------

SESSION = requests.Session()
SESSION.headers.update({"Accept": "application/json"})

LAST_ENDPOINT_STATUS = {
    "statistics": False,
    "admin_metrics": False,
}


def fetch_and_parse(
    endpoint: str,
    url: str,
    parser: Callable[[dict[str, Any]], None],
) -> bool:
    started = time.monotonic()

    try:
        response = SESSION.get(url, timeout=REQUEST_TIMEOUT_SECS)
        response.raise_for_status()

        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Orion response is not a JSON object")

        parser(payload)

        ENDPOINT_UP.labels(endpoint=endpoint).set(1)
        LAST_SUCCESS.labels(endpoint=endpoint).set(time.time())
        LAST_ENDPOINT_STATUS[endpoint] = True
        return True

    except (requests.RequestException, ValueError, json.JSONDecodeError) as exc:
        ENDPOINT_UP.labels(endpoint=endpoint).set(0)
        SCRAPE_ERRORS.labels(endpoint=endpoint).inc()
        LAST_ENDPOINT_STATUS[endpoint] = False
        logger.warning("Failed to scrape %s (%s): %s", endpoint, url, exc)
        return False

    except Exception:
        ENDPOINT_UP.labels(endpoint=endpoint).set(0)
        SCRAPE_ERRORS.labels(endpoint=endpoint).inc()
        LAST_ENDPOINT_STATUS[endpoint] = False
        logger.exception("Unexpected error while processing %s", endpoint)
        return False

    finally:
        SCRAPE_DURATION.labels(endpoint=endpoint).set(time.monotonic() - started)


def collect_all_orion_data() -> None:
    statistics_ok = fetch_and_parse(
        "statistics",
        URL_STATISTICS,
        parse_statistics_endpoint,
    )

    admin_metrics_ok = fetch_and_parse(
        "admin_metrics",
        URL_ADMIN_METRICS,
        parse_admin_metrics_endpoint,
    )

    ORION_UP.set(1 if statistics_ok and admin_metrics_ok else 0)


# ------------------------------------------------------------------------------
# HTTP endpoints
# ------------------------------------------------------------------------------

class ExporterHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]

        if path == "/metrics":
            body = generate_latest(REGISTRY)
            self.send_response(200)
            self.send_header("Content-Type", CONTENT_TYPE_LATEST)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/health":
            self._json_response(
                200,
                {
                    "status": "ok",
                    "version": EXPORTER_VERSION,
                },
            )
            return

        if path == "/ready":
            ready = all(LAST_ENDPOINT_STATUS.values())
            self._json_response(
                200 if ready else 503,
                {
                    "status": "ready" if ready else "not_ready",
                    "orion": LAST_ENDPOINT_STATUS,
                },
            )
            return

        if path == "/version":
            self._json_response(
                200,
                {"version": EXPORTER_VERSION},
            )
            return

        self._json_response(404, {"error": "not_found"})

    def _json_response(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        logger.debug("HTTP: " + format, *args)


def start_exporter_http_server() -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(
        (EXPORTER_BIND, EXPORTER_PORT),
        ExporterHandler,
    )
    thread = threading.Thread(
        target=server.serve_forever,
        name="exporter-http",
        daemon=True,
    )
    thread.start()
    return server


# ------------------------------------------------------------------------------
# Main
# ------------------------------------------------------------------------------

def main() -> None:
    logger.info("Starting Orion exporter v%s", EXPORTER_VERSION)
    logger.info("Orion base URL: %s", ORION_BASE_URL)
    logger.info(
        "Exporter HTTP server: http://%s:%s",
        EXPORTER_BIND,
        EXPORTER_PORT,
    )
    logger.info("Scrape interval: %.1fs", SCRAPE_INTERVAL_SECS)

    server = start_exporter_http_server()

    try:
        while True:
            cycle_started = time.monotonic()
            collect_all_orion_data()

            elapsed = time.monotonic() - cycle_started
            sleep_for = max(0.0, SCRAPE_INTERVAL_SECS - elapsed)
            time.sleep(sleep_for)

    except KeyboardInterrupt:
        logger.info("Stopping Orion exporter")

    finally:
        server.shutdown()
        server.server_close()
        SESSION.close()


if __name__ == "__main__":
    main()
