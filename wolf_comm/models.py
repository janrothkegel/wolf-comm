from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class Device:
    id: int
    gateway: int
    name: str

    def __str__(self) -> str:
        return f"Name: {self.name}, Id: {self.id}, Gateway {self.gateway}"


class Parameter(ABC):

    @property
    @abstractmethod
    def value_id(self) -> int:
        ...

    @value_id.setter
    @abstractmethod
    def value_id(self, value_id: int):
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @name.setter
    @abstractmethod
    def name(self, name: str):
        ...

    @property
    @abstractmethod
    def parameter_id(self) -> int:
        ...

    @property
    @abstractmethod
    def bundle_id(self) -> int:
        ...

    @property
    @abstractmethod
    def read_only(self) -> bool:
        ...

    @property
    @abstractmethod
    def parent(self) -> str:
        ...

    def __str__(self) -> str:
        return (
            f"{self.__class__.__name__} -> {self.name}"
            f"[{self.parameter_id}][{self.bundle_id}][{self.read_only}][{self.value_id}]"
            f" of {self.parent}"
        )


class _ParameterBase(Parameter, ABC):
    """Shared __init__ and properties for all concrete parameter types."""

    def __init__(self, value_id: int, name: str, parent: str,
                 parameter_id: int, bundle_id: int, read_only: bool):
        self._value_id = value_id
        self._name = name
        self._parent = parent
        self._parameter_id = parameter_id
        self._bundle_id = bundle_id
        self._read_only = read_only

    @property
    def value_id(self) -> int:
        return self._value_id

    @value_id.setter
    def value_id(self, value_id: int):
        self._value_id = value_id

    @property
    def name(self) -> str:
        return self._name

    @name.setter
    def name(self, name: str):
        self._name = name

    @property
    def parameter_id(self) -> int:
        return self._parameter_id

    @property
    def bundle_id(self) -> int:
        return self._bundle_id

    @property
    def read_only(self) -> bool:
        return self._read_only

    @property
    def parent(self) -> str:
        return self._parent


class SimpleParameter(_ParameterBase):
    pass


class UnitParameter(_ParameterBase, ABC):
    _unit: str = ""

    @property
    def unit(self) -> str:
        return self._unit

    def __str__(self) -> str:
        return super().__str__() + f" unit: [{self.unit}]"


class Temperature(UnitParameter):
    _unit = "°C"


class Pressure(UnitParameter):
    _unit = "bar"


class HoursParameter(UnitParameter):
    _unit = "H"


class PercentageParameter(UnitParameter):
    _unit = "%"


class PowerParameter(UnitParameter):
    _unit = "kW"


class EnergyParameter(UnitParameter):
    _unit = "kWh"


class RPMParameter(UnitParameter):
    _unit = "U/min"


class FlowParameter(UnitParameter):
    _unit = "l/min"


class FrequencyParameter(UnitParameter):
    _unit = "Hz"


@dataclass
class ListItem:
    value: int
    name: str

    def __post_init__(self):
        self.value = int(self.value)

    def __str__(self) -> str:
        return f"{self.value} -> {self.name}"


class ListItemParameter(_ParameterBase):

    def __init__(
            self,
            value_id: int,
            name: str,
            parent: str,
            items: list[ListItem],
            parameter_id: int,
            bundle_id: int,
            read_only: bool
    ):
        super().__init__(value_id, name, parent, parameter_id, bundle_id, read_only)
        self.items = items

    def __str__(self) -> str:
        return super().__str__() + " items: " + ", ".join([str(item) for item in self.items])


@dataclass
class Value:
    value_id: int
    value: str
    state: str

    def __str__(self) -> str:
        return f"Value id: {self.value_id}, value: {self.value}, state {self.state}"
