"""Explicit country presets and sandbox-only entry points."""

import argparse
import json
import os
import shlex
import sys
from pathlib import Path

from mascot_wardrobe.application import (
    TelegramTarget,
    WhatsAppTarget,
    export_outfit,
    prepare_wardrobe,
    publish_outfit,
    select_outfit,
    sync_sandbox,
)
from mascot_wardrobe.interfaces.prompts import KEEP, ask_palette, ask_style, is_interactive
from mascot_wardrobe.wardrobe import AUTO, CATEGORIES, STYLES
from mascot_wardrobe.wardrobe.styles import validate_palette

DEFAULT_OUT = Path("mascot-wardrobe-out")


def parser():
    root = argparse.ArgumentParser(description="Isolated mascot wardrobe; no production channels")
    root.add_argument(
        "--no-input",
        action="store_true",
        help="Never prompt, even on a terminal: style and palette must then be passed explicitly",
    )
    root.add_argument("--data-dir", type=Path, help="Persistent wardrobe data home (resolved at run time)")
    commands = root.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("generate", help="Prepare six local outfits from your PNG")
    generate.add_argument("--mascot", required=True, type=Path)
    generate.add_argument("--id", "--mascot-id", dest="mascot_id", required=True)
    generate.add_argument(
        "--country", required=True, help="Explicit country preset; no automatic geolocation"
    )
    generate.add_argument("--profiles", "--profile", dest="profiles", type=Path)
    generate.add_argument(
        "--body-box", nargs=4, type=float, default=(0.22, 0.60, 0.78, 0.88), metavar=("X1", "Y1", "X2", "Y2")
    )
    generate.add_argument("--output", type=Path, default=DEFAULT_OUT / "catalog")
    generate.add_argument("--no-inventory", action="store_true", help="Do not register generated garments")
    _choice_arguments(generate)
    select = commands.add_parser("select", help="Select a prepared outfit, without generation")
    _selection_arguments(select)
    for name in ("sandbox", "demo"):
        sandbox = commands.add_parser(
            name, help="Sync prepared outfit through an ephemeral local HTTP bridge"
        )
        _selection_arguments(sandbox)
        sandbox.add_argument("--state-dir", type=Path, default=DEFAULT_OUT / "state")
        sandbox.add_argument("--export-dir", type=Path, default=DEFAULT_OUT / "exports")
        sandbox.add_argument(
            "--channel", choices=("whatsapp", "telegram", "discord", "hermes-cli"), default="whatsapp"
        )
    publish = commands.add_parser(
        "publish",
        help="Select one outfit and deliver its JPEG to opt-in live channels (dry-run unless --apply)",
    )
    _selection_arguments(publish)
    publish.add_argument(
        "--apply", action="store_true", help="Really change profile photos; default is a dry-run"
    )
    publish.add_argument(
        "--force", action="store_true", help="Re-send to Telegram even if the image is unchanged"
    )
    publish.add_argument(
        "--whatsapp-catalog-dir", type=Path, help="Approved photo directory read by the bridge"
    )
    publish.add_argument("--whatsapp-bridge-url", help="Loopback bridge URL, e.g. http://127.0.0.1:3000")
    publish.add_argument(
        "--whatsapp-bridge-header",
        default=os.environ.get("MASCOT_WARDROBE_WHATSAPP_BRIDGE_HEADER"),
        help="'Name: value' header the bridge requires (or env MASCOT_WARDROBE_WHATSAPP_BRIDGE_HEADER)",
    )
    publish.add_argument(
        "--telegram", action="store_true", help="Update the Telegram BOT's own profile photo"
    )
    publish.add_argument("--telegram-expected-bot-id", type=int, help="Numeric bot id verified with getMe")
    publish.add_argument("--telegram-token-env", default="MASCOT_WARDROBE_TELEGRAM_BOT_TOKEN")
    publish.add_argument("--telegram-state-dir", type=Path, default=DEFAULT_OUT / "state")
    _publish_closet_parser(commands)
    _closet_parser(commands)
    _inventory_parser(commands)
    _collections_parser(commands)
    return root


