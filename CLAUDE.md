# CLAUDE.md — `wolf_comm`

Guidance for working in this repository. `wolf_comm` is the Python client library that the
Home Assistant **wolflink** integration uses to talk to the **Wolf SmartSet Cloud**
(`https://www.wolf-smartset.com`). It authenticates a SmartSet user, discovers their heating
systems, reads the parameter/GUI description for a gateway, fetches current values, and writes
parameter changes back.

- **Package name / import:** `wolf_comm`
- **Version:** `0.0.51` (see [setup.py](setup.py))
- **Upstream:** https://github.com/janrothkegel/wolf-comm
- **License:** Apache 2.0
- **Python:** async-first; **requires 3.14+** (`python_requires=">=3.14"`; older versions unsupported)

---

## 1. Repository layout

```
wolf-comm/
├── wolf_comm/                  # the package
│   ├── __init__.py             # re-exports models.*, wolf_client.*, and the token_auth exceptions
│   ├── constants.py            # ALL API field-name strings + unit strings (no magic strings elsewhere)
│   ├── helpers.py              # bearer_header(token)
│   ├── token_auth.py           # OAuth2/OpenID PKCE login → Tokens; InvalidAuth / PortalUnavailable / PasswordToLong
│   ├── create_session.py       # create_session() / update_session() portal session lifecycle
│   ├── models.py               # Device, Parameter hierarchy, ListItem, Value
│   ├── wolf_client.py          # WolfClient — the main entry point + exception hierarchy
│   └── py.typed                # PEP 561 marker (library ships type hints)
├── parameters-examples/        # real GetGuiDescriptionForGateway responses (see §6)
│   ├── gasparameters.json          # gas boiler
│   ├── gashybridparameters.json    # gas hybrid
│   ├── heatpumpparameter.json      # heat pump
│   └── luftung.json                # ventilation
├── tests/                      # pytest suite (run: venv/bin/python -m pytest)
├── setup.py                    # packaging + deps
├── requirements.txt            # pinned dev deps
├── build/ , dist/ , *.egg-info # build artifacts (ignore)
└── venv/                       # local virtualenv (ignore)
```

The pytest suite lives in `tests/` (run `venv/bin/python -m pytest`; config in `pytest.ini`,
deps in `requirements_test.txt`). The `parameters-examples/` JSON files double as its fixtures —
treat them as the authoritative record of what the cloud API returns.

### Dependencies
`httpx` (ALL async HTTP, including the localization JS fetch — do not add `aiohttp` back: creating
a session/SSL context inside the event loop is a blocking operation Home Assistant flags, and a
test guards against the import), `lxml` (parsing the login HTML form), `pkce` (PKCE pair),
`shortuuid` (OAuth `state`). `requirements.txt` uses minimum bounds (`httpx>=0.26.0`,
`lxml>=6.0.0`, `pkce>=1.0.3`, `shortuuid>=1.0.11`) — the lxml floor is what guarantees Python 3.14
wheels. `setup.py install_requires` mirrors these.

---

## 2. The big picture / typical call flow

```python
from wolf_comm import WolfClient

client = WolfClient("username", "password")        # optional: expert_p=, region=, client=, client_lambda=
devices = await client.fetch_system_list()          # list[Device]  (auth + session created lazily here)
online  = await client.fetch_system_state_list(device.id, device.gateway)   # bool
params  = await client.fetch_parameters(device.gateway, device.id)          # list[Parameter]
values  = await client.fetch_value(device.gateway, device.id, params)       # list[Value]
await client.write_value(device.gateway, device.id, bundle_id, {...})       # change a parameter
await client.close_system()
```

Conceptual data flow:

```
TokenAuth.token() ──► Tokens (access_token, expire_date)
        │
        ▼
create_session() ──► BrowserSessionId  (refreshed every 60s by update_session())
        │
        ▼
GetSystemList ──► Device(id, gateway, name)
        │
        ▼
GetGuiDescriptionForGateway ──► huge nested MenuItems/TabViews/ParameterDescriptors tree
        │   (_map_view / _map_parameter / _extract_parameter_descriptors → typed Parameter objects)
        ▼
GetParameterValues  (batched per bundle_id) ──► Value(value_id, value, state)
WriteParameterValues ──► change a value
```

