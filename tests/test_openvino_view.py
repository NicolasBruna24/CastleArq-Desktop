from unittest.mock import MagicMock

from castlearq_desktop.core.openvino_client import OpenVINOInferResult
from castlearq_desktop.core.openvino_service import OpenVINOService
from castlearq_desktop.ui.openvino_view import OpenVINOView


def test_view_build(qtbot):
    svc = MagicMock(spec=OpenVINOService)
    view = OpenVINOView(svc)
    qtbot.addWidget(view)
    assert view is not None


def test_view_run(qtbot):
    svc = MagicMock(spec=OpenVINOService)
    view = OpenVINOView(svc)
    qtbot.addWidget(view)
    view._model.setText("m")
    view._prompt.setText("hi")
    view._run.click()
    svc.infer.assert_called_once()