def _collections_parser(commands):
    from mascot_wardrobe.application.collections import GATEWAY_LABELS

    command = commands.add_parser("collections", help="Manage saved named wardrobes across the data home")
    actions = command.add_subparsers(dest="action", required=True)
    listing = actions.add_parser(
        "list", help="List stable selector IDs, names, closet roots, counts and active state"
    )
    listing.add_argument("--json", action="store_true")
    select = actions.add_parser(
        "select", help="Save a separate weather publisher choice; never switch active wardrobe"
    )
    select.add_argument(
        "selector", nargs="?", help="Stable ID from 'collections list'; prompted on a terminal"
    )
    select.add_argument(
        "--gateway",
        action="append",
        choices=tuple(GATEWAY_LABELS),
        help="Repeat for gateways: hermes = active Hermes PetDex pet/avatar; whatsapp = profile photo",
    )
    remove = actions.add_parser(
        "remove", help="Remove only a named entry; retain all item assets and inventory"
    )
    remove.add_argument("selector", help="Stable ID from 'collections list'")
    remove.add_argument("--yes", action="store_true", help="Required; active wardrobes cannot be removed")


def _collection_row(row):
    return (
        f"{row['id']}\t{row['wardrobe']}\t{row['closet_root']}\t{row['item_count']}\t"
        f"{'yes' if row['active'] else 'no'}"
    )


def _collections(args, interactive, read):
    from mascot_wardrobe.application.collections import (
        find_collection,
        list_collections,
        remove_collection,
        save_publisher_selection,
    )
    from mascot_wardrobe.infrastructure.inventory_store import data_home

    home = data_home(args.data_dir)
    if args.action == "list":
        return list_collections(home)
    if args.action == "remove":
        return remove_collection(home, args.selector, yes=args.yes)
    selector = args.selector
    if selector is None:
        if not interactive:
            raise ValueError("Non-interactive selection requires a collection selector ID")
        rows = list_collections(home)
        if not rows:
            raise ValueError("No saved collections found in the data home")
        for row in rows:
            print(_collection_row(row), file=sys.stderr)
        selector = read("Collection selector ID: ").strip()
    find_collection(home, selector)
    gateways = args.gateway
    if not gateways:
        if not interactive:
            raise ValueError("Non-interactive selection requires explicit repeated --gateway flags")
        gateways = (
            read(
                "Gateways (one or more, comma/space separated): "
                "hermes (active PetDex pet/avatar with --apply), "
                "whatsapp (profile photo with --apply): "
            )
            .replace(",", " ")
            .split()
        )
    return save_publisher_selection(home, selector, gateways)


def _inventory_parser(commands):
    inventory = commands.add_parser("inventory", help="Persistent numbered garments and user defaults")
    actions = inventory.add_subparsers(dest="action", required=True)
    listing = actions.add_parser("list", help="Show every garment, with optional suitability hints")
    listing.add_argument("--range", choices=CATEGORIES)
    listing.add_argument("--json", action="store_true")
    ranges = actions.add_parser("ranges", help="Show named ranges and their Celsius boundaries")
    ranges.add_argument("--country", required=True)
    ranges.add_argument("--profiles", type=Path)
    manifest = actions.add_parser("import-manifest", help="Import a prepared six-category catalog")
    manifest.add_argument("manifest", type=Path)
    closet = actions.add_parser("import-closet", help="Import a closet without choosing defaults")
    closet.add_argument("--root", required=True, type=Path)
    closet.add_argument("--item")
    closet.add_argument("--replaces", help="Previous registered hash; requires --item")
    closet.add_argument("--range", dest="ranges", choices=CATEGORIES, action="append", default=[])
    closet.add_argument(
        "--allow-first-registration",
        action="store_true",
        help="Recover a first edit registration if absent; requires --item and --replaces",
    )
    asset = actions.add_parser("import-asset", help="Import one existing garment asset")
    asset.add_argument("file", type=Path)
    asset.add_argument("--name", required=True)
    asset.add_argument("--range", choices=CATEGORIES, action="append", required=True)
    asset.add_argument("--style")
    asset.add_argument("--palette", nargs="+")
    asset.add_argument("--format", default="png")
    default = actions.add_parser("set-default", help="Save the user's garment number for a named range")
    default.add_argument("--range", choices=CATEGORIES, required=True)
    default.add_argument("--number", type=int, required=True)
    change = actions.add_parser("change", aliases=["mudar-peca"], help="Ask a range, then a garment number")
    change.add_argument("--country", required=True)
    change.add_argument("--profiles", type=Path)
    default = actions.add_parser("default", help="Resolve the user's saved default")
    choice = default.add_mutually_exclusive_group(required=True)
    choice.add_argument("--range", choices=CATEGORIES)
    choice.add_argument("--temperature", type=float)
    default.add_argument("--country")
    default.add_argument("--unit", choices=("C", "F"), default="C")
    default.add_argument("--profiles", type=Path)
    select = actions.add_parser("select", help="Select only the mapped garment; never publishes")
    select.add_argument("--country", required=True)
    select.add_argument("--temperature", type=float, required=True)
    select.add_argument("--unit", choices=("C", "F"), default="C")
    select.add_argument("--profiles", type=Path)
    remove = actions.add_parser("remove", help="Retire an unused garment number")
    remove.add_argument("number", type=int)
    actions.add_parser("verify", help="Check hashes, numbering and mappings; report orphan files")


