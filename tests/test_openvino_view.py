from unittest.mock import MagicMock

import pytest

from castlearq_desktop.core.openvino_client import OpenVINOInferResult
from castlearq_desktop.core.openvino_service import OpenVINOService
from castlearq_desktop.ui.openvino_view import OpenVINOView


def test_view_build(qtbot):
    svc = MagicMock(spec=OpenVINOService)
    view = OpenVINOView(svc)
    view.set_inference_supported(True)
    qtbot.addWidget(view)
    assert view is not None


def test_set_models_populates_combo(qtbot):
    svc = MagicMock(spec=OpenVINOService)
    view = OpenVINOView(svc)
    view.set_inference_supported(True)
    qtbot.addWidget(view)
    view.set_models(["a", "b"])
    assert [view._model.itemText(i) for i in range(view._model.count())] == ["a", "b"]
    assert view._model.currentText() == "a"


def test_set_models_replaces(qtbot):
    svc = MagicMock(spec=OpenVINOService)
    view = OpenVINOView(svc)
    view.set_inference_supported(True)
    qtbot.addWidget(view)
    view.set_models(["a", "b"])
    view.set_models(["c"])
    assert [view._model.itemText(i) for i in range(view._model.count())] == ["c"]


def test_set_models_empty(qtbot):
    svc = MagicMock(spec=OpenVINOService)
    view = OpenVINOView(svc)
    view.set_inference_supported(True)
    qtbot.addWidget(view)
    view.set_models([])
    assert view._model.itemText(0) == "Sin modelos OpenVINO disponibles"
    assert view._model.placeholderText() == "Selecciona un modelo OpenVINO"
    assert "artefactos OpenVINO locales" in view._status.text()
    assert "Actualiza la biblioteca local" in view._model_status.text()
    view._on_run()
    svc.infer.assert_not_called()


def test_set_models_error_shows_local_library_failure(qtbot):
    svc = MagicMock(spec=OpenVINOService)
    view = OpenVINOView(svc)
    view.set_inference_supported(True)
    qtbot.addWidget(view)

    view.set_load_error("No interpreter configured.")

    assert "No se pudo cargar la biblioteca local" in view._status.text()
    assert "No interpreter configured" in view._status.text()
    assert view._run.isEnabled() is False
    view._prompt.setText("hola")
    view._on_run()
    svc.infer.assert_not_called()


def test_pending_artifact_selection_is_applied_when_library_models_arrive(qtbot):
    svc = MagicMock(spec=OpenVINOService)
    view = OpenVINOView(svc)
    view.set_inference_supported(True)
    qtbot.addWidget(view)

    view.set_selected_model("model-b", "OpenVINO IR")
    view.set_loading()
    assert view._pending_model_id == "model-b"

    view.set_models(
        ["model-a", "model-b"],
        formats={"model-a": "GGUF", "model-b": "OpenVINO IR"},
    )

    assert view._model.currentText() == "model-b"
    assert view._pending_model_id is None
    assert view.get_selected_format() == "OpenVINO IR"
    assert "Modelo seleccionado: model-b" in view._status.text()


def test_refresh_preserves_the_current_openvino_model(qtbot):
    svc = MagicMock(spec=OpenVINOService)
    view = OpenVINOView(svc)
    view.set_inference_supported(True)
    qtbot.addWidget(view)
    view.set_models(["model-a", "model-b"])
    view._model.setCurrentText("model-b")

    view.set_models(["model-a", "model-b"])

    assert view._model.currentText() == "model-b"


def test_set_models_empty_then_valid(qtbot):
    svc = MagicMock(spec=OpenVINOService)
    view = OpenVINOView(svc)
    view.set_inference_supported(True)
    qtbot.addWidget(view)
    view.set_models([])
    view.set_models(["x"])
    assert [view._model.itemText(i) for i in range(view._model.count())] == ["x"]


def test_build_prompt_chatml_markers_and_generation_prefix(qtbot):
    svc = MagicMock(spec=OpenVINOService)
    view = OpenVINOView(svc)
    view.set_inference_supported(True)
    qtbot.addWidget(view)

    prompt = view._build_prompt("model-a", "Hola mundo")
    expected = "<|im_start|>user\nHola mundo<|im_end|>\n<|im_start|>assistant\n"
    assert prompt == expected
    assert prompt.endswith("<|im_start|>assistant\n")


