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


def test_cuda_groups_use_matching_pytorch_indexes():
    launch = load_launch("launch_cuda_index_test")

    assert launch.get_variant_index_url("cuda13.0") == "https://download.pytorch.org/whl/cu130"
    assert launch.get_variant_index_url("cuda12.6") == "https://download.pytorch.org/whl/cu126"


def test_nvidia_cuda_major_selects_matching_dependency_group():
    launch = load_launch("launch_nvidia_variant_test")

    assert launch.select_nvidia_dependency_variant(13) == "cuda13.0"
    assert launch.select_nvidia_dependency_variant(14) == "cuda13.0"
    assert launch.select_nvidia_dependency_variant(12) == "cuda12.6"
    assert launch.select_nvidia_dependency_variant(11) is None
    assert launch.select_nvidia_dependency_variant(None) is None


def test_installed_cuda_12_runtime_preserves_cuda126_group(monkeypatch):
    launch = load_launch("launch_installed_cuda12_variant_test")
    monkeypatch.setattr(
        launch,
        "detect_installed_pytorch_version",
        lambda: ("GPU", "CUDA 12.6"),
    )

    assert launch.get_requirements_file_from_env() == (
        "cuda12.6",
        "GPU",
        "CUDA 12.6",
    )