def _choice_arguments(command):
    command.add_argument(
        "--style",
        choices=(*STYLES, AUTO),
        help=(
            "Garment style; 'auto' lets the generator choose, deterministically and locally "
            "(no network, no model). Required when not interactive"
        ),
    )
    command.add_argument(
        "--palette",
        nargs="+",
        metavar="#RRGGBB",
        help=(
            "One to six colors: base, secondary, accent and highlight roles (first is the base, "
            "last the accent); or the single word 'auto' for a deterministic local choice. "
            "Required when not interactive"
        ),
    )


def _prompt_io(read):
    def ask(prompt=""):
        sys.stderr.write(prompt)
        sys.stderr.flush()
        return read("")

    def write(text):
        print(text, file=sys.stderr)

    return ask, write


def _choices(args, interactive, read, *, keep=False):
    """Turn flags or prompt answers into the ``style`` and ``palette`` library arguments.

    With ``keep`` (closet edit) a value that is not supplied stays ``None`` (keep the stored one):
    it is asked only when a garment attribute was supplied on an interactive run, with a
    "keep current" option, and is never asked on a label-only edit or a non-interactive run.
    """
    palette = args.palette
    if palette is not None:
        palette = AUTO if palette == [AUTO] else list(validate_palette(palette))
    style = args.style
    if keep:
        garment = any(value is not None for value in (getattr(args, "template", None), style, palette))
        if not (garment and interactive):
            return style, palette
    else:
        for name, value in (("style", style), ("palette", palette)):
            if value is None and not interactive:
                options = "classic, streetwear or auto" if name == "style" else "colors or auto"
                raise ValueError(f"--{name} is required when not interactive: pass one of {options}")
    ask, write = _prompt_io(read)
    if style is None:
        style = ask_style(ask, write, allow_keep=keep)
    if palette is None:
        palette = ask_palette(ask, write, allow_keep=keep)
    return (None if style is KEEP else style), (None if palette is KEEP else palette)


