from pathlib import Path

from vector1a.events import (
    load_definitions,
    load_director_event_policy,
    resolve_bundled_config,
)


def test_director_allowlist_includes_all_definitions_except_exclude():
    definitions, _ = load_definitions()
    catalog, overrides, min_seconds, max_seconds = load_director_event_policy()
    assert min_seconds == 2.0
    assert max_seconds == 600.0
    assert "tranquil" not in catalog
    assert "test_slaps" not in catalog
    assert set(catalog) == set(definitions) - {"tranquil", "test_slaps"}
    assert "mcb_tease" in overrides
    assert overrides["mcb_tease"]["tease_amplitude"] == 0.08
    assert catalog["mcb_extract"][2] == 265.367


def test_director_allowlist_respects_custom_exclude_file(tmp_path: Path):
    allowlist = tmp_path / "allowlist.yml"
    allowlist.write_text(
        "exclude:\n  - mcb_tease\n"
        "duration_seconds:\n  minimum: 3\n  maximum: 40\n",
        encoding="utf-8",
    )
    catalog, _overrides, min_seconds, max_seconds = load_director_event_policy(
        allowlist_path=allowlist)
    assert min_seconds == 3.0
    assert max_seconds == 40.0
    assert "mcb_tease" not in catalog
    assert "tranquil" in catalog
    assert "test_slaps" in catalog


def test_frozen_exe_prefers_yaml_beside_executable(tmp_path: Path):
    module_dir = tmp_path / "inside"
    module_dir.mkdir()
    (module_dir / "director_event_allowlist.yml").write_text("inside", encoding="utf-8")
    exe_dir = tmp_path / "release"
    exe_dir.mkdir()
    exe = exe_dir / "Vector1A.exe"
    exe.write_bytes(b"")
    beside = exe_dir / "director_event_allowlist.yml"
    beside.write_text("edited", encoding="utf-8")
    found = resolve_bundled_config(
        "director_event_allowlist.yml", module_dir, frozen=True, executable=exe)
    assert found == beside


def test_frozen_exe_uses_packed_yaml_when_beside_file_missing(tmp_path: Path):
    module_dir = tmp_path / "inside"
    module_dir.mkdir()
    packed = module_dir / "event_definitions.yml"
    packed.write_text("packed", encoding="utf-8")
    exe = tmp_path / "Vector1A.exe"
    exe.write_bytes(b"")
    found = resolve_bundled_config(
        "event_definitions.yml", module_dir, frozen=True, executable=exe)
    assert found == packed


def test_source_run_ignores_yaml_beside_interpreter(tmp_path: Path):
    module_dir = tmp_path / "vector1a"
    module_dir.mkdir()
    packed = module_dir / "event_definitions.yml"
    packed.write_text("source", encoding="utf-8")
    exe_dir = tmp_path / "python"
    exe_dir.mkdir()
    exe = exe_dir / "python.exe"
    exe.write_bytes(b"")
    (exe_dir / "event_definitions.yml").write_text("unrelated", encoding="utf-8")
    found = resolve_bundled_config(
        "event_definitions.yml", module_dir, frozen=False, executable=exe)
    assert found == packed
