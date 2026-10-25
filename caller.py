"""
caller.py — HTTP API caller with timeout, retry, and circuit breaker.
Simulates another service calling the movie-booking HTTP API.

Settings:
  Timeout:        3 seconds per request
  Retry limit:    3 attempts with exponential backoff (1s, 2s, 4s)
  Circuit breaker: opens after 3 consecutive failures,
                   half-opens after 10 seconds recovery period
"""

import time
import requests

BASE_URL = "http://localhost:8000"

# ── Circuit Breaker State ──────────────────────────────────────────────────────
CB_FAILURE_THRESHOLD = 3      # open breaker after this many consecutive failures
CB_RECOVERY_SECONDS  = 10     # wait this long before trying again (half-open)
RETRY_LIMIT          = 3      # max attempts per call
TIMEOUT_SECONDS      = 3.0    # per-request timeout

cb_failures     = 0
cb_open_since   = None   # timestamp when breaker opened


def cb_is_open() -> bool:
    """Return True if the circuit breaker is currently open (blocking calls)."""
    global cb_open_since
    if cb_open_since is None:
        return False
    if time.time() - cb_open_since >= CB_RECOVERY_SECONDS:
        print(f"[CB] Recovery period elapsed — moving to half-open, will try one request.")
        cb_open_since = None   # half-open: allow one probe
        return False
    return True


def cb_record_success():
    global cb_failures, cb_open_since
    cb_failures   = 0
    cb_open_since = None


def cb_record_failure():
    global cb_failures, cb_open_since
    cb_failures += 1
    if cb_failures >= CB_FAILURE_THRESHOLD and cb_open_since is None:
        cb_open_since = time.time()
        print(f"[CB] OPEN — {cb_failures} consecutive failures. "
              f"Blocking calls for {CB_RECOVERY_SECONDS}s.")


# ── Retry with backoff ─────────────────────────────────────────────────────────
def post_with_retry(path: str, payload: dict) -> dict:
    """POST to path with timeout, retry+backoff, and circuit breaker."""
    url = BASE_URL + path

    if cb_is_open():
        print(f"[CB] BLOCKED — breaker is open, skipping {path}")
        return None

    for attempt in range(1, RETRY_LIMIT + 1):
        try:
            print(f"  [attempt {attempt}] POST {path}")
            resp = requests.post(url, json=payload, timeout=TIMEOUT_SECONDS)

            if resp.status_code == 429:
                print(f"  → 429 Rate limited. Retry-After: "
                      f"{resp.headers.get('Retry-After', '?')}s")
                cb_record_failure()
                return None

            if resp.status_code >= 500:
                raise requests.HTTPError(f"Server error {resp.status_code}")

            cb_record_success()
            return resp.json()

        except (requests.Timeout, requests.ConnectionError) as e:
            print(f"  → Transient error: {e}")
            cb_record_failure()
            if cb_is_open():
                print(f"  [CB] Breaker just opened — aborting retries.")
                return None
            if attempt < RETRY_LIMIT:
                wait = 2 ** (attempt - 1)   # 1s, 2s, 4s
                print(f"  → Waiting {wait}s before retry...")
                time.sleep(wait)

        except requests.HTTPError as e:
            print(f"  → HTTP error: {e}")
            cb_record_failure()
            return None

    print(f"  → All {RETRY_LIMIT} attempts failed.")
    return None


# ── Demo scenarios ─────────────────────────────────────────────────────────────
def scenario_normal():
    """Normal flow: hold → confirm → cancel."""
    print("\n=== Scenario 1: Normal booking flow ===")

    print("\n[1] Hold seat 11")
    result = post_with_retry("/showtimes/1/hold",
                             {"seat_ids": [11], "user_id": "caller-bob"})
    print(f"  ← {result}")

    print("\n[2] Confirm booking")
    result = post_with_retry("/bookings/confirm",
                             {"showtime_id": 1, "seat_ids": [11], "user_id": "caller-bob"})
    print(f"  ← {result}")
    if result:
        booking_id = result["booking_id"]
        print(f"\n[3] Cancel booking {booking_id}")
        result = post_with_retry(f"/bookings/{booking_id}/cancel", {})
        print(f"  ← {result}")


def scenario_retry():
    """Simulate a transient failure by hitting a bad endpoint — triggers retry."""
    print("\n=== Scenario 2: Retry on transient failure ===")
    print("Calling a non-existent endpoint to force connection-level retries...")
    result = post_with_retry("/bookings/confirm",
                             {"showtime_id": 1, "seat_ids": [999], "user_id": "caller-x"})
    print(f"  ← {result}")


def scenario_circuit_breaker():
    """Hammer a failing endpoint until the breaker opens, then watch it recover."""
    print("\n=== Scenario 3: Circuit breaker ===")
    print("Sending requests to a bad endpoint to trip the breaker...")

    # temporarily point at wrong port to force connection errors
    original = requests.post
    def bad_post(*a, **kw):
        raise requests.ConnectionError("simulated outage")
    requests.post = bad_post

    for i in range(5):
        print(f"\n  Call {i+1}:")
        post_with_retry("/showtimes/1/hold",
                        {"seat_ids": [], "user_id": "tester"})

    requests.post = original  # restore

    print(f"\n[CB] Waiting {CB_RECOVERY_SECONDS}s for breaker to recover...")
    time.sleep(CB_RECOVERY_SECONDS + 1)

    print("\n  Recovery probe:")
    result = post_with_retry("/showtimes/1/hold",
                             {"seat_ids": [], "user_id": "tester"})
    print(f"  ← {result}")


if __name__ == "__main__":
    scenario_normal()
    scenario_retry()
    scenario_circuit_breaker()