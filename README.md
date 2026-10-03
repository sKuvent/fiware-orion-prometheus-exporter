# FIWARE Orion Prometheus Exporter

Prometheus exporter for FIWARE Orion Context Broker statistics and metrics.

The exporter collects operational metrics from the Orion Context Broker endpoints:

- `/statistics`
- `/admin/metrics`

and exposes them in Prometheus format via `/metrics`.

It is intended for monitoring Orion itself rather than exporting NGSI-v2 entity attributes or application data.

## Features

- Orion availability monitoring
- Transaction metrics by FIWARE service and service path
- Incoming and outgoing transaction counters
- Incoming and outgoing transaction errors
- Request and response sizes
- Orion service time
- HTTP request counters
- Notification queue monitoring
- Notification delivery errors and rejects
- Internal Orion timing metrics
- Semaphore wait metrics
- Exporter scrape health metrics
- Prometheus-compatible `/metrics` endpoint
- `/health`, `/ready` and `/version` endpoints
- Handles Orion counter resets while keeping Prometheus counters monotonic
- Configurable through environment variables

## Architecture

```text
FIWARE Orion Context Broker
        │
        ├── /statistics
        │
        └── /admin/metrics
        │
        ▼
FIWARE Orion Prometheus Exporter
        │
        └── /metrics
        │
        ▼
Prometheus
        │
        ▼
Grafana
```

The exporter does not store historical data itself. Metrics are kept in memory and exposed to Prometheus.

## Requirements

For a local Python installation:

- Python 3.10+
- `requests`
- `prometheus-client`

For containerized operation:

- Docker
- Docker Compose

## Configuration

The exporter is configured through environment variables.

| Variable | Default | Description |
|---|---|---|
| `ORION_BASE_URL` | `http://orion:1026` | Base URL of the Orion Context Broker |
| `EXPORTER_BIND` | `0.0.0.0` | Address used by the exporter HTTP server |
| `EXPORTER_PORT` | `7001` | Exporter HTTP port |
| `SCRAPE_INTERVAL_SECS` | `30` | Interval between Orion metric collections |
| `REQUEST_TIMEOUT_SECS` | `4` | HTTP timeout when querying Orion |
| `LOG_LEVEL` | `INFO` | Python logging level |

Example:

```env
ORION_BASE_URL=http://orion:1026
EXPORTER_PORT=7001
SCRAPE_INTERVAL_SECS=30
REQUEST_TIMEOUT_SECS=4
LOG_LEVEL=INFO
```

## Running with Python

Install the dependencies:

```bash
pip install -r requirements.txt
```

Start the exporter:

```bash
ORION_BASE_URL=http://localhost:1026 \
python3 orion_exporter.py
```

The exporter is then available at:

```text
http://localhost:7001
```

## Running with Docker

Build the image:

```bash
docker build -t fiware-orion-prometheus-exporter .
```

Run it:

```bash
docker run --rm \
  -p 7001:7001 \
  -e ORION_BASE_URL=http://orion:1026 \
  fiware-orion-prometheus-exporter
```

If Orion runs on another host, replace `ORION_BASE_URL` accordingly.

## Docker Compose

Example `docker-compose.yml`:

```yaml
services:
  orion-exporter:
    build:
      context: .
      dockerfile: Dockerfile

    container_name: orion-prometheus-exporter

    restart: unless-stopped

    environment:
      ORION_BASE_URL: ${ORION_BASE_URL:-http://orion:1026}
      EXPORTER_PORT: 7001
      SCRAPE_INTERVAL_SECS: 30
      REQUEST_TIMEOUT_SECS: 4
      LOG_LEVEL: INFO

    ports:
      - "7001:7001"

  prometheus:
    image: prom/prometheus:latest

    container_name: prometheus

    restart: unless-stopped

    ports:
      - "9090:9090"

    volumes:
      - ./prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro

    depends_on:
      - orion-exporter
```

Create a `.env` file or copy the example:

```bash
cp .env.example .env
```

Example:

```env
ORION_BASE_URL=http://orion:1026
```

Start the stack:

```bash
docker compose up -d --build
```

## Prometheus Configuration

Example `prometheus/prometheus.yml`:

```yaml
global:
  scrape_interval: 30s

scrape_configs:
  - job_name: orion-exporter
    static_configs:
      - targets:
          - orion-exporter:7001
```

When Prometheus runs in another Docker container, use the Docker service name such as:

```text
orion-exporter:7001
```

instead of:

```text
localhost:7001
```

## HTTP Endpoints

### `/metrics`

Prometheus metrics:

```bash
curl http://localhost:7001/metrics
```

### `/health`

Checks whether the exporter process itself is running:

