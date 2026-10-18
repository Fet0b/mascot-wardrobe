# ADR 0004: Weather and time semantics

Status: accepted

## Context

A wrong or stale temperature silently picks the wrong outfit. Open-Meteo returns a naive
timestamp, so time handling must be explicit.

## Decision

- Weather is opt-in (`--live-weather`) with explicit latitude and longitude; there is no
  geolocation. The country profile is passed separately.
- The request is `current=temperature_2m`, `temperature_unit=celsius`, `timezone=GMT`. The
  response unit must be `°C`.
- A naive `current.time` is interpreted with the response `utc_offset_seconds` (a missing
  offset is a malformed reading); every comparison happens in UTC.
- A reading older than 3 hours or more than 15 minutes in the future is rejected.
- Network, HTTP, timeout, incomplete read, JSON, size and shape failures become `WeatherError`
  with a chained cause; the CLI exits with status 2. No fallback temperature is ever invented.
- `fetch_weather(..., opener=None)` takes an injectable transport and resolves the default
  `urlopen` at call time, so tests run with a deterministic fake and never use the network.

## Consequences

- Failures are loud and bounded. Scheduled runs that cannot get weather leave the previous
  export alone.
- The project depends on the public Open-Meteo API only for this optional feature.
