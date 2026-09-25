from s2_watch.geo import extract_message_locations, maidenhead_to_coordinates


def test_maidenhead_grid_converts_to_coordinates() -> None:
    latitude, longitude = maidenhead_to_coordinates("FN20")
    assert 39 < latitude < 41
    assert -76 < longitude < -74


def test_location_extraction_reads_coordinates_and_grid() -> None:
    locations = extract_message_locations("Report at 40.1, -105.2 near FN20", {})
    assert (40.1, -105.2) in locations
    assert any(39 < latitude < 41 and -76 < longitude < -74 for latitude, longitude in locations)


def test_extract_mars_regions_from_text_and_fields() -> None:
    from s2_watch.geo import extract_mars_regions

    assert extract_mars_regions("CQ @MR06 net check-in", {}) == [6]
    assert extract_mars_regions("relay to MR1 and mr10", {"recipient": "@MR05"}) == [1, 10, 5]
    assert extract_mars_regions("MR06 again MR6", {"group": "MR06"}) == [6]


def test_extract_mars_regions_ignores_non_region_codes() -> None:
    from s2_watch.geo import extract_mars_regions

    assert extract_mars_regions("MR11 MR00 AMR05 MR055 MRS MR", {"id": "XMR04"}) == []


def test_mars_region_codes_are_not_read_as_maidenhead_grids() -> None:
    from s2_watch.geo import extract_message_locations

    assert extract_message_locations("F!104 100 ST[OK] MR06OK #ERSL to @MR10", {}) == []
    assert extract_message_locations("MR06 station at EM12", {}) != []


def test_extract_mars_region_references_with_state_subgroups() -> None:
    from s2_watch.geo import extract_mars_region_references

    assert extract_mars_region_references("@MR06OK net", {}) == [(6, "OK")]
    # The state subgroup supersedes a plain mention of the same region.
    assert extract_mars_region_references("MR06 relay", {"recipient": "@MR06TX"}) == [(6, "TX")]
    assert extract_mars_region_references("MR04TN and MR05", {}) == [(4, "TN"), (5, None)]
    # Longer tails are not state suffixes, but the region code still counts.
    assert extract_mars_region_references("MR08COLORADO", {}) == [(8, None)]
    assert extract_mars_region_references("MR08-CO", {}) == [(8, None)]
