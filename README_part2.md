Link to the Explanation Video --> https://youtu.be/XHH3JI1OzqU?si=nawN-SbyapKoxZ8i 

# Movie Booking - Assignment 2 Extension

Extends the Assignment 1 observability project with REST constraints, rate limiting, idempotency, a resilient Python caller, a gRPC seat-hold service, and Prometheus metrics for gRPC.

## Prerequisites

Assignment 1 stack must be present. Start everything:
```bash
docker compose up --build -d
```

## Part A - REST API Changes

### Rate limiting
The hold endpoint is limited to 5 requests per minute per IP.
Test it by hitting the endpoint 6 times rapidly in Swagger UI at http://localhost:8000/docs — the 6th returns HTTP 429.

### Idempotency
Cancelling an already-cancelled booking returns 200 with `"idempotent": true` instead of an error.
Test it:
```powershell
Invoke-WebRequest -Uri http://localhost:8000/bookings/1/cancel -Method POST -UseBasicParsing
Invoke-WebRequest -Uri http://localhost:8000/bookings/1/cancel -Method POST -UseBasicParsing
```
Second response will include `"idempotent": true`.

### Python caller (timeout + retry + circuit breaker)
```powershell
python caller.py
```
Demonstrates three scenarios:
1. Normal booking flow
2. Retry with exponential backoff on transient failure (1s, 2s, 4s delays)
3. Circuit breaker — opens after 3 consecutive failures, blocks calls for 10s, then recovers

## Part B - gRPC Service

### Build and start
The gRPC service is included in docker-compose.yml and starts automatically:
```bash
docker compose up --build -d
```
gRPC server listens on port 50051. Prometheus metrics exposed on port 8001.

### Run the gRPC client
```powershell
python grpc-client.py
```
Demonstrates four scenarios:
1. Successful hold of seat 12
2. Successful hold of seat 13
3. ALREADY_EXISTS error when re-holding seat 12
4. NOT_FOUND error for invalid showtime 999

Each successful response includes a `request_id` for trace correlation.

### Proto compilation
The `.proto` file is compiled inside the Docker image at build time:
```bash
python -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. booking.proto
```
This generates `booking_pb2.py` and `booking_pb2_grpc.py`.

## Part C - Prometheus Metrics for gRPC

gRPC metrics are exposed at http://localhost:8001/metrics

| Metric                        | Type      | Description                           |
|-------------------------------|-----------|---------------------------------------|
| grpc_requests_total           | Counter   | Total calls by method and status code |
| grpc_request_duration_seconds | Histogram | Latency per call                      |
| grpc_in_flight_requests_total | Counter   | Requests started                      |

Check all Prometheus targets are UP at http://localhost:9090/targets

Grafana dashboard "Booking Overview" at http://localhost:3000 shows gRPC request rate, p95 latency, error rate, and cancellation rate.

## Cleanup
```powershell
docker compose down        # stop, keep volumes
docker compose down -v     # stop and delete all data
```