import datetime
import json
import logging
import re
from typing import Union, Optional

import aiohttp
import httpx
from httpx import Headers

from wolf_comm.constants import BASE_URL_PORTAL, ID, GATEWAY_ID, NAME, SYSTEM_ID, MENU_ITEMS, TAB_VIEWS, BUNDLE_ID, \
    BUNDLE, VALUE_ID_LIST, GUI_ID_CHANGED, SESSION_ID, VALUE_ID, GROUP, VALUE, STATE, VALUES, PARAMETER_ID, UNIT, \
    CELSIUS_TEMPERATURE, BAR, RPM, FLOW, FREQUENCY, PERCENTAGE, LIST_ITEMS, DISPLAY_TEXT, PARAMETER_DESCRIPTORS, TAB_NAME, HOUR, KILOWATT, KILOWATTHOURS, \
    LAST_ACCESS, ERROR_CODE, ERROR_TYPE, ERROR_MESSAGE, ERROR_READ_PARAMETER, SYSTEM_LIST, GATEWAY_STATE, IS_ONLINE, WRITE_PARAMETER_VALUES
from wolf_comm.create_session import create_session, update_session
from wolf_comm.helpers import bearer_header
from wolf_comm.models import Temperature, Parameter, SimpleParameter, Device, Pressure, ListItemParameter, \
    PercentageParameter, Value, ListItem, HoursParameter, PowerParameter, EnergyParameter, RPMParameter, FlowParameter, FrequencyParameter
from wolf_comm.token_auth import Tokens, TokenAuth

_LOGGER = logging.getLogger(__name__)
SPLIT = '---'

