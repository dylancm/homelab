"""Integration-style tests for the loader using a fake GrocyClient.

Two scenarios:

1. `--dry-run` resolves names and produces correct output, but makes ZERO
   write calls (no POST/PUT).
2. A normal run for one product issues the expected sequence of POST/PUT
   calls in order.

We don't use httpx-level mocking here because the GrocyClient interface is
small and stable; faking the client directly is simpler and more readable.
The unit being tested is the loader orchestration, not httpx.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from tools.grocy_loader import loader as loader_mod
from tools.grocy_loader.grocy_client import GrocyClient
from tools.grocy_loader.loader import NameMaps, RunSummary, run
from tools.grocy_loader.models import GrocyConversion, GrocyNamed, GrocyProduct


# ---------------------------------------------------------------------------
# Fake client
# ---------------------------------------------------------------------------


class FakeClient:
    """Minimal stand-in for GrocyClient. Records every write call."""

    def __init__(
        self,
        *,
        named: dict[str, list[GrocyNamed]],
        userfield_keys: dict[str, set[str]] | None = None,
        existing_products: list[GrocyProduct] | None = None,
        # Conversions returned by list_conversions_for_product, keyed by the
        # product_id we'll assign to the next created product. The loader
        # always lists conversions immediately after creating a product, so
        # this lets a test simulate "Grocy auto-created Bag<->g for product 100".
        auto_conversions_for_next_id: list[GrocyConversion] | None = None,
        next_object_id: int = 100,
    ) -> None:
        self._named = named
        self._userfield_keys = userfield_keys or {}
        self._existing = list(existing_products or [])
        self._auto_conversions_for_next_id = list(auto_conversions_for_next_id or [])
        self._next_id = next_object_id
        self._conversions_by_product: dict[int, list[GrocyConversion]] = {}
        # Recorded write calls, in order:
        #   ('POST', entity, body) | ('PUT_OBJ', entity, id, body)
        #   | ('PUT_USERFIELD', entity, id, body)
        self.calls: list[tuple[Any, ...]] = []

    # GrocyClient protocol surface used by the loader:
    def list_named(self, entity: str) -> list[GrocyNamed]:
        return list(self._named.get(entity, []))

    def list_userfield_keys(self, entity: str) -> set[str]:
        return set(self._userfield_keys.get(entity, set()))

    def list_conversions_for_product(self, product_id: int) -> list[GrocyConversion]:
        return list(self._conversions_by_product.get(product_id, []))

    def find_product_by_name(self, name: str) -> GrocyProduct | None:
        target = name.strip().casefold()
        for p in self._existing:
            if p.name.strip().casefold() == target:
                return p
        return None

    def create_object(self, entity: str, body: dict[str, Any]) -> int:
        self.calls.append(("POST", entity, body))
        new_id = self._next_id
        self._next_id += 1
        if entity == "products" and self._auto_conversions_for_next_id:
            self._conversions_by_product[new_id] = list(
                self._auto_conversions_for_next_id
            )
            # consume — only the next product gets these
            self._auto_conversions_for_next_id = []
        return new_id

    def update_object(self, entity: str, object_id: int, body: dict[str, Any]) -> None:
        self.calls.append(("PUT_OBJ", entity, object_id, body))

    def put_userfield(self, entity: str, object_id: int, body: dict[str, Any]) -> None:
        self.calls.append(("PUT_USERFIELD", entity, object_id, body))

    def close(self) -> None:
        pass

    def __enter__(self) -> FakeClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        pass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

NAMED_FIXTURE: dict[str, list[GrocyNamed]] = {
    "locations": [
        GrocyNamed(id=2, name="Fridge"),
        GrocyNamed(id=3, name="Pantry"),
    ],
    "product_groups": [
        GrocyNamed(id=3, name="Dairy & Eggs"),
        GrocyNamed(id=5, name="Pantry"),
        GrocyNamed(id=7, name="Beverages"),
    ],
    "shopping_locations": [
        GrocyNamed(id=1, name="HEB"),
    ],
    "quantity_units": [
        GrocyNamed(id=2, name="Piece"),
        GrocyNamed(id=3, name="Pack"),
        GrocyNamed(id=6, name="Bag"),
        GrocyNamed(id=12, name="g"),
    ],
}

USERFIELD_KEYS_FIXTURE: dict[str, set[str]] = {
    "products": {"heb_sku", "pack_size", "pack_size_grams"},
}


YAML_FIXTURE = """
products:
  - name: "Test Eggs"
    description: "A dozen large eggs."
    barcode: "012345678901"
    qu_purchase: Pack
    qu_stock: Piece
    conversions:
      - from: Pack
        to: Piece
        factor: 12
    location: Fridge
    product_group: "Dairy & Eggs"
    shopping_location: HEB
    min_stock_amount: 6
    default_best_before_days: 28
    userfields:
      heb_sku: "HEB-EGG-DZ-01"
      pack_size: 12
