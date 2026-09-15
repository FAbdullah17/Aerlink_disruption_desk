from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import httpx
import pytest

from src.ops_client import OpsApiError, OpsClient


class TestOpsClientInit:
    def test_default_url(self):
        c = OpsClient()
        assert c.base_url == "http://127.0.0.1:8642"
        c.close()

    def test_custom_url(self):
        c = OpsClient(base_url="http://localhost:9999")
        assert c.base_url == "http://localhost:9999"
        c.close()

    def test_strips_trailing_slash(self):
        c = OpsClient(base_url="http://localhost:9999/")
        assert c.base_url == "http://localhost:9999"
        c.close()


class TestOpsApiError:
    def test_error_attributes(self):
        e = OpsApiError(404, "not_found", "No booking", {"extra": "data"})
        assert e.status == 404
        assert e.error == "not_found"
        assert e.message == "No booking"
        assert e.data == {"extra": "data"}

    def test_error_str(self):
        e = OpsApiError(400, "bad", "message")
        assert "400" in str(e)


def _mock_response(status_code: int, json_data: dict | None = None, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = text
    return resp


class TestRetryOn503:
    @patch("src.ops_client.time.sleep")
    def test_retries_then_succeeds(self, mock_sleep):
        c = OpsClient()
        responses = [
            _mock_response(503, {"error": "unavailable", "message": "starting up"}),
            _mock_response(200, {"status": "ok"}),
        ]
        c._client.get = MagicMock(side_effect=responses)
        result = c.health()
        assert result == {"status": "ok"}
        assert c._client.get.call_count == 2
        mock_sleep.assert_called_once()

    @patch("src.ops_client.time.sleep")
    def test_retries_three_times_then_raises(self, mock_sleep):
        c = OpsClient()
        c._client.get = MagicMock(return_value=_mock_response(503, {"error": "unavailable"}))
        with pytest.raises(OpsApiError) as exc_info:
            c.health()
        assert exc_info.value.status == 503
        assert c._client.get.call_count == 3


class TestRetryOn429:
    @patch("src.ops_client.time.sleep")
    def test_retries_then_succeeds(self, mock_sleep):
        c = OpsClient()
        responses = [
            _mock_response(429, {"error": "rate_limited"}),
            _mock_response(200, {"results": []}),
        ]
        c._client.get = MagicMock(side_effect=responses)
        result = c.search_bookings("test")
        assert result == {"results": []}
        assert c._client.get.call_count == 2
        mock_sleep.assert_called_once_with(3.0)


class TestRetryOnConnectionError:
    @patch("src.ops_client.time.sleep")
    def test_retries_then_succeeds(self, mock_sleep):
        c = OpsClient()
        c._client.get = MagicMock(
            side_effect=[httpx.ConnectError("Connection refused"), _mock_response(200, {"status": "ok"})]
        )
        result = c.health()
        assert result == {"status": "ok"}
        mock_sleep.assert_called_once()

    @patch("src.ops_client.time.sleep")
    def test_retries_exhausted_raises(self, mock_sleep):
        c = OpsClient()
        c._client.get = MagicMock(side_effect=httpx.ConnectError("Connection refused"))
        with pytest.raises(OpsApiError) as exc_info:
            c.health()
        assert exc_info.value.status == 0
        assert "connection_error" in exc_info.value.error


class TestRateLimiter:
    def test_throttle_waits_when_limit_reached(self):
        c = OpsClient(rate_limit_window=1.0, rate_limit_max=3)
        now = time.time()
        c._request_times = [now - 0.1, now - 0.05, now]
        with patch("src.ops_client.time.sleep") as mock_sleep:
            c._throttle()
            assert mock_sleep.called

    def test_throttle_no_wait_when_under_limit(self):
        c = OpsClient(rate_limit_window=10.0, rate_limit_max=28)
        c._request_times = []
        with patch("src.ops_client.time.sleep") as mock_sleep:
            c._throttle()
            mock_sleep.assert_not_called()
            assert len(c._request_times) == 1

    def test_throttle_prunes_old_entries(self):
        c = OpsClient(rate_limit_window=1.0, rate_limit_max=28)
        old_time = time.time() - 2.0
        c._request_times = [old_time, old_time + 0.1]
        c._throttle()
        assert len(c._request_times) == 1
