# Scheduler examples

`examples/scheduler/` contains three templates that run a selection every 6 hours:

- `mascot-wardrobe.cron.example`: a cron line (`0 */6 * * *`).
- `mascot-wardrobe.service.example` and `mascot-wardrobe.timer.example`: a systemd user
  service and timer (`OnCalendar=*-*-* 00/6:00:00`).

They are examples. They are never installed by this project, they carry the `.example` suffix
and live outside any directory read by cron or systemd, and nothing in the repository
installs, enables or starts them. Copy and adapt them yourself if you want them.

`inventory select` resolves a user-chosen range default and changes only inventory active state;
it changes no gateway or profile photo and installs no scheduler. Missing mappings exit 3
without fallback. These scheduler examples remain opt-in and use their existing manifest flow.

## What they do

Only existing images are used. The commands call `mascot-wardrobe select` or `demo` with an
explicit country and temperature against a manifest you generated earlier. They never
regenerate outfits. Because selection is a manifest lookup, running it every 6 hours is
cheap. The templates use an explicit temperature; add `--live-weather --latitude ... --longitude ...`
yourself if you want Open-Meteo, accepting that failures exit with status 2 and leave the
previous export untouched.

Replace every `/path/to/...` placeholder before use. Do not run a scheduler as a privileged user.

## Periodic publishing (opt-in, never installed)

`examples/publish/` holds a second pair of templates, `mascot-wardrobe-publish.service.example`
and `mascot-wardrobe-publish.timer.example`. They run `mascot-wardrobe publish ... --apply`
every 6 hours, which selects one outfit and delivers the same JPEG to the channels you
configured (see [channels](channels.md)). Like the others they carry the `.example` suffix, are
never installed, and nothing in this repository enables or starts them.

- No LLM, agent or image API is involved: selection is a manifest lookup and the images were
  composed locally by Pillow. Weather is requested only because the template spells out
  `--live-weather` with coordinates; replace it with `--temperature` to stay fully offline.
- Credentials come from an `EnvironmentFile` that you keep outside the repository. Never
  put a token in the unit file.
- Run it on the Hermes host so the loopback bridge is reachable. Do not run it as a privileged user.
- Test first: remove `--apply` for a dry-run that only prints what would happen.
- Disable and roll back: `systemctl --user disable --now mascot-wardrobe-publish.timer`, remove
  the copied unit files, and delete the bot token from your environment file. A cron line works
  the same way: any scheduler only needs to run the same `publish` command.
