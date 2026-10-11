# CastleArq Desktop

CastleArq Desktop is the graphical front-end for CastleArq. This repository is
the **Phase 1** skeleton: a minimal, runnable PySide6 application shell with
clean module boundaries and basic tests. Core integration is intentionally not
implemented yet.

## Status

Phase 1 (application skeleton). The following are **not** implemented and are
deliberately out of scope for this phase:

- Real HTTP communication with CastleArq Core.
- Starting, stopping, or managing a Core process.
- Model discovery and model acquisition.
- Chat, inference, or any simulated conversation.
- SQLite (or any) conversation persistence.

The main window shows a clear placeholder stating that Core integration is not
yet implemented. There is no fake model data and no misleading claim that Core
is connected.

## Requirements

- Python 3.10 or newer (developed and verified against the system Python).
- [PySide6](https://pypi.org/project/PySide6/) (the GUI dependency).

## Project layout

```text
src/castlearq_desktop/
├── __init__.py            # Package metadata and application identity constants.
├── __main__.py            # Enables `python -m castlearq_desktop`.
├── app.py                 # Application bootstrap and Qt lifecycle.
├── ui/
│   ├── __init__.py
│   └── main_window.py     # Main window with a Core-integration placeholder.
├── core/
│   ├── __init__.py
│   ├── http_client.py     # HTTP adapter boundary (no real requests).
│   └── process_manager.py # Core process-management boundary (no processes).
└── storage/
    ├── __init__.py
    └── conversation_store.py  # Persistence boundary (no database).
tests/
└── test_application_structure.py
```

### Module responsibilities

- `app.py` — creates the `QApplication`, sets the application/organization
  identity, shows the main window, and runs the Qt event loop. It owns the
  application lifecycle only.
- `ui/` — presentation. The main window is a minimal, functional layout with a
  placeholder for the future model-library and chat screens. UI code does not
  perform HTTP, process management, or persistence.
- `core/http_client.py` — defines the future HTTP adapter boundary. In Phase 1
  every request method raises `NotImplementedError`; no network activity occurs.
- `core/process_manager.py` — defines the future Core process-management
  boundary. In Phase 1 `start`/`stop` raise `NotImplementedError` and no process
  is ever launched.
- `storage/conversation_store.py` — defines the future persistence boundary. In
  Phase 1 no database is created and nothing is read or written.

## Development

Install the package in editable mode together with the test extra (this installs
PySide6 and pytest):

```bash
python -m pip install -e ".[test]"
```

Run the application:

```bash
python -m castlearq_desktop
# or, if installed:
castlearq-desktop
```

Run the tests:

```bash
python -m pytest
```

The non-GUI tests import no GUI code and run even when PySide6 is absent; the
GUI smoke test is skipped automatically when PySide6 is not installed.

## License

No license has been selected for this phase.
