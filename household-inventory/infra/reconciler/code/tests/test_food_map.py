"""Food map loader tests."""

from __future__ import annotations

from pathlib import Path

from app.food_map import FoodMap


def test_loads_mappings(tmp_path: Path) -> None:
    yaml_file = tmp_path / "food_map.yaml"
    yaml_file.write_text('mappings:\n  "all-purpose flour": 14\n  "Olive Oil": 8\n')
    fm = FoodMap(yaml_file)
    assert len(fm) == 2
    assert fm.get("all-purpose flour") == 14
    # Lookup is case-insensitive.
    assert fm.get("olive oil") == 8
    assert fm.get("unknown") is None


def test_missing_file_is_empty(tmp_path: Path) -> None:
    fm = FoodMap(tmp_path / "nope.yaml")
    assert len(fm) == 0
    assert fm.get("anything") is None


def test_hot_reload(tmp_path: Path) -> None:
    yaml_file = tmp_path / "food_map.yaml"
    yaml_file.write_text('mappings: {"flour": 1}\n')
    fm = FoodMap(yaml_file)
    assert fm.get("flour") == 1
    yaml_file.write_text('mappings: {"flour": 1, "salt": 2}\n')
    fm.reload()
    assert fm.get("salt") == 2
