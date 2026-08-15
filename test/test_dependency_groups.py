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


def test_nvidia_cuda_variant_requires_turing_for_cuda13():
    launch = load_launch("launch_nvidia_variant_test")

    assert launch.select_nvidia_dependency_variant(13, (12, 0)) == "cuda13.0"
    assert launch.select_nvidia_dependency_variant(13, (8, 9)) == "cuda13.0"
    assert launch.select_nvidia_dependency_variant(13, (7, 5)) == "cuda13.0"
    assert launch.select_nvidia_dependency_variant(13, (7, 0)) == "cuda12.6"
    assert launch.select_nvidia_dependency_variant(13, (6, 1)) == "cuda12.6"
    assert launch.select_nvidia_dependency_variant(13, None) == "cuda12.6"
    assert launch.select_nvidia_dependency_variant(14, (7, 0)) == "cuda12.6"
    assert launch.select_nvidia_dependency_variant(12, (12, 0)) == "cuda12.6"
    assert launch.select_nvidia_dependency_variant(11, (7, 5)) is None
    assert launch.select_nvidia_dependency_variant(None, (7, 5)) is None

def test_geforce_10_series_forces_cuda126_by_model_name():
    launch = load_launch("launch_nvidia_10_series_test")

    ten_series_names = (
        "NVIDIA GeForce GTX 1080 Ti",
        "GeForce GTX 1080",
        "NVIDIA GeForce GTX 1070 Ti",
        "GeForce GTX 1060 6GB",
        "NVIDIA GeForce GTX 1050 Ti",
        "GeForce GT 1030",
    )
    for gpu_name in ten_series_names:
        assert launch.is_nvidia_10_series_gpu(gpu_name)
        assert launch.select_nvidia_dependency_variant(13, (9, 0), gpu_name) == "cuda12.6"


def test_10_series_match_does_not_capture_newer_geforce_models():
    launch = load_launch("launch_nvidia_non_10_series_test")

    for gpu_name in ("GeForce RTX 2060", "GeForce RTX 4090", "GeForce RTX 5090"):
        assert not launch.is_nvidia_10_series_gpu(gpu_name)
        assert launch.select_nvidia_dependency_variant(13, (7, 5), gpu_name) == "cuda13.0"


def test_nvidia_compute_capability_matches_selected_gpu(monkeypatch):
    launch = load_launch("launch_nvidia_compute_capability_test")
    monkeypatch.setattr(
        launch.subprocess,
        "check_output",
        lambda *args, **kwargs: "NVIDIA GeForce GTX 1080 Ti, 6.1\nNVIDIA GeForce RTX 5090, 12.0\n",
    )

    assert launch.detect_nvidia_compute_capability("GeForce GTX 1080 Ti") == (6, 1)
    assert launch.detect_nvidia_compute_capability("NVIDIA GeForce RTX 5090") == (12, 0)
    assert launch.detect_nvidia_compute_capability("NVIDIA Unknown GPU") is None


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


def test_rtx_50_update_variant_uses_driver_cuda_instead_of_installed_wheel(monkeypatch):
    launch = load_launch("launch_rtx50_update_variant_test")
    monkeypatch.setattr(
        launch,
        "detect_installed_pytorch_version",
        lambda: ("GPU", "CUDA 12.6"),
    )
    detect_calls = []

    def detect_gpu(*, interactive=True):
        detect_calls.append(interactive)
        return "NVIDIA", "NVIDIA GeForce RTX 5090", 13, "13.0", "580.00", (12, 0)

    monkeypatch.setattr(launch, "detect_gpu", detect_gpu)

    assert launch.get_update_variant_info() == (
        "cuda13.0",
        "cuda12.6",
        "GPU",
        "CUDA 12.6",
    )
    assert detect_calls == [False]


def test_rtx_50_update_variant_stays_on_cuda126_when_driver_is_cuda12(monkeypatch):
    launch = load_launch("launch_rtx50_cuda12_update_variant_test")
    monkeypatch.setattr(
        launch,
        "detect_installed_pytorch_version",
        lambda: ("GPU", "CUDA 12.6"),
    )
    monkeypatch.setattr(
        launch,
        "detect_gpu",
        lambda *, interactive=True: (
            "NVIDIA",
            "NVIDIA GeForce RTX 5090",
            12,
            "12.8",
            "572.00",
            (12, 0),
        ),
    )

    assert launch.get_update_variant_info()[0] == "cuda12.6"


def test_update_dependencies_applies_driver_selected_cuda_variant(monkeypatch):
    launch = load_launch("launch_apply_driver_cuda_variant_test")
    args = type("Args", (), {"requirements": "auto"})()
    applied_variants = []

    monkeypatch.setattr(launch, "ensure_pytorch_runtime_ready", lambda: True)
    monkeypatch.setattr(
        launch,
        "get_update_variant_info",
        lambda: ("cuda13.0", "cuda12.6", "GPU", "CUDA 12.6"),
    )
    monkeypatch.setattr(
        launch,
        "prepare_environment",
        lambda current_args: applied_variants.append(current_args.requirements),
    )

    assert launch.update_dependencies(args, select_cuda_variant=True)
    assert applied_variants == ["cuda13.0"]


def test_update_runtime_only_selects_driver_cuda_for_torch_upgrade(monkeypatch):
    launch = load_launch("launch_runtime_cuda_detection_gate_test")
    args = type("Args", (), {"requirements": "auto"})()
    update_calls = []
    selective_calls = []

    monkeypatch.setattr(launch, "ensure_pytorch_runtime_ready", lambda: True)
    monkeypatch.setattr(launch, "cleanup_caches", lambda: None)
    monkeypatch.setattr(launch, "run_deps_with_retry", lambda task, *_: task())
    monkeypatch.setattr(
        launch,
        "update_dependencies",
        lambda current_args, **kwargs: update_calls.append(kwargs["select_cuda_variant"]) or True,
    )
    monkeypatch.setattr(
        launch,
        "update_dependencies_selective",
        lambda current_args, missing: selective_calls.append(missing) or True,
    )

    assert launch.update_runtime_dependencies(args, "cuda12.6", ["numpy==2.0"])
    assert selective_calls == [["numpy==2.0"]]
    assert update_calls == []

    assert launch.update_runtime_dependencies(args, "cuda12.6", ["torch==2.13.0"])
    assert update_calls == [True]


def test_legacy_rocm_runtime_preserves_rocm_group(monkeypatch):
    launch = load_launch("launch_installed_rocm_variant_test")
    monkeypatch.setattr(
        launch,
        "detect_installed_pytorch_version",
        lambda: ("ROCm", "ROCm 7.2.53211-158bd99533"),
    )

    assert launch.get_requirements_file_from_env() == (
        "rocm7.2.1",
        "ROCm",
        "ROCm 7.2.53211-158bd99533",
    )
