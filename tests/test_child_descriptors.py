"""Nested ChildParameterDescriptors: time programs and party/holiday dates and times."""
import copy

import pytest

from wolf_comm import (
    DateParameter, TimeParameter, TimeProgramParameter, SimpleParameter, WolfClient,
)


def _day(value_id, name="Mo", value="05:00-19:30;00:00-00:00;00:00-00:00;00:00-00:00;00:00-00:00"):
    return {
        "ValueId": value_id, "ParameterId": value_id, "IsReadOnly": False, "Name": name,
        "ControlType": 14, "Value": value, "Unit": "Uhr",
    }


def _program_tree():
    return {
        "MenuItems": [{
            "Name": "Benutzer",
            "TabViews": [{
                "TabName": "Heizkreis", "BundleId": 1000,
                "ParameterDescriptors": [{
                    "ValueId": 34001600000, "ParameterId": 34001600000, "Name": "Zeitprogramm",
                    "ControlType": 13, "IsReadOnly": True,
                    "ChildParameterDescriptors": [
                        {"ValueId": 0, "Name": "Zeitprogramm 1", "ControlType": 19,
                         "ChildParameterDescriptors": [_day(32000000000, "Mo"), _day(32000100000, "Di")]},
                        {"ValueId": 0, "Name": "Zeitprogramm 2", "ControlType": 19,
                         "ChildParameterDescriptors": [_day(32000700000, "Mo")]},
                    ],
                }],
            }],
            "SubMenuEntries": [],
        }],
    }


def test_with_children_skips_containers_and_stamps_label_and_bundle():
    tree = _program_tree()
    top = tree["MenuItems"][0]["TabViews"][0]["ParameterDescriptors"]
    result = list(WolfClient._with_children(top, 1000))

    assert [d["ValueId"] for d in result] == [34001600000, 32000000000, 32000100000, 32000700000]
    assert result[1]["ChildOf"] == "Zeitprogramm > Zeitprogramm 1"
    assert result[3]["ChildOf"] == "Zeitprogramm > Zeitprogramm 2"
    assert all(d["BundleId"] == 1000 for d in result[1:])
    assert "ChildOf" not in result[0]


def test_with_children_does_not_stamp_bundle_when_unknown():
    top = _program_tree()["MenuItems"][0]["TabViews"][0]["ParameterDescriptors"]
    assert all("BundleId" not in d for d in WolfClient._with_children(top))


def test_extract_descriptors_includes_children_with_enclosing_bundle():
    extracted = WolfClient._extract_parameter_descriptors(_program_tree())
    ids = [d["ValueId"] for d in extracted]
    assert ids == [34001600000, 32000000000, 32000100000, 32000700000]
    assert all(d["BundleId"] == 1000 for d in extracted)


def test_map_parameter_types_time_program_days_despite_uhr_unit():
    day = _day(32000000000)
    day["ChildOf"] = "Zeitprogramm > Zeitprogramm 1"
    day["BundleId"] = 1000
    p = WolfClient._map_parameter(day, None)
    assert isinstance(p, TimeProgramParameter)
    assert p.parent == "Zeitprogramm > Zeitprogramm 1"
    assert p.bundle_id == 1000
    assert p.read_only is False


@pytest.mark.parametrize("control_type,cls", [(9, DateParameter), (10, TimeParameter)])
def test_map_parameter_types_nested_date_and_time(control_type, cls):
    d = {"ValueId": 33000000000, "ParameterId": 33000000000, "Name": "Datum Start",
         "ControlType": control_type, "IsReadOnly": False, "ChildOf": "Urlaubsmodus"}
    assert isinstance(WolfClient._map_parameter(d, None), cls)


def test_top_level_descriptors_keep_their_old_types():
    # without ChildOf the ControlType is ignored: a top-level date/time
    # descriptor is still a SimpleParameter, and a top-level "Uhr" unit is still dropped
    base = {"ValueId": 1, "ParameterId": 1, "Name": "x", "IsReadOnly": True}
    assert type(WolfClient._map_parameter({**base, "ControlType": 10}, "T")) is SimpleParameter
    assert WolfClient._map_parameter({**base, "ControlType": 10, "Unit": "Uhr"}, "T") is None


def test_normal_mode_view_includes_children():
    view = _program_tree()["MenuItems"][0]["TabViews"][0]
    mapped = WolfClient._map_view(view)
    assert [type(p) for p in mapped] == [
        SimpleParameter, TimeProgramParameter, TimeProgramParameter, TimeProgramParameter,
    ]
    assert mapped[1].parent == "Zeitprogramm > Zeitprogramm 1"


@pytest.mark.parametrize("fixture_name,expected_days", [
    ("gas_desc", 42), ("gashybrid_desc", 63), ("heatpump_desc", 42), ("luftung_desc", 21),
])
def test_fixtures_expose_all_time_program_days(fixture_name, expected_days, request):
    desc = copy.deepcopy(request.getfixturevalue(fixture_name))
    descriptors = WolfClient._extract_parameter_descriptors(desc)
    mapped = [WolfClient._map_parameter(d, None) for d in descriptors]
    days = [p for p in mapped if isinstance(p, TimeProgramParameter)]

    assert len(days) == expected_days
    assert len({p.value_id for p in days}) == expected_days
    assert all(not p.read_only for p in days)
    # one date and one time per party/holiday start, end and BM2 entry
    assert len([p for p in mapped if isinstance(p, DateParameter)]) == 6
    assert len([p for p in mapped if isinstance(p, TimeParameter)]) == 6


def test_time_program_parse_and_format_round_trip():
    raw = "04:50-12:00;13:30-21:30;00:00-00:00;00:00-00:00;00:00-00:00"
    windows = TimeProgramParameter.parse(raw)
    assert windows == [("04:50", "12:00"), ("13:30", "21:30")]
    assert TimeProgramParameter.format(windows) == raw


def test_time_program_format_empty_and_overflow():
    assert TimeProgramParameter.format([]) == ";".join(["00:00-00:00"] * 5)
    with pytest.raises(ValueError):
        TimeProgramParameter.format([("00:00", "01:00")] * 6)
