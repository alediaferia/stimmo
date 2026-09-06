"""Guard against `scripts/refresh_omi.py` silently downgrading the bundled OMI vintage.

CKAN lags Agenzia Entrate (see the module docstring of the script), so its newest package
can be older than what is bundled. `scripts/` is not a package, so load it by path.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "refresh_omi.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("refresh_omi", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


refresh_omi = _load_script()


def _manifest(tmp_path: Path, payload: str) -> Path:
    (tmp_path / "manifest.json").write_text(payload)
    return tmp_path


def test_bundled_semester_reads_manifest(tmp_path):
    assets = _manifest(tmp_path, json.dumps({"semester": "2025-2"}))
    assert refresh_omi._bundled_semester(assets) == (2025, 2)


def test_bundled_semester_none_when_missing(tmp_path):
    assert refresh_omi._bundled_semester(tmp_path) is None


def test_bundled_semester_none_when_unparseable(tmp_path):
    assets = _manifest(tmp_path, "{not json")
    assert refresh_omi._bundled_semester(assets) is None


def test_bundled_semester_none_when_semester_key_missing(tmp_path):
    assets = _manifest(tmp_path, json.dumps({"other": "2025-2"}))
    assert refresh_omi._bundled_semester(assets) is None


def test_guard_refuses_older_candidate():
    with pytest.raises(SystemExit) as exc:
        refresh_omi._guard_downgrade((2024, 2), (2025, 2))
    message = str(exc.value)
    assert "2024-2" in message
    assert "2025-2" in message
    assert "--allow-downgrade" in message


def test_guard_allows_older_candidate_with_escape_hatch():
    refresh_omi._guard_downgrade((2024, 2), (2025, 2), allow_downgrade=True)


def test_guard_allows_equal_semester():
    refresh_omi._guard_downgrade((2025, 2), (2025, 2))


def test_guard_allows_newer_semester():
    refresh_omi._guard_downgrade((2026, 1), (2025, 2))


def test_guard_allows_missing_manifest():
    refresh_omi._guard_downgrade((2024, 2), None)


def test_bundled_assets_are_newer_than_ckan_lag_sentinel():
    """The real bundled manifest must never regress below the 2025-2 Agenzia Entrate vintage."""
    assets = _SCRIPT.resolve().parent.parent / "src" / "stimmo" / "data" / "assets"
    assert refresh_omi._bundled_semester(assets) >= (2025, 2)


def test_main_exposes_allow_downgrade_flag(capsys):
    with pytest.raises(SystemExit):
        refresh_omi.main(["--help"])
    assert "--allow-downgrade" in capsys.readouterr().out