def _closet_parser(commands):
    from mascot_wardrobe.closet import MAX_DESCRIPTION_LENGTH, MAX_NAME_LENGTH

    closet = commands.add_parser(
        "closet",
        help="Identity-locked closet: immutable mascot, item inventory, named wardrobes, catalog sheet",
        description=(
            "Keep one user-supplied mascot as an immutable source (pinned by sha256) with an explicit "
            "binary edit mask: garments only change masked pixels, every other byte stays identical. "
            "Manage clothing items (add/edit/remove), save and switch named wardrobes, and build a "
            "catalog sheet from the saved item assets. A changed mascot, mask or item is detected."
        ),
    )
    closet.add_argument("--root", type=Path, default=DEFAULT_OUT / "closet", help="Closet directory")
    actions = closet.add_subparsers(dest="action", required=True)
    init = actions.add_parser("init", help="Create a closet; copies the mascot and mask, pins sha256")
    init.add_argument("--mascot", required=True, type=Path, help="Static PNG; stored as an immutable source")
    init.add_argument(
        "--mask", required=True, type=Path, help="Binary PNG mask, same size: 255 editable, 0 protected"
    )
    init.add_argument(
        "--body-box",
        nargs=4,
        type=float,
        metavar=("X1", "Y1", "X2", "Y2"),
        help="Optional normalized (0..1) garment fit box, independent of the edit mask",
    )
    for name, text in (
        ("add", "Add a clothing item"),
        ("edit", "Edit an item (garment changes bump its version; name/description do not)"),
    ):
        item = actions.add_parser(name, help=text)
        item.add_argument("item", help="Item id (lowercase slug)")
        item.add_argument("--range", dest="ranges", choices=CATEGORIES, action="append", default=[])
        item.add_argument(
            "--template",
            choices=CATEGORIES,
            required=name == "add",
            help="Garment template (the six deterministic outfits)",
        )
        item.add_argument(
            "--style",
            choices=(*STYLES, AUTO),
            help=(
                "Garment style; 'auto' lets the generator choose, deterministically and locally "
                "(from the mascot and the item id; no network, no model)"
                + (
                    ". Required when not interactive"
                    if name == "add"
                    else "; omitted keeps the stored style, even when the template changes"
                )
            ),
        )
        item.add_argument(
            "--palette",
            nargs="+",
            metavar="#RRGGBB",
            help=(
                "One to six colors with four roles: base (first), secondary (second when there are "
                "three or more), accent (last) and highlight (second to last when there are four or "
                "more); or the single word 'auto' for a deterministic local choice"
                + (
                    ". Required when not interactive"
                    if name == "add"
                    else "; omitted keeps the stored palette, even when the template changes"
                )
            ),
        )
        item.add_argument(
            "--name",
            help=(
                f"Human-readable label shown in listings and the catalog (max {MAX_NAME_LENGTH} chars, "
                "one line; default derives from the id). The id stays the stable slug"
                + ("" if name == "add" else "; changing only name/description keeps the version and image")
            ),
        )
        item.add_argument(
            "--description",
            help=(
                f"Free-text description printed in full under the item in the catalog "
                f"(max {MAX_DESCRIPTION_LENGTH} chars, over-long text is rejected, not truncated; "
                "'' clears it)"
            ),
        )
    remove = actions.add_parser("remove", help="Remove an item that no wardrobe uses")
    remove.add_argument("item")
    save = actions.add_parser("save", help="Save (or replace) a named wardrobe pinning item versions")
    save.add_argument("name")
    save.add_argument("--items", nargs="+", required=True, metavar="ITEM")
    use = actions.add_parser("use", help="Switch the active wardrobe (refused if an item changed since)")
    use.add_argument("name")
    catalog = actions.add_parser("catalog", help="Build a catalog sheet from saved item assets")
    catalog.add_argument("--wardrobe", help="Wardrobe name (default: the active one)")
    migrate = actions.add_parser(
        "migrate",
        help="Upgrade a template-set-2 closet to the current renderer (keeps a backup; atomic)",
    )
    migrate.add_argument("--dry-run", action="store_true", help="Show the plan; write nothing")
    piece = actions.add_parser(
        "piece",
        help="Interactive: create one piece at a time (ranges or Celsius, style, palette), revise, repeat",
        description=(
            "Ask named suitability ranges or the intended use temperature in Celsius (mapped through "
            "the country profile thresholds), then style and palette, separately for every piece. "
            "Only that piece "
            "is rendered and saved; you can revise it, and then choose to create another. Needs a "
            "terminal; scripts use 'closet add'. Local rendering only."
        ),
    )
    piece.add_argument("--country", required=True, help="Explicit country preset for the thresholds")
    piece.add_argument("--profiles", "--profile", dest="profiles", type=Path)
    piece.add_argument(
        "--wardrobe",
        help="At the end, save this session's pieces as this wardrobe and build its catalog",
    )
    actions.add_parser("list", help="Show items, wardrobes and the active wardrobe")
    actions.add_parser("verify", help="Check mascot, mask and items against their recorded sha256")


