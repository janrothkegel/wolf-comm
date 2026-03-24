# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

**Install dependencies:**
```bash
pip install -e .
pip install httpx lxml pkce shortuuid aiohttp
```

**Build and publish:**
```bash
python -m build
# Publishing is automated via GitHub Actions on release creation
```

**Version management:** Update version string in `setup.py`.

There are no automated tests or linting configurations in this project.

## Architecture

Python library for communicating with Wolf SmartSet Cloud API (heating systems). Layered architecture:

- **`wolf_client.py`** — `WolfClient`: top-level entry point. Handles auth, session, all API calls.
- **`models.py`** — Parameter type hierarchy and data classes (`Device`, `Value`, `ListItem`).
- **`token_auth.py`** — OAuth2 PKCE authentication against Wolf's identity server.
- **`create_session.py`** — Session lifecycle: create and refresh (every 60 seconds).
- **`constants.py`** — All API endpoint URLs and field name constants. Always use these instead of string literals.

### Parameter Type System

`Parameter` (abstract) → `UnitParameter` → unit-specific subclasses (`Temperature`, `Pressure`, `PercentageParameter`, `RPMParameter`, `FlowParameter`, `FrequencyParameter`, `PowerParameter`, `EnergyParameter`, `HoursParameter`) plus `ListItemParameter` (enum/dropdown) and `SimpleParameter` (untyped).

To add a new parameter type: add class in `models.py`, add unit constant to `constants.py`, update `_map_parameter()` in `wolf_client.py`.

### Bundle-Based Fetching

Parameters have a `bundle_id`. `fetch_value()` groups parameters by `bundle_id` before calling the API to minimize requests. Never fetch parameters individually.

### Expert Mode vs Standard Mode

- **Standard**: traverses TabViews structure for organized groups
- **Expert**: uses `_extract_parameter_descriptors()` to recursively extract all `ParameterDescriptors`

### Key Behaviors

- **Deduplication**: `fetch_parameters()` deduplicates by `value_id` and `name` — critical for Wolf API returning duplicates (especially FGB-28 systems).
- **Localization**: Parameter names are fetched from Wolf's CDN as JS files. Falls back to English if the region translation fails.
- **Client injection**: `WolfClient` accepts either `client=httpx.AsyncClient()` directly or `client_lambda=lambda: get_client()` for dynamic client creation.

### Exception Hierarchy

`FetchFailed`, `ParameterReadError`, `ParameterWriteError`, `WriteFailed` — catch these rather than generic HTTP errors.

## Reference Data

`parameters-examples/` contains real Wolf API responses useful for understanding parameter structure:
- `gasparameters.json` — gas heating systems
- `heatpumpparameter.json` — heat pump systems
- `luftung.json` — ventilation systems
