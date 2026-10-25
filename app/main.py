import time
import asyncio
import random
import os
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from db import init_db, db_cursor
from metrics import (
    BOOKINGS_TOTAL,
    BOOKING_FAILURES_TOTAL,
    CANCELLATIONS_TOTAL,
    SEATS_HELD_CURRENT,
    HTTP_REQUEST_DURATION,
    PAYMENT_DURATION_HISTOGRAM,
    PAYMENT_DURATION_SUMMARY,
    DEMO_REQUESTS_TOTAL,
)
from logging_setup import setup_logging, new_request_id, request_id_ctx, log

HOLD_TTL_SECONDS = 120

# --- Fault injection controls for Part E.1 (reproduce a problem) ---
# When enabled, every Nth confirm request gets an extra artificial delay
# added to the payment step, simulating a slow payment provider.
FAULT_ENABLED = os.getenv("FAULT_ENABLED", "false").lower() == "true"
FAULT_EVERY_N = int(os.getenv("FAULT_EVERY_N", "5"))
FAULT_DELAY_SECONDS = float(os.getenv("FAULT_DELAY_SECONDS", "0.5"))
_confirm_call_count = 0

setup_logging()
app = FastAPI(title="Movie Booking API")
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.on_event("startup")
async def on_startup():
    init_db()
    log("INFO", "app started", fault_enabled=FAULT_ENABLED)
    asyncio.create_task(_hold_expiry_loop())


async def _hold_expiry_loop():
    """Background task: releases expired seat holds every 15s, independent
    of incoming traffic, so the seats_held_current gauge stays accurate."""
    while True:
        await asyncio.sleep(15)
        try:
            with db_cursor(commit=True) as cur:
                _expire_stale_holds(cur, time.time())
        except Exception as e:
            log("ERROR", "hold expiry loop failed", error=str(e))


@app.middleware("http")
async def observability_middleware(request: Request, call_next):
    rid = new_request_id()
    token = request_id_ctx.set(rid)
    start = time.time()
    try:
        response = await call_next(request)
    finally:
        duration = time.time() - start
        HTTP_REQUEST_DURATION.labels(endpoint=request.url.path, method=request.method).observe(duration)
        request_id_ctx.reset(token)
    response.headers["X-Request-ID"] = rid
    return response


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


# ---------- Browsing ----------

@app.get("/movies")
def list_movies():
    with db_cursor() as cur:
        rows = cur.execute("SELECT id, title FROM movies").fetchall()
        return [dict(r) for r in rows]


@app.get("/showtimes/{showtime_id}")
def get_showtime(showtime_id: int):
    with db_cursor() as cur:
        st = cur.execute("SELECT * FROM showtimes WHERE id=?", (showtime_id,)).fetchone()
        if not st:
            raise HTTPException(404, "showtime not found")
        seats = cur.execute(
            "SELECT id, seat_label, status FROM seats WHERE showtime_id=?", (showtime_id,)
        ).fetchall()
        return {"showtime": dict(st), "seats": [dict(s) for s in seats]}


# ---------- Holding seats ----------

class HoldRequest(BaseModel):
    seat_ids: list[int]
    user_id: str


@app.post("/showtimes/{showtime_id}/hold")
@limiter.limit("5/minute")
def hold_seats(showtime_id: int, req: HoldRequest, request: Request):
    now = time.time()
    with db_cursor(commit=True) as cur:
        _expire_stale_holds(cur, now)

        placeholders = ",".join("?" for _ in req.seat_ids)
        rows = cur.execute(
            f"SELECT id, status FROM seats WHERE showtime_id=? AND id IN ({placeholders})",
            (showtime_id, *req.seat_ids),
        ).fetchall()

        if len(rows) != len(req.seat_ids):
            BOOKING_FAILURES_TOTAL.labels(reason="invalid_seat").inc()
            raise HTTPException(400, "one or more seats not found")

        unavailable = [r["id"] for r in rows if r["status"] != "free"]
        if unavailable:
            BOOKING_FAILURES_TOTAL.labels(reason="sold_out").inc()
            log("WARNING", "seat hold failed - sold out", showtime_id=showtime_id, seat_ids=unavailable)
            raise HTTPException(409, f"seats already held/booked: {unavailable}")

        for sid in req.seat_ids:
            cur.execute(
                "UPDATE seats SET status='held', held_at=?, held_by=? WHERE id=?",
                (now, req.user_id, sid),
            )
        SEATS_HELD_CURRENT.inc(len(req.seat_ids))
        log("INFO", "seats held", showtime_id=showtime_id, seat_ids=req.seat_ids, user_id=req.user_id)
        return {"held_seat_ids": req.seat_ids, "expires_in_seconds": HOLD_TTL_SECONDS}

def _expire_stale_holds(cur, now):
    """Release holds older than HOLD_TTL_SECONDS. Called opportunistically
    on requests, and also by the background task in worker.py."""
    stale = cur.execute(
        "SELECT id FROM seats WHERE status='held' AND held_at < ?",
        (now - HOLD_TTL_SECONDS,),
    ).fetchall()
    if stale:
        ids = [r["id"] for r in stale]
        placeholders = ",".join("?" for _ in ids)
        cur.execute(f"UPDATE seats SET status='free', held_at=NULL, held_by=NULL WHERE id IN ({placeholders})", ids)
        SEATS_HELD_CURRENT.dec(len(ids))
        log("INFO", "seat holds expired", seat_ids=ids)