def test_build_prompt_preserves_turn_order_and_current_message_once(qtbot):
    svc = MagicMock(spec=OpenVINOService)
    view = OpenVINOView(svc)
    view.set_inference_supported(True)
    qtbot.addWidget(view)

    view._history_model = "model-a"
    view._history = [
        ("Pregunta 1", "Respuesta 1"),
        ("Pregunta 2", "Respuesta 2"),
    ]

    current = "Pregunta 3"
    prompt = view._build_prompt("model-a", current)

    expected = (
        "<|im_start|>user\nPregunta 1<|im_end|>\n"
        "<|im_start|>assistant\nRespuesta 1<|im_end|>\n"
        "<|im_start|>user\nPregunta 2<|im_end|>\n"
        "<|im_start|>assistant\nRespuesta 2<|im_end|>\n"
        "<|im_start|>user\nPregunta 3<|im_end|>\n"
        "<|im_start|>assistant\n"
    )
    assert prompt == expected
    assert prompt.count(current) == 1
    assert prompt.endswith("<|im_start|>assistant\n")


def test_history_limit_capped_at_ten_turns(qtbot):
    svc = MagicMock(spec=OpenVINOService)
    view = OpenVINOView(svc)
    view.set_inference_supported(True)
    qtbot.addWidget(view)

    view._history_model = "model-a"
    view._history = [(f"U{i}", f"A{i}") for i in range(15)]

    prompt = view._build_prompt("model-a", "Current")
    # Should only retain the last 10 turns (5..14)
    assert "U4" not in prompt
    assert "A4" not in prompt
    for i in range(5, 15):
        assert f"<|im_start|>user\nU{i}<|im_end|>\n" in prompt
        assert f"<|im_start|>assistant\nA{i}<|im_end|>\n" in prompt
    assert prompt.endswith("<|im_start|>user\nCurrent<|im_end|>\n<|im_start|>assistant\n")


def test_history_reset_on_model_change(qtbot):
    svc = MagicMock(spec=OpenVINOService)
    view = OpenVINOView(svc)
    view.set_inference_supported(True)
    qtbot.addWidget(view)

    view._history_model = "model-a"
    view._history = [("Pregunta previa", "Respuesta previa")]

    prompt = view._build_prompt("model-b", "Nueva pregunta")
    assert "Pregunta previa" not in prompt
    assert "Respuesta previa" not in prompt
    assert view._history == []
    assert view._history_model == "model-b"
    assert prompt == "<|im_start|>user\nNueva pregunta<|im_end|>\n<|im_start|>assistant\n"


def test_reset_conversation(qtbot):
    svc = MagicMock(spec=OpenVINOService)
    view = OpenVINOView(svc)
    view.set_inference_supported(True)
    qtbot.addWidget(view)

    view._history_model = "model-a"
    view._history = [("U1", "A1")]
    view._pending_user_text = "Pending"

    view.reset_conversation()
    assert view._history == []
    assert view._pending_user_text is None


def test_inference_failure_does_not_add_to_history(qtbot):
    svc = MagicMock(spec=OpenVINOService)
    view = OpenVINOView(svc)
    view.set_inference_supported(True)
    qtbot.addWidget(view)
    view.set_models(["model-a"], formats={"model-a": "OpenVINO IR"})

    view._prompt.setText("Mi pregunta")
    view._on_run()

    assert view._pending_user_text == "Mi pregunta"
    assert view._history == []

    # Simulate inference failure
    view._on_failed("Error de conexión con OpenVINO")

    assert view._pending_user_text is None
    assert view._history == []


def test_inference_completion_excludes_think_from_history(qtbot):
    svc = MagicMock(spec=OpenVINOService)
    view = OpenVINOView(svc)
    view.set_inference_supported(True)
    qtbot.addWidget(view)
    view.set_models(["model-a"], formats={"model-a": "OpenVINO IR"})

    view._prompt.setText("¿Qué es 2+2?")
    view._on_run()

    # Model outputs thought block + final text
    raw_response = "<think>Calculando 2+2 = 4</think>El resultado es 4."
    view._on_completed(raw_response, "CPU")

    assert len(view._history) == 1
    user_turn, assistant_turn = view._history[0]
    assert user_turn == "¿Qué es 2+2?"
    assert assistant_turn == "El resultado es 4."
    assert "<think>" not in assistant_turn
    assert "Calculando" not in assistant_turn