def _closet(args, interactive=False, read=input):
    from mascot_wardrobe.closet import Closet

    if args.action == "init":
        closet = Closet.init(args.root, args.mascot, args.mask, body_box=args.body_box)
        return {
            "root": str(closet.root),
            "mascot_sha256": closet.mascot_sha256,
            "mask_sha256": closet.mask_sha256,
        }
    if args.action == "migrate":
        return Closet.migrate(args.root, dry_run=args.dry_run)
    if args.action == "piece":
        if not interactive:
            raise ValueError(
                "closet piece is interactive and needs a terminal; for scripts use "
                "'closet add' with --template, --style and --palette"
            )
        from mascot_wardrobe.infrastructure.policies import load_profile
        from mascot_wardrobe.interfaces.piece import run_piece_session

        profile = load_profile(args.country, args.profiles)
        ask, write = _prompt_io(read)
        return run_piece_session(
            Closet.open(args.root), profile, ask, write, wardrobe=args.wardrobe, store=_inventory_store(args)
        )
    style = palette = None
    if args.action in ("add", "edit"):
        style, palette = _choices(args, interactive, read, keep=args.action == "edit")
    closet = Closet.open(args.root)
    if args.action == "add":
        from mascot_wardrobe.application.inventory import require_closet_item_id_free

        store = _inventory_store(args)
        require_closet_item_id_free(closet, args.item, store)
        result = closet.add_item(
            args.item,
            args.template,
            palette,
            style=style,
            name=args.name,
            description=args.description or "",
        )
        result["inventory"] = _register_saved_item(args, closet, store)
        return result
    if args.action == "edit":
        previous = closet.items().get(args.item, {}).get("sha256")
        if args.ranges and all(
            value is None for value in (args.template, style, palette, args.name, args.description)
        ):
            if previous is None:
                raise ValueError(f"Unknown closet item: {args.item}")
            result = closet.items()[args.item]
        else:
            result = closet.edit_item(
                args.item,
                template=args.template,
                style=style,
                palette=palette,
                name=args.name,
                description=args.description,
            )
        result["inventory"] = _register_saved_item(args, closet, replaces=previous)
        return result
    if args.action == "remove":
        closet.remove_item(args.item)
        return {"removed": args.item}
    if args.action == "save":
        return closet.save_wardrobe(args.name, args.items)
    if args.action == "use":
        return closet.use_wardrobe(args.name)
    if args.action == "catalog":
        return closet.build_catalog(args.wardrobe)
    if args.action == "list":
        return {"active": closet.active, "items": closet.items(), "wardrobes": closet.wardrobes()}
    return {"ok": True, "items": len(closet.items())}  # verify: open() already checked everything


def _register_saved_item(args, closet, store=None, *, replaces=None):
    from mascot_wardrobe.application.inventory import register_closet_item

    try:
        if store is None:
            store = _inventory_store(args)
        return register_closet_item(closet, args.item, store, ranges=args.ranges, replaces=replaces)
    except (ValueError, OSError, RuntimeError) as error:
        arguments = ["import-closet", "--root", closet.root, "--item", args.item]
        if replaces is not None:
            arguments.extend(("--replaces", replaces, "--allow-first-registration"))
        for category in args.ranges:
            arguments.extend(("--range", category))
        retry = _inventory_retry(args, *arguments)
        raise ValueError(
            f"Closet item {args.item} was saved, but inventory registration failed: {error}. Retry: {retry}"
        ) from error


def _whatsapp_target(args):
    from mascot_wardrobe.channels.live import HermesWhatsAppProfileAdapter

    settings = (args.whatsapp_catalog_dir, args.whatsapp_bridge_url, args.whatsapp_bridge_header)
    if not any(settings):
        return None
    if not all(settings):
        raise ValueError(
            "whatsapp needs --whatsapp-catalog-dir, --whatsapp-bridge-url and --whatsapp-bridge-header"
        )
    adapter = HermesWhatsAppProfileAdapter(
        args.whatsapp_bridge_url,
        bridge_header=args.whatsapp_bridge_header,
        state_dir=args.whatsapp_catalog_dir / ".avatar-sync",
    )
    return WhatsAppTarget(args.whatsapp_catalog_dir, adapter)


