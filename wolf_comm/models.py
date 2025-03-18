from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class Device:
    id: int
    gateway: int
    name: str

    def __str__(self) -> str:
        return f'Name: {self.name}, Id: {self.id}, Gateway {self.gateway}'

@dataclass
class Parameter(ABC):
    value_id: int
    name: str
    parent: str
    parameter_id: int
    bundle_id: int
    readonly: bool

    @property
    @abstractmethod
    def unit(self):
        ...

    def __str__(self) -> str:
        return f"{self.__class__.__name__} -> {self.name}[{self.parameter_id}][{self.bundle_id}][{self.value_id}]{self.readonly} of {self.parent}"

@dataclass
class UnitParameter(Parameter):
    unit: str

    def __str__(self) -> str:
        return super().__str__() + f" unit: [{self.unit}]"

@dataclass
class Temperature(UnitParameter):
    unit: str = "°C"

@dataclass
class Pressure(UnitParameter):
    unit: str = "bar"

@dataclass
class HoursParameter(UnitParameter):
    unit: str = "H"

@dataclass
class PercentageParameter(UnitParameter):
    unit: str = "%"

@dataclass
class PowerParameter(UnitParameter):
    unit: str = "kW"

@dataclass
class EnergyParameter(UnitParameter):
    unit: str = "kWh"

@dataclass
class RPMParameter(UnitParameter):
    unit: str = "U/min"

@dataclass
class FlowParameter(UnitParameter):
    unit: str = "l/min"

@dataclass
class FrequencyParameter(UnitParameter):
    unit: str = "Hz"

@dataclass
class ListItem:
    value: int
    name: str

    def __str__(self) -> str:
        return f'{self.value} -> {self.name}'

@dataclass
class ListItemParameter(Parameter):
    items: list[ListItem]

    def __str__(self) -> str:
        return super().__str__() + " items: " + ", ".join([str(item) for item in self.items])

@dataclass
class Value:
    value_id: int
    value: str
    state: str

    def __str__(self) -> str:
        return f'Value id: {self.value_id}, value: {self.value}, state {self.state}'

