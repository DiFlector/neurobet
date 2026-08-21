"""Unit tests for DeepSeek SSE error / content extraction."""

from app.deepseek.client import (
    DeepSeekStreamError,
    _extract_sse_content,
    _extract_sse_error,
    _is_stream_finished,
    _sanitize_stream_text,
)


def test_extract_rate_limit_error_top_level():
    chunk = {
        "type": "error",
        "content": "Messages too frequent. Try again later.",
        "finish_reason": "rate_limit_reached",
    }
    err = _extract_sse_error(chunk)
    assert err is not None
    assert err["finish_reason"] == "rate_limit_reached"
    exc = DeepSeekStreamError(err["content"], finish_reason=err["finish_reason"])
    assert exc.is_rate_limited


def test_extract_rate_limit_error_nested_v():
    chunk = {
        "v": {
            "type": "error",
            "content": "Messages too frequent. Try again later.",
            "finish_reason": "rate_limit_reached",
        }
    }
    err = _extract_sse_error(chunk)
    assert err is not None
    assert _extract_sse_content(chunk) is None


def test_extract_response_fragment():
    chunk = {
        "v": {
            "response": {
                "fragments": [{"type": "RESPONSE", "content": '{"0":1,"1":0}'}]
            }
        }
    }
    assert _extract_sse_content(chunk) == '{"0":1,"1":0}'


def test_finished_status_not_content():
    chunk = {"p": "response/status", "v": "FINISHED"}
    assert _extract_sse_content(chunk) is None
    assert _is_stream_finished(chunk) is True


def test_sanitize_strips_finished_glue():
    assert _sanitize_stream_text('{"0":1}.FINISHED') == '{"0":1}'
