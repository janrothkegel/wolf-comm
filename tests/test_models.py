"""Tests for wolf_comm.models."""
import pytest

from wolf_comm.models import (
    Device,
    EnergyParameter,
    EnergyWhParameter,
    FlowParameter,
    FrequencyParameter,
    HoursParameter,
    ListItem,
    ListItemParameter,
    Parameter,
    PercentageParameter,
    PowerParameter,
    Pressure,
    RPMParameter,
    SimpleParameter,
    Temperature,
    UnitParameter,
    Value,
)

ARGS = dict(value_id=1, name="Test", parent="Tab", parameter_id=10, bundle_id=1000, read_only=True)


def test_device_attributes_and_str():
    device = Device(5, 7, "Home")
    assert device.id == 5
    assert device.gateway == 7
    assert device.name == "Home"
    assert str(device) == "Name: Home, Id: 5, Gateway 7"


@pytest.mark.parametrize(
    "cls,expected_unit",
    [
        (Temperature, "°C"),
        (Pressure, "bar"),
        (PercentageParameter, "%"),
        # NOTE: the API sends 'Std' but the model deliberately reports 'H'.
        (HoursParameter, "H"),
        (PowerParameter, "kW"),
        (EnergyParameter, "kWh"),
        # raw API value is Wh, but it is reported as a standard kWh energy param
        (EnergyWhParameter, "kWh"),
        (RPMParameter, "U/min"),
        (FlowParameter, "l/min"),
        (FrequencyParameter, "Hz"),
    ],
)
def test_unit_parameter_units(cls, expected_unit):
    param = cls(**ARGS)
    assert isinstance(param, UnitParameter)
    assert isinstance(param, Parameter)
    assert param.unit == expected_unit


@pytest.mark.parametrize(
    "cls",
    [
        SimpleParameter,
        Temperature,
        Pressure,
        PercentageParameter,
        HoursParameter,
        PowerParameter,
        EnergyParameter,
        EnergyWhParameter,
        RPMParameter,
        FlowParameter,
        FrequencyParameter,
    ],
)
def test_parameter_common_properties(cls):
    param = cls(**ARGS)
    assert param.value_id == 1
    assert param.name == "Test"
    assert param.parent == "Tab"
    assert param.parameter_id == 10
    assert param.bundle_id == 1000
    assert param.read_only is True


def test_energy_wh_parameter_is_energy_parameter():
    # Consumers that isinstance-check for EnergyParameter must pick it up.
    assert isinstance(EnergyWhParameter(**ARGS), EnergyParameter)


def test_convert_raw_value_identity_by_default():
    assert SimpleParameter(**ARGS).convert_raw_value("42.5") == "42.5"
    assert Temperature(**ARGS).convert_raw_value("21.5") == "21.5"


def test_energy_wh_convert_raw_value_wh_to_kwh():
    param = EnergyWhParameter(**ARGS)
    assert param.convert_raw_value("1204866") == "1204.866"
    assert param.convert_raw_value("1308") == "1.308"


def test_energy_wh_convert_raw_value_passes_non_numeric_through():
    # Placeholder readings (sensor offline) must not raise out of the hook.
    param = EnergyWhParameter(**ARGS)
    assert param.convert_raw_value("--") == "--"
    assert param.convert_raw_value("") == ""
    assert param.convert_raw_value(None) is None


def test_parameter_setters():
    param = SimpleParameter(**ARGS)
    param.name = "Renamed"
    param.value_id = 99
    assert param.name == "Renamed"
    assert param.value_id == 99


def test_parameter_str_contains_identity():
    param = Temperature(**ARGS)
    text = str(param)
    assert "Temperature" in text
    assert "Test" in text
    assert "unit: [°C]" in text


def test_list_item_casts_value_to_int():
    item = ListItem("33", "CGB-2")
    assert item.value == 33
    assert item.name == "CGB-2"
    assert str(item) == "33 -> CGB-2"


def test_list_item_parameter_holds_items():
    items = [ListItem("0", "Auto"), ListItem("1", "Manual")]
    param = ListItemParameter(1, "Mode", "Tab", items, 10, 1000, False)
    assert param.items == items
    assert "0 -> Auto" in str(param)
    assert "1 -> Manual" in str(param)


def test_value_container():
    value = Value(42, "21.5", 1)
    assert value.value_id == 42
    assert value.value == "21.5"
    assert value.state == 1
    assert str(value) == "Value id: 42, value: 21.5, state 1"