def test_normal_response_saved_correctly(qtbot):
    svc = MagicMock(spec=OpenVINOService)
    view = OpenVINOView(svc)
    view.set_inference_supported(True)
    qtbot.addWidget(view)
    view.set_models(["model-a"], formats={"model-a": "OpenVINO IR"})

    view._prompt.setText("Hola")
    view._on_run()

    view._on_completed("¡Hola! ¿Cómo te ayudo?", "CPU")

    assert len(view._history) == 1
    assert view._history[0] == ("Hola", "¡Hola! ¿Cómo te ayudo?")
    assert view._pending_user_text is None


def test_unclosed_think_is_not_saved_and_does_not_contaminate_next_prompt(qtbot):
    svc = MagicMock(spec=OpenVINOService)
    view = OpenVINOView(svc)
    view.set_inference_supported(True)
    qtbot.addWidget(view)
    view.set_models(["model-a"], formats={"model-a": "OpenVINO IR"})

    # 1. Turno con <think> incompleto / sin cerrar
    view._prompt.setText("Pregunta con think incompleto")
    view._on_run()

    unclosed_output = "<think>Razonando algo que se cortó a la mitad"
    view._on_completed(unclosed_output, "CPU")

    # No se guarda en historial
    assert view._history == []
    assert view._pending_user_text is None

    # 2. Siguiente turno no debe contener nada de razonamiento incompleto
    next_prompt = view._build_prompt("model-a", "Siguiente pregunta")
    assert "<think>" not in next_prompt
    assert "Razonando algo que se cortó" not in next_prompt
    assert next_prompt == "<|im_start|>user\nSiguiente pregunta<|im_end|>\n<|im_start|>assistant\n"


def test_only_think_block_without_final_response_is_not_saved(qtbot):
    svc = MagicMock(spec=OpenVINOService)
    view = OpenVINOView(svc)
    view.set_inference_supported(True)
    qtbot.addWidget(view)
    view.set_models(["model-a"], formats={"model-a": "OpenVINO IR"})

    view._prompt.setText("Pregunta solo think")
    view._on_run()

    # El modelo emitió un bloque think pero ninguna respuesta final
    only_think = "<think>Solo razonamiento interno sin respuesta final</think>"
    view._on_completed(only_think, "CPU")

    # Al no haber texto de respuesta final, no debe guardarse en el historial
    assert view._history == []
    assert view._pending_user_text is None


def test_orphan_close_think_is_not_saved(qtbot):
    svc = MagicMock(spec=OpenVINOService)
    view = OpenVINOView(svc)
    view.set_inference_supported(True)
    qtbot.addWidget(view)
    view.set_models(["model-a"], formats={"model-a": "OpenVINO IR"})

    view._prompt.setText("Pregunta cierre huerfano")
    view._on_run()

    orphan_close = "Texto con cierre huerfano </think>"
    view._on_completed(orphan_close, "CPU")

    assert view._history == []
    assert view._pending_user_text is None


def test_max_tokens_default_value_and_range(qtbot):
    svc = MagicMock(spec=OpenVINOService)
    view = OpenVINOView(svc)
    view.set_inference_supported(True)
    qtbot.addWidget(view)

    assert view._max_tokens.value() == 1024
    assert view._max_tokens.minimum() == 1
    assert view._max_tokens.maximum() == 2048


def test_on_run_sends_default_1024_max_tokens(qtbot):
    svc = MagicMock(spec=OpenVINOService)
    view = OpenVINOView(svc)
    view.set_inference_supported(True)
    qtbot.addWidget(view)
    view.set_models(["model-a"], formats={"model-a": "OpenVINO IR"})

    view._prompt.setText("Pregunta con default 1024")
    view._on_run()

    svc.infer.assert_called_once()
    _, kwargs = svc.infer.call_args
    assert kwargs.get("max_tokens") == 1024


