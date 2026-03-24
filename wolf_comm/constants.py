"""
File with all constants
"""
from enum import Enum


class ParameterUnit(str, Enum):
    CELSIUS = "°C"
    BAR = "bar"
    PERCENTAGE = "%"
    HOUR = "Std"
    KILOWATT = "kW"
    KILOWATTHOURS = "kWh"
    RPM = "U/min"
    FLOW = "l/min"
    FREQUENCY = "Hz"


BASE_URL = "https://www.wolf-smartset.com"

BASE_URL_PORTAL = BASE_URL + "/portal"

AUTHENTICATION_URL = "/idsrv"
AUTHENTICATION_BASE_URL = BASE_URL + AUTHENTICATION_URL
AUTHENTICATION_CLIENT = "smartset.web"

SESSION_ID = 'SessionId'

GUI_ID_CHANGED = 'GuiIdChanged'

BUNDLE_ID = 'BundleId'

ISREADONLY = 'IsReadOnly'

BUNDLE = 'IsSubBundle'

VALUE_ID_LIST = 'ValueIdList'

ID = 'Id'

SYSTEM_ID = 'SystemId'

TAB_VIEWS = 'TabViews'

MENU_ITEMS = 'MenuItems'

SUB_MENU_ENTRIES = 'SubMenuEntries'

GATEWAY_ID = "GatewayId"

LAST_ACCESS = "LastAccess"

TIMESTAMP = "Timestamp"

ERROR_CODE = "ErrorCode"

ERROR_TYPE = "ErrorType"

ERROR_READ_PARAMETER = 'internal msg: ReadParameterValues error'

ERROR_MESSAGE = 'Message'

NAME = 'Name'

PERCENTAGE = ParameterUnit.PERCENTAGE

HOUR = ParameterUnit.HOUR

KILOWATT = ParameterUnit.KILOWATT

KILOWATTHOURS = ParameterUnit.KILOWATTHOURS

BAR = ParameterUnit.BAR

RPM = ParameterUnit.RPM

FLOW = ParameterUnit.FLOW

FREQUENCY = ParameterUnit.FREQUENCY

CELSIUS_TEMPERATURE = ParameterUnit.CELSIUS

VALUES = 'Values'

STATE = 'State'

LIST_ITEMS = 'ListItems'

DISPLAY_TEXT = 'DisplayText'

VALUE = 'Value'

PARAMETER_ID = 'ParameterId'

PARAMETER_DESCRIPTORS = 'ParameterDescriptors'

UNIT = 'Unit'

TAB_NAME = 'TabName'

VALUE_ID = 'ValueId'

GROUP = 'Group'

SYSTEM_LIST = 'SystemList'

GATEWAY_STATE = 'GatewayState'

IS_ONLINE = 'IsOnline'

WRITE_PARAMETER_VALUES = 'WriteParameterValues'
