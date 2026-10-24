import json
from unittest.mock import MagicMock
from PySide6.QtCore import QThreadPool

from castlearq_desktop.core.openvino_client import CoreOpenVINOClient, OpenVINOInferResult
from castlearq_desktop.core.openvino_service import OpenVINOService


def make_client(status=200, body=None, exc=None):
    c = MagicMock(spec=CoreOpenVINOClient)
    if exc is not None:
        c.infer.side_effect = exc
    else:
        payload = json.loads(body) if isinstance(body, (str, bytes)) else body
        c.infer.return_value = OpenVINOInferResult(**payload)
    return c


def test_service_success(qtbot):
    client = make_client(body={"text": "hi", "model_id": "m", "effective_device": "CPU"})
    svc = OpenVINOService(client)
    with qtbot.waitSignal(svc.completed, timeout=2000):
        svc.infer("m", "prompt", device="GPU", max_tokens=4)
    client.infer.assert_called_once_with("m", "prompt", device="GPU", max_tokens=4)
    QThreadPool.globalInstance().waitForDone(2000)


def test_service_error(qtbot):
    from castlearq_desktop.core.openvino_client import OpenVINOClientError
    err = OpenVINOClientError("boom", status=500, reason="core_failure")
    client = make_client(exc=err)
    svc = OpenVINOService(client)
    with qtbot.waitSignal(svc.failed, timeout=2000):
        svc.infer("m", "prompt")
    QThreadPool.globalInstance().waitForDone(2000)


def test_service_busy(qtbot):
    import time
    client = MagicMock(spec=CoreOpenVINOClient)
    def slow(*a, **k):
        time.sleep(0.2)
        return OpenVINOInferResult(text="x", model_id="m", effective_device="CPU")
    client.infer.side_effect = slow
    svc = OpenVINOService(client)
    svc.infer("m", "p")
    # second call while busy should emit failed
    with qtbot.waitSignal(svc.failed, timeout=2000):
        svc.infer("m", "p2")
    QThreadPool.globalInstance().waitForDone(2000)
