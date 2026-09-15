from __future__ import annotations

import os
import time
from typing import Any

import httpx


class OpsApiError(Exception):
    def __init__(self, status: int, error: str, message: str, data: dict | None = None):
        self.status = status
        self.error = error
        self.message = message
        self.data = data or {}
        super().__init__(f"OpsAPI {status}: {error} - {message}")


class OpsClient:
    """HTTP client for the Aerlink Operations API with retry and rate limiting."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        rate_limit_window: float = 10.0,
        rate_limit_max: int = 28,
    ):
        self.base_url = (base_url or os.environ.get("OPS_BASE_URL", "http://127.0.0.1:8642")).rstrip("/")
        self.api_key = api_key or os.environ.get("OPS_API_KEY", "aerlink-ops-local-key")
        self.rate_limit_window = rate_limit_window
        self.rate_limit_max = rate_limit_max
        self._request_times: list[float] = []
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={"X-Ops-Key": self.api_key, "Content-Type": "application/json"},
            timeout=httpx.Timeout(30.0, connect=5.0),
        )

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _throttle(self):
        now = time.time()
        self._request_times = [t for t in self._request_times if now - t < self.rate_limit_window]
        if len(self._request_times) >= self.rate_limit_max:
            sleep_for = self.rate_limit_window - (now - self._request_times[0]) + 0.1
            if sleep_for > 0:
                time.sleep(sleep_for)
        self._request_times.append(time.time())

    def _request(self, method: str, path: str, params: dict[str, str] | None = None, body: dict | None = None) -> dict[str, Any]:
        for attempt in range(3):
            self._throttle()
            try:
                if method == "GET":
                    resp = self._client.get(path, params=params)
                else:
                    resp = self._client.post(path, json=body)

                if resp.status_code >= 400:
                    try:
                        err_data = resp.json()
                    except Exception:
                        err_data = {"error": "unknown", "message": resp.text}

                    if resp.status_code == 503 and attempt < 2:
                        time.sleep(2.0 * (attempt + 1))
                        continue
                    if resp.status_code == 429 and attempt < 2:
                        time.sleep(3.0)
                        continue

                    raise OpsApiError(
                        resp.status_code,
                        err_data.get("error", "unknown"),
                        err_data.get("message", resp.text),
                        err_data,
                    )

                return resp.json()

            except httpx.ConnectError:
                if attempt < 2:
                    time.sleep(2.0 * (attempt + 1))
                    continue
                raise OpsApiError(0, "connection_error", "Could not connect to operations API", {})

        raise OpsApiError(0, "max_retries", "Exceeded retry limit", {})

    # -- Read endpoints --

    def health(self) -> dict:
        return self._request("GET", "/health")

    def search_bookings(self, query: str) -> dict:
        return self._request("GET", "/bookings/search", params={"q": query})

    def get_booking(self, ref: str) -> dict:
        return self._request("GET", f"/bookings/{ref}")

    def get_flight(self, flight_no: str, date: str) -> dict:
        return self._request("GET", f"/flights/{flight_no}", params={"date": date})

    def search_availability(self, origin: str, dest: str, date: str, after: str | None = None, booking_ref: str | None = None, partners: bool = False) -> dict:
        params: dict[str, str] = {"from": origin, "to": dest, "date": date}
        if after:
            params["after"] = after
        if booking_ref:
            params["booking_ref"] = booking_ref
        path = "/flights/availability/partners" if partners else "/flights/availability"
        return self._request("GET", path, params=params)

    def get_entitlements(self, booking_ref: str, passenger_id: str | None = None) -> dict:
        params: dict[str, str] = {"booking_ref": booking_ref}
        if passenger_id:
            params["passenger_id"] = passenger_id
        return self._request("GET", "/entitlements/calculate", params=params)

    def get_customer_history(self, customer_id: str) -> dict:
        return self._request("GET", f"/customers/{customer_id}/history")

    def get_hotel_allocation(self, station: str, night: str) -> dict:
        return self._request("GET", f"/stations/{station}/hotel-allocation", params={"night": night})

    def search_policy(self, query: str, limit: int = 10) -> dict:
        return self._request("GET", "/policy/search", params={"q": query, "limit": str(limit)})

    def get_policy_document(self) -> dict:
        return self._request("GET", "/policy/document")

    def get_disruption_feed(self) -> dict:
        return self._request("GET", "/disruption/feed")

    # -- Write endpoints --

    def rebook(self, booking_ref: str, passenger_ids: list[str], option_id: str, flight_no: str, date: str, cabin: str, fare_gbp: float, notes: str = "") -> dict:
        return self._request("POST", "/rebooking", body={
            "booking_ref": booking_ref,
            "passenger_ids": passenger_ids,
            "option_id": option_id,
            "flight_no": flight_no,
            "date": date,
            "cabin": cabin,
            "fare_gbp": fare_gbp,
            "notes": notes,
        })

    def pay_compensation(self, booking_ref: str, amount_gbp: float, passenger_ids: list[str] | None = None, reason: str = "") -> dict:
        body: dict[str, Any] = {"booking_ref": booking_ref, "amount_gbp": amount_gbp}
        if passenger_ids:
            body["passenger_ids"] = passenger_ids
        if reason:
            body["reason"] = reason
        return self._request("POST", "/payments/compensation", body=body)

    def pay_goodwill(self, booking_ref: str, amount_gbp: float, reason: str) -> dict:
        return self._request("POST", "/payments/goodwill", body={
            "booking_ref": booking_ref, "amount_gbp": amount_gbp, "reason": reason,
        })

    def issue_refund(self, booking_ref: str, passenger_ids: list[str], amount_gbp: float, reason: str = "") -> dict:
        return self._request("POST", "/refunds", body={
            "booking_ref": booking_ref, "passenger_ids": passenger_ids,
            "amount_gbp": amount_gbp, "reason": reason,
        })

    def issue_hotel_voucher(self, booking_ref: str, station: str, night: str, passenger_ids: list[str], notes: str = "") -> dict:
        return self._request("POST", "/vouchers/hotel", body={
            "booking_ref": booking_ref, "station": station, "night": night,
            "passenger_ids": passenger_ids, "notes": notes,
        })

    def escalate(self, summary: str, requested_decision: str, booking_ref: str | None = None, queue: str = "GENERAL", recommendation: str = "", blocking_clause: str = "") -> dict:
        body: dict[str, Any] = {"summary": summary, "requested_decision": requested_decision}
        if booking_ref:
            body["booking_ref"] = booking_ref
        if queue:
            body["queue"] = queue
        if recommendation:
            body["recommendation"] = recommendation
        if blocking_clause:
            body["blocking_clause"] = blocking_clause
        return self._request("POST", "/escalations", body=body)

    def audit(self) -> dict:
        return self._request("GET", "/_audit")

    def reset(self) -> dict:
        return self._request("POST", "/_reset")
