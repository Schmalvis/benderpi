"""Tests for handlers/entity_matcher.py — pure functions, no I/O."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from handlers.entity_matcher import EntityMatcher, normalise, _token_score, _fuzzy_score


def _entity(entity_id, domain, friendly_name):
    return {
        "entity_id": entity_id,
        "domain": domain,
        "friendly_name": friendly_name,
        "normalised": normalise(friendly_name),
        "normalised_id": normalise(entity_id.replace(".", " ")),
    }


ENTITIES = [
    _entity("light.office_ceiling", "light", "Office Ceiling"),
    _entity("light.kitchen", "light", "Kitchen"),
    _entity("climate.office_radiator", "climate", "Office Radiator"),
]


class TestNormalise:
    def test_lowercases_and_joins(self):
        assert normalise("Office Ceiling") == "office ceiling"

    def test_strips_noise_words(self):
        assert normalise("Living Room Light") == "living room"

    def test_splits_camel_case(self):
        assert normalise("CamelCaseName") == "camel case name"

    def test_strips_apostrophes(self):
        assert normalise("O'Brien's Room") == "obriens room"

    def test_empty_string(self):
        assert normalise("") == ""

    def test_entity_id_style_input(self):
        # entity_registry passes entity_id with "." replaced by " " first
        assert normalise("light.office_ceiling".replace(".", " ")) == "office ceiling"


class TestTokenScore:
    def test_perfect_single_token_match(self):
        assert _token_score("office", "office ceiling") == 1.0

    def test_partial_match_divides_by_user_token_count(self):
        assert _token_score("office ceiling", "office radiator") == 0.5

    def test_no_overlap(self):
        assert _token_score("kitchen", "office ceiling") == 0.0

    def test_empty_term(self):
        assert _token_score("", "office") == 0.0

    def test_empty_entity(self):
        assert _token_score("office", "") == 0.0


class TestFuzzyScore:
    def test_similar_strings_score_higher_than_dissimilar(self):
        similar = _fuzzy_score("bed", "bedroom ceiling")
        dissimilar = _fuzzy_score("garage", "kitchen")
        assert similar > dissimilar

    def test_identical_strings_score_one(self):
        assert _fuzzy_score("office ceiling", "office ceiling") == 1.0

    def test_below_threshold_for_unrelated_terms(self):
        assert _fuzzy_score("garage", "office ceiling") < 0.5


class TestEntityMatcherMatch:
    def test_happy_path_single_match(self):
        matcher = EntityMatcher()
        matches = matcher.match("kitchen", ENTITIES)
        assert [e["entity_id"] for e in matches] == ["light.kitchen"]

    def test_ambiguous_term_matches_multiple_entities(self):
        # "office" token-matches both the light and the climate entity
        matcher = EntityMatcher()
        matches = matcher.match("office", ENTITIES)
        assert {e["entity_id"] for e in matches} == {
            "light.office_ceiling", "climate.office_radiator",
        }

    def test_no_match_returns_empty(self):
        matcher = EntityMatcher()
        assert matcher.match("garage", ENTITIES) == []

    def test_domain_filter_restricts_pool(self):
        matcher = EntityMatcher()
        matches = matcher.match("office", ENTITIES, domain="climate")
        assert [e["entity_id"] for e in matches] == ["climate.office_radiator"]

    def test_synonym_resolution(self):
        matcher = EntityMatcher(synonyms={"lounge": "office"})
        matches = matcher.match("lounge", ENTITIES)
        assert {e["entity_id"] for e in matches} == {
            "light.office_ceiling", "climate.office_radiator",
        }

    def test_empty_entity_list(self):
        matcher = EntityMatcher()
        assert matcher.match("office", []) == []


class TestParseAction:
    def test_turn_on_phrase(self):
        assert EntityMatcher.parse_action("turn on the kitchen light") == "on"

    def test_switch_off_phrase(self):
        assert EntityMatcher.parse_action("switch off the bedroom lamp") == "off"

    def test_bare_off(self):
        assert EntityMatcher.parse_action("bedroom lights off") == "off"

    def test_no_action_present(self):
        assert EntityMatcher.parse_action("what about the kitchen") is None


class TestParseRoomTerm:
    def test_extracts_room_from_on_phrase(self):
        assert EntityMatcher.parse_room_term("turn on the office light") == "office"

    def test_extracts_room_from_off_phrase(self):
        assert EntityMatcher.parse_room_term("turn off kitchen lights") == "kitchen"

    def test_pronoun_passes_through_unresolved(self):
        # Caller is responsible for detecting PRONOUNS and resolving separately.
        assert EntityMatcher.parse_room_term("turn them off") == "them"

    def test_returns_none_when_nothing_left(self):
        assert EntityMatcher.parse_room_term("turn on the lights") is None


class TestParseTemperature:
    def test_finds_number_with_degrees(self):
        assert EntityMatcher.parse_temperature("set temperature to 21 degrees") == 21.0

    def test_no_number_present(self):
        assert EntityMatcher.parse_temperature("turn on the light") is None
