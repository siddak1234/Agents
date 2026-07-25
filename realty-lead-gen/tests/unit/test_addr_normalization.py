"""Address normalization + hashing."""

from __future__ import annotations

import pytest

from realty_lead_gen.utils.addr import hash_address, normalize_address


@pytest.mark.unit
class TestNormalizeAddress:
    def test_expands_and_abbreviates_suffix(self) -> None:
        n = normalize_address("123 Main Street, Anytown, TX 78701")
        assert n.street_name == "MAIN ST"
        assert n.city == "ANYTOWN"
        assert n.state == "TX"
        assert n.postal_code == "78701"

    def test_case_insensitive(self) -> None:
        a = normalize_address("123 Main St, Anytown, TX 78701")
        b = normalize_address("123 main street, anytown, tx 78701")
        assert a.canonical_form == b.canonical_form

    def test_directional_normalization(self) -> None:
        n = normalize_address("456 North Elm Avenue, Chicago, IL 60614")
        assert "N" in n.street_name.split()
        assert "AVE" in n.street_name.split()

    def test_unit_normalization(self) -> None:
        n = normalize_address("789 Oak Rd Apt 4B, Boston, MA 02116")
        assert "APT" in n.unit
        assert "4B" in n.unit

    def test_handles_garbage_gracefully(self) -> None:
        # Should not raise; degraded output OK.
        n = normalize_address("!!! not an address !!!")
        assert isinstance(n.canonical_form, str)


@pytest.mark.unit
class TestHashAddress:
    def test_stable_across_formatting(self) -> None:
        h1 = hash_address("123 Main St, Anytown, TX 78701")
        h2 = hash_address("123 MAIN STREET Anytown TX 78701")
        assert h1 == h2

    def test_different_addresses_hash_differently(self) -> None:
        assert hash_address("1 Elm St, X, CA 90000") != hash_address("2 Elm St, X, CA 90000")

    def test_unit_matters(self) -> None:
        assert hash_address("1 Elm St, X, CA 90000") != hash_address("1 Elm St Apt 2, X, CA 90000")
