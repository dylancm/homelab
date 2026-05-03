"""Thin synchronous httpx wrapper for the Grocy endpoints used by the loader.

Only the methods we actually need. Sequential by design — see prompt.

Endpoints (all under /api):
    GET    /objects/locations
    GET    /objects/product_groups
    GET    /objects/shopping_locations
    GET    /objects/quantity_units
    GET    /objects/userfields?query[]=entity=<entity>
    GET    /objects/products?query[]=name=<value>
    GET    /objects/quantity_unit_conversions?query[]=product_id=<id>
    POST   /objects/products
    POST   /objects/product_barcodes
    POST   /objects/quantity_unit_conversions
    PUT    /objects/quantity_unit_conversions/{id}
    PUT    /userfields/products/{id}

Auth: GROCY-API-KEY header (long-lived API key).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import httpx

from .models import (
    GrocyConversion,
    GrocyCreatedObject,
    GrocyNamed,
    GrocyProduct,
)


class GrocyAPIError(RuntimeError):
    """Raised when Grocy returns a non-2xx response."""

    def __init__(self, method: str, url: str, status: int, body: str) -> None:
        super().__init__(f"{method} {url} -> {status}: {body}")
        self.method = method
        self.url = url
        self.status = status
        self.body = body


class GrocyClient:
    """Sync Grocy API client. One instance per loader run."""

    def __init__(self, base_url: str, api_key: str, *, timeout: float = 15.0) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={
                "GROCY-API-KEY": api_key,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(timeout),
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> GrocyClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Internal request helper
    # ------------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Sequence[tuple[str, str]] | None = None,
        json: Any = None,
    ) -> Any:
        # httpx accepts Sequence[tuple[str, str]] as query params; pyright
        # narrows to the tuple form when we pass via tuple(...) below.
        request_params = tuple(params) if params is not None else None
        r = self._client.request(method, path, params=request_params, json=json)
        if r.status_code >= 400:
            raise GrocyAPIError(method, path, r.status_code, r.text)
        # Grocy returns JSON for everything we care about; some PUTs return
        # an empty body with 204/200. Tolerate that.
        if not r.content:
            return None
        return r.json()

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def list_named(self, entity: str) -> list[GrocyNamed]:
        """List entities with id+name (locations, product_groups, etc.)."""
        data = self._request("GET", f"/api/objects/{entity}")
        return [GrocyNamed.model_validate(item) for item in data]

    def list_userfield_keys(self, entity: str) -> set[str]:
        """List the userfield key names defined for `entity` (e.g. 'products').

        Grocy validates userfield keys at write time against this list — keys
        not registered here will get HTTP 400. Pre-flighting against this set
        gives the same all-or-nothing UX as resolving location/QU names.
        """
        params = [("query[]", f"entity={entity}")]
        data = self._request("GET", "/api/objects/userfields", params=params)
        if not isinstance(data, list):
            return set()
        return {str(item["name"]) for item in data if "name" in item}

    def list_conversions_for_product(self, product_id: int) -> list[GrocyConversion]:
        """List all QU conversion records for a product, including the
        purchase->stock pair (and inverse) that Grocy auto-creates.
        """
        params = [("query[]", f"product_id={product_id}")]
        data = self._request(
            "GET", "/api/objects/quantity_unit_conversions", params=params
        )
        if not isinstance(data, list):
            return []
        return [GrocyConversion.model_validate(item) for item in data]

    def find_product_by_name(self, name: str) -> GrocyProduct | None:
        """Return the first product whose name matches exactly, else None.

        Grocy filter syntax: `?query[]=<field>=<value>` does an exact match.
        We still verify case-insensitively client-side because Grocy's match
        behavior on `=` has historically depended on the SQLite collation.
        """
        params = [("query[]", f"name={name}")]
        data = self._request("GET", "/api/objects/products", params=params)
        if not isinstance(data, list):
            return None
        target = name.strip().casefold()
        for item in data:
            if str(item.get("name", "")).strip().casefold() == target:
                return GrocyProduct.model_validate(item)
        return None

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def create_object(self, entity: str, body: dict[str, Any]) -> int:
        """POST /objects/{entity}; return the created object's id."""
        data = self._request("POST", f"/api/objects/{entity}", json=body)
        return GrocyCreatedObject.model_validate(data).created_object_id

    def update_object(self, entity: str, object_id: int, body: dict[str, Any]) -> None:
        """PUT /objects/{entity}/{id}; partial update."""
        self._request("PUT", f"/api/objects/{entity}/{object_id}", json=body)

    def put_userfield(self, entity: str, object_id: int, body: dict[str, Any]) -> None:
        """PUT /userfields/{entity}/{object_id}.

        Grocy merges by key — keys not present in `body` are preserved.
        """
        self._request("PUT", f"/api/userfields/{entity}/{object_id}", json=body)