class WolfClient:
    session_id: Optional[int]
    tokens: Optional[Tokens]
    last_access: Optional[datetime.datetime]
    last_failed: bool
    last_session_refesh: Optional[datetime.datetime]
    language: Optional[dict]
    l_choice: str

    @property
    def client(self):
        if hasattr(self, '_client') and self._client != None:
            return self._client
        elif hasattr(self, '_client_lambda') and self._client_lambda != None:
            return self._client_lambda()
        else:
            raise RuntimeError("No valid client configuration")

    def __init__(self, username: str, password: str, lang=None, client=None, client_lambda=None):
        if client is not None and client_lambda is not None:
            raise RuntimeError("Only one of client and client_lambda is allowed!")
        elif client is not None:
            self._client = client
        elif client_lambda is not None:
            self._client_lambda = client_lambda
        else:
            self._client = httpx.AsyncClient()

        self.tokens = None
        self.token_auth = TokenAuth(username, password)
        self.session_id = None
        self.last_access = None
        self.last_failed = False
        self.last_session_refesh = None
        self.language = None
        
        if lang is None:
            self.l_choice = 'en'
        else:
            self.l_choice = lang
    

    async def __request(self, method: str, path: str, **kwargs) -> Union[dict, list]:
        if self.tokens is None or self.tokens.is_expired():
            await self.__authorize_and_session()

        headers = kwargs.get('headers')

        if headers is None:
            headers = bearer_header(self.tokens.access_token)
        else:
            headers = {**bearer_header(self.tokens.access_token), **dict(headers)}

        if self.last_session_refesh is None or self.last_session_refesh <= datetime.datetime.now():
            await update_session(self.client, self.tokens.access_token, self.session_id)
            self.last_session_refesh = datetime.datetime.now() + datetime.timedelta(seconds=60)
            _LOGGER.debug('Sessionid: %s extented', self.session_id)

        if 'json' in kwargs and self.session_id is not None:
            if isinstance(kwargs['json'], dict):  # Check if json is a dict
                kwargs['json'][SESSION_ID] = self.session_id  # add sessionId to json-object

        resp = await self.__execute(headers, kwargs, method, path)
        if resp.status_code == 401 or resp.status_code == 500:
            _LOGGER.info('Retrying failed request (status code %d)',
                         resp.status_code)
            await self.__authorize_and_session()
            headers = {**bearer_header(self.tokens.access_token), **dict(headers)}
            try:
                execution = await self.__execute(headers, kwargs, method, path)
                return execution.json()
            except FetchFailed as e:
                self.last_failed = True
                raise e
        else:
            self.last_failed = False
            return resp.json()

    async def __execute(self, headers, kwargs, method, path):
        return await self.client.request(method, f"{BASE_URL_PORTAL}/{path}", **dict(kwargs, headers=Headers(headers)))

    async def __authorize_and_session(self):
        self.tokens = await self.token_auth.token(self.client)
        self.session_id = await create_session(self.client, self.tokens.access_token)
        self.last_session_refesh = datetime.datetime.now() + datetime.timedelta(seconds=60)

    # api/portal/GetSystemList
    async def fetch_system_list(self) -> list[Device]:
        system_list = await self.__request('get', 'api/portal/GetSystemList')
        _LOGGER.debug('Fetched systems: %s', system_list)
        return [Device(system[ID], system[GATEWAY_ID], system[NAME]) for system in system_list]

    # api/portal/GetSystemStateList
    async def fetch_system_state_list(self, system_id, gateway_id) -> bool:
        payload = {SESSION_ID: self.session_id, SYSTEM_LIST: [{SYSTEM_ID: system_id, GATEWAY_ID: gateway_id}]}
        system_state_response = await self.__request('post', 'api/portal/GetSystemStateList', json=payload)
        _LOGGER.debug('Fetched system state: %s', system_state_response)
        return system_state_response[0][GATEWAY_STATE][IS_ONLINE]

    # api/portal/GetGuiDescriptionForGateway?GatewayId={gateway_id}&SystemId={system_id}
    async def fetch_parameters_v2(self, gateway_id, system_id) -> list[Parameter]:
        payload = {GATEWAY_ID: gateway_id, SYSTEM_ID: system_id}
        answer = await self.__request('get', 'api/portal/GetGuiDescriptionForGateway', params=payload)
        _LOGGER.debug('Fetched parameters: %s', answer)

        descriptors = WolfClient._extract_parameter_descriptors(answer)
        _LOGGER.debug('Found parameter descriptors: %s', len(descriptors))

        mapped = [WolfClient._map_parameter(p, None) for p in descriptors]

        deduplicated  = self.fix_duplicated_parameters(mapped)
        _LOGGER.debug('Deduplicated descriptors: %s', len(deduplicated))

        return deduplicated

    # api/portal/GetGuiDescriptionForGateway?GatewayId={gateway_id}&SystemId={system_id}
    async def fetch_parameters(self, gateway_id, system_id) -> list[Parameter]:
        await self.load_localized_json(self.l_choice)
        payload = {GATEWAY_ID: gateway_id, SYSTEM_ID: system_id}
        desc = await self.__request('get', 'api/portal/GetGuiDescriptionForGateway', params=payload)
        _LOGGER.debug('Fetched parameters: %s', desc)
        tab_views = desc[MENU_ITEMS][0][TAB_VIEWS]

        result = [WolfClient._map_view(view) for view in tab_views]
        result.reverse()
        distinct_ids = []
        flattened = []
        for sublist in result:
            for val in sublist:
                spaceSplit = val.name.split(SPLIT, 2)
                if len(spaceSplit) == 2:
                    key = spaceSplit[0].split('_')[1] if spaceSplit[0].count('_') > 0 else spaceSplit[0]
                    name = self.replace_with_localized_text(key) + ' ' + self.replace_with_localized_text(spaceSplit[1])
                    val.name = name
                else:
                    val.name = self.replace_with_localized_text(val.name)

                if val.value_id not in distinct_ids:
                    distinct_ids.append(val.value_id)
                    flattened.append(val)
                else:
                    _LOGGER.debug('Skipping parameter with id %s and name %s', val.value_id, val.name)
        flattened_fixed = self.fix_duplicated_parameters(flattened)
        return flattened_fixed

    def fix_duplicated_parameters(self, parameters):
       """Fix duplicated parameters."""
       seen = set()
       new_parameters = []
       for parameter in parameters:
          if parameter.name not in seen:
              new_parameters.append(parameter)
              seen.add(parameter.name)
              _LOGGER.debug("Adding parameter: %s", parameter.name)
          else:
                _LOGGER.debug(
                "Duplicated parameter found: %s. Skipping this parameter",
                parameter.name,
            )
       return new_parameters
    
    
    def replace_with_localized_text(self, text: str):
        if self.language is not None and text in self.language:
            return self.language[text]
        return text

    # api/portal/CloseSystem
    async def close_system(self):
        data = {
            SESSION_ID: self.session_id
        }
        res = await self.__request('post', 'api/portal/CloseSystem', json=data)
        _LOGGER.debug('Close system response: %s', res)

    @staticmethod
    def extract_messages_json(text):
        json_match = re.search(r'messages:\s*({.*?})\s*}', text, re.DOTALL)

        if json_match:
            json_string = json_match.group(1)
            return WolfClient.try_and_parse(json_string, 1000)
        else:
            return None

    @staticmethod
    def try_and_parse(text, times):
        if times == 0:
            return text
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            line = e.lineno - 1

            text_lines = text.split('\n')

            if line < len(text_lines):
                text_lines.pop(line)

            new_text = '\n'.join(text_lines)
            return WolfClient.try_and_parse(new_text, times - 1)

    @staticmethod
    async def fetch_localized_text(language: str):
        url = f'https://www.wolf-smartset.com/js/localized-text/text.culture.{language}.js'

        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200 or response.status == 304:
                    return await response.text()
                else:
                    return ""

    async def load_localized_json(self, language_input: str):
        res = await self.fetch_localized_text(language_input)

        parsed_json = WolfClient.extract_messages_json(res)

        if parsed_json is not None:
            self.language = parsed_json
        else:
            _LOGGER.error('Failed to parse localized text for language: %s', language_input)

    # api/portal/GetParameterValues
    async def fetch_value(self, gateway_id, system_id, parameters: list[Parameter]):
        data = {
            BUNDLE_ID: 1000,
            BUNDLE: False,
            VALUE_ID_LIST: [param.value_id for param in parameters],
            GATEWAY_ID: gateway_id,
            SYSTEM_ID: system_id,
            GUI_ID_CHANGED: False,
            SESSION_ID: self.session_id,
            LAST_ACCESS: self.last_access
        }
        res = await self.__request('post', 'api/portal/GetParameterValues', json=data,
                                   headers={"Content-Type": "application/json"})

        if ERROR_CODE in res or ERROR_TYPE in res:
            if ERROR_MESSAGE in res and res[ERROR_MESSAGE] == ERROR_READ_PARAMETER:
                raise ParameterReadError(res)
            raise FetchFailed(res)

        self.last_access = res[LAST_ACCESS]
        return [Value(v[VALUE_ID], v[VALUE], v[STATE]) for v in res[VALUES] if VALUE in v]