def test_on_run_sends_custom_max_tokens(qtbot):
    svc = MagicMock(spec=OpenVINOService)
    view = OpenVINOView(svc)
    view.set_inference_supported(True)
    qtbot.addWidget(view)
    view.set_models(["model-a"], formats={"model-a": "OpenVINO IR"})

    view._max_tokens.setValue(2000)
    view._prompt.setText("Pregunta con 2000 tokens")
    view._on_run()

    svc.infer.assert_called_once()
    _, kwargs = svc.infer.call_args
    assert kwargs.get("max_tokens") == 2000


def test_on_run_validates_max_tokens_bounds(qtbot):
    svc = MagicMock(spec=OpenVINOService)
    view = OpenVINOView(svc)
    view.set_inference_supported(True)
    qtbot.addWidget(view)
    view.set_models(["model-a"], formats={"model-a": "OpenVINO IR"})

    view._prompt.setText("Pregunta fuera de rango")
    # Forzar un valor fuera de rango programáticamente en el spinbox
    view._max_tokens.setRange(-10, 5000)
    view._max_tokens.setValue(0)
    view._on_run()

    svc.infer.assert_not_called()
    assert "max_tokens debe estar entre 1 y 2048" in view._status.text()


def test_truncated_by_length_with_incomplete_think_shows_notice(qtbot):
    svc = MagicMock(spec=OpenVINOService)
    view = OpenVINOView(svc)
    view.set_inference_supported(True)
    qtbot.addWidget(view)
    view.set_models(["model-a"], formats={"model-a": "OpenVINO IR"})

    # Simular resultado de servicio con finish_reason="length"
    mock_res = MagicMock()
    mock_res.finish_reason = "length"
    svc.last_result = mock_res

    view._prompt.setText("Pregunta compleja")
    view._on_run()

    truncated_think = "<think>Pensando profundamente y se corta antes de terminar"
    view._on_completed(truncated_think, "CPU")

    # 1. No se muestra el think sin cerrar como respuesta normal
    assert "<think>" not in view._output.toPlainText()
    assert "incompleta o malformada" in view._output.toPlainText()

    # 2. El razonamiento incompleto se preserva en la sección de razonamiento
    assert not view._reasoning_toggle.isHidden()
    assert "Pensando profundamente" in view._reasoning_output.toPlainText()

    # 3. El historial no se contamina
    assert view._history == []
    assert view._pending_user_text is None
    assert "Motivo de finalización: length" in view._status.text()


def test_truncated_by_length_with_valid_final_response_is_displayed_normally(qtbot):
    svc = MagicMock(spec=OpenVINOService)
    view = OpenVINOView(svc)
    view.set_inference_supported(True)
    qtbot.addWidget(view)
    view.set_models(["model-a"], formats={"model-a": "OpenVINO IR"})

    mock_res = MagicMock()
    mock_res.finish_reason = "length"
    svc.last_result = mock_res

    view._prompt.setText("Genera una lista larga")
    view._on_run()

    # Respuesta donde el modelo cerró el think y generó texto normal pero se quedó sin tokens
    output_text = "<think>Planificando lista</think>1. Item 1\n2. Item 2\n3. Item 3"
    view._on_completed(output_text, "CPU")

    # Debe mostrarse la respuesta final generada, no la advertencia de corte de razonamiento
    assert "1. Item 1" in view._output.toPlainText()
    assert "incompleta o malformada" not in view._output.toPlainText()

    # El razonamiento debe estar en el toggle
    assert not view._reasoning_toggle.isHidden()
    assert "Planificando lista" in view._reasoning_output.toPlainText()

    # Se guarda en el historial la respuesta parcial pero válida
    assert len(view._history) == 1
    assert view._history[0] == ("Genera una lista larga", "1. Item 1\n2. Item 2\n3. Item 3")
    assert "Motivo de finalización: length" in view._status.text()


