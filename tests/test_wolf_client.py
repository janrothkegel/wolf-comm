"""Tests for wolf_comm.wolf_client.

Sections:
  1. Static parameter-mapping helpers (_map_parameter, _map_view,
     _extract_parameter_descriptors, fix_duplicated_parameters)
  2. Localization helpers (extract_messages_json, try_and_parse,
     replace_with_localized_text)
  3. API methods against a mocked httpx client
  4. End-to-end mapping over the real parameters-examples/ fixtures
"""
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from conftest import EXAMPLES_DIR, make_authorized_client
from wolf_comm.models import (
    EnergyParameter,
    FlowParameter,
    FrequencyParameter,
    HoursParameter,
    ListItemParameter,
    PercentageParameter,
    PowerParameter,
    Pressure,
    RPMParameter,
    SimpleParameter,
    Temperature,
    UnitParameter,
)
from wolf_comm.wolf_client import (
    FetchFailed,
    ParameterReadError,
    ParameterWriteError,
    WolfClient,
    WriteFailed,
)

# ---------------------------------------------------------------------------
# 1. Static parameter-mapping helpers
# ---------------------------------------------------------------------------


def descriptor(**overrides):
    base = {
        "ValueId": 1,
        "ParameterId": 10,
        "Name": "Param",
        "IsReadOnly": True,
        "BundleId": 1000,
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    "unit,expected_cls",
    [
        ("°C", Temperature),
        ("bar", Pressure),
        ("%", PercentageParameter),
        ("Std", HoursParameter),
        ("kW", PowerParameter),
        ("kWh", EnergyParameter),
        ("U/min", RPMParameter),
        ("l/min", FlowParameter),
        ("Hz", FrequencyParameter),
    ],
)
def test_map_parameter_known_units(unit, expected_cls):
    param = WolfClient._map_parameter(descriptor(Unit=unit), "Tab")
    assert isinstance(param, expected_cls)
    assert param.value_id == 1
    assert param.parameter_id == 10
    assert param.bundle_id == 1000
    assert param.read_only is True
    assert param.parent == "Tab"


def test_map_parameter_list_items():
    desc = descriptor(
        ListItems=[
            {"Value": "0", "DisplayText": "Auto"},
            {"Value": "1", "DisplayText": "Manual"},
        ]
    )
    param = WolfClient._map_parameter(desc, "Tab")
    assert isinstance(param, ListItemParameter)
    assert [(i.value, i.name) for i in param.items] == [(0, "Auto"), (1, "Manual")]


def test_map_parameter_simple_fallback():
    param = WolfClient._map_parameter(descriptor(), "Tab")
    assert isinstance(param, SimpleParameter)


def test_map_parameter_defaults_for_missing_fields():
    desc = {"ValueId": 1, "ParameterId": 10, "Name": "Param"}
    param = WolfClient._map_parameter(desc, "Tab")
    # Current behavior: missing IsReadOnly defaults to True and
    # missing BundleId defaults to the *string* "1000".
    assert param.read_only is True
    assert param.bundle_id == "1000"


@pytest.mark.parametrize("unit", ["Uhr", "min", "K", "m³/h", "ppm", "Pa", "V", "Wh;kWh;MWh"])
def test_map_parameter_unknown_unit_returns_none(unit):
    """Known limitation: unrecognized units fall through and map to None.

    These units all occur in the real parameters-examples fixtures. Callers
    (fetch_parameters / fix_duplicated_parameters) skip the None entries, so
    such parameters are silently dropped. If a fallback is ever added, update
    this test accordingly.
    """
    assert WolfClient._map_parameter(descriptor(Unit=unit), "Tab") is None


def test_map_parameter_unknown_unit_with_list_items_returns_none():
    """Known limitation: a Unit key (even unrecognized) shadows ListItems."""
    desc = descriptor(Unit="Uhr", ListItems=[{"Value": "0", "DisplayText": "Auto"}])
    assert WolfClient._map_parameter(desc, "Tab") is None


def test_map_view_plain():
    view = {
        "TabName": "Overview",
        "ParameterDescriptors": [
            descriptor(ValueId=1, Unit="°C"),
            descriptor(ValueId=2),
        ],
    }
    params = WolfClient._map_view(view)
    assert isinstance(params[0], Temperature)
    assert isinstance(params[1], SimpleParameter)
    assert all(p.parent == "Overview" for p in params)


