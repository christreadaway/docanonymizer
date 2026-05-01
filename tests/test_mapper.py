"""Entity registry / replacement map tests."""

import re

import pytest


def test_basic_add_assigns_new_hex():
    from app.mapper import EntityRegistry
    r = EntityRegistry()
    p = r.add("Jane Smith", "PERSON")
    assert re.fullmatch(r"\[PERSON_[0-9A-F]{4}\]", p)
    assert "Jane Smith" in r.replacements


def test_linked_to_hex_shares_suffix():
    from app.mapper import EntityRegistry
    r = EntityRegistry()
    person = r.add("Jane Smith", "PERSON")
    suffix = person.split("_")[1].rstrip("]")
    email = r.add("jane@x.org", "EMAIL", linked_to=suffix)
    assert email == f"[EMAIL_{suffix}]"
    phone = r.add("555-0001", "PHONE", linked_to=suffix)
    assert phone == f"[PHONE_{suffix}]"
    # All three should map to the same hex_id in the entity registry.
    assert len(r.entities) == 1
    assert set(r.entities[suffix]["types"] if isinstance(r.entities[suffix], dict) and "types" in r.entities[suffix] else r.entities[suffix].keys()) == {"PERSON", "EMAIL", "PHONE"} \
        or set(r.entities[suffix].keys()) == {"PERSON", "EMAIL", "PHONE"}


def test_linked_to_text_shares_suffix():
    from app.mapper import EntityRegistry
    r = EntityRegistry()
    r.add("Jane Smith", "PERSON")
    e = r.add("jane@x.org", "EMAIL", linked_to="Jane Smith")
    p = r.add("Jane Smith", "PERSON").split("_")[1].rstrip("]")
    assert e == f"[EMAIL_{p}]"


def test_repeat_text_returns_existing_placeholder():
    from app.mapper import EntityRegistry
    r = EntityRegistry()
    a = r.add("Bob", "PERSON")
    b = r.add("Bob", "PERSON")
    assert a == b
    assert r.total_replacements() == 1


def test_replacement_map_sorted_longest_first():
    from app.mapper import EntityRegistry
    r = EntityRegistry()
    r.add("Jane", "PERSON")
    r.add("Jane Smith", "PERSON")
    keys = list(r.as_replacement_map().keys())
    assert keys == ["Jane Smith", "Jane"]


def test_drop_removes_replacement_and_entity():
    from app.mapper import EntityRegistry
    r = EntityRegistry()
    r.add("Jane Smith", "PERSON")
    assert r.drop("Jane Smith") is True
    assert "Jane Smith" not in r.replacements
    assert r.total_entities() == 0


def test_invalid_tag_raises():
    from app.mapper import EntityRegistry
    r = EntityRegistry()
    with pytest.raises(ValueError):
        r.add("x", "NOPE")


def test_merge_chunks_skips_invalid():
    from app.mapper import EntityRegistry
    r = EntityRegistry()
    r.merge_chunks([
        {"text": "Jane Smith", "type": "PERSON", "linked_to": None},
        {"text": "", "type": "PERSON"},                       # empty, skipped
        {"text": "x", "type": "BOGUS"},                       # bad tag, skipped
        {"text": "jane@x.org", "type": "EMAIL", "linked_to": "Jane Smith"},
    ])
    assert r.total_replacements() == 2
    assert r.total_entities() == 1


def test_hex_uniqueness_under_load():
    from app.mapper import EntityRegistry
    r = EntityRegistry()
    for i in range(500):
        r.add(f"person_{i}", "PERSON")
    assert len(r.entities) == 500
    # All hex ids unique
    assert len({hid for hid in r.entities}) == 500


def test_serializable_shape():
    from app.mapper import EntityRegistry
    r = EntityRegistry()
    r.add("Jane Smith", "PERSON")
    r.add("jane@x.org", "EMAIL", linked_to="Jane Smith")
    out = r.as_serializable()
    assert len(out) == 1
    rec = next(iter(out.values()))
    assert set(rec["types"]) == {"PERSON", "EMAIL"}
    assert rec["values"]["PERSON"] == "Jane Smith"
    assert rec["values"]["EMAIL"] == "jane@x.org"