def test_unclosed_think_with_finish_reason_none_shows_notice_and_status(qtbot):
    svc = MagicMock(spec=OpenVINOService)
    view = OpenVINOView(svc)
    view.set_inference_supported(True)
    qtbot.addWidget(view)
    view.set_models(["model-a"], formats={"model-a": "OpenVINO IR"})

    # finish_reason es None (caso típico de OpenVINO en Core)
    mock_res = MagicMock()
    mock_res.finish_reason = None
    svc.last_result = mock_res

    view._prompt.setText("Pregunta OpenVINO con think incompleto")
    view._on_run()

    truncated = "<think>Razonando en OpenVINO pero se cortó"
    view._on_completed(truncated, "unverified")

    # 1. Aviso de salida incompleta
    assert "<think>" not in view._output.toPlainText()
    assert "La salida parece incompleta o malformada; no se mostrará como respuesta final." in view._output.toPlainText()

    # 2. Razonamiento preservado
    assert not view._reasoning_toggle.isHidden()
    assert "Razonando en OpenVINO pero se cortó" in view._reasoning_output.toPlainText()

    # 3. No guardado en historial
    assert view._history == []
    assert view._pending_user_text is None

    # 4. Estado con finish_reason="no disponible" y dispositivo
    assert view._status.text() == "Completado. Motivo de finalización: no disponible. Dispositivo: No verificado"


def test_malformed_think_with_finish_reason_none(qtbot):
    svc = MagicMock(spec=OpenVINOService)
    view = OpenVINOView(svc)
    view.set_inference_supported(True)
    qtbot.addWidget(view)
    view.set_models(["model-a"], formats={"model-a": "OpenVINO IR"})

    mock_res = MagicMock()
    mock_res.finish_reason = None
    svc.last_result = mock_res

    view._prompt.setText("Pregunta malformada")
    view._on_run()

    # Cierre huérfano
    malformed = "Texto con cierre huérfano </think>"
    view._on_completed(malformed, "CPU")

    assert "La salida parece incompleta o malformada; no se mostrará como respuesta final." in view._output.toPlainText()
    assert view._history == []
    assert view._status.text() == "Completado. Motivo de finalización: no disponible. Dispositivo: CPU"


def test_valid_response_with_finish_reason_stop(qtbot):
    svc = MagicMock(spec=OpenVINOService)
    view = OpenVINOView(svc)
    view.set_inference_supported(True)
    qtbot.addWidget(view)
    view.set_models(["model-a"], formats={"model-a": "OpenVINO IR"})

    mock_res = MagicMock()
    mock_res.finish_reason = "stop"
    svc.last_result = mock_res

    view._prompt.setText("Pregunta normal")
    view._on_run()

    output = "<think>Pensamiento cerrado</think>Respuesta final normal."
    view._on_completed(output, "GPU")

    assert view._output.toPlainText() == "Respuesta final normal."
    assert not view._reasoning_toggle.isHidden()
    assert view._reasoning_output.toPlainText() == "Pensamiento cerrado"
    assert len(view._history) == 1
    assert view._history[0] == ("Pregunta normal", "Respuesta final normal.")
    assert view._status.text() == "Completado. Motivo de finalización: stop. Dispositivo: GPU"


def test_on_run_blocks_gguf_format(qtbot):
    """Un modelo con formato GGUF no puede iniciar inferencia OpenVINO."""
    svc = MagicMock(spec=OpenVINOService)
    view = OpenVINOView(svc)
    view.set_inference_supported(True)
    qtbot.addWidget(view)
    view.set_models(["model-gguf"], formats={"model-gguf": "GGUF"})

    view._prompt.setText("Pregunta sobre modelo GGUF")
    view._on_run()

    svc.infer.assert_not_called()
    assert "no tiene un formato OpenVINO IR admitido" in view._status.text()
    assert "GGUF" in view._status.text()


def test_on_run_blocks_unknown_format(qtbot):
    """Un modelo con formato desconocido no puede iniciar inferencia OpenVINO."""
    svc = MagicMock(spec=OpenVINOService)
    view = OpenVINOView(svc)
    view.set_inference_supported(True)
    qtbot.addWidget(view)
    view.set_models(["model-unk"], formats={"model-unk": None})

    view._prompt.setText("Pregunta sobre modelo desconocido")
    view._on_run()

    svc.infer.assert_not_called()
    assert "no tiene un formato OpenVINO IR admitido" in view._status.text()
    assert "desconocido" in view._status.text()


def test_on_run_blocks_format_not_provided(qtbot):
    """Un modelo sin información de formato no puede iniciar inferencia OpenVINO."""
    svc = MagicMock(spec=OpenVINOService)
    view = OpenVINOView(svc)
    view.set_inference_supported(True)
    qtbot.addWidget(view)
    view.set_models(["model-nofmt"])  # no formats provided

    view._prompt.setText("Pregunta sin formato")
    view._on_run()

    svc.infer.assert_not_called()
    assert "no tiene un formato OpenVINO IR admitido" in view._status.text()


