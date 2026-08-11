import _bootstrap  # noqa: F401

import importlib.util


ROOT = _bootstrap.ROOT


def load_launch(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "packaging" / "launch.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_runtime_variant_excludes_test_only_dependencies():
    launch = load_launch("launch_runtime_dependency_group_test")
    runtime_names = {
        launch._dep_base_name(dependency).lower()
        for dependency in launch.get_variant_packages("cpu")
    }

    assert "pytest" not in runtime_names
    assert "pytest-anyio" not in runtime_names


def test_default_development_environment_includes_test_group():
    launch = load_launch("launch_default_dependency_group_test")
    data = launch._load_pyproject()

    assert "pytest==9.1.1" in data["dependency-groups"]["test"]
    assert "test" in data["tool"]["uv"]["default-groups"]


def test_gpu_uses_cuda_126_pytorch_index():
    launch = load_launch("launch_gpu_index_test")

    assert launch.get_variant_index_url("gpu") == "https://download.pytorch.org/whl/cu126"
