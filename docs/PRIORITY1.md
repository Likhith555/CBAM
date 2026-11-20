# Priority 1 features (developer handoff, 20 Nov 2026)

All arithmetic is deterministic Python with Pydantic v2 models. Every validation result, gap, fallback,
assumption and status change writes a row to `audit_log` (who, when, old value, new value, reason).
Original uploaded values are never overwritten: overrides and canonical-unit conversions are stored beside them.

## Modules

| Task | Module | What it does |
|---|---|---|
| 5 Register | `app/modules/registry/factors.py`, tables `factor_set`, `emission_factor` | Versioned, cited factors. Rows cannot be edited or deleted (SQLAlchemy guard, API returns 409); a change is a new factor set and the old row becomes `superseded`. A key resolves to the newest row at or before the pinned version, so a passport on `2026.1` is reproducible after `2026.2` is added. Seed `2026.1`: IPCC 2006 fuel NCV/EF/OF (furnace oil, diesel, natural gas, LPG) and CEA v19 grid factor 0.716 tCO2/MWh. |
| 6 Engine | `app/modules/calculation/engine.py` (`compute_emissions`) | Direct = Σ(Q × NCV × EF × OF) + Process; α applied to the combined shared fuel + process total; per-fuel / process / precursor breakdown; rejects OF outside 0–1, negative quantities and Q/NCV unit mismatches. Same evaluation function drives the Monte Carlo. |
| 1 Validation | `app/modules/validation/rules.py` (`validate_workspace`) | V1 unit consistency (canonical MWh, L, t, m³), V2 billing-period overlap (inclusive end dates), coverage gaps and window checks, V3 exact duplicates (HTTP 409 at upload) and near duplicates (±0.5 %). Findings can be resolved or waived (reason + name). |
| 2 Gaps | `app/modules/gaps/register.py` (`compute_gaps`) | Required-input list for the fastener pilot; workspace is `Incomplete` when a gap without fallback is open; the engine raises `IncompleteInputError` (HTTP 422 with the gap list). |
| 3 Fallbacks | `app/modules/gaps/fallbacks.py`, `app/modules/uncertainty/monte_carlo.py` | Explicit “Apply fallback” with a reason creates an `Assumption`; `allowed = false` keeps the gap open. Fallback inputs use the register’s distribution in a seeded `numpy.random.default_rng` Monte Carlo; data-quality label A / B / C by fallback share of emissions. |
| 4 Review | `app/modules/review/status.py`, tables `passport_version`, `status_history` | Draft → User-Confirmed → Expert-Reviewed → Externally Verified, no skipping (409). Each version stores an input hash; any change creates a new Draft version. PDF shows the status badge on every page, assumptions, emission factors used and the status history. |

## API (prefix `/api/v1`)

- `PUT /workspaces/{id}`, `PUT /workspaces/{id}/inputs`, `GET /workspaces/{id}`
- `POST /workspaces/{id}/validate`, `GET /workspaces/{id}/findings`, `POST /findings/{id}/waive`, `POST /findings/{id}/resolve`
- `GET /workspaces/{id}/gaps`, `POST /workspaces/{id}/gaps/{input_key}/apply-fallback`, `GET /workspaces/{id}/assumptions`, `POST /workspaces/{id}/assumptions/{aid}/withdraw`
- `POST /workspaces/{id}/calculate`, `GET /workspaces/{id}/result`, `GET /workspaces/{id}/audit`, `POST /workspaces/{id}/factor-set`
- `GET /passports/{id}`, `POST /passports/{id}/transition`, `GET /passports/{id}/history`, `GET /passports/{id}/versions`, `POST /passports/{id}/versions/{n}/rerun`, `GET /passports/{id}/pdf`
- `GET /registry/factor-sets`, `GET /registry/factors?version=`, `POST /registry/factor-sets`
- Documents: `GET /documents/{hash}`, `PATCH /documents/{hash}/metadata`, `POST /documents/{hash}/exclude|include`, `POST /documents/fields/{id}/approve`

The workspace id is the installation id the React app already uses for document uploads.
The existing `/calculations/calculate` endpoint and Streamlit UI are unchanged.

## Limitations and open questions (to settle with the mentor)

- Roles are a prototype “acting as” selector with a typed name; real authentication is future work.
- “Externally Verified” is recorded by the user from a document they hold; the tool does not check it.
- Fallback uncertainty percentages (±5 % NCV, ±3 % EF, OF triangular 0.98–1.0, ±10 % grid) and the
  20 % data-quality threshold are placeholders kept in the register / `app/config.py`.
- Furnace-oil NCV 0.0402 GJ/L implies a density of 0.995 kg/L (typical is 0.94–0.98); flagged in the register notes.
- No precursor default SEE is seeded: wire-rod SEE is supplier data. Precursor gaps therefore block until a
  supplier value is entered or an approved default is added to the register.
- Electricity is treated as always required (open question 1: does Scope 2 count for CN 7318?).
- Whether forging / heat treatment need a process line is undecided; the user must declare sources or “none” with a reason.
- Waiving a data gap is not exposed, because every required input is needed for the arithmetic.
