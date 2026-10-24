import json
from unittest import mock

from castlearq_desktop.core.openvino_client import (
    CoreOpenVINOClient, OpenVINOClientError, translate_error,
    OPENVINO_INFER_PATH,
)


def make_transport(status, body, raise_exc=None):
    def t(method, path, body_bytes, timeout):
        if raise_exc is not None:
            raise raise_exc
        return status, body
    return t


def test_valid_request_and_response():
    body = json.dumps({"text": "hello", "model_id": "m", "effective_device": "unknown"}).encode()
    c = CoreOpenVINOClient("http://x", transport=make_transport(200, body))
    r = c.infer("m", "Hi", device="GPU", max_tokens=4)
    assert r.text == "hello"
    assert r.model_id == "m"
    assert r.effective_device == "unknown"


def test_request_body_construction():
    captured = {}
    def t(method, path, body_bytes, timeout):
        captured["method"] = method
        captured["path"] = path
        captured["body"] = json.loads(body_bytes)
        return 200, json.dumps({"text":"ok","model_id":"m","effective_device":"CPU"}).encode()
    c = CoreOpenVINOClient("http://x", transport=t)
    c.infer("m", "Hi", device="CPU", max_tokens=8)
    assert captured["method"] == "POST"
    assert captured["path"] == OPENVINO_INFER_PATH
    assert captured["body"] == {"model_id":"m","prompt":"Hi","device":"CPU","max_tokens":8}


def test_validation():
    c = CoreOpenVINOClient("http://x", transport=make_transport(200, b"{}"))
    for kw in [dict(model_id="",prompt="Hi"), dict(model_id="m",prompt=""), dict(model_id="m",prompt="Hi",device="TPU"), dict(model_id="m",prompt="Hi",max_tokens=0)]:
        try:
            c.infer(**kw)
            assert False
        except OpenVINOClientError:
            pass


def test_http_errors():
    for status in (400,404,422,503,500):
        body = json.dumps({"error":"boom"}).encode()
        c = CoreOpenVINOClient("http://x", transport=make_transport(status, body))
        try:
            c.infer("m","Hi")
            assert False
        except OpenVINOClientError as e:
            assert e.status == status


def test_transport_failure():
    c = CoreOpenVINOClient("http://x", transport=make_transport(None, None, raise_exc=OSError("net")))
    try:
        c.infer("m","Hi")
        assert False
    except OpenVINOClientError as e:
        assert e.status is None


def test_malformed_response():
    c = CoreOpenVINOClient("http://x", transport=make_transport(200, b"{bad"))
    try:
        c.infer("m","Hi")
        assert False
    except OpenVINOClientError:
        pass
