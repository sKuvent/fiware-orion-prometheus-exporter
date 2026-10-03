from unittest.mock import Mock

import pytest
import requests

import orion_exporter as oe


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def metric_value(metric, **labels):
    if labels:
        return metric.labels(**labels)._value.get()
    return metric._value.get()


# -----------------------------------------------------------------------------
# Service path
# -----------------------------------------------------------------------------

def test_normalize_service_path_root():
    assert oe.normalize_service_path("root-subserv") == "/"


def test_normalize_service_path_existing_slash():
    assert oe.normalize_service_path("/foo") == "/foo"


def test_normalize_service_path_adds_slash():
    assert oe.normalize_service_path("foo") == "/foo"


# -----------------------------------------------------------------------------
# Number conversion
# -----------------------------------------------------------------------------

def test_number_rejects_bool():
    assert oe._number(True) is None


def test_number_rejects_string():
    assert oe._number("5") is None


def test_number_rejects_none():
    assert oe._number(None) is None


def test_number_accepts_int():
    assert oe._number(5) == 5.0


def test_number_accepts_float():
    assert oe._number(1.5) == 1.5


# -----------------------------------------------------------------------------
# Counter synchronization
# -----------------------------------------------------------------------------

def test_update_counter_initial_value():
    registry = oe.CollectorRegistry()

    metric = oe.Counter(
        "test_initial_total",
        "test",
        registry=registry,
    )

    oe.LAST_SEEN_COUNTERS.clear()

    oe.update_counter(metric, {}, 10)

    assert metric._value.get() == 10


def test_update_counter_only_adds_delta():
    registry = oe.CollectorRegistry()

    metric = oe.Counter(
        "test_delta_total",
        "test",
        registry=registry,
    )

    oe.LAST_SEEN_COUNTERS.clear()

    oe.update_counter(metric, {}, 10)
    oe.update_counter(metric, {}, 15)

    assert metric._value.get() == 15


def test_update_counter_handles_source_reset():
    registry = oe.CollectorRegistry()

    metric = oe.Counter(
        "test_reset_total",
        "test",
        registry=registry,
    )

    oe.LAST_SEEN_COUNTERS.clear()

    oe.update_counter(metric, {}, 10)
    oe.update_counter(metric, {}, 15)

    # Orion counter reset/restart.
    oe.update_counter(metric, {}, 3)

    # Prometheus counter remains monotonic.
    assert metric._value.get() == 18


def test_update_counter_ignores_negative_values():
    registry = oe.CollectorRegistry()

    metric = oe.Counter(
        "test_negative_total",
        "test",
        registry=registry,
    )

    oe.LAST_SEEN_COUNTERS.clear()

    oe.update_counter(metric, {}, -1)

    assert metric._value.get() == 0


def test_update_counter_ignores_non_numeric_values():
    registry = oe.CollectorRegistry()

    metric = oe.Counter(
        "test_invalid_total",
        "test",
        registry=registry,
    )

    oe.LAST_SEEN_COUNTERS.clear()

    oe.update_counter(metric, {}, "invalid")

    assert metric._value.get() == 0


# -----------------------------------------------------------------------------
# /admin/metrics parser
# -----------------------------------------------------------------------------

def test_parse_admin_metrics_endpoint():
    data = {
        "services": {
            "test-service": {
                "subservs": {
                    "test-path": {
                        "incomingTransactions": 10,
                        "incomingTransactionRequestSize": 1000,
                        "incomingTransactionResponseSize": 500,
                        "incomingTransactionErrors": 1,
                        "outgoingTransactions": 5,
                        "outgoingTransactionRequestSize": 600,
                        "outgoingTransactionResponseSize": 300,
                        "outgoingTransactionErrors": 2,
                        "serviceTime": 0.25,
                    }
                }
            }
        }
    }

    labels = {
        "service": "test-service",
        "service_path": "/test-path",
    }

    # Values before parser execution. This keeps the test independent
    # from values left behind by other tests.
    incoming_before = metric_value(
        oe.INCOMING_TRANSACTIONS,
        **labels,
    )

    incoming_errors_before = metric_value(
        oe.INCOMING_ERRORS,
        **labels,
    )

    outgoing_before = metric_value(
        oe.OUTGOING_TRANSACTIONS,
        **labels,
    )

    outgoing_errors_before = metric_value(
        oe.OUTGOING_ERRORS,
        **labels,
    )

    oe.LAST_SEEN_COUNTERS.clear()

    oe.parse_admin_metrics_endpoint(data)

    assert (
        metric_value(oe.INCOMING_TRANSACTIONS, **labels)
        - incoming_before
        == 10
    )

    assert (
        metric_value(oe.INCOMING_ERRORS, **labels)
        - incoming_errors_before
        == 1
    )

    assert (
        metric_value(oe.OUTGOING_TRANSACTIONS, **labels)
        - outgoing_before
        == 5
    )

    assert (
        metric_value(oe.OUTGOING_ERRORS, **labels)
        - outgoing_errors_before
        == 2
    )

    assert metric_value(
        oe.SERVICE_TIME,
        **labels,
    ) == 0.25


