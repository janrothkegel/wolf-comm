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
    EnergyWhParameter,
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
        ("Wh;kWh", EnergyWhParameter),
        ("Wh;kWh;MWh", EnergyWhParameter),
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
    # Missing IsReadOnly defaults to True; missing BundleId defaults to the
    # *int* 1000 (matching the API type, so value batching keys stay uniform
    # and Parameter.__str__'s %d formatting works).
    assert param.read_only is True
    assert param.bundle_id == 1000
    assert "[1000]" in str(param)


@pytest.mark.parametrize("unit", ["Uhr", "min", "K", "m³/h", "ppm", "Pa", "V"])
def test_map_parameter_unknown_unit_returns_none(unit):
    """Intended behavior: unrecognized units fall through and map to None.

    These units all occur in the real parameters-examples fixtures. Callers
    (fetch_parameters / fix_duplicated_parameters) skip the None entries, so
    such parameters are dropped. This is deliberate — unknown units must not
    surface as untyped sensors; supporting one requires an explicit constant,
    model class, and _map_parameter branch.
    """
    assert WolfClient._map_parameter(descriptor(Unit=unit), "Tab") is None


def test_map_parameter_unknown_unit_with_list_items_returns_none():
    """Intended behavior: a Unit key (even unrecognized) shadows ListItems."""
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


def test_extract_descriptors_without_any_bundle_id_keeps_default():
    # A ParameterDescriptors array with no BundleId anywhere up the tree must
    # NOT be stamped with None — None would bypass _map_parameter's 1000
    # default and end up as "BundleId": null in GetParameterValues requests.
    tree = {"SomeNode": {"ParameterDescriptors": [
        {"ValueId": 1, "ParameterId": 10, "Name": "P"}
    ]}}
    descriptors = WolfClient._extract_parameter_descriptors(tree)
    assert len(descriptors) == 1
    assert "BundleId" not in descriptors[0]
    param = WolfClient._map_parameter(descriptors[0], "Tab")
    assert param.bundle_id == 1000


def test_extract_descriptors_inherits_bundle_id_from_ancestor():
    # "Nearest enclosing BundleId": when the immediate parent of a
    # ParameterDescriptors array has no BundleId, the closest ancestor's is
    # threaded down the recursion instead of silently falling back to 1000.
    tree = {
        "TabViews": [{
            "BundleId": 4200,
            "SomeWrapper": {"ParameterDescriptors": [
                {"ValueId": 1, "ParameterId": 10, "Name": "P"}
            ]},
        }]
    }
    descriptors = WolfClient._extract_parameter_descriptors(tree)
    assert len(descriptors) == 1
    assert descriptors[0]["BundleId"] == 4200


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


def test_try_and_parse_exhausted_returns_none():
    # Returns None on exhaustion so callers can safely guard with `is not None`
    # rather than receiving a raw string that would corrupt self.regional.
    assert WolfClient.try_and_parse("not json", 0) is None


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


async def test_fetch_value_converts_wh_to_kwh_for_energy_wh_parameters():
    # 'Wh;kWh' / 'Wh;kWh;MWh' parameters deliver raw Wh; fetch_value must
    # report them in kWh. Plain EnergyParameter values pass through untouched.
    params = [
        EnergyWhParameter(11, "Gesamtertrag", "Tab", 110, 1000, True),
        EnergyParameter(12, "E1", "Tab", 120, 1000, True),
    ]
    wc, _ = make_authorized_client(
        ok({"Values": [
            {"ValueId": 11, "Value": "1204866", "State": 1},
            {"ValueId": 12, "Value": "45.0", "State": 1},
        ], "LastAccess": "x"})
    )
    values = await wc.fetch_value(7, 5, params)
    assert [(v.value_id, v.value) for v in values] == [(11, "1204.866"), (12, "45.0")]


async def test_fetch_value_passes_non_numeric_wh_value_through():
    # A placeholder reading (sensor offline) must not abort the whole fetch;
    # the raw string is passed through unconverted.
    params = [
        EnergyWhParameter(11, "Gesamtertrag", "Tab", 110, 1000, True),
        Temperature(12, "T1", "Tab", 120, 1000, True),
    ]
    wc, _ = make_authorized_client(
        ok({"Values": [
            {"ValueId": 11, "Value": "--", "State": 0},
            {"ValueId": 12, "Value": "21.5", "State": 1},
        ], "LastAccess": "x"})
    )
    values = await wc.fetch_value(7, 5, params)
    assert [(v.value_id, v.value) for v in values] == [(11, "--"), (12, "21.5")]


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
    assert wc.last_failed is False


