---
name: mudar-peca
description: Let the user choose a numbered inventory garment as the default for a named range.
---

# Change a garment default

Use the installed Mascot Wardrobe CLI. Ask for the country code if it is unknown. Use an
explicit data directory only when the user supplies one; the default is persistent user data.

1. Run `mascot-wardrobe inventory ranges --country <code>` and ask the user which named range
   applies. Show the six names and their temperature boundaries. Accept a category name, never
   a numeric range index.
2. Run `mascot-wardrobe inventory list --range <range> --json`. Show EVERY returned garment's
   number, name and range list, without filtering or ranking. Suitability is only a hint. If
   the inventory is empty, explain that a garment must be created or imported first and stop.
3. Ask the user for the garment number. The agent must not choose a garment or range on the
   user's behalf. If the answer is invalid, ask again; do not substitute another number.
4. Run `mascot-wardrobe inventory set-default --range <range> --number <n>` with the user's
   explicit answers. Report the stored mapping only when the command succeeds.

This flow must not publish, change a live profile or photo, or select a publication target.
It must not touch any gateway. It does not install or activate schedulers, install this skill
into a profile, or create garments. Bot Mode, Telegram and WhatsApp need a separate user-approved
workflow after garment approval.