---

## 3. Authentication & session ([token_auth.py](wolf_comm/token_auth.py), [create_session.py](wolf_comm/create_session.py))

**`TokenAuth.token(client)`** implements the OpenID Connect **authorization-code + PKCE** flow
against the IdentityServer at `/idsrv`:

1. `pkce.generate_pkce_pair()` → `code_verifier`, `code_challenge`; `shortuuid.uuid()` → `state`.
2. `GET /idsrv/Account/Login` with the OAuth params; parse the returned HTML with `lxml` and pull
   the `__RequestVerificationToken` via the name-targeted XPath
   `//input[@name="__RequestVerificationToken"]/@value` (`_extract_verification_token`; the live
   page has two forms each carrying the same per-response token, so the first match is taken).
3. `POST /idsrv/Account/Login` with `Input.Username`, `Input.Password`, the verification token,
   `follow_redirects=True`; the authorization `code` is read out of the final redirect URL
   (`r.url.params['code']`).
4. `POST /idsrv/connect/token` with `grant_type=authorization_code` + `code_verifier` →
   `{access_token, expires_in}`.

- Client id is `smartset.web` (`AUTHENTICATION_CLIENT`); scope `openid profile api role`;
  `redirect_uri = https://www.wolf-smartset.com/signin-callback.html`; `lang=en-GB`.
- **No refresh token** is used. When the token expires the whole flow is re-run.
- **`Tokens`** stores `access_token` and a computed `expire_date`; `is_expired()` compares to now.
- **`TokenAuth.__init__` rejects passwords longer than 30 chars** → `PasswordToLong`. The Wolf
  servers silently reject longer passwords, hence the client-side guard.
- Any failure inside `token()` is caught broadly and re-raised as **`InvalidAuth`** (chained via
  `raise … from e`, so the root cause stays inspectable through `__cause__`; an `InvalidAuth`
  raised inside the flow propagates unwrapped).
- **A login page without a usable verification token raises `PortalUnavailable`** — covering a
  missing field, an empty `value=""`, and an empty/unparseable body (`_extract_verification_token`
  returns `None` for all three; importable from the package root: `from wolf_comm import
  PortalUnavailable`). It is a subclass of `InvalidAuth` and means the portal is
  rate-limiting/in maintenance — credentials were never submitted.
  Consumers should catch it *before* `InvalidAuth` and retry/back off instead of
  prompting for re-authentication; handlers that only catch `InvalidAuth` keep working.
  A genuine credential failure takes a different path (token endpoint returns `"error"` in
  its JSON) and raises plain `InvalidAuth`.

