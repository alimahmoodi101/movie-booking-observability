"""
gRPC server for seat holding.
Talks directly to the shared SQLite database — same business rules as the HTTP API.
Exposes Prometheus metrics on port 8001.
"""

import time
import os
import sqlite3
import grpc
import logging
import uuid
import json
import datetime
from concurrent import futures

import booking_pb2
import booking_pb2_grpc

from prometheus_client import start_http_server, Counter, Histogram

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format='%(message)s')

def log(severity, message, **kwargs):
    record = {
        "time": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "severity": severity,
        "service": "movie-booking-grpc",
        "message": message,
        **kwargs
    }
    print(json.dumps(record))

# ── Config ─────────────────────────────────────────────────────────────────────
DB_PATH  = os.getenv("DB_PATH", "/data/booking.db")
HOLD_TTL = 120

# ── Metrics ────────────────────────────────────────────────────────────────────
GRPC_REQUESTS = Counter(
    "grpc_requests_total",
    "Total gRPC requests by method and status",
    ["method", "status"],
)

GRPC_DURATION = Histogram(
    "grpc_request_duration_seconds",
    "gRPC request duration in seconds",
    ["method"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0),
)

GRPC_IN_FLIGHT = Counter(
    "grpc_in_flight_requests_total",
    "Total in-flight gRPC requests started",
    ["method"],
)

# ── Database helpers ───────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def expire_stale_holds(cur, now):
    stale = cur.execute(
        "SELECT id FROM seats WHERE status='held' AND held_at < ?",
        (now - HOLD_TTL,),
    ).fetchall()
    if stale:
        ids = [r["id"] for r in stale]
        placeholders = ",".join("?" for _ in ids)
        cur.execute(
            f"UPDATE seats SET status='free', held_at=NULL, held_by=NULL WHERE id IN ({placeholders})",
            ids,
        )


# ── gRPC Servicer ──────────────────────────────────────────────────────────────
class BookingServicer(booking_pb2_grpc.BookingServiceServicer):

    def HoldSeats(self, request, context):
        method = "HoldSeats"
        GRPC_IN_FLIGHT.labels(method=method).inc()
        start = time.time()

        seat_ids = list(request.seat_ids)
        showtime_id = request.showtime_id
        user_id = request.user_id
        request_id = str(uuid.uuid4())[:8]

        log("INFO", "grpc hold request received",
            request_id=request_id,
            showtime_id=showtime_id,
            seat_ids=seat_ids,
            user_id=user_id)

        try:
            conn = get_db()
            cur = conn.cursor()
            now = time.time()

            expire_stale_holds(cur, now)

            placeholders = ",".join("?" for _ in seat_ids)
            rows = cur.execute(
                f"SELECT id, status FROM seats WHERE showtime_id=? AND id IN ({placeholders})",
                (showtime_id, *seat_ids),
            ).fetchall()

            if len(rows) != len(seat_ids):
                conn.close()
                GRPC_REQUESTS.labels(method=method, status="NOT_FOUND").inc()
                GRPC_DURATION.labels(method=method).observe(time.time() - start)
                log("WARNING", "grpc hold failed - seats not found",
                    request_id=request_id,
                    showtime_id=showtime_id)
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details("one or more seats not found for this showtime")
                return booking_pb2.HoldResponse()

            unavailable = [r["id"] for r in rows if r["status"] != "free"]
            if unavailable:
                conn.close()
                GRPC_REQUESTS.labels(method=method, status="ALREADY_EXISTS").inc()
                GRPC_DURATION.labels(method=method).observe(time.time() - start)
                log("WARNING", "grpc hold failed - seats unavailable",
                    request_id=request_id,
                    seat_ids=unavailable)
                context.set_code(grpc.StatusCode.ALREADY_EXISTS)
                context.set_details(f"seats already held/booked: {unavailable}")
                return booking_pb2.HoldResponse()

            for sid in seat_ids:
                cur.execute(
                    "UPDATE seats SET status='held', held_at=?, held_by=? WHERE id=?",
                    (now, user_id, sid),
                )
            conn.commit()
            conn.close()

            GRPC_REQUESTS.labels(method=method, status="OK").inc()
            GRPC_DURATION.labels(method=method).observe(time.time() - start)

            log("INFO", "grpc hold success",
                request_id=request_id,
                seat_ids=seat_ids,
                user_id=user_id)

            return booking_pb2.HoldResponse(
                held_seat_ids=seat_ids,
                expires_in_seconds=HOLD_TTL,
                request_id=request_id,
            )

        except Exception as e:
            GRPC_REQUESTS.labels(method=method, status="INTERNAL").inc()
            GRPC_DURATION.labels(method=method).observe(time.time() - start)
            log("ERROR", "grpc hold exception",
                request_id=request_id,
                error=str(e))
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return booking_pb2.HoldResponse()


# ── Entry point ────────────────────────────────────────────────────────────────
def serve():
    start_http_server(8001)
    print("[gRPC] Prometheus metrics on :8001")

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    booking_pb2_grpc.add_BookingServiceServicer_to_server(BookingServicer(), server)
    server.add_insecure_port("[::]:50051")
    server.start()
    print("[gRPC] Server listening on :50051")
    server.wait_for_termination()


if __name__ == "__main__":
    serve()