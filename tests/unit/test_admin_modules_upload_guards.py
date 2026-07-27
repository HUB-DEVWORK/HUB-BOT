"""Security guards of the module-upload endpoint (src/web/routes/admin/modules.py).

Uploaded modules are arbitrary Python, so these pure helpers are the trust boundary:
a zip-slip / NUL / bad-name / oversize upload must be rejected before anything touches
disk. All helpers are pure (no app, no request), so we call them directly and assert on
the raised ``HTTPException.status_code``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from src.web.routes.admin import modules

# -- _safe_relpath --------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "../x",  # climbs out of the module root
        "a/../../etc",  # climbs out after a step in
        "a/\x00b/c.py",  # NUL byte inside a segment
        "",  # empty
        ".",  # dot-only
        "././.",  # collapses to nothing
        "/../secret",  # leading slash stripped, then a climb
    ],
)
def test_safe_relpath_rejects_traversal_nul_and_empty(raw: str) -> None:
    with pytest.raises(HTTPException) as ei:
        modules._safe_relpath(raw)
    assert ei.value.status_code == 400


def test_safe_relpath_strips_leading_slash_and_collapses_dots() -> None:
    assert modules._safe_relpath("/mymod/manifest.py") == "mymod/manifest.py"
    assert modules._safe_relpath("mymod/./sub/./file.py") == "mymod/sub/file.py"
    # Backslashes normalise to forward slashes.
    assert modules._safe_relpath("mymod\\sub\\file.py") == "mymod/sub/file.py"


def test_safe_relpath_passes_normal_input() -> None:
    assert modules._safe_relpath("mymod/manifest.py") == "mymod/manifest.py"


# -- _validate_name -------------------------------------------------------------


def test_validate_name_accepts_lowercase_identifier() -> None:
    # "greeter" is not reserved and is not a baked-in built-in dir, so no 409 branch.
    assert modules._validate_name("greeter") == "greeter"
    assert modules._validate_name(" widget2 ") == "widget2"  # surrounding space is trimmed


@pytest.mark.parametrize(
    "name",
    [
        "Mixed",  # uppercase
        "1abc",  # leading digit
        "a",  # too short (needs >= 2)
        "bad-name",  # hyphen not allowed
        "has space",  # space not allowed
        "x" * 41,  # too long (> 40)
        "__init__",  # dunder shape also fails the identifier rule
    ],
)
def test_validate_name_rejects_bad_shape(name: str) -> None:
    with pytest.raises(HTTPException) as ei:
        modules._validate_name(name)
    assert ei.value.status_code == 400


@pytest.mark.parametrize("name", ["api", "loader", "hooks", "discovery"])
def test_validate_name_rejects_reserved(name: str) -> None:
    # These pass the identifier shape but are framework/meta names.
    with pytest.raises(HTTPException) as ei:
        modules._validate_name(name)
    assert ei.value.status_code == 400


# -- _split_top -----------------------------------------------------------------


def test_split_top_single_dir_strips_prefix() -> None:
    top, stripped = modules._split_top(["mymod/manifest.py", "mymod/sub/deep.py"])
    assert top == "mymod"
    assert stripped == {
        "manifest.py": "mymod/manifest.py",
        "sub/deep.py": "mymod/sub/deep.py",
    }


def test_split_top_ignores_stray_root_file() -> None:
    top, stripped = modules._split_top(["mymod/manifest.py", "readme.txt"])
    assert top == "mymod"
    # The stray file sitting next to the module dir is dropped, not stripped.
    assert stripped == {"manifest.py": "mymod/manifest.py"}


def test_split_top_rejects_zero_top_dirs() -> None:
    with pytest.raises(HTTPException) as ei:
        modules._split_top(["a.txt", "b.txt"])
    assert ei.value.status_code == 400


def test_split_top_rejects_multiple_top_dirs() -> None:
    with pytest.raises(HTTPException) as ei:
        modules._split_top(["a/x.py", "b/y.py"])
    assert ei.value.status_code == 400


# -- _install -------------------------------------------------------------------


@pytest.fixture
def ext_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point the installer at a throwaway dir instead of the real /app/ext_modules."""
    root = tmp_path / "ext_modules"
    monkeypatch.setattr(modules, "EXTERNAL_MODULES_DIR", str(root))
    return root


def test_install_writes_package_with_auto_init(ext_dir: Path) -> None:
    modules._install("greeter", {"manifest.py": b"# hi"})

    pkg = ext_dir / "greeter"
    assert pkg.is_dir()
    assert (pkg / "manifest.py").read_bytes() == b"# hi"
    # A package needs __init__.py even when the upload forgot it.
    assert (pkg / "__init__.py").read_bytes() == b""


def test_install_rejects_missing_manifest(ext_dir: Path) -> None:
    with pytest.raises(HTTPException) as ei:
        modules._install("greeter", {"__init__.py": b""})
    assert ei.value.status_code == 400
    assert not (ext_dir / "greeter").exists()


def test_install_rejects_too_many_files(ext_dir: Path) -> None:
    files: dict[str, bytes] = {"manifest.py": b"x"}
    files.update({f"f{i}.py": b"x" for i in range(modules.MAX_FILES)})
    assert len(files) > modules.MAX_FILES
    with pytest.raises(HTTPException) as ei:
        modules._install("greeter", files)
    assert ei.value.status_code == 400


def test_install_rejects_total_over_cap(ext_dir: Path) -> None:
    files = {"manifest.py": b"x", "big.bin": b"\x00" * (modules.MAX_TOTAL + 1)}
    with pytest.raises(HTTPException) as ei:
        modules._install("greeter", files)
    assert ei.value.status_code == 413