def _publish_closet_parser(commands):
    command = commands.add_parser(
        "publish-closet",
        aliases=["weather-run"],
        help="Plan a weather-selected look for saved gateways; writes require --apply",
    )
    command.add_argument("--closet-root", type=Path, help="Explicit closet; requires --wardrobe")
    command.add_argument(
        "--wardrobe", help="Explicit named wardrobe; otherwise use saved publisher selection"
    )
    command.add_argument(
        "--gateway",
        action="append",
        choices=("hermes", "whatsapp"),
        help="Repeat for one or both gateways; defaults to the saved collection choice",
    )
    command.add_argument("--country", required=True, help="Explicit country preset")
    command.add_argument("--profiles", "--profile", dest="profiles", type=Path)
    temperatures = command.add_mutually_exclusive_group(required=True)
    temperatures.add_argument("--temperature", type=float, help="Explicit input, not a live weather reading")
    temperatures.add_argument(
        "--state-file", type=Path, help="Weather updater state JSON (temperature_c, updated_at_utc)"
    )
    command.add_argument("--unit", choices=("C", "F"), default="C")
    command.add_argument(
        "--background", default="#102b32", help="Explicit #rrggbb background (default: approved #102b32)"
    )
    command.add_argument(
        "--overlay", type=Path, help="Optional square RGBA PNG you supply; clipped to the circular-safe area"
    )
    command.add_argument(
        "--overlay-start-date",
        help="Inclusive local calendar date, YYYY-MM-DD; requires overlay and end date",
    )
    command.add_argument(
        "--overlay-end-date",
        help="Inclusive local calendar date, YYYY-MM-DD; requires overlay and start date",
    )
    command.add_argument(
        "--overlay-timezone", default="UTC", help="IANA timezone for overlay dates (default: UTC)"
    )
    command.add_argument(
        "--apply",
        action="store_true",
        help="Select the active Hermes PetDex pet and/or update the WhatsApp photo; default is a dry-run",
    )
    command.add_argument(
        "--whatsapp-catalog-dir", type=Path, help="Approved photo directory read by the bridge"
    )
    command.add_argument("--whatsapp-bridge-url", help="Loopback bridge URL, e.g. http://127.0.0.1:3000")
    command.add_argument(
        "--whatsapp-bridge-header",
        default=os.environ.get("MASCOT_WARDROBE_WHATSAPP_BRIDGE_HEADER"),
        help="'Name: value' header the bridge requires (or env MASCOT_WARDROBE_WHATSAPP_BRIDGE_HEADER)",
    )


def _publish_closet(args):
    from mascot_wardrobe.application.closet_weather import (
        overlay_for_date,
        publish_closet_look,
        read_weather_state,
    )

    if (args.closet_root is None) != (args.wardrobe is None):
        raise ValueError("Explicit publishing requires both --closet-root and --wardrobe")
    gateways = args.gateway
    if args.wardrobe is None:
        from mascot_wardrobe.application.collections import read_publisher_selection
        from mascot_wardrobe.infrastructure.inventory_store import data_home

        home = data_home(args.data_dir)
        selection = read_publisher_selection(home)
        args.closet_root, args.wardrobe = Path(selection["closet_root"]), selection["wardrobe"]
        gateways = gateways or selection["gateways"]
    if args.state_file is not None:
        if args.unit != "C":
            raise ValueError("The state file is in Celsius; --unit F only applies to --temperature")
        reading = read_weather_state(args.state_file)
        temperature, unit = reading.temperature_c, "C"
        weather = {
            "source": reading.source,
            "temperature_c": reading.temperature_c,
            "observed_at": reading.observed_at.isoformat(),
        }
    else:
        temperature, unit = args.temperature, args.unit
        weather = {"source": "explicit-input-not-live-weather"}
    return publish_closet_look(
        args.closet_root,
        args.wardrobe,
        args.country,
        temperature,
        unit,
        weather=weather,
        profiles=args.profiles,
        background=args.background,
        overlay=overlay_for_date(
            args.overlay, args.overlay_start_date, args.overlay_end_date, timezone=args.overlay_timezone
        ),
        apply=args.apply,
        whatsapp=_whatsapp_target(args) if gateways is None or "whatsapp" in gateways else None,
        gateways=gateways,
    )


def _publish_targets(args):
    from mascot_wardrobe.channels.live import TelegramBotProfileAdapter, environment_token_provider

    whatsapp = _whatsapp_target(args)
    telegram = None
    if args.telegram:
        if args.telegram_expected_bot_id is None:
            raise ValueError("--telegram requires --telegram-expected-bot-id")
        provider = environment_token_provider(args.telegram_token_env)
        adapter = TelegramBotProfileAdapter(provider, expected_bot_id=args.telegram_expected_bot_id)
        telegram = TelegramTarget(adapter, args.telegram_state_dir)
    return whatsapp, telegram