"""


@pytest.fixture
def yaml_path(tmp_path: Path) -> Path:
    p = tmp_path / "products.yaml"
    p.write_text(YAML_FIXTURE)
    return p


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> FakeClient:
    """Patch GrocyClient construction to return our fake."""
    fake = FakeClient(
        named=NAMED_FIXTURE,
        userfield_keys=USERFIELD_KEYS_FIXTURE,
    )

    def _factory(*_args: object, **_kwargs: object) -> FakeClient:
        return fake

    # The loader instantiates `GrocyClient(...)` from its own import; patch
    # the symbol in the loader module's namespace.
    monkeypatch.setattr(loader_mod, "GrocyClient", _factory)
    return fake


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_dry_run_makes_no_write_calls(
    yaml_path: Path,
    fake_client: FakeClient,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = run(
        yaml_path=yaml_path,
        base_url="http://fake",
        api_key="fake",
        dry_run=True,
    )
    assert code == 0
    # No POST or PUT calls at all.
    assert fake_client.calls == []
    out = capsys.readouterr().out
    assert "would create" in out
    assert "Test Eggs" in out


def test_real_run_emits_expected_call_sequence(
    yaml_path: Path,
    fake_client: FakeClient,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = run(
        yaml_path=yaml_path,
        base_url="http://fake",
        api_key="fake",
        dry_run=False,
    )
    assert code == 0

    calls = fake_client.calls
    # Expected: 1 product + 1 barcode + 1 conversion + 2 userfields = 5 calls.
    assert len(calls) == 5

    # 1. Product creation.
    method, entity, body = calls[0]
    assert method == "POST"
    assert entity == "products"
    assert body["name"] == "Test Eggs"
    assert body["location_id"] == 2
    assert body["qu_id_purchase"] == 3  # Pack
    assert body["qu_id_stock"] == 2  # Piece
    assert body["product_group_id"] == 3
    assert body["shopping_location_id"] == 1
    assert body["min_stock_amount"] == 6
    assert body["default_best_before_days"] == 28
    # qu_factor_purchase_to_stock is NOT set (Grocy 4.x dropped it).
    assert "qu_factor_purchase_to_stock" not in body

    # 2. Barcode.
    method, entity, body = calls[1]
    assert method == "POST"
    assert entity == "product_barcodes"
    assert body == {"product_id": 100, "barcode": "012345678901"}

    # 3. Conversion.
    method, entity, body = calls[2]
    assert method == "POST"
    assert entity == "quantity_unit_conversions"
    assert body == {
        "product_id": 100,
        "from_qu_id": 3,  # Pack
        "to_qu_id": 2,  # Piece
        "factor": 12,
    }

    # 4 & 5. Userfields, one PUT per key. Order follows YAML.
    assert calls[3][0] == "PUT_USERFIELD"
    assert calls[3][1] == "products"
    assert calls[3][2] == 100
    assert calls[3][3] == {"heb_sku": "HEB-EGG-DZ-01"}

    assert calls[4][0] == "PUT_USERFIELD"
    assert calls[4][3] == {"pack_size": 12}

    out = capsys.readouterr().out
    assert "created" in out
    assert "Test Eggs" in out
    assert "1 created, 0 skipped, 0 failed" in out


def test_idempotent_skip_when_product_exists(
    yaml_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = FakeClient(
        named=NAMED_FIXTURE,
        userfield_keys=USERFIELD_KEYS_FIXTURE,
        existing_products=[GrocyProduct(id=17, name="Test Eggs")],
    )
    monkeypatch.setattr(loader_mod, "GrocyClient", lambda *a, **kw: fake)

    code = run(
        yaml_path=yaml_path,
        base_url="http://fake",
        api_key="fake",
        dry_run=False,
    )
    assert code == 0
    # No write calls at all.
    assert fake.calls == []
    out = capsys.readouterr().out
    assert "skip" in out
    assert "id=17" in out
    assert "0 created, 1 skipped, 0 failed" in out


def test_unresolved_names_aborts_with_no_writes(
    tmp_path: Path,
    fake_client: FakeClient,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bad_yaml = tmp_path / "bad.yaml"
    bad_yaml.write_text(
        """
products:
  - name: "Bad Product"
    qu_purchase: NotAUnit
    qu_stock: Piece
    location: NotALocation
"""
    )
    code = run(
        yaml_path=bad_yaml,
        base_url="http://fake",
        api_key="fake",
        dry_run=False,
    )
    assert code == 2
    assert fake_client.calls == []
    err = capsys.readouterr().err
    assert "name resolution failed" in err
    assert "NotAUnit" in err
    assert "NotALocation" in err


def test_resolve_collects_all_unresolved_in_one_pass() -> None:
    """Pre-flight reports ALL bad names at once, not just the first."""
    from tools.grocy_loader.loader import resolve_all
    from tools.grocy_loader.models import YamlFile

    raw = yaml.safe_load(
        """
