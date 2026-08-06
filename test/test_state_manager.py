import _bootstrap  # noqa: F401

import threading

from desktop_qt_ui.services.state_manager import AppStateKey, StateManager


def test_signal_callback_does_not_hold_state_lock():
    manager = StateManager()
    key = AppStateKey.STATUS_MESSAGE
    callback_completed = threading.Event()
    callback_values = []

    class ProbeSignal:
        def emit(self, value):
            def read_state():
                callback_values.append(manager.get_state(key))
                callback_completed.set()

            reader = threading.Thread(target=read_state)
            reader.start()
            reader.join(timeout=1)

    manager._signal_map[key] = ProbeSignal()
    manager.set_state(key, "updated")

    assert callback_completed.is_set()
    assert callback_values == ["updated"]
