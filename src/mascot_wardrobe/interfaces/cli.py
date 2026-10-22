"""Explicit country presets and sandbox-only entry points."""

import argparse
import json
import os
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
    _closet_parser(commands)
    return root


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
    for name, text in (
        ("add", "Add a clothing item"),
        ("edit", "Edit an item (garment changes bump its version; name/description do not)"),
    ):
        item = actions.add_parser(name, help=text)
        item.add_argument("item", help="Item id (lowercase slug)")
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
    actions.add_parser("list", help="Show items, wardrobes and the active wardrobe")
    actions.add_parser("verify", help="Check mascot, mask and items against their recorded sha256")


def _closet(args, interactive=False, read=input):
    from mascot_wardrobe.closet import Closet

    if args.action == "init":
        closet = Closet.init(args.root, args.mascot, args.mask)
        return {
            "root": str(closet.root),
            "mascot_sha256": closet._data["mascot"]["sha256"],
            "mask_sha256": closet._data["mask"]["sha256"],
        }
    style = palette = None
    if args.action in ("add", "edit"):
        style, palette = _choices(args, interactive, read, keep=args.action == "edit")
    closet = Closet.open(args.root)
    if args.action == "add":
        return closet.add_item(
            args.item,
            args.template,
            palette,
            style=style,
            name=args.name,
            description=args.description or "",
        )
    if args.action == "edit":
        return closet.edit_item(
            args.item,
            template=args.template,
            style=style,
            palette=palette,
            name=args.name,
            description=args.description,
        )
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


def _publish_targets(args):
    from mascot_wardrobe.channels.live import (
        HermesWhatsAppProfileAdapter,
        TelegramBotProfileAdapter,
        environment_token_provider,
    )

    whatsapp = telegram = None
    settings = (args.whatsapp_catalog_dir, args.whatsapp_bridge_url, args.whatsapp_bridge_header)
    if any(settings):
        if not all(settings):
            raise ValueError(
                "whatsapp needs --whatsapp-catalog-dir, --whatsapp-bridge-url and --whatsapp-bridge-header"
            )
        adapter = HermesWhatsAppProfileAdapter(
            args.whatsapp_bridge_url, bridge_header=args.whatsapp_bridge_header
        )
        whatsapp = WhatsAppTarget(args.whatsapp_catalog_dir, adapter)
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


def main(argv=None, *, interactive=None, read=input):
    args = parser().parse_args(argv)
    if args.no_input:
        interactive = False
    elif interactive is None:
        interactive = is_interactive(False, sys.stdin, sys.stderr)
    try:
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