products:
  - name: P1
    qu_purchase: BadUnit1
    qu_stock: Piece
    location: BadLoc1
  - name: P2
    qu_purchase: Pack
    qu_stock: BadUnit2
    location: BadLoc2
"""
    )
    parsed = YamlFile.model_validate(raw)
    maps = NameMaps(
        locations={"fridge": 2},
        product_groups={},
        shopping_locations={},
        quantity_units={"piece": 2, "pack": 3},
        product_userfield_keys=set(),
    )
    _resolved, unresolved = resolve_all(parsed.products, maps)
    assert unresolved.locations == {"BadLoc1", "BadLoc2"}
    assert unresolved.quantity_units == {"BadUnit1", "BadUnit2"}


def test_invalid_userfield_keys_aborts_pre_flight(
    tmp_path: Path,
    fake_client: FakeClient,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Userfield keys not registered in Grocy must fail pre-flight, since
    Grocy validates them server-side and would reject the per-key PUTs.
    """
    bad_yaml = tmp_path / "bad.yaml"
    bad_yaml.write_text(
        """
products:
  - name: "Bad UF"
    qu_purchase: Pack
    qu_stock: Piece
    location: Fridge
    userfields:
      not_a_real_field: "x"
      heb_sku: "ok"
"""
    )
    code = run(
        yaml_path=bad_yaml,
        base_url="http://fake",
        api_key="fake",
        dry_run=False,
    )
    assert code == 2
    assert fake_client.calls == []
    err = capsys.readouterr().err
    assert "not_a_real_field" in err
    assert "product_userfield_keys" in err


def test_conversion_upsert_against_auto_created_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Grocy auto-creates Bag<->g for a product whose qu_purchase=Bag and
    qu_stock=g. The loader must PUT-update the existing Bag->g record's
    factor (not POST, which would 400 with 'already exists') and POST a
    fresh record for Cup->g.
    """
    yp = tmp_path / "flour.yaml"
    yp.write_text(
        """
products:
  - name: "Flour"
    qu_purchase: Bag
    qu_stock: g
    conversions:
      - from: Bag
        to: g
        factor: 2268
      - from: Cup
        to: g
        factor: 120
    location: Pantry
"""
    )
    # Add Cup to QU map for this test.
    named = dict(NAMED_FIXTURE)
    named["quantity_units"] = list(NAMED_FIXTURE["quantity_units"]) + [
        GrocyNamed(id=14, name="Cup"),
    ]
    fake = FakeClient(
        named=named,
        userfield_keys=USERFIELD_KEYS_FIXTURE,
        # Simulate Grocy auto-creating Bag<->g at factor=1 when product is made.
        auto_conversions_for_next_id=[
            GrocyConversion(
                id=500, product_id=100, from_qu_id=6, to_qu_id=12, factor=1
            ),
            GrocyConversion(
                id=501, product_id=100, from_qu_id=12, to_qu_id=6, factor=1
            ),
        ],
    )
    monkeypatch.setattr(loader_mod, "GrocyClient", lambda *a, **kw: fake)

    code = run(
        yaml_path=yp,
        base_url="http://fake",
        api_key="fake",
        dry_run=False,
    )
    assert code == 0

    # Expected sequence: POST product, then for the YAML's Bag->g conversion
    # PUT_OBJ on conversion id=500 (the auto-created one), then POST a new
    # Cup->g conversion. No barcode, no userfields in this YAML.
    methods_entities = [(c[0], c[1]) for c in fake.calls]
    assert methods_entities == [
        ("POST", "products"),
        ("PUT_OBJ", "quantity_unit_conversions"),
        ("POST", "quantity_unit_conversions"),
    ]

    # Verify the PUT body and target id.
    put_call = fake.calls[1]
    assert put_call[2] == 500  # the auto-created Bag->g id
    assert put_call[3] == {"factor": 2268}

    # Verify the POST body for Cup->g.
    post_call = fake.calls[2]
    assert post_call[2] == {
        "product_id": 100,
        "from_qu_id": 14,
        "to_qu_id": 12,
        "factor": 120,
    }
    assert "1 created, 0 skipped, 0 failed" in capsys.readouterr().out


def test_run_summary_arithmetic() -> None:
    s = RunSummary()
    s.created += 2
    s.skipped += 1
    s.failed += 0
    assert (s.created, s.skipped, s.failed) == (2, 1, 0)


def test_grocy_client_constructed_with_correct_header() -> None:
    """Smoke test that the real client builds with the expected header."""
    c = GrocyClient(base_url="http://example.invalid", api_key="abc")
    try:
        assert c._client.headers["GROCY-API-KEY"] == "abc"
        assert c._client.headers["Accept"] == "application/json"
    finally:
        c.close()
