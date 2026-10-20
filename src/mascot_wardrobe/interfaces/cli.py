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

DEFAULT_OUT = Path("mascot-wardrobe-out")


def parser():
    root = argparse.ArgumentParser(description="Isolated mascot wardrobe; no production channels")
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
    return root


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


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "generate":
            manifest = prepare_wardrobe(
                args.mascot,
                args.mascot_id,
                args.country,
                args.output,
                body_box=args.body_box,
                profiles=args.profiles,
            )
            result = {"manifest": str(manifest), "generator": "local-compositor", "remote_inference": False}
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
