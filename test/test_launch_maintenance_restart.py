import _bootstrap  # noqa: F401

import importlib.util

import pytest


ROOT = _bootstrap.ROOT


def load_launch(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'packaging' / 'launch.py')
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('action', ['install', 'update'])
def test_restart_maintenance_uses_absolute_script_and_resume_action(action, tmp_path, monkeypatch):
    launch = load_launch(f'launch_maintenance_restart_{action}_test')
    script_path = tmp_path / 'packaging' / 'launch.py'
    script_path.parent.mkdir()
    script_path.touch()
    exec_calls = []

    monkeypatch.setattr(launch, '__file__', str(script_path))
    monkeypatch.setattr(launch.os, 'execv', lambda executable, args: exec_calls.append((executable, args)))

    launch.restart_maintenance(action)

    assert exec_calls == [(
        launch.sys.executable,
        [launch.sys.executable, str(script_path.resolve()), '--maintenance', f'--resume-{action}'],
    )]


def test_restart_maintenance_rejects_unknown_action(monkeypatch):
    launch = load_launch('launch_maintenance_restart_invalid_test')
    monkeypatch.setattr(launch.os, 'execv', lambda *_: pytest.fail('must not restart'))

    with pytest.raises(ValueError, match='Unsupported maintenance resume action'):
        launch.restart_maintenance('unknown')