def test_map_view_injects_units_from_svg_schema():
    """Units missing on descriptors are filled in from the SVG heating schema."""
    view = {
        "TabName": "Overview",
        "ParameterDescriptors": [
            descriptor(ValueId=1, Name="KF Kesselfühler"),  # no Unit on its own
            descriptor(ValueId=2, Name="Pumpe"),
        ],
        "SVGHeatingSchemaConfigDevices": [
            {
                "parameters": [
                    {"valueId": 1, "unit": "°C", "parameterName": "KF Kesselfühler"},
                    {"valueId": 2, "parameterName": "Pumpe"},  # no unit -> untouched
                ]
            }
        ],
    }
    params = WolfClient._map_view(view)
    assert isinstance(params[0], Temperature)
    assert isinstance(params[1], SimpleParameter)


def test_extract_parameter_descriptors_recurses_and_stamps_bundle_id():
    desc = {
        "MenuItems": [
            {
                "Name": "Benutzer",
                "TabViews": [
                    {
                        "TabName": "Overview",
                        "BundleId": 1000,
                        "ParameterDescriptors": [descriptor(ValueId=1)],
                    }
                ],
            },
            {
                "Name": "Fachmann",
                "TabViews": [],
                "SubMenuEntries": [
                    {
                        "Name": "Heizgerät",
                        "TabViews": [
                            {
                                "TabName": "Expert",
                                "BundleId": 2000,
                                "ParameterDescriptors": [
                                    {"ValueId": 3, "ParameterId": 30, "Name": "Deep"}
                                ],
                            }
                        ],
                    }
                ],
            },
        ]
    }
    extracted = WolfClient._extract_parameter_descriptors(desc)
    assert [d["ValueId"] for d in extracted] == [1, 3]
    # BundleId of the enclosing TabView is stamped onto each descriptor.
    assert extracted[0]["BundleId"] == 1000
    assert extracted[1]["BundleId"] == 2000


def test_fix_duplicated_parameters_dedups_and_skips_none():
    client = WolfClient.__new__(WolfClient)  # no auth needed for this helper
    p1 = SimpleParameter(1, "A", "Tab", 10, 1000, True)
    p1_dup = SimpleParameter(1, "A again", "Tab", 10, 1000, True)
    p2 = SimpleParameter(2, "B", "Tab", 20, 1000, True)
    result = client.fix_duplicated_parameters([p1, None, p1_dup, p2])
    assert result == [p1, p2]


# ---------------------------------------------------------------------------
# 2. Localization helpers
# ---------------------------------------------------------------------------


def make_bare_client():
    client = WolfClient.__new__(WolfClient)
    client.regional = None
    return client


def test_extract_messages_json_parses_js_payload():
    js = 'angular.module("x").value({ messages: {\n"Heizung": "Heating",\n"Warmwasser": "Hot water"\n} });'
    parsed = WolfClient.extract_messages_json(js)
    assert parsed == {"Heizung": "Heating", "Warmwasser": "Hot water"}


def test_extract_messages_json_returns_none_without_match():
    assert WolfClient.extract_messages_json("no messages object here") is None


def test_try_and_parse_valid_json():
    assert WolfClient.try_and_parse('{"a": "b"}', 10) == {"a": "b"}


def test_try_and_parse_removes_bad_lines():
    text = '{\n"a": "b",\nTHIS IS NOT JSON\n"c": "d"\n}'
    assert WolfClient.try_and_parse(text, 10) == {"a": "b", "c": "d"}


def test_try_and_parse_exhausted_returns_input_unchanged():
    # Current behavior: when retries run out the raw *string* is returned
    # (not None), which callers must guard against.
    assert WolfClient.try_and_parse("not json", 0) == "not json"


def test_replace_with_localized_text_hit_and_miss():
    client = make_bare_client()
    client.regional = {"Heizung": "Heating"}
    assert client.replace_with_localized_text("Heizung") == "Heating"
    assert client.replace_with_localized_text("Unbekannt") == "Unbekannt"


def test_replace_with_localized_text_without_regional():
    client = make_bare_client()
    assert client.replace_with_localized_text("Heizung") == "Heizung"


# ---------------------------------------------------------------------------
# 3. API methods against a mocked httpx client
# ---------------------------------------------------------------------------


