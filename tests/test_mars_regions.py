from s2_watch.ui.map_view import load_mars_regions


def test_mars_regions_cover_fema_regions_one_through_ten() -> None:
    regions = load_mars_regions()
    features = regions["features"]
    assert sorted(feature["properties"]["region"] for feature in features) == list(range(1, 11))
    for feature in features:
        assert feature["geometry"]["type"] in {"Polygon", "MultiPolygon"}
        assert feature["properties"]["name"] == f"Region {feature['properties']['region']}"
        assert feature["properties"]["labels"]


def test_region_ten_is_labelled_in_alaska_and_the_pacific_northwest() -> None:
    region_ten = next(
        feature for feature in load_mars_regions()["features"]
        if feature["properties"]["region"] == 10
    )
    latitudes = sorted(label[0] for label in region_ten["properties"]["labels"])
    assert latitudes[0] < 50 < latitudes[-1]


def test_every_region_has_an_anchor_inside_the_lower_48_or_its_territory() -> None:
    from s2_watch.ui.map_view import mars_region_anchors

    anchors = mars_region_anchors()
    assert sorted(anchors) == list(range(1, 11))
    # Region 10's anchor is in the Pacific Northwest, not Alaska.
    assert anchors[10][0] < 50


def test_state_anchors_belong_to_their_region() -> None:
    from s2_watch.ui.map_view import mars_state_anchors

    anchors = mars_state_anchors()
    assert len(anchors) == 50
    assert anchors[(6, "OK")][0] == "Oklahoma"
    assert (9, "OK") not in anchors
    latitude, longitude = anchors[(6, "OK")][1]
    assert 33.6 < latitude < 37 and -103 < longitude < -94.4


def test_map_gives_every_location_its_own_key() -> None:
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    import s2_watch.ui.map_view as map_view
    from s2_watch.models import Message

    app = QApplication.instance() or QApplication([])
    original = map_view.QWebEngineView
    map_view.QWebEngineView = None  # the fallback view; no browser needed
    try:
        view = map_view.MapView()
        view.add_message(Message("JS8Call", "RACE IN GRAND RAPIDS. CPLX CONDEMNED IN KALAMAZOO."))
        view.add_message(Message("JS8Call", "73 FROM TULSA", fields={"grid": "EM26"}))
        keys = [location["key"] for location in view._locations]
        assert len(keys) == 4 and len(set(keys)) == 4
    finally:
        map_view.QWebEngineView = original
