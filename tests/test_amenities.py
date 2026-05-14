from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests
from fastapi.testclient import TestClient

from stimmo.data import amenities
from stimmo.data.amenities import AmenityFetchError, _post_overpass
from stimmo.web.app import app


def _make_response(status: int, body: dict | None = None, headers: dict | None = None):
    r = MagicMock(spec=requests.Response)
    r.status_code = status
    r.ok = status < 400
    r.headers = headers or {}
    if body is not None:
        r.json.return_value = body
    r.raise_for_status = MagicMock()
    return r


EMPTY_RESPONSE = {"elements": []}
OK_RESPONSE = {
    "elements": [
        {"type": "node", "id": 1, "lat": 45.46, "lon": 9.19, "tags": {"station": "subway"}}
    ]
}


# ---------------------------------------------------------------------------
# _post_overpass retry scenarios
# ---------------------------------------------------------------------------


@patch("stimmo.data.amenities.time.sleep")
@patch("stimmo.data.amenities.requests.post")
def test_502_then_200_succeeds(mock_post, mock_sleep):
    mock_post.side_effect = [
        _make_response(502),
        _make_response(200, OK_RESPONSE),
    ]
    result = _post_overpass("dummy")
    assert result == OK_RESPONSE
    assert mock_sleep.call_count == 1


@patch("stimmo.data.amenities.time.sleep")
@patch("stimmo.data.amenities.requests.post")
def test_502_then_502_raises(mock_post, mock_sleep):
    mock_post.side_effect = [_make_response(502), _make_response(502)]
    with pytest.raises(AmenityFetchError) as exc_info:
        _post_overpass("dummy")
    assert exc_info.value.attempts == 2


@patch("stimmo.data.amenities.time.sleep")
@patch("stimmo.data.amenities.requests.post")
def test_429_retry_after_honored(mock_post, mock_sleep):
    """429 with Retry-After within budget → retry once."""
    mock_post.side_effect = [
        _make_response(429, headers={"Retry-After": "1"}),
        _make_response(200, OK_RESPONSE),
    ]
    result = _post_overpass("dummy")
    assert result == OK_RESPONSE
    mock_sleep.assert_called_once_with(1.0)


@patch("stimmo.data.amenities.time.sleep")
@patch("stimmo.data.amenities.requests.post")
def test_429_retry_after_too_large_aborts(mock_post, mock_sleep):
    """429 with Retry-After exceeding budget → raises immediately."""
    mock_post.return_value = _make_response(429, headers={"Retry-After": "10"})
    with pytest.raises(AmenityFetchError) as exc_info:
        _post_overpass("dummy")
    assert exc_info.value.attempts == 1
    mock_sleep.assert_not_called()


@patch("stimmo.data.amenities.time.sleep")
@patch("stimmo.data.amenities.requests.post")
def test_soft_failure_remark_triggers_retry(mock_post, mock_sleep):
    """200 with runtime-error remark and no elements → retry."""
    soft_fail = {"remark": "runtime error: Query timed out", "elements": []}
    mock_post.side_effect = [
        _make_response(200, soft_fail),
        _make_response(200, OK_RESPONSE),
    ]
    result = _post_overpass("dummy")
    assert result == OK_RESPONSE
    assert mock_sleep.call_count == 1


@patch("stimmo.data.amenities.time.sleep")
@patch("stimmo.data.amenities.requests.post")
def test_request_exception_then_ok(mock_post, mock_sleep):
    mock_post.side_effect = [
        requests.ConnectionError("refused"),
        _make_response(200, OK_RESPONSE),
    ]
    result = _post_overpass("dummy")
    assert result == OK_RESPONSE


@patch("stimmo.data.amenities.time.sleep")
@patch("stimmo.data.amenities.requests.post")
def test_request_exception_twice_raises(mock_post, mock_sleep):
    mock_post.side_effect = [
        requests.ConnectionError("refused"),
        requests.ConnectionError("refused"),
    ]
    with pytest.raises(AmenityFetchError) as exc_info:
        _post_overpass("dummy")
    assert exc_info.value.attempts == 2


# ---------------------------------------------------------------------------
# /api/amenities web endpoint
# ---------------------------------------------------------------------------


_CLIENT = TestClient(app)


def test_api_amenities_happy_path(monkeypatch):
    monkeypatch.setattr(amenities, "fetch_amenities", lambda lat, lon: amenities.AmenityScore())
    r = _CLIENT.get("/en/api/amenities", params={"lat": 45.464, "lon": 9.190})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "score" in body
    assert "html" in body


def test_api_amenities_failure_path(monkeypatch):
    def _raise(lat, lon):
        raise AmenityFetchError("timeout", attempts=2)

    monkeypatch.setattr(amenities, "fetch_amenities", _raise)
    r = _CLIENT.get("/en/api/amenities", params={"lat": 45.464, "lon": 9.190})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "failed"
    assert body["attempts"] == 2
    assert "timeout" in body["error"]