```bash
curl http://localhost:7001/health
```

Example:

```json
{
  "status": "ok",
  "version": "1.0.0"
}
```

### `/ready`

Checks whether the exporter can successfully collect both Orion monitoring endpoints:

```bash
curl http://localhost:7001/ready
```

Example:

```json
{
  "status": "ready",
  "orion": {
    "statistics": true,
    "admin_metrics": true
  }
}
```

### `/version`

Returns the exporter version:

```bash
curl http://localhost:7001/version
```

## Exported Metrics

Examples of exporter and Orion health metrics:

```text
orion_up
orion_exporter_endpoint_up
orion_exporter_scrape_errors_total
orion_exporter_scrape_duration_seconds
orion_exporter_last_success_unixtime
orion_exporter_build_info
```

Transaction metrics:

```text
orion_incoming_transactions_total
orion_incoming_transaction_errors_total
orion_incoming_transaction_request_size_bytes_total
orion_incoming_transaction_response_size_bytes_total

orion_outgoing_transactions_total
orion_outgoing_transaction_errors_total
orion_outgoing_transaction_request_size_bytes_total
orion_outgoing_transaction_response_size_bytes_total

orion_service_time_seconds
```

Transaction metrics contain FIWARE labels:

```text
service
service_path
```

Example:

```text
orion_incoming_transactions_total{
  service="smartenergy",
  service_path="/"
}
```

Orion statistics:

```text
orion_uptime_seconds
orion_measuring_interval_seconds
orion_global_requests_total
orion_http_requests_total
```

Internal timing metrics:

```text
orion_semaphore_wait_seconds_total
orion_timing_accumulated_seconds_total
orion_timing_last_seconds
```

Notification queue metrics:

```text
orion_notification_queue_events_total
orion_notification_queue_size
orion_notification_queue_time_seconds_total
orion_notification_queue_average_time_seconds
```

The notification event metric uses an `action` label.

Examples:

```text
orion_notification_queue_events_total{action="in"}
orion_notification_queue_events_total{action="out"}
orion_notification_queue_events_total{action="sentOk"}
orion_notification_queue_events_total{action="sentError"}
orion_notification_queue_events_total{action="reject"}
```

## Example PromQL Queries

Incoming transaction rate:

```promql
sum by (service, service_path) (
  rate(orion_incoming_transactions_total[5m])
)
```

Incoming errors:

```promql
sum by (service, service_path) (
  increase(orion_incoming_transaction_errors_total[5m])
)
```

Notification delivery errors:

```promql
increase(
  orion_notification_queue_events_total{
    action="sentError"
  }[5m]
)
```

Rejected notifications:

```promql
increase(
  orion_notification_queue_events_total{
    action="reject"
  }[5m]
)
```

Current notification queue size:

```promql
orion_notification_queue_size
```

Orion availability:

```promql
orion_up
```

## Monitoring Use Case

A typical FIWARE deployment can use the exporter to monitor the Orion part of an IoT data pipeline:

```text
MQTT
  │
  ▼
FIWARE IoT Agent
  │
  ▼
Orion Context Broker
  │
  ├── Application data
  │        │
  │        ▼
  │    QuantumLeap / Database
  │
  └── Operational metrics
           │
           ▼
     Orion Prometheus Exporter
           │
           ▼
       Prometheus
           │
           ▼
        Grafana
```

Prometheus is intended here for operational monitoring and observability.

NGSI-v2 entity attributes and application time-series data are not converted into Prometheus metrics by this exporter.

## Tests

Install the development dependencies:

```bash
pip install -r requirements-dev.txt
```

Run the tests:

```bash
pytest -v
```

The test suite covers, among other things:

- FIWARE service-path normalization
- numeric input validation
- Orion source counter resets
- `/statistics` parsing
- `/admin/metrics` parsing
- HTTP errors
- request timeouts
- invalid Orion responses

## GitHub Actions

Tests can automatically run on pushes and pull requests using:

```text
.github/workflows/tests.yml
```

Example:

```yaml
name: tests

on:
  push:
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - run: pip install -r requirements.txt

      - run: pip install -r requirements-dev.txt

      - run: pytest -v
```

## Project Structure

```text
fiware-orion-prometheus-exporter/
├── .github/
│   └── workflows/
│       └── tests.yml
├── prometheus/
│   └── prometheus.yml
├── tests/
│   └── test_orion_exporter.py
├── .env.example
├── .gitignore
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── requirements-dev.txt
├── orion_exporter.py
└── README.md
```

## Disclaimer

This is an independent open-source project and is not affiliated with, endorsed by, or sponsored by the FIWARE Foundation.

FIWARE and FIWARE-related trademarks belong to their respective owners.