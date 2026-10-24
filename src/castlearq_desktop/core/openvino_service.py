from __future__ import annotations
from typing import Optional
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from castlearq_desktop.core.openvino_client import CoreOpenVINOClient, OpenVINOClientError, OpenVINOInferResult


class OpenVINOService(QObject):
    completed = Signal(str, str)  # text, effective_device
    failed = Signal(str)
    busy_changed = Signal(bool)

    def __init__(self, client: CoreOpenVINOClient, parent=None):
        super().__init__(parent)
        self._client = client
        self._busy = False

    @property
    def is_busy(self):
        return self._busy

    def infer(self, model_id, prompt, device="CPU", max_tokens=8):
        if self._busy:
            self.failed.emit("OpenVINO ocupado.")
            return
        self._busy = True
        self.busy_changed.emit(True)
        bridge = self
        runnable = _OVRunnable(bridge, self._client, model_id, prompt, device, max_tokens)
        QThreadPool.globalInstance().start(runnable)

    def _finish(self, result=None, error=None):
        self._busy = False
        self.busy_changed.emit(False)
        if error is not None:
            self.failed.emit(str(error))
        else:
            self.completed.emit(result.text, result.effective_device)


class _OVRunnable(QRunnable):
    def __init__(self, service, client, model_id, prompt, device, max_tokens):
        super().__init__()
        self._service = service
        self._client = client
        self._model_id = model_id
        self._prompt = prompt
        self._device = device
        self._max_tokens = max_tokens
        self.setAutoDelete(True)

    def run(self):
        try:
            res = self._client.infer(self._model_id, self._prompt, device=self._device, max_tokens=self._max_tokens)
        except OpenVINOClientError as exc:
            self._service._finish(error=exc.user_message)
            return
        except Exception as exc:
            self._service._finish(error=str(exc))
            return
        self._service._finish(result=res)