def ok(payload):
    return httpx.Response(200, json=payload)


def test_client_requires_single_client_config():
    with pytest.raises(RuntimeError):
        WolfClient("u", "p", client=AsyncMock(), client_lambda=lambda: AsyncMock())


async def test_fetch_system_list():
    wc, http = make_authorized_client(
        ok([{"Id": 5, "GatewayId": 7, "Name": "Home"}])
    )
    devices = await wc.fetch_system_list()
    assert len(devices) == 1
    assert (devices[0].id, devices[0].gateway, devices[0].name) == (5, 7, "Home")
    method, url = http.request.call_args.args[:2]
    assert method == "get"
    assert url.endswith("api/portal/GetSystemList")


async def test_fetch_system_state_list():
    wc, http = make_authorized_client(
        ok([{"GatewayState": {"IsOnline": True}}])
    )
    assert await wc.fetch_system_state_list(5, 7) is True
    payload = http.request.call_args.kwargs["json"]
    assert payload["SystemList"] == [{"SystemId": 5, "GatewayId": 7}]
    # __request injects the session id into every JSON payload.
    assert payload["SessionId"] == 1


async def test_fetch_value_batches_by_bundle_id():
    params = [
        Temperature(11, "T1", "Tab", 110, 1000, True),
        Pressure(12, "P1", "Tab", 120, 1000, True),
        Temperature(21, "T2", "Tab", 210, 2000, True),
    ]
    wc, http = make_authorized_client(
        ok({"Values": [
            {"ValueId": 11, "Value": "21.5", "State": 1},
            {"ValueId": 12, "Value": "1.8", "State": 1},
        ], "LastAccess": "2025-01-01T00:00:00Z"}),
        ok({"Values": [
            {"ValueId": 21, "Value": "45.0", "State": 1},
        ], "LastAccess": "2025-01-01T00:00:00Z"}),
    )

    values = await wc.fetch_value(7, 5, params)

    # one request per bundle, not per parameter
    assert http.request.call_count == 2
    first = http.request.call_args_list[0].kwargs["json"]
    second = http.request.call_args_list[1].kwargs["json"]
    assert first["BundleId"] == 1000
    assert first["ValueIdList"] == [11, 12]
    assert second["BundleId"] == 2000
    assert second["ValueIdList"] == [21]

    assert [(v.value_id, v.value) for v in values] == [(11, "21.5"), (12, "1.8"), (21, "45.0")]


async def test_fetch_value_skips_entries_without_value():
    params = [Temperature(11, "T1", "Tab", 110, 1000, True)]
    wc, _ = make_authorized_client(
        ok({"Values": [{"ValueId": 11, "State": 0}], "LastAccess": "x"})
    )
    assert await wc.fetch_value(7, 5, params) == []


async def test_fetch_value_raises_parameter_read_error():
    params = [Temperature(11, "T1", "Tab", 110, 1000, True)]
    wc, _ = make_authorized_client(
        ok({"ErrorCode": 5, "Message": "internal msg: ReadParameterValues error"})
    )
    with pytest.raises(ParameterReadError):
        await wc.fetch_value(7, 5, params)


async def test_fetch_value_raises_fetch_failed_on_other_errors():
    params = [Temperature(11, "T1", "Tab", 110, 1000, True)]
    wc, _ = make_authorized_client(
        ok({"ErrorCode": 99, "Message": "something else"})
    )
    with pytest.raises(FetchFailed):
        await wc.fetch_value(7, 5, params)


async def test_write_value_payload_and_result():
    wc, http = make_authorized_client(ok({"Status": "OK"}))
    result = await wc.write_value(7, 5, 1000, {"ValueId": 11, "State": "1"})
    assert result == {"Status": "OK"}
    payload = http.request.call_args.kwargs["json"]
    assert payload["WriteParameterValues"] == [{"ValueId": 11, "Value": "1"}]
    assert payload["SystemId"] == 5
    assert payload["GatewayId"] == 7
    assert payload["BundleId"] == 1000
    assert payload["SessionId"] == 1


async def test_write_value_raises_parameter_write_error():
    wc, _ = make_authorized_client(
        ok({"ErrorCode": 5, "Message": "internal msg: ReadParameterValues error"})
    )
    with pytest.raises(ParameterWriteError):
        await wc.write_value(7, 5, 1000, {"ValueId": 11, "State": "1"})


