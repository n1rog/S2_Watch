from s2_watch.geocoding import extract_place_candidates, is_usable_geocode_result


def test_place_candidates_use_structured_fields_and_explicit_phrases() -> None:
    candidates = extract_place_candidates(
        "Road closed near Denver",
        {"state": "Colorado"},
    )
    assert "Colorado" in candidates
    assert "Denver" in candidates


def test_place_candidates_detect_state_names_in_report_text() -> None:
    candidates = extract_place_candidates("Severe weather reported across Iowa.", {})
    assert "Iowa" in candidates


def test_place_candidates_detect_city_and_state() -> None:
    candidates = extract_place_candidates("Flooding reported in Des Moines, Iowa.", {})
    assert "Des Moines" in candidates
    assert "Des Moines, Iowa" in candidates


def test_place_candidates_ignore_generic_location_phrases() -> None:
    candidates = extract_place_candidates(
        "Environmental conditions are worsening in the region.",
        {},
    )
    assert candidates == []


def test_geocoder_accepts_city_and_state_results() -> None:
    assert is_usable_geocode_result({"class": "place", "type": "city"})
    assert is_usable_geocode_result({"class": "boundary", "type": "administrative"})
    assert is_usable_geocode_result({"type": "administrative"})


def test_geocoder_rejects_continent_and_country_results() -> None:
    assert not is_usable_geocode_result({"class": "place", "type": "continent"})
    assert not is_usable_geocode_result({"class": "boundary", "type": "country"})


def test_place_candidates_detect_standalone_city_name() -> None:
    candidates = extract_place_candidates("Detroit is experiencing severe weather.", {})
    assert "Detroit" in candidates