def _selection_arguments(command):
    command.add_argument("--manifest", type=Path, required=True)
    command.add_argument(
        "--country", required=True, help="Explicit country preset, independent of coordinates"
    )
    command.add_argument("--profiles", "--profile", dest="profiles", type=Path)
    temperatures = command.add_mutually_exclusive_group(required=True)
    temperatures.add_argument("--temperature", type=float, help="Explicit input, not a live weather reading")
    temperatures.add_argument(
        "--live-weather", action="store_true", help="Fetch actual Open-Meteo current temperature"
    )
    command.add_argument("--unit", choices=("C", "F"), default="C")
    command.add_argument("--latitude", type=float)
    command.add_argument("--longitude", type=float)


def _temperature(args):
    if args.live_weather:
        if args.latitude is None or args.longitude is None:
            raise ValueError("Live weather requires explicit --latitude and --longitude")
        if args.unit != "C":
            raise ValueError("Live weather already uses Celsius; --unit F only applies to explicit inputs")
        from mascot_wardrobe.infrastructure.weather import fetch_weather

        reading = fetch_weather(args.latitude, args.longitude)
        return (
            reading.temperature_c,
            "C",
            {
                "source": reading.source,
                "temperature_c": reading.temperature_c,
                "observed_at": reading.observed_at.isoformat(),
            },
        )
    if args.latitude is not None or args.longitude is not None:
        raise ValueError("Coordinates require --live-weather")
    label = (
        "explicit-demo-input-not-live-weather"
        if args.command in ("sandbox", "demo")
        else "explicit-input-not-live-weather"
    )
    return args.temperature, args.unit, {"source": label}


def _inventory(args, interactive, read):
    if args.action in ("change", "mudar-peca") and not interactive:
        raise ValueError(
            "inventory change is interactive and needs a terminal; for scripts use "
            "'inventory set-default --range RANGE --number NUMBER'"
        )
    if (
        args.action == "import-closet"
        and args.allow_first_registration
        and (args.item is None or args.replaces is None)
    ):
        raise ValueError("--allow-first-registration requires --item and --replaces")
    if args.action == "import-closet" and args.replaces is not None and args.item is None:
        raise ValueError("--replaces requires --item to limit recovery to one garment")
    if args.action == "import-closet" and args.ranges and args.item is None:
        raise ValueError("--range requires --item to limit recovery to one garment")
    if args.action == "default" and args.temperature is not None and args.country is None:
        raise ValueError("--temperature requires --country")
    if args.action == "ranges":
        from mascot_wardrobe.application.inventory import inventory_ranges

        return inventory_ranges(args.country, args.profiles)
    from mascot_wardrobe.application.inventory import (
        inventory_view,
        register_asset,
        register_closet,
        register_manifest,
        remove_garment,
        resolve_default,
        select_from_inventory,
        set_default,
    )

    store = _inventory_store(args)
    if args.action == "list":
        return inventory_view(store, args.range)
    if args.action == "verify":
        return store.verify()
    if args.action == "import-asset":
        return register_asset(
            args.file,
            store,
            name=args.name,
            ranges=args.range,
            style=args.style,
            palette=args.palette,
            fmt=args.format,
        )
    if args.action == "import-manifest":
        return register_manifest(args.manifest, store)
    if args.action == "import-closet":
        from mascot_wardrobe.closet import Closet

        return register_closet(
            Closet.open(args.root),
            store,
            item_id=args.item,
            replaces=args.replaces,
            ranges=args.ranges,
            allow_first_registration=args.allow_first_registration,
        )
    if args.action == "set-default":
        return set_default(store, args.range, args.number)
    if args.action == "default":
        if args.range is not None:
            number = store.read().default_for(args.range)
            if number is None:
                return {"status": "no-default", "category": args.range}
            return {"status": "default", "category": args.range, "number": number}
        return resolve_default(store, args.country, args.temperature, args.unit, args.profiles)
    if args.action == "select":
        return select_from_inventory(store, args.country, args.temperature, args.unit, args.profiles)
    if args.action == "remove":
        return remove_garment(store, args.number)
    if args.action in ("change", "mudar-peca"):
        from mascot_wardrobe.application.inventory import inventory_ranges
        from mascot_wardrobe.interfaces.prompts import ask_number, ask_range_name

        if not inventory_view(store):
            raise ValueError("Inventory is empty; import or create a garment first")
        ask, write = _prompt_io(read)
        ranges = inventory_ranges(args.country, args.profiles)
        write("Named ranges in Celsius (the upper boundary belongs to the warmer range):")
        for row in ranges:
            write(f"  {row['range']}: {row['min_c']} to {row['max_c']}")
        chosen_range = ask_range_name(ask, write, ranges)
        rows = inventory_view(store, chosen_range)
        for row in rows:
            write(_inventory_row(row, with_hint=True))
        number = ask_number(ask, write, [row["number"] for row in rows])
        return set_default(store, chosen_range, number)
    raise ValueError("Inventory command is not available")