# api/portal/WriteParameterValues
    async def write_value(self, gateway_id, system_id, Value):
        data = {
            WRITE_PARAMETER_VALUES: [{"ValueId": Value[VALUE_ID], "Value": Value[STATE]}],
            SYSTEM_ID: system_id,
            GATEWAY_ID: gateway_id,
        }

        res = await self.__request('post', 'api/portal/WriteParameterValues', json=data,
                                   headers={"Content-Type": "application/json"})

        _LOGGER.debug('Written values: %s', res)

        if ERROR_CODE in res or ERROR_TYPE in res:
            if ERROR_MESSAGE in res and res[ERROR_MESSAGE] == ERROR_READ_PARAMETER:
                raise ParameterWriteError(res)
            raise WriteFailed(res)

        if LAST_ACCESS in res:
            self.last_access = res[LAST_ACCESS]

        return res


    @staticmethod
    def _map_parameter(parameter: dict, parent: str) -> Parameter:
        group = ""
        if GROUP in parameter:
            group = parameter[GROUP] + SPLIT
            
        value_id = parameter[VALUE_ID]
        name = group + parameter[NAME]
        parameter_id = parameter[PARAMETER_ID]

        if BUNDLE_ID in parameter:
            bundle_id = parameter[BUNDLE_ID]

        if UNIT in parameter:
            unit = parameter[UNIT]
            if unit == CELSIUS_TEMPERATURE:
                return Temperature(value_id, name, parent, parameter_id, bundle_id)
            elif unit == BAR:
                return Pressure(value_id, name, parent, parameter_id, bundle_id)
            elif unit == PERCENTAGE:
                return PercentageParameter(value_id, name, parent, parameter_id, bundle_id)
            elif unit == HOUR:
                return HoursParameter(value_id, name, parent, parameter_id, bundle_id)
            elif unit == KILOWATT:
                return PowerParameter(value_id, name, parent, parameter_id, bundle_id)
            elif unit == KILOWATTHOURS:
                return EnergyParameter(value_id, name, parent, parameter_id, bundle_id)
            elif unit == RPM:
                return RPMParameter(value_id, name, parent, parameter_id, bundle_id)
            elif unit == FLOW:
                return FlowParameter(value_id, name, parent, parameter_id, bundle_id)
            elif unit == FREQUENCY:
                return FrequencyParameter(value_id, name, parent, parameter_id, bundle_id)
        elif LIST_ITEMS in parameter:
            items = [ListItem(list_item[VALUE], list_item[DISPLAY_TEXT]) for list_item in parameter[LIST_ITEMS]]
            return ListItemParameter(value_id, name, parent, items, parameter_id, bundle_id)
        return SimpleParameter(value_id, name, parent, parameter_id, bundle_id)

    @staticmethod
    def _map_view(view: dict):
        if 'SVGHeatingSchemaConfigDevices' in view:
            units = dict([(unit['valueId'], unit['unit']) for unit
                          in view['SVGHeatingSchemaConfigDevices'][0]['parameters'] if 'unit' in unit])

            new_params = []
            for param in view[PARAMETER_DESCRIPTORS]:
                if param[VALUE_ID] in units:
                    param[UNIT] = units[param[VALUE_ID]]
                new_params.append(WolfClient._map_parameter(param, view[TAB_NAME]))
            return new_params
        else:
            return [WolfClient._map_parameter(p, view[TAB_NAME]) for p in view[PARAMETER_DESCRIPTORS]]

    @staticmethod
    def _extract_parameter_descriptors(desc):
        # recursively traverses datastructure returned by GetGuiDescriptionForGateway API and extracts all ParameterDescriptors arrays
        def traverse(item, path=''):
            # Object is a dict, crawl keys
            if type(item) is dict:
                for key in item:
                    if key == "ParameterDescriptors":
                        _LOGGER.debug('Found ParameterDescriptors at path: %s', path)
                        yield from item[key]
                    yield from traverse(item[key], path + key + '>')

            # Object is a list, crawl list items
            elif type(item) is list:
                i = 0
                for a in item:
                    yield from traverse(a, path + str(i) + '>')
                    i += 1
        return list(traverse(desc))



class FetchFailed(Exception):
    """Server returned 500 code with message while executing query"""
    pass


class ParameterReadError(Exception):
    """Server returned RedParameterValues error"""
    pass

class WriteFailed(Exception):
    """Server returned 500 code with message while executing query"""
    pass

class ParameterWriteError(Exception):
    """Server returned RedParameterValues error"""
    pass
