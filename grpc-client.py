"""
grpc-client.py — Test client for the movie-booking gRPC service.
Run from the project root after installing dependencies:
  pip install grpcio grpcio-tools
  python -m grpc_tools.protoc -I./grpc-service --python_out=. --grpc_python_out=. ./grpc-service/booking.proto
  python grpc-client.py
"""

import grpc
import sys
sys.path.insert(0, "./grpc-service")

import booking_pb2
import booking_pb2_grpc

SERVER = "localhost:50051"
DEADLINE = 5.0  # seconds


def run_request(stub, showtime_id: int, seat_ids: list, user_id: str, label: str):
    print(f"\n--- {label} ---")
    print(f"  Request: showtime={showtime_id}, seats={seat_ids}, user={user_id}")
    try:
        response = stub.HoldSeats(
            booking_pb2.HoldRequest(
                showtime_id=showtime_id,
                seat_ids=seat_ids,
                user_id=user_id,
            ),
            timeout=DEADLINE,
        )
        print(f"  ✓ SUCCESS")
        print(f"    held_seat_ids:      {list(response.held_seat_ids)}")
        print(f"    expires_in_seconds: {response.expires_in_seconds}")
        print(f"    request_id:         {response.request_id}")
    except grpc.RpcError as e:
        print(f"  ✗ FAILED")
        print(f"    gRPC status: {e.code()}")
        print(f"    details:     {e.details()}")


def main():
    channel = grpc.insecure_channel(SERVER)
    stub = booking_pb2_grpc.BookingServiceStub(channel)

    print(f"Connected to gRPC server at {SERVER} (deadline={DEADLINE}s)")

    # 1. Successful request
    run_request(stub, 1, [12], "grpc-user-1", "Test 1: Successful hold")

    # 2. Another successful request
    run_request(stub, 1, [13], "grpc-user-2", "Test 2: Another successful hold")

    # 3. Failed request — seats already held
    run_request(stub, 1, [12], "grpc-user-3", "Test 3: Already held seat (expect ALREADY_EXISTS)")

    # 4. Failed request — invalid showtime
    run_request(stub, 999, [1], "grpc-user-4", "Test 4: Invalid showtime (expect error)")

    channel.close()


if __name__ == "__main__":
    main()