def test_parse_admin_metrics_root_service_path():
    data = {
        "services": {
            "root-test-service": {
                "subservs": {
                    "root-subserv": {
                        "serviceTime": 0.1,
                    }
                }
            }
        }
    }

    oe.parse_admin_metrics_endpoint(data)

    assert metric_value(
        oe.SERVICE_TIME,
        service="root-test-service",
        service_path="/",
    ) == 0.1


def test_parse_admin_metrics_handles_missing_services():
    oe.parse_admin_metrics_endpoint({})


def test_parse_admin_metrics_handles_invalid_services():
    oe.parse_admin_metrics_endpoint(
        {
            "services": "invalid"
        }
    )


# -----------------------------------------------------------------------------
# /statistics parser
# -----------------------------------------------------------------------------

def test_parse_statistics_endpoint():
    data = {
        "uptime_in_secs": 100,
        "measuring_interval_in_secs": 30,
        "counters": {
            "jsonRequests": 50,
            "notificationsSent": 20,
            "invalidRequests": 2,
            "requests": {
                "/v2/entities": {
                    "GET": 15,
                }
            },
        },
        "semWait": {
            "request": 1.5,
        },
        "timing": {
            "accumulated": {
                "mongo": 4.5,
            },
            "last": {
                "mongo": 0.02,
                "total": 0.03,
            },
        },
        "notifQueue": {
            "in": 20,
            "out": 20,
            "reject": 0,
            "sentOk": 20,
            "sentError": 0,
            "size": 0,
            "timeInQueue": 1.2,
            "avgTimeInQueue": 0.05,
        },
    }

    oe.LAST_SEEN_COUNTERS.clear()

    oe.parse_statistics_endpoint(data)

    assert metric_value(oe.UPTIME) == 100
    assert metric_value(oe.MEASURING_INTERVAL) == 30
    assert metric_value(oe.NOTIF_QUEUE_SIZE) == 0
    assert metric_value(oe.NOTIF_QUEUE_AVG_TIME) == 0.05

    assert metric_value(
        oe.TIMING_LAST,
        module="mongo",
    ) == 0.02

    assert metric_value(
        oe.TIMING_LAST,
        module="total",
    ) == 0.03


def test_parse_statistics_handles_empty_data():
    oe.parse_statistics_endpoint({})


# -----------------------------------------------------------------------------
# HTTP collection
# -----------------------------------------------------------------------------

def test_fetch_and_parse_success(monkeypatch):
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "uptime_in_secs": 42
    }

    monkeypatch.setattr(
        oe.SESSION,
        "get",
        lambda *args, **kwargs: response,
    )

    ok = oe.fetch_and_parse(
        "statistics",
        "http://orion:1026/statistics",
        oe.parse_statistics_endpoint,
    )

    assert ok is True

    assert metric_value(
        oe.ENDPOINT_UP,
        endpoint="statistics",
    ) == 1

    assert oe.LAST_ENDPOINT_STATUS["statistics"] is True


def test_fetch_and_parse_http_error(monkeypatch):
    response = Mock()

    response.raise_for_status.side_effect = (
        requests.HTTPError("500 Server Error")
    )

    monkeypatch.setattr(
        oe.SESSION,
        "get",
        lambda *args, **kwargs: response,
    )

    ok = oe.fetch_and_parse(
        "statistics",
        "http://orion:1026/statistics",
        oe.parse_statistics_endpoint,
    )

    assert ok is False

    assert metric_value(
        oe.ENDPOINT_UP,
        endpoint="statistics",
    ) == 0

    assert oe.LAST_ENDPOINT_STATUS["statistics"] is False


def test_fetch_and_parse_timeout(monkeypatch):
    def raise_timeout(*args, **kwargs):
        raise requests.Timeout("timeout")

    monkeypatch.setattr(
        oe.SESSION,
        "get",
        raise_timeout,
    )

    ok = oe.fetch_and_parse(
        "statistics",
        "http://orion:1026/statistics",
        oe.parse_statistics_endpoint,
    )

    assert ok is False
    assert oe.LAST_ENDPOINT_STATUS["statistics"] is False


def test_fetch_and_parse_invalid_json_object(monkeypatch):
    response = Mock()
    response.raise_for_status.return_value = None

    # Valid JSON, but wrong top-level structure.
    response.json.return_value = []

    monkeypatch.setattr(
        oe.SESSION,
        "get",
        lambda *args, **kwargs: response,
    )

    ok = oe.fetch_and_parse(
        "statistics",
        "http://orion:1026/statistics",
        oe.parse_statistics_endpoint,
    )

    assert ok is False