def test_changing_model_updates_format_validation(qtbot):
    """Cambiar de modelo actualiza la validación de formato correctamente."""
    svc = MagicMock(spec=OpenVINOService)
    view = OpenVINOView(svc)
    view.set_inference_supported(True)
    qtbot.addWidget(view)
    view.set_models(
        ["model-ov", "model-gguf"],
        formats={"model-ov": "OpenVINO IR", "model-gguf": "GGUF"},
    )

    # Seleccionar modelo OpenVINO válido
    view._model.setCurrentIndex(0)
    view._update_controls()
    view._prompt.setText("Pregunta válida")
    view._on_run()
    svc.infer.assert_called_once()

    # Cambiar a modelo GGUF - la validación debe bloquear
    svc.reset_mock()
    view._model.setCurrentIndex(1)
    view._update_controls()
    view._prompt.setText("Pregunta sobre GGUF")
    view._on_run()
    svc.infer.assert_not_called()
    assert "no tiene un formato OpenVINO IR admitido" in view._status.text()

    # Volver a modelo OpenVINO - debería funcionar de nuevo
    svc.reset_mock()
    view._model.setCurrentIndex(0)
    view._update_controls()
    view._prompt.setText("Pregunta válida otra vez")
    view._on_run()
    svc.infer.assert_called_once()


def test_ambiguous_model_id_blocks_inference_and_unique_model_still_runs(qtbot):
    svc = MagicMock(spec=OpenVINOService)
    view = OpenVINOView(svc)
    view.set_inference_supported(True)
    qtbot.addWidget(view)
    view.set_models(
        ["unique", "ambiguous"],
        formats={"unique": "OpenVINO IR", "ambiguous": "OpenVINO IR"},
        ambiguous_model_ids={"ambiguous"},
    )
    view._prompt.setText("hola")
    view._model.setCurrentText("ambiguous")
    view._on_run()
    svc.infer.assert_not_called()
    assert "no permite identificar inequívocamente" in view._status.text()

    view._model.setCurrentText("unique")
    view._on_run()
    svc.infer.assert_called_once()


@pytest.mark.parametrize("finish_reason", ["length", "LENGTH", "Length", " length "])
def test_finish_reason_length_normalization_preserves_original(qtbot, finish_reason):
    svc = MagicMock(spec=OpenVINOService)
    view = OpenVINOView(svc)
    view.set_inference_supported(True)
    qtbot.addWidget(view)
    view.set_models(["model-a"], formats={"model-a": "OpenVINO IR"})
    result = MagicMock()
    result.finish_reason = finish_reason
    svc.last_result = result
    view._prompt.setText("pregunta")
    view._on_run()
    view._on_completed("<think>razonamiento</think>", "CPU")
    assert "incompleta o malformada" in view._output.toPlainText()
    assert finish_reason in view._status.text()


@pytest.mark.parametrize("finish_reason", [None, "", "unknown", "stop"])
def test_finish_reason_other_values_do_not_trigger_length_rule(qtbot, finish_reason):
    svc = MagicMock(spec=OpenVINOService)
    view = OpenVINOView(svc)
    view.set_inference_supported(True)
    qtbot.addWidget(view)
    view.set_models(["model-a"], formats={"model-a": "OpenVINO IR"})
    result = MagicMock()
    result.finish_reason = finish_reason
    svc.last_result = result
    view._prompt.setText("pregunta")
    view._on_run()
    view._on_completed("<think>razonamiento</think>", "CPU")
    assert "incompleta o malformada" not in view._output.toPlainText()
    assert view._status.text().endswith(
        f"Motivo de finalización: {finish_reason or 'no disponible'}. Dispositivo: CPU"
    )


def test_unknown_version_keeps_integrated_inference_disabled(qtbot):
    svc = MagicMock(spec=OpenVINOService)
    view = OpenVINOView(svc)
    qtbot.addWidget(view)
    view.set_models(["model-a"], formats={"model-a": "OpenVINO IR"})
    view._prompt.setText("hola")
    view._on_run()
    svc.infer.assert_not_called()
    assert "no está disponible para la versión" in view._status.text()
