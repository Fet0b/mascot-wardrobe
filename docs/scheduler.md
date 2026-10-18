# Scheduler examples

`examples/scheduler/` contains three templates that run a selection every 6 hours:

- `mascot-wardrobe.cron.example`: a cron line (`0 */6 * * *`).
- `mascot-wardrobe.service.example` and `mascot-wardrobe.timer.example`: a systemd user
  service and timer (`OnCalendar=*-*-* 00/6:00:00`).

They are examples. They are never installed by this project, they carry the `.example` suffix
and live outside any directory read by cron or systemd, and nothing in the repository
installs, enables or starts them. Copy and adapt them yourself if you want them.

## What they do

Only existing images are used. The commands call `mascot-wardrobe select` or `demo` with an
explicit country and temperature against a manifest you generated earlier. They never
regenerate outfits. Because selection is a manifest lookup, running it every 6 hours is
cheap. The templates use an explicit temperature; add `--live-weather --latitude ... --longitude ...`
yourself if you want Open-Meteo, accepting that failures exit with status 2 and leave the
previous export untouched.

Replace every `/path/to/...` placeholder before use. Do not run a scheduler as a privileged user.