async def test_request_raises_fetch_failed_when_retry_also_fails():
    wc, http = make_authorized_client(
        httpx.Response(500, json={"error": "server"}),
        httpx.Response(500, json={"error": "still broken"}),
    )
    wc._WolfClient__authorize_and_session = AsyncMock()

    with pytest.raises(FetchFailed) as exc_info:
        await wc.fetch_system_list()

    # exactly one retry, then give up — the error body must not be returned
    # to the caller as if it were data, but it IS preserved on the exception
    assert http.request.call_count == 2
    assert wc.last_failed is True
    assert exc_info.value.response == {"error": "still broken"}


async def test_request_retry_uses_fresh_token_after_reauth():
    # The retry must carry the token obtained by re-auth, not the stale one
    # from the failed attempt (regression test: the old header merge let the
    # stale Authorization win, so token-invalidation 401s could never recover).
    from wolf_comm.token_auth import Tokens

    wc, http = make_authorized_client(
        httpx.Response(401, json={}),
        ok([{"Id": 1, "GatewayId": 2, "Name": "Home"}]),
    )

    async def reauth():
        wc.tokens = Tokens("fresh-token", 3600)
        wc.session_id = 2

    wc._WolfClient__authorize_and_session = AsyncMock(side_effect=reauth)

    await wc.fetch_system_list()

    first_headers = http.request.call_args_list[0].kwargs["headers"]
    retry_headers = http.request.call_args_list[1].kwargs["headers"]
    assert first_headers["Authorization"] == "Bearer test-token"
    assert retry_headers["Authorization"] == "Bearer fresh-token"


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
    # Intentional: fixtures contain units the mapper does not recognize
    # (Uhr, min, K, m³/h, ppm, Pa, V, ...), which map to None and get
    # dropped by design.
    assert dropped, "expected some unrecognized-unit descriptors in real data"
    assert all("Unit" in d for d in dropped)


@pytest.mark.parametrize("fixture_name", ["gas_desc", "gashybrid_desc", "heatpump_desc", "luftung_desc"])
def test_expert_mode_bundle_stamping_on_all_fixtures(fixture_name, request):
    """Expert-mode pipeline: every descriptor gets a real int BundleId from its
    enclosing TabView, so fetch_value's per-bundle batching produces one
    request per bundle with no None/str keys."""
    desc = request.getfixturevalue(fixture_name)
    descriptors = WolfClient._extract_parameter_descriptors(desc)
    descriptors.sort(key=lambda d: d["ValueId"])

    # every ParameterDescriptors parent in the real data is a TabView with a BundleId
    assert all(isinstance(d.get("BundleId"), int) for d in descriptors)

    params = [p for p in (WolfClient._map_parameter(d, None) for d in descriptors) if p is not None]
    seen, deduped = set(), []
    for p in params:
        if p.value_id not in seen:
            seen.add(p.value_id)
            deduped.append(p)

    # replicate fetch_value's grouping: each surviving param lands in exactly
    # one int-keyed bundle, so one GetParameterValues request per bundle
    bundles = {}
    for p in deduped:
        bundles.setdefault(p.bundle_id, []).append(p.value_id)
    assert all(isinstance(b, int) for b in bundles)
    assert len(bundles) > 1, "expert mode should span multiple bundles"
    assert sum(len(v) for v in bundles.values()) == len(deduped)


def test_gashybrid_wh_energy_descriptors_map_to_energy_wh_parameter(gashybrid_desc):
    descriptors = WolfClient._extract_parameter_descriptors(gashybrid_desc)
    wh_descriptors = [d for d in descriptors if d.get("Unit") in ("Wh;kWh", "Wh;kWh;MWh")]
    assert wh_descriptors, "fixture should contain Wh-based energy descriptors"

    mapped = [WolfClient._map_parameter(d, "Test") for d in wh_descriptors]
    assert all(isinstance(p, EnergyWhParameter) for p in mapped)
    assert all(p.unit == "kWh" for p in mapped)


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
