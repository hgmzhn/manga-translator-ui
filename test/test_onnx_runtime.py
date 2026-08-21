import _bootstrap  # noqa: F401

from manga_translator.utils.onnx_runtime import build_execution_providers


def test_cuda_provider_does_not_pass_default_device_id():
    class FakeOrt:
        def __init__(self):
            self.preload_calls = 0

        def preload_dlls(self):
            self.preload_calls += 1

        @staticmethod
        def get_available_providers():
            return ["CUDAExecutionProvider", "CPUExecutionProvider"]

    ort = FakeOrt()

    providers = build_execution_providers(ort, device="cuda")

    assert providers == [
        ("CUDAExecutionProvider", {}),
        "CPUExecutionProvider",
    ]
    assert ort.preload_calls == 1


def test_cuda_provider_keeps_explicit_options():
    class FakeOrt:
        @staticmethod
        def get_available_providers():
            return ["CUDAExecutionProvider", "CPUExecutionProvider"]

    assert build_execution_providers(
        FakeOrt(),
        device="cuda",
        cuda_options={"arena_extend_strategy": "kSameAsRequested"},
    ) == [
        (
            "CUDAExecutionProvider",
            {"arena_extend_strategy": "kSameAsRequested"},
        ),
        "CPUExecutionProvider",
    ]
