"""
Prometheus metrics for the movie booking app.

Covers all four metric types required by the assignment:
- Counter:   bookings_total, booking_failures_total, cancellations_total
- Gauge:     seats_held_current
- Histogram: http_request_duration_seconds, payment_duration_seconds
- Summary:   payment_duration_summary_seconds (avg only; use histogram for p95/p99)
"""
from prometheus_client import Counter, Gauge, Histogram, Summary

# ---- Business metrics (Counters) ----
BOOKINGS_TOTAL = Counter(
    "bookings_total",
    "Total number of confirmed bookings",
    ["showtime_id"],
)

BOOKING_FAILURES_TOTAL = Counter(
    "booking_failures_total",
    "Total number of failed booking attempts",
    ["reason"],  # e.g. sold_out, hold_expired, payment_failed
)

CANCELLATIONS_TOTAL = Counter(
    "cancellations_total",
    "Total number of cancelled bookings",
    ["showtime_id"],
)

# ---- Business metric (Gauge) ----
SEATS_HELD_CURRENT = Gauge(
    "seats_held_current",
    "Number of seats currently held (locked) across all showtimes, awaiting confirmation",
)

# ---- App metric (Histogram) ----
HTTP_REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "Duration of HTTP requests in seconds",
    ["endpoint", "method"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)

PAYMENT_DURATION_HISTOGRAM = Histogram(
    "payment_duration_seconds",
    "Duration of the mock payment step in seconds",
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 1.5, 2.0, 3.0),
)

# ---- App metric (Summary) ----
# Python's Summary has no percentile support - average only.
# We keep the histogram above for p95/p99 and use this Summary just to
# satisfy/explain the "average" reporting requirement in the assignment.
PAYMENT_DURATION_SUMMARY = Summary(
    "payment_duration_summary_seconds",
    "Average duration of the mock payment step (no percentiles - Python Summary limitation)",
)

# Demo counter for the Part E.2 cardinality explosion experiment.
# label_mode toggles between the "bad" high-cardinality version and the fixed version.
DEMO_REQUESTS_TOTAL = Counter(
    "demo_requests_total",
    "Demo counter used for the cardinality explosion experiment",
    ["request_id"],
)
