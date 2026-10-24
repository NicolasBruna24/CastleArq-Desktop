from types import SimpleNamespace
from unittest.mock import MagicMock

from castlearq_desktop.ui.main_window import MainWindow


def _make_window(chat_view=None):
    window = MainWindow.__new__(MainWindow)
    window._chat_view = chat_view
    window._artifact_status_label = MagicMock()
    window._selected_artifact = None
    return window


def _artifact():
    return SimpleNamespace(filename="a.gguf", model_id="m", quantization="q4", installed_at=None, verified=False)


def test_artifact_selected_propagates_same_artifact():
    chat_view = MagicMock()
    window = _make_window(chat_view)
    artifact = _artifact()

    window._on_artifact_selected(artifact)

    assert window._selected_artifact is artifact
    chat_view.set_artifact.assert_called_once_with(artifact)


def test_artifact_unselected_propagates_none():
    chat_view = MagicMock()
    window = _make_window(chat_view)

    window._on_artifact_unselected()

    assert window._selected_artifact is None
    chat_view.set_artifact.assert_called_once_with(None)


def test_artifact_selected_without_chat_does_not_raise():
    window = _make_window(None)
    artifact = _artifact()

    window._on_artifact_selected(artifact)

    assert window._selected_artifact is artifact


def test_artifact_unselected_without_chat_does_not_raise():
    window = _make_window(None)

    window._on_artifact_unselected()

    assert window._selected_artifact is None