# ---------- Confirming a booking (this is where we inject the payment fault) ----------

class ConfirmRequest(BaseModel):
    showtime_id: int
    seat_ids: list[int]
    user_id: str


async def _mock_payment() -> float:
    """Simulate a payment provider call. Returns the duration actually taken."""
    global _confirm_call_count
    _confirm_call_count += 1

    base_delay = random.uniform(0.05, 0.2)
    extra_delay = 0.0
    if FAULT_ENABLED and _confirm_call_count % FAULT_EVERY_N == 0:
        extra_delay = FAULT_DELAY_SECONDS

    total_delay = base_delay + extra_delay
    await asyncio.sleep(total_delay)
    return total_delay


@app.post("/bookings/confirm")
async def confirm_booking(req: ConfirmRequest):
    with db_cursor(commit=True) as cur:
        placeholders = ",".join("?" for _ in req.seat_ids)
        rows = cur.execute(
            f"SELECT id, status, held_by FROM seats WHERE showtime_id=? AND id IN ({placeholders})",
            (req.showtime_id, *req.seat_ids),
        ).fetchall()

        bad = [r["id"] for r in rows if r["status"] != "held" or r["held_by"] != req.user_id]
        if bad:
            BOOKING_FAILURES_TOTAL.labels(reason="hold_expired").inc()
            log("WARNING", "confirm failed - hold expired or not owned", seat_ids=bad, user_id=req.user_id)
            raise HTTPException(409, f"seats not held by you or hold expired: {bad}")

    # Payment happens outside the DB transaction
    payment_start = time.time()
    with PAYMENT_DURATION_HISTOGRAM.time():
        with PAYMENT_DURATION_SUMMARY.time():
            actual_delay = await _mock_payment()
    payment_duration = time.time() - payment_start

    # Simple mock failure mode: payment "fails" occasionally at random
    if random.random() < 0.02:
        BOOKING_FAILURES_TOTAL.labels(reason="payment_failed").inc()
        log("ERROR", "payment failed", showtime_id=req.showtime_id, seat_ids=req.seat_ids, user_id=req.user_id)
        raise HTTPException(402, "payment failed, please try again")

    with db_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO bookings (showtime_id, user_id, seat_ids, status, created_at) VALUES (?, ?, ?, 'confirmed', ?)",
            (req.showtime_id, req.user_id, ",".join(map(str, req.seat_ids)), time.time()),
        )
        booking_id = cur.lastrowid
        placeholders = ",".join("?" for _ in req.seat_ids)
        cur.execute(f"UPDATE seats SET status='booked' WHERE id IN ({placeholders})", req.seat_ids)

    SEATS_HELD_CURRENT.dec(len(req.seat_ids))
    BOOKINGS_TOTAL.labels(showtime_id=str(req.showtime_id)).inc()
    log(
        "INFO",
        "booking confirmed",
        booking_id=booking_id,
        showtime_id=req.showtime_id,
        seat_ids=req.seat_ids,
        user_id=req.user_id,
        payment_duration_seconds=round(payment_duration, 3),
    )
    return {"booking_id": booking_id, "status": "confirmed", "payment_duration_seconds": round(payment_duration, 3)}


# ---------- Cancelling ----------

@app.post("/bookings/{booking_id}/cancel")
def cancel_booking(booking_id: int):
    with db_cursor(commit=True) as cur:
        booking = cur.execute("SELECT * FROM bookings WHERE id=?", (booking_id,)).fetchone()
        if not booking:
            raise HTTPException(404, "booking not found")
        if booking["status"] == "cancelled":
            # Idempotent: already cancelled, return same result safely
            return {"booking_id": booking_id, "status": "cancelled", "idempotent": True}

        seat_ids = [int(x) for x in booking["seat_ids"].split(",")]
        placeholders = ",".join("?" for _ in seat_ids)
        cur.execute(f"UPDATE seats SET status='free', held_at=NULL, held_by=NULL WHERE id IN ({placeholders})", seat_ids)
        cur.execute("UPDATE bookings SET status='cancelled' WHERE id=?", (booking_id,))

    CANCELLATIONS_TOTAL.labels(showtime_id=str(booking["showtime_id"])).inc()
    log("INFO", "booking cancelled", booking_id=booking_id, showtime_id=booking["showtime_id"], seat_ids=seat_ids)
    return {"booking_id": booking_id, "status": "cancelled", "idempotent": False}


# ---------- Part E.2: cardinality explosion demo endpoint ----------

@app.post("/demo/cardinality-bad")
def demo_cardinality_bad():
    """Each call creates a NEW label value (request_id) -> new time series.
    Use this to demonstrate cardinality explosion, then stop at ~100 calls."""
    rid = new_request_id()
    DEMO_REQUESTS_TOTAL.labels(request_id=rid).inc()
    return {"request_id": rid}