async def test_write_value_raises_write_failed_on_other_errors():
    wc, _ = make_authorized_client(
        ok({"ErrorCode": 99, "Message": "boom"})
    )
    with pytest.raises(WriteFailed):
        await wc.write_value(7, 5, 1000, {"ValueId": 11, "State": "1"})


async def test_request_retries_once_on_500():
    wc, http = make_authorized_client(
        httpx.Response(500, json={"error": "server"}),
        ok([{"Id": 1, "GatewayId": 2, "Name": "Home"}]),
    )
    # Avoid the real re-auth flow on retry.
    wc._WolfClient__authorize_and_session = AsyncMock()

    devices = await wc.fetch_system_list()

    assert http.request.call_count == 2
    wc._WolfClient__authorize_and_session.assert_awaited_once()
    assert devices[0].name == "Home"


GUI_DESC = {
    "MenuItems": [
        {
            "Name": "Benutzer",
            "TabViews": [
                {
                    "TabName": "Overview",
                    "BundleId": 1000,
                    "ParameterDescriptors": [
                        {"ValueId": 1, "ParameterId": 10, "Name": "Heizung",
                         "Unit": "°C", "IsReadOnly": True, "BundleId": 1000},
                        {"ValueId": 2, "ParameterId": 20, "Name": "Mode",
                         "ListItems": [{"Value": "0", "DisplayText": "Auto"}],
                         "IsReadOnly": False, "BundleId": 1000},
                        # duplicate of ValueId 1 — must be dropped
                        {"ValueId": 1, "ParameterId": 10, "Name": "Heizung",
                         "Unit": "°C", "IsReadOnly": True, "BundleId": 1000},
                        # SPLIT naming convention: group prefix --- name
                        {"ValueId": 4, "ParameterId": 40, "Name": "210_Kessel---Temperatur",
                         "IsReadOnly": True, "BundleId": 1000},
                    ],
                }
            ],
        },
        {
            "Name": "Fachmann",
            "TabViews": [],
            "SubMenuEntries": [
                {
                    "Name": "Heizgerät",
                    "TabViews": [
                        {
                            "TabName": "Expert",
                            "BundleId": 2000,
                            "ParameterDescriptors": [
                                {"ValueId": 3, "ParameterId": 30,
                                 "Name": "ExpertTemp", "Unit": "°C", "IsReadOnly": True}
                            ],
                        }
                    ],
                }
            ],
        },
    ]
}


def prepare_for_fetch_parameters(wc, regional):
    """Skip the network localization fetch and seed the dictionary."""
    wc.load_localized_json = AsyncMock()
    wc.regional = regional


async def test_fetch_parameters_standard_mode():
    wc, _ = make_authorized_client(ok(GUI_DESC))
    prepare_for_fetch_parameters(wc, {"Heizung": "Heating", "Kessel": "Boiler",
                                      "Temperatur": "Temperature"})

    params = await wc.fetch_parameters(7, 5)

    by_id = {p.value_id: p for p in params}
    # Only MenuItems[0] (Benutzer) is used in standard mode; expert id 3 absent.
    assert set(by_id) == {1, 2, 4}
    assert isinstance(by_id[1], Temperature)
    assert isinstance(by_id[2], ListItemParameter)
    # names are localized
    assert by_id[1].name == "Heating"
    # SPLIT names: "210_Kessel---Temperatur" -> localized "Boiler Temperature"
    assert by_id[4].name == "Boiler Temperature"


async def test_fetch_parameters_expert_mode():
    wc, _ = make_authorized_client(ok(GUI_DESC))
    wc.expert_mode = True
    prepare_for_fetch_parameters(wc, {})

    params = await wc.fetch_parameters(7, 5)

    by_id = {p.value_id: p for p in params}
    # Expert mode recurses the whole tree, including the Fachmann submenu.
    assert set(by_id) == {1, 2, 3, 4}
    # BundleId of the enclosing tab view is stamped onto extracted descriptors.
    assert by_id[3].bundle_id == 2000


# ---------------------------------------------------------------------------
# 4. End-to-end mapping over the real parameters-examples/ fixtures
# ---------------------------------------------------------------------------