**Session lifecycle:**
- `create_session(client, token)` → `POST /api/portal/CreateSession2` with a `Timestamp`
  (`"%Y-%m-%d %H:%M:%S"`); returns `BrowserSessionId` — an **int** on the wire (verified against
  the openHAB wolfsmartset binding's `CreateSession2DTO`).
- `update_session(client, token, session_id)` → `POST /api/portal/UpdateSession` to keep it alive.

---

## 4. `WolfClient` ([wolf_client.py](wolf_comm/wolf_client.py)) — the core

### Construction
```python
WolfClient(username, password, expert_p=None, region=None, client=None, client_lambda=None)
```
- `expert_p` → `self.expert_mode` (default `False`). See §5 for what it changes.
- `region` → `self.region_set` (default `"en"`), used for localization.
- Provide **either** `client` (a reused `httpx.AsyncClient`) **or** `client_lambda` (a factory
  returning one) — not both. If neither, a default `httpx.AsyncClient` is created **lazily on
  first request, in an executor** (constructing it builds the SSL context — blocking I/O that
  must not run in the event loop; HA's flagged-operations list). Direct synchronous access to
  the `client` property before any request still creates one as a fallback. The `client`
  property resolves the active client and raises `RuntimeError` if misconfigured.

### Request plumbing (private)
- **`__request(method, path, **kwargs)`** is the choke point for every portal call:
  - lazily calls `__authorize_and_session()` when `tokens` is `None`/expired;
  - injects the `Authorization: Bearer` header;
  - calls `update_session()` if `last_session_refesh` (note the typo in the attribute name) is due
    — refresh cadence is **60 seconds**;
  - for any request with a `json=` **dict** body, injects `SessionId` into it;
  - on **HTTP 401 or 500**, re-authorizes and **retries exactly once**; a second failure sets
    `last_failed = True` and raises `FetchFailed`.
- **`__execute(...)`** issues the actual `client.request(method, f"{BASE_URL_PORTAL}/{path}", ...)`.
  Note `BASE_URL_PORTAL = https://www.wolf-smartset.com/portal`, so `path` is e.g. `api/portal/...`.

### Public API
| Method | HTTP call | Returns |
|---|---|---|
| `fetch_system_list()` | `GET api/portal/GetSystemList` | `list[Device]` |
| `fetch_system_state_list(system_id, gateway_id)` | `POST api/portal/GetSystemStateList` | `bool` (`[0].GatewayState.IsOnline`) |
| `fetch_parameters(gateway_id, system_id)` | `GET api/portal/GetGuiDescriptionForGateway` | `list[Parameter]` |
| `fetch_value(gateway_id, system_id, parameters)` | `POST api/portal/GetParameterValues` (batched per bundle) | `list[Value]` |
| `write_value(gateway_id, system_id, bundle_id, value)` | `POST api/portal/WriteParameterValues` | `dict` |
| `close_system()` | `POST api/portal/CloseSystem` | `None` |

**`fetch_value` batching:** parameters are grouped by `bundle_id` into `bundles`; one request is
made per bundle with `ValueIdList`, `GatewayId`, `SystemId`, `GuiIdChanged=False`, and a
per-bundle `LastAccess` cursor (`last_access_map`) that is updated from each response. Only
response entries that actually contain a `Value` key become `Value` objects. Entries belonging
to an `EnergyWhParameter` (`Wh;kWh` / `Wh;kWh;MWh` units) arrive as raw **Wh** and are converted
to kWh before the `Value` is built — via the `Parameter.convert_raw_value(raw)` hook (identity by
default; `EnergyWhParameter` overrides it with `round(wh / 1000, 3)`, passing non-numeric
placeholder readings through untouched).

**Error handling:** both `fetch_value` and `write_value` inspect the response for `ErrorCode` /
`ErrorType`. If `Message == 'internal msg: ReadParameterValues error'` they raise the parameter-
specific error (`ParameterReadError` / `ParameterWriteError`); otherwise `FetchFailed` /
`WriteFailed`.

**`write_value`'s `value` argument is a dict**, not a `models.Value` object — it reads
`value[ValueId]` and `value[State]` and posts
`{"WriteParameterValues": [{"ValueId":…, "Value": <state>}], …}`.

### Exception hierarchy (defined at the bottom of `wolf_client.py`)
```
Exception
└── WolfError(message, response=None)         # .response holds the raw API dict
    ├── FetchFailed                            # "Failed to fetch data: …"
    ├── WriteFailed                            # "Failed to write data: …"
    └── ParameterError
        ├── ParameterReadError                 # "Failed to read parameters: …"
        └── ParameterWriteError                # "Failed to write parameters: …"
```
Plus from `token_auth.py`: `InvalidAuth` (with subclass `PortalUnavailable` — login page served
without the verification token, i.e. rate limit/maintenance; catch before `InvalidAuth` to avoid
a misleading re-auth prompt) and `PasswordToLong`.

### Localization
`fetch_parameters` first calls `load_localized_json(region_set)`:
- `fetch_localized_text(culture)` GETs
  `https://www.wolf-smartset.com/js/localized-text/text.culture.{culture}.js` **via the shared
  httpx client** (`self.client` — in HA the injected, safely-constructed one),
  falling back to `en` if the culture 404s.
- `extract_messages_json(text)` regex-extracts the `messages: {…}` object out of that JS file;
  `load_localized_json` runs it **in an executor** (CPU-bound work over a large payload — keep it
  off the event loop).
- `try_and_parse(text, 1000)` is a **resilient JSON parser**: on `JSONDecodeError` it deletes the
  offending line and retries (up to 1000 times) — a workaround for malformed entries in Wolf's JS.
- The result is stored in `self.regional`; `replace_with_localized_text(text)` looks names up
  there (returning the original string on a miss).

---

## 5. Parameter model & mapping ([models.py](wolf_comm/models.py))

### `Device`
`Device(id, gateway, name)` — one heating system. `id` ⇒ `SystemId`, `gateway` ⇒ `GatewayId`.

### `Parameter` (ABC) and subclasses
Every parameter exposes: `value_id` (get/set), `name` (get/set), `parameter_id`, `bundle_id`,
`read_only`, `parent`. `__str__` →
`"<Class> -> <name>[parameter_id][bundle_id][read_only][value_id] of <parent>"`.

Concrete types and their `unit`:

| Class | `unit` property | Selected when API `Unit` == | constant |
|---|---|---|---|
| `Temperature` | `°C` | `°C` | `CELSIUS_TEMPERATURE` |
| `Pressure` | `bar` | `bar` | `BAR` |
| `PercentageParameter` | `%` | `%` | `PERCENTAGE` |
| `HoursParameter` | **`H`** | `Std` | `HOUR` (note: API sends `Std`, model reports `H`) |
| `PowerParameter` | `kW` | `kW` | `KILOWATT` |
| `EnergyParameter` | `kWh` | `kWh` | `KILOWATTHOURS` |
| `EnergyWhParameter` | `kWh` (raw value is **Wh**; `fetch_value` converts to kWh) | `Wh;kWh` or `Wh;kWh;MWh` | `WATTHOURS_KILOWATTHOURS` / `WATTHOURS_KILOWATTHOURS_MEGAWATTHOURS` |
| `RPMParameter` | `U/min` | `U/min` | `RPM` |
| `FlowParameter` | `l/min` | `l/min` | `FLOW` |
| `FrequencyParameter` | `Hz` | `Hz` | `FREQUENCY` |
| `SimpleParameter` | *(none)* | no `Unit` and no `ListItems` | — |
| `ListItemParameter` | *(none, has `items`)* | has `ListItems` | — |

`UnitParameter` is an abstract intermediate base (adds the abstract `unit`); the unit classes
subclass it. `EnergyWhParameter` subclasses `EnergyParameter` (so isinstance checks for
`EnergyParameter` pick it up) — the API delivers its raw values in Wh and `fetch_value` divides
by 1000 before building the `Value`. `ListItemParameter` and `SimpleParameter` extend
`Parameter` directly.

- **`ListItem(value, name)`** — note **arg order is `(value, name)`** and `value` is cast to `int`.
  Built from `ListItems[].Value` / `ListItems[].DisplayText`. `ListItemParameter.items` is the list.
- **`Value(value_id, value, state)`** — the current reading: `value` is a string, `state` an int.

### `_map_parameter(parameter, parent)` — the dispatcher
Reads `ValueId`, `Name`, `ParameterId`, defaults `BundleId→1000` (int, matching the API type) and
`IsReadOnly→True`, then:
1. **If `Unit` key present** → match against the table above.
2. **elif `ListItems` present** → `ListItemParameter`.
3. **else** → `SimpleParameter`.

> ⚠️ **Deliberate design:** the `Unit` branch only handles the units above. If a descriptor
> has a `Unit` the library doesn't recognize (the examples contain `Uhr`, `min`, `K`, `K/K`,
> `K/(K*h)`, `s`/`sec`, `m³/h`, `ppm`, `Pa`, `V`, `Cent/kWh`, `pls/kWh`,
> …), **none of the branches return and `_map_parameter` returns `None`** — and it does **not**
> fall through to the `ListItems`/`Simple` branches. These `None`s are filtered out downstream
> (`fetch_parameters` and `fix_duplicated_parameters` skip `None`). Net effect: parameters with
> unrecognized units are dropped. **This is intentional** — unknown units must not surface as
> untyped sensors; supporting a unit is an explicit opt-in (see §8 "Adding a new unit type").
> Keep this in mind when a value seems "missing."

### `_map_view(view)` — and the SVG schema unit trick
For a TabView, maps each `ParameterDescriptors` entry. **Special case:** if the view has
`SVGHeatingSchemaConfigDevices`, it builds a `valueId → unit` map from
`SVGHeatingSchemaConfigDevices[0].parameters[*]` and **injects that `Unit`** into matching
descriptors before mapping. This is how parameters that lack a top-level `Unit` (e.g. a boiler
sensor `KF Kesselfühler`) still become typed (`Temperature`, `Pressure`, …).

### `_extract_parameter_descriptors(desc)` — expert mode
Recursively walks the whole response, yielding every `ParameterDescriptors` array it finds, and
**stamps the nearest enclosing `BundleId` onto each descriptor** (needed later for value batching).

### `fetch_parameters` standard vs. expert mode
- **Standard (`expert_mode=False`):** uses `desc["MenuItems"][0]["TabViews"]` only — i.e. the first
  menu, **"Benutzer" (user)**, mapped via `_map_view`. The "Fachmann" (expert) menu is **not**
  traversed in this mode.
- **Expert (`expert_mode=True`):** uses `_extract_parameter_descriptors` over the entire tree
  (so it picks up Fachmann/sub-menu/sub-bundle parameters too), sorted by `ValueId`.
- After mapping, names are localized: a name containing the `SPLIT` token **`"---"`** is split and
  each half localized; otherwise the whole name is localized. Then duplicates are removed twice —
  once inline by `value_id`, then again via `fix_duplicated_parameters`.

---

## 6. The `parameters-examples/` fixtures — what the cloud returns

These are captured **`GetGuiDescriptionForGateway`** responses for four appliance classes. Use
them to understand the response shape and to test mapping changes offline.

### File formats
All four files are **valid JSON** (`json.load` works directly). Historical note: 
`heatpumpparameter.json` and `luftung.json` were originally Python-`repr` dumps (single quotes,
`True`/`False`, raw newlines inside strings) and were converted to JSON in June 2026 with the
descriptor counts verified unchanged. Embedded newlines in string values (German marketing copy in
`FunctionOnMessage`/`FunctionOffMessage`) are now proper `\n` escapes.

### Top-level shape (all four)
```jsonc
{
  "MenuItems": [ /* see below */ ],
  "DynFaultMessageDevices": [],          // dynamic fault messages (empty in all examples)
  "SystemHasWRSClassicDevices": true
}
```

### `MenuItems` structure
Two top-level menus in every example: **`Benutzer`** (user) and **`Fachmann`** (expert).
```jsonc
{
  "Name": "Benutzer",
  "ParameterNode": true,
  "ImageName": "MenuIcon_Home.png",
  "SubMenuEntries": [ /* nested menus, each with its own TabViews */ ],
  "TabViews": [ /* see below */ ]
}
```
- `Benutzer` carries its parameters directly in `TabViews`; `SubMenuEntries` is empty.
- `Fachmann` has empty `TabViews` and nests everything under `SubMenuEntries` (e.g. `Heizgerät`,
  `Bedienmodul BM-2`, `Solarmodul SM1`, `ISM`, `Mischermodul MM1`, …), each entry having its own
  `TabViews`. This is why **standard mode only sees `MenuItems[0]` (Benutzer)** and expert mode
  must recurse.

### `TabView` structure
```jsonc
{
  "IsExpertView": false,
  "TabName": "Übersicht",          // becomes the Parameter.parent
  "GuiId": 9500,
  "BundleId": 1000,                // grouping key for value fetches
  "ViewType": 7,
  "SvgSchemaDeviceId": 0,
  "GetValueLastAccess": "2025-02-26T21:32:03.6683521Z",
  "TabViewGroups": [],
  "ParameterDescriptors": [ /* the actual parameters */ ],
  "SVGHeatingSchemaConfigDevices": [ /* optional; supplies units, see §5 */ ]
}
```

### `ParameterDescriptor` — three flavours

**Simple (no unit, no list)** → `SimpleParameter`:
```jsonc
{
  "ValueId": 18000500001, "ParameterId": 18000500001, "SortId": 0, "SubBundleId": 0,
  "IsReadOnly": false, "NoDataPoint": false, "IsExpertProtectable": false,
  "Name": "KF Kesselfühler", "ControlType": 0, "Value": "33.9",
  "ValueState": 0, "HasDependentParameter": false
}
```

**Unit parameter** → `Temperature`/`Pressure`/… :
```jsonc
{
  "ValueId": 18015400001, "ParameterId": 18015400001, "IsReadOnly": true,
  "Name": "Kesselsolltemperatur", "Group": "210_Wärmeerzeuger 1", "ProtGrp": "HG <1>",
  "ControlType": 6, "Value": "31.7", "ValueState": 1,
  "Unit": "°C", "Decimals": 1
}
```

**List/enum parameter** → `ListItemParameter`:
```jsonc
{
  "ValueId": 18014100001, "ParameterId": 18014100001, "IsReadOnly": true,
  "Name": "Typ", "Group": "210_Wärmeerzeuger 1", "ControlType": 1, "Value": "33",
  "ListItems": [
    { "Value": "33", "DisplayText": "CGB-2", "IsSelectable": true, "HighlightIfSelected": false }
  ],
  "MinValueCondition": "33", "MaxValueCondition": "33", "MinValue": 33, "MaxValue": 33
}
```

**`SVGHeatingSchemaConfigDevices[]`** (per-device schema; source of injected units):
```jsonc
{
  "dt": "HG", "di": 1, "deviceTemplateName": "CGB_2", "configIndex": 2,
  "parameters": [
    { "valueId": 18000500001, "unit": "°C", "parameterName": "KF Kesselfühler", "parameterId": 18000500001 },
    { "valueId": 18002500001, "parameterName": "ZHP Heizkreispumpe", "parameterId": 18002500001 },   // no unit
    { "valueId": 18005100001, "unit": "bar", "parameterName": "DHK Druck Heizkreis", "parameterId": 18005100001 }
  ]
}
```

### Per-fixture facts (counts are descriptors / units actually present)
| File | Total `ValueId`s* | Notable units present | Notes |
|---|---|---|---|
| `gasparameters.json` | 442 descriptors | `°C`(133), `bar`(18), `%`(14), `Std`(6), `Uhr`(43), `min`, `K`, `K/K`, `K/(K*h)`, `s`, `l/min` | gas boiler; 94 list params |
| `gashybridparameters.json` | 520 descriptors | adds `kW`, `l/min`, Wh-based energy units `Wh;kWh`(2), `Wh;kWh;MWh`(36) → `EnergyWhParameter`; plus `EnergyCockpitParameterType` | gas hybrid; 101 list params |
| `heatpumpparameter.json` | ~529 ValueIds | `°C`(112), `kWh`(28), `kW`, `Hz`(7), `U/min`, `l/min`, `bar`, `pls/kWh`, `Cent/kWh`, `min`, `sec` | heat pump; tabs incl. Mischermodul MM1 |
| `luftung.json` | ~139 ValueIds | `m³/h`(9), `°C`(15), `ppm`(2), `Pa`(2), `V`(4), `Uhr`, `K` | ventilation; many units the mapper drops (see gotcha) |

\* Counts marked `~` are total `'ValueId'` key occurrences anywhere in the file (slightly higher
than descriptor counts since nested structures also carry ValueIds). Note that
`_extract_parameter_descriptors` collects fewer than these totals — it only visits arrays keyed
exactly `ParameterDescriptors` (e.g. 226 of the 442 in the gas fixture); `ChildParameterDescriptors`
and `SchemaTabViewConfigDTO>Configs>Parameters` (where the heat pump's `Hz` params live) are skipped.

> Practical takeaway: the gas files exercise the common path; **`luftung.json` is the best stress
> test** for unrecognized units (`m³/h`, `ppm`, `Pa`, `V`) that currently map to `None`.

---

## 7. Constants worth knowing ([constants.py](wolf_comm/constants.py))

All API field names live here as constants — **always reuse them instead of hardcoding strings**.
Highlights: `SESSION_ID='SessionId'`, `BUNDLE_ID='BundleId'`, `BUNDLE='IsSubBundle'`,
`VALUE_ID_LIST='ValueIdList'`, `GUI_ID_CHANGED='GuiIdChanged'`, `LAST_ACCESS='LastAccess'`,
`MENU_ITEMS='MenuItems'`, `TAB_VIEWS='TabViews'`, `PARAMETER_DESCRIPTORS='ParameterDescriptors'`,
`VALUE_ID='ValueId'`, `PARAMETER_ID='ParameterId'`, `VALUE='Value'`, `VALUES='Values'`,
`STATE='State'`, `LIST_ITEMS='ListItems'`, `DISPLAY_TEXT='DisplayText'`, `UNIT='Unit'`,
`ISREADONLY='IsReadOnly'`, `GATEWAY_STATE='GatewayState'`, `IS_ONLINE='IsOnline'`,
`ERROR_READ_PARAMETER='internal msg: ReadParameterValues error'`. Base URLs: `BASE_URL`,
`BASE_URL_PORTAL = BASE_URL + "/portal"`, `AUTHENTICATION_BASE_URL = BASE_URL + "/idsrv"`.

---

## 8. Conventions & gotchas for contributors

- **Async everywhere.** All network methods are `async`; call them with `await`.
- **No magic strings** — add new API field names to `constants.py` and import them.
- **Adding a new unit type** requires three coordinated edits: a constant in `constants.py`, a new
  `UnitParameter` subclass in `models.py`, and a new `elif` branch in `_map_parameter`
  (`wolf_client.py`). Missing the `_map_parameter` branch = the parameter silently becomes `None`.
  If the raw API value uses a different scale than the reported unit, override
  `convert_raw_value(raw)` on the new model class (see `EnergyWhParameter`) — `fetch_value` calls
  it on every reading; never put conversions in `fetch_value` itself.
- **`HoursParameter.unit` returns `"H"`** even though the API/constant uses `"Std"`. Don't "fix"
  one without checking the integration's expectations.
- **Attribute typo `last_session_refesh`** is load-bearing (referenced in `__request`); don't
  rename casually.
- **Reusing an `httpx.AsyncClient`**: pass `client=` (or `client_lambda=`) so cookies/connections
  persist; Home Assistant supplies its own shared client this way.
- **Sessions expire**; the client auto-refreshes every 60s and re-auths on 401/500 with one retry.
- The login flow requests the portal with `lang=en-GB`; parameter `Name`s come back in German and
  are localized via the `text.culture.<region>.js` files, falling back to English.
- **Run the tests** (`venv/bin/python -m pytest`) after changes — the suite in `tests/` pins
  current behavior (including the quirks above) and exercises the real fixtures in
  `parameters-examples/`.

---

## 9. Build / release

- Packaging: [setup.py](setup.py) (`version='0.0.51'`, ships `py.typed`).
- CI publish workflow: `.github/workflows/python-publish.yml`.
- Releases are cut by bumping the version in `setup.py` (recent commits follow
  `Bump version from X to Y`).
