from s2_watch.locating import LocationResolver
from s2_watch.models import Message


def resolve(text, geocoder=None, **fields):
    return LocationResolver(geocoder).resolve(Message("JS8Call", text, fields=fields))


def levels(locations):
    return {location.level for location in locations}


def test_city_wins_over_county_state_and_region() -> None:
    found = resolve("FLOODING IN TULSA, OK. OKLAHOMA COUNTY ALSO AFFECTED", recipient="@MR06")
    assert levels(found) == {"city"}
    assert [location.label for location in found] == ["Tulsa, OK"]


def test_county_is_not_mistaken_for_the_city_of_the_same_name() -> None:
    found = resolve("Roads closed in Tulsa County, Oklahoma")
    assert levels(found) == {"county"}
    assert found[0].label == "Tulsa County, OK"


def test_ambiguous_county_is_narrowed_by_region() -> None:
    # Washington County exists in about 30 states; Region 1 leaves only Maine, Rhode Island and Vermont.
    assert levels(resolve("SHELTER OPEN IN WASHINGTON COUNTY")) != {"county"}
    found = resolve("SHELTER OPEN IN WASHINGTON COUNTY", group="@MR01")
    assert levels(found) != {"county"}  # still three candidates, so it falls back to the region
    found = resolve("SHELTER OPEN IN WASHINGTON COUNTY, VERMONT")
    assert levels(found) == {"county"} and found[0].label.endswith(", VT")


def test_state_then_region_fallback() -> None:
    found = resolve("WILDFIRE SMOKE REPORTED ACROSS OREGON", group="@MR10")
    assert [location.label for location in found] == ["Oregon"]
    found = resolve("SMOKE IS STARTING TO SETTLE IN AGAIN", group="@MR10")
    assert [(location.level, location.label) for location in found] == [("region", "MARS Region 10 (MR10)")]


def test_state_suffix_counts_as_state() -> None:
    found = resolve("NET CHECK", recipient="@MR06OK")
    assert [(location.level, location.label) for location in found] == [("state", "Oklahoma")]


def test_grid_ranks_between_county_and_state() -> None:
    assert levels(resolve("STATION IN EM15 REPORTS HAIL, OKLAHOMA")) == {"grid"}
    assert levels(resolve("HAIL AT EM15FL IN TULSA, OK")) == {"precise"}


def test_statrep_uses_sender_grid_at_its_level() -> None:
    found = resolve("ALL GREEN", type="STATREP", grid="EM15", recipient="@MR06")
    assert levels(found) == {"grid"}
    found = resolve("ALL GREEN", type="CSSTATREP", grid="EM15fl")
    assert levels(found) == {"precise"}
    # Non-StatRep traffic does not use the sender's grid as the report location.
    assert levels(resolve("ALL GREEN", type="MESSAGES", grid="EM15", recipient="@MR06")) == {"region"}


def test_every_place_at_the_winning_level_is_returned() -> None:
    found = resolve("Flooding in Tulsa and Norman, Oklahoma")
    assert sorted(location.label for location in found) == ["Norman, OK", "Tulsa, OK"]


def test_dates_and_counts_are_not_coordinates() -> None:
    assert levels(resolve("NET ON 10/12 AT 1900, 5, 10 STATIONS", group="@MR05")) == {"region"}
    assert levels(resolve("Position 35.47, -97.52")) == {"precise"}


def test_bare_small_town_needs_context() -> None:
    # "NORMAN" alone could be a name; inside Oklahoma it is the town.
    assert "city" not in levels(resolve("NORMAN SAYS HELLO"))
    assert levels(resolve("NORMAN SAYS HELLO", recipient="@MR06OK")) == {"city"}


def test_geocoder_results_outside_the_named_region_are_rejected() -> None:
    class FakeGeocoder:
        def __init__(self, result):
            self.result, self.queries = result, []

        def geocode(self, query):
            self.queries.append(query)
            return self.result

    # Sisters, OR is too small for the bundled town list, so the geocoder is asked.
    in_oregon = FakeGeocoder((44.29, -121.55))
    found = resolve("SMOKE NEAR SISTERS", in_oregon, group="@MR10")
    assert [(location.level, location.label) for location in found] == [("city", "Sisters")]
    assert in_oregon.queries == ["Sisters, USA"]

    in_indiana = FakeGeocoder((41.68, -86.25))  # a result in the wrong region
    assert levels(resolve("SMOKE NEAR SISTERS", in_indiana, group="@MR10")) == {"region"}

    with_state = FakeGeocoder((44.29, -121.55))
    resolve("SMOKE NEAR SISTERS, OR", with_state)
    assert with_state.queries == ["Sisters, Oregon, USA"]


def test_bundled_towns_are_found_without_the_geocoder() -> None:
    found = resolve("SMOKE NEAR BEND", None, group="@MR10")
    assert [(location.level, location.label) for location in found] == [("city", "Bend, OR")]


def test_links_are_ignored() -> None:
    found = resolve("Asteroid watch: https://example.org/track?pos=12.5,45.25&g=FN20ab", group="@MR05")
    assert levels(found) == {"region"}


def test_state_prefix_of_status_lines() -> None:
    assert [l.label for l in resolve("MO - FLOOD WATCH DUE TO EXCESIVE RAINFALL")] == ["Missouri"]
    assert [l.label for l in resolve("AZ: Dust engulfs metro area")] == ["Arizona"]
    # A 6-character grid in the status line is more precise than the state, so it wins.
    found = resolve("K6UDA: @MAGNET  ,DN22RN,1,L55,212111111111,ID EXTREME DROUGHT CONDITIONS,{&%} ♢ ")
    assert [(l.level, l.state) for l in found] == [("precise", "ID")]
    # Without a separator, a leading two-letter word is only a state in status lines.
    assert "state" not in {l.level for l in resolve("IN THE NEWS TODAY")}
    assert [l.label for l in resolve("ID EXTREME DROUGHT", type="STATREP")] == ["Idaho"]