ALL_FIXTURES = [
    "gasparameters.json",
    "gashybridparameters.json",
    "heatpumpparameter.json",
    "luftung.json",
]


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_all_fixtures_are_valid_json(name):
    with open(EXAMPLES_DIR / name, encoding="utf-8") as f:
        data = json.load(f)
    assert set(data) == {"MenuItems", "DynFaultMessageDevices", "SystemHasWRSClassicDevices"}
    assert [m["Name"] for m in data["MenuItems"]] == ["Benutzer", "Fachmann"]


def test_extract_descriptors_from_gas_fixture(gas_desc):
    descriptors = WolfClient._extract_parameter_descriptors(gas_desc)
    # Only arrays keyed exactly 'ParameterDescriptors' are collected (226 in
    # this fixture); ChildParameterDescriptors etc. are intentionally not.
    assert len(descriptors) > 200
    assert all("ValueId" in d for d in descriptors)
    # BundleId stamping must have run on every extracted descriptor.
    assert all("BundleId" in d for d in descriptors)


@pytest.mark.parametrize(
    "fixture_name,min_temps,min_lists",
    [
        ("gas_desc", 50, 20),
        ("gashybrid_desc", 50, 20),
        ("heatpump_desc", 50, 20),
        ("luftung_desc", 5, 5),
    ],
)
def test_map_all_descriptors_without_exceptions(fixture_name, min_temps, min_lists, request):
    desc = request.getfixturevalue(fixture_name)
    descriptors = WolfClient._extract_parameter_descriptors(desc)
    mapped = [WolfClient._map_parameter(d, "Test") for d in descriptors]

    temperatures = [p for p in mapped if isinstance(p, Temperature)]
    list_params = [p for p in mapped if isinstance(p, ListItemParameter)]
    dropped = [d for d, p in zip(descriptors, mapped) if p is None]

    assert len(temperatures) > min_temps
    assert len(list_params) > min_lists
    # Known limitation: fixtures contain units the mapper does not recognize
    # (Uhr, min, K, m³/h, ppm, Pa, V, ...), which currently map to None and
    # get dropped.
    assert dropped, "expected some unrecognized-unit descriptors in real data"
    assert all("Unit" in d for d in dropped)


def test_map_view_svg_unit_injection_on_gas_fixture(gas_desc):
    """The Benutzer Übersicht view supplies units via SVGHeatingSchemaConfigDevices."""
    view = gas_desc["MenuItems"][0]["TabViews"][0]
    assert "SVGHeatingSchemaConfigDevices" in view

    svg_units = {
        p["valueId"]: p["unit"]
        for p in view["SVGHeatingSchemaConfigDevices"][0]["parameters"]
        if "unit" in p
    }
    descriptor_ids = {d["ValueId"] for d in view["ParameterDescriptors"]}
    injectable = svg_units.keys() & descriptor_ids
    assert injectable, "fixture should contain descriptors whose unit comes from the SVG schema"

    params = WolfClient._map_view(view)
    by_id = {p.value_id: p for p in params if p is not None}
    for value_id in injectable:
        assert isinstance(by_id[value_id], UnitParameter)
        assert by_id[value_id].unit == svg_units[value_id].replace("Std", "H")


def test_standard_mode_view_mapping_on_gas_fixture(gas_desc):
    """Map every Benutzer tab view as fetch_parameters does in standard mode."""
    tab_views = gas_desc["MenuItems"][0]["TabViews"]
    all_params = []
    for view in tab_views:
        all_params.extend(p for p in WolfClient._map_view(view) if p is not None)
    assert len(all_params) > 20
    parents = {p.parent for p in all_params}
    assert "Übersicht" in parents


def test_heatpump_has_heatpump_specific_units(heatpump_desc):
    """The heat pump fixture exercises units absent from the gas systems.

    Note: the fixture also contains 'Hz' parameters, but only inside
    SchemaTabViewConfigDTO>Configs>Parameters, which
    _extract_parameter_descriptors does not visit — so 'Hz' never reaches
    the mapper despite FrequencyParameter existing for it.
    """
    descriptors = WolfClient._extract_parameter_descriptors(heatpump_desc)
    units = {d["Unit"] for d in descriptors if "Unit" in d}
    assert {"kWh", "kW", "U/min"} <= units
    assert "Hz" not in units
