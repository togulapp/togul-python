from __future__ import annotations

import togul


def test_every_public_name_is_importable():
    expected = {
        "AsyncTogulClient",
        "AsyncTogulStreamClient",
        "Config",
        "DEFAULT_BASE_URL",
        "EvaluateResult",
        "TogulAPIError",
        "TogulClient",
        "TogulError",
        "TogulStreamClient",
        "ValueType",
        "__version__",
    }
    assert expected.issubset(set(togul.__all__) | {"__version__"})
    for name in expected:
        assert hasattr(togul, name), name


def test_no_typed_evaluation_helpers_are_exposed():
    # Deliberate parity with the Go, JS and PHP SDKs. See the design doc.
    for absent in ("evaluate_string", "evaluate_number", "evaluate_json", "FallbackMode"):
        assert not hasattr(togul, absent), absent


def test_package_is_marked_as_typed():
    import pathlib

    marker = pathlib.Path(togul.__file__).parent / "py.typed"
    assert marker.exists()
