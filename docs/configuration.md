# Configuration

## Categories and the boundary rule

There are six categories: `very_cold`, `cold`, `mild`, `ambient`, `warm`, `very_hot`
(English labels: very cold, cold, mild, ambient, warm, very hot). `mild` is the intentional
sixth category between `cold` and `ambient`.

Each country profile has five strictly increasing Celsius boundaries. **A temperature exactly on
a boundary belongs to the upper category.** For example, in BR 15 C is `mild`, not `cold`.

Temperatures are validated: `NaN`, infinity and booleans are rejected, and values outside
-90 to 65 C are rejected. Fahrenheit input (`--unit F`) is converted deterministically with
`C = (F - 32) * 5 / 9` before classification (32 F is 0 C, 77 F is 25 C), and the converted
value is checked against the same range.

## Country presets

BR, GB, US and JP are adjustable product presets. They are not official meteorological
standards, health advice or a description of every climate in a country. You always pass the
country explicitly (`--country`); nothing is inferred from location. The tables below are
checked against `src/mascot_wardrobe/infrastructure/profiles.json` by `tests/test_docs.py`.
Fahrenheit values are the Celsius boundaries converted, shown for reference.

### BR

| Category | Celsius | Fahrenheit |
| --- | --- | --- |
| very_cold | < 5 | < 41 |
| cold | 5 to < 15 | 41 to < 59 |
| mild | 15 to < 20 | 59 to < 68 |
| ambient | 20 to < 25 | 68 to < 77 |
| warm | 25 to < 32 | 77 to < 89.6 |
| very_hot | >= 32 | >= 89.6 |

### GB

| Category | Celsius | Fahrenheit |
| --- | --- | --- |
| very_cold | < -5 | < 23 |
| cold | -5 to < 5 | 23 to < 41 |
| mild | 5 to < 12 | 41 to < 53.6 |
| ambient | 12 to < 18 | 53.6 to < 64.4 |
| warm | 18 to < 25 | 64.4 to < 77 |
| very_hot | >= 25 | >= 77 |

### US

| Category | Celsius | Fahrenheit |
| --- | --- | --- |
| very_cold | < -10 | < 14 |
| cold | -10 to < 0 | 14 to < 32 |
| mild | 0 to < 12 | 32 to < 53.6 |
| ambient | 12 to < 24 | 53.6 to < 75.2 |
| warm | 24 to < 32 | 75.2 to < 89.6 |
| very_hot | >= 32 | >= 89.6 |

### JP

| Category | Celsius | Fahrenheit |
| --- | --- | --- |
| very_cold | < 0 | < 32 |
| cold | 0 to < 10 | 32 to < 50 |
| mild | 10 to < 18 | 50 to < 64.4 |
| ambient | 18 to < 25 | 64.4 to < 77 |
| warm | 25 to < 33 | 77 to < 91.4 |
| very_hot | >= 33 | >= 91.4 |

## Custom profiles file

Pass `--profiles path/to/profiles.json` to `generate`, `select`, `sandbox` and `demo`. The file
must be at most 256 KiB of JSON:

```json
{
  "schema_version": 1,
  "notice": "Free text describing your presets.",
  "profiles": {
    "XX": {
      "thresholds_c": [4, 12, 18, 24, 30],
      "palette": ["#43537D", "#437F8D", "#68AD94", "#87BDDC", "#E5A56B", "#ED8066"],
      "garments": ["coat", "jacket", "cardigan", "t-shirt", "tee", "tank top"]
    }
  }
}
```

- The key is a two-letter uppercase country code. `thresholds_c` has five finite, strictly
  increasing Celsius numbers; `palette` and `garments` each need exactly six entries, one per
  category in order. The default compositor paints every outfit with the first palette color
  and uses the last one as the accent, and it does not read the garment text (see
  `wardrobe.md`); a custom `Renderer` may use the per-category entries.
- An unknown country fails with an error that asks for a reviewed custom profiles file.
- The manifest records the country; `select` refuses a manifest generated for another country.

## Output locations

Defaults are relative to the current directory and ignored by git:
`./mascot-wardrobe-out/catalog` (generate), `./mascot-wardrobe-out/state` (sandbox bridge state)
and `./mascot-wardrobe-out/exports` (channel exports). Paths containing `..` or a symbolic link
anywhere in their ancestry are rejected.

## Live weather (Open-Meteo)

Weather is optional and is requested only with `--live-weather` plus explicit `--latitude` and
`--longitude`. The project depends on the public Open-Meteo forecast API
(`https://api.open-meteo.com/v1/forecast`) only for this opt-in feature; nothing else uses the
network. Live weather is Celsius only, so combining it with `--unit F` is an error.

### Timezone and time semantics

- The request asks for `current=temperature_2m`, `temperature_unit=celsius` and `timezone=GMT`.
- The response must report the unit `°C`; anything else is rejected.
- Open-Meteo returns `current.time` as a naive local-style timestamp together with
  `utc_offset_seconds`. A naive timestamp is interpreted with that offset (`utc_offset_seconds`
  must be an integer within one day); a missing offset is a malformed reading. A timestamp that
  already has a timezone is used as is.
- All comparisons are done in UTC.

### Stale and future readings

A reading older than 3 hours, or more than 15 minutes ahead of the current UTC time, is
rejected. It is never replaced with an invented value or with the last known temperature.

### Failure behavior

HTTP errors, connection failures, timeouts, other OS errors, incomplete reads, invalid JSON,
non-object JSON, bodies over 256 KiB, missing fields and invalid numbers all become a
`WeatherError` with a short message and the original cause chained. The CLI prints
`error: ...` and exits with status 2. The default timeout is 10 seconds. Tests use a
deterministic fake transport (`fetch_weather(..., opener=...)`) and never touch the network.