def _inventory_row(row, *, with_hint=False):
    hint = f"; suitable: {row['suitable']}" if with_hint else ""
    return (
        f"#{row['number']} {row['name']}; style: {row['style']}; palette: {', '.join(row['palette'])}; "
        f"ranges: {', '.join(row['ranges'])}; format: {row['format']}; version: {row['version']}{hint}"
    )


def _inventory_store(args):
    from mascot_wardrobe.infrastructure.inventory_store import InventoryStore, data_home

    return InventoryStore.open(data_home(args.data_dir))


def _inventory_retry(args, *arguments):
    command = ["mascot-wardrobe"]
    if args.data_dir is not None:
        command.extend(("--data-dir", str(args.data_dir)))
    command.extend(("inventory", *map(str, arguments)))
    return shlex.join(command)


def main(argv=None, *, interactive=None, read=input):
    args = parser().parse_args(argv)
    if args.no_input:
        interactive = False
    elif interactive is None:
        interactive = is_interactive(False, sys.stdin, sys.stderr)
    try:
        if args.command == "collections":
            result = _collections(args, interactive, read)
            if args.action == "list" and not args.json:
                print("ID\tWardrobe\tCloset root\tItems\tActive")
                for row in result:
                    print(_collection_row(row))
            else:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == "inventory":
            result = _inventory(args, interactive, read)
            if args.action == "list" and not args.json:
                for row in result:
                    print(_inventory_row(row, with_hint=args.range is not None))
                if not result:
                    print("Inventory is empty.")
            else:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            return 3 if isinstance(result, dict) and result.get("status") == "no-default" else 0
        if args.command in ("publish-closet", "weather-run"):
            result = _publish_closet(args)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["ok"] else 1
        if args.command == "closet":
            print(json.dumps(_closet(args, interactive, read), ensure_ascii=False, indent=2))
            return 0
        if args.command == "generate":
            style, palette = _choices(args, interactive, read)
            manifest = prepare_wardrobe(
                args.mascot,
                args.mascot_id,
                args.country,
                args.output,
                body_box=args.body_box,
                profiles=args.profiles,
                style=style,
                palette=palette,
            )
            request = json.loads(Path(manifest).read_text(encoding="utf-8"))["request"]
            result = {
                "manifest": str(manifest),
                "generator": "local-compositor",
                "remote_inference": False,
                **{key: request[key] for key in ("style", "palette", "style_choice", "palette_choice")},
            }
            if not args.no_inventory:
                from mascot_wardrobe.application.inventory import register_manifest

                try:
                    result["inventory"] = register_manifest(manifest, _inventory_store(args))
                except (ValueError, OSError, RuntimeError) as error:
                    retry = _inventory_retry(args, "import-manifest", manifest)
                    raise ValueError(
                        f"Catalog saved at {manifest}, but inventory registration failed: "
                        f"{error}. Retry: {retry}"
                    ) from error
        else:
            temperature, unit, weather = _temperature(args)
            result = select_outfit(args.manifest, args.country, temperature, unit, profiles=args.profiles)
            weather["temperature_c"] = result["temperature_c"]
            result["weather"] = weather
            if args.command == "publish":
                whatsapp, telegram = _publish_targets(args)
                result = publish_outfit(
                    result, apply=args.apply, force=args.force, whatsapp=whatsapp, telegram=telegram
                )
                result["weather"] = weather
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 0 if result["ok"] else 1
            if args.command in ("sandbox", "demo"):
                selection = result
                if args.channel == "whatsapp":
                    first = sync_sandbox(selection, args.state_dir)
                    second = sync_sandbox(selection, args.state_dir)
                else:
                    first = export_outfit(selection, args.export_dir, args.channel)
                    second = export_outfit(selection, args.export_dir, args.channel)
                result = {
                    "sandbox": True,
                    "selection": selection,
                    "weather": weather,
                    "sync": first,
                    "repeat_sync": second,
                }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
