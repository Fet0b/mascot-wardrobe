"""Interactive one-piece-at-a-time session.

Every piece asks its own ranges, style and palette (never inherited from the previous piece),
renders and saves only that item, lets the user revise it, then asks whether to make another. Prompts
and their echo go through ``write`` (stderr in the CLI). Rendering is the local deterministic
compositor: no network, no model.
"""

from __future__ import annotations

import shlex

from mascot_wardrobe.closet import _name
from mascot_wardrobe.interfaces.prompts import (
    KEEP,
    EndOfInput,
    ask_palette,
    ask_ranges,
    ask_style,
    ask_yes_no,
)

ATTEMPTS = 3


def _slug(category: str) -> str:
    return category.replace("_", "-")


def _id_free(closet, item_id: str, store) -> bool:
    if store is None:
        return True
    from mascot_wardrobe.application.inventory import closet_item_id_free

    return closet_item_id_free(closet, item_id, store)


def _default_id(closet, category: str, store=None) -> str:
    taken = closet.items()
    number = 1
    while f"{_slug(category)}-{number}" in taken or not _id_free(
        closet, f"{_slug(category)}-{number}", store
    ):
        number += 1
    return f"{_slug(category)}-{number}"


def _ask_item_id(closet, read, write, default: str, store=None) -> str:
    for _ in range(ATTEMPTS):
        try:
            answer = read(f"item id [{default}]> ").strip() or default
        except EOFError as exc:
            raise EndOfInput("end of input while waiting for an answer") from exc
        try:
            _name(answer, "item id")
        except ValueError as error:
            write(f"  {error}")
            continue
        if answer in closet.items():
            write(f"  Item {answer} already exists; pick another id.")
            continue
        if not _id_free(closet, answer, store):
            from mascot_wardrobe.application.inventory import require_closet_item_id_free

            try:
                require_closet_item_id_free(closet, answer, store)
            except ValueError as error:
                write(f"  {error}")
            continue
        return answer
    raise ValueError(f"No valid item id after {ATTEMPTS} attempts.")


def _description(temperature_c: float | None, ranges: tuple[str, ...], country: str) -> str:
    names = ", ".join(ranges)
    if temperature_c is not None:
        return f"Intended for {temperature_c} °C ({names}, {country} profile)."
    return f"Intended ranges: {names} ({country} profile)."


def _summary(closet, item_id: str, record: dict, temperature_c: float | None, ranges) -> dict:
    return {
        "id": item_id,
        "path": str(closet.item_path(item_id)),
        "template": record["template"],
        "category": ranges[0],
        "ranges": list(ranges),
        "temperature_c": temperature_c,
        "style": record["style"],
        "style_choice": record["style_choice"],
        "palette": list(record["palette"]),
        "palette_choice": record["palette_choice"],
        "version": record["version"],
        "sha256": record["sha256"],
        "revisions": 0,
    }


def _show(write, made: dict) -> None:
    write(f"Piece {made['id']} (version {made['version']}):")
    if "inventory" in made:
        write(f"  inventory: #{made['inventory']['number']}")
    write(f"  preview: {made['path']}")
    if made["temperature_c"] is not None:
        write(f"  temperature: {made['temperature_c']} °C -> {made['category'].replace('_', ' ')}")
    write(f"  ranges: {', '.join(made['ranges'])}")
    write(f"  style: {made['style']} ({made['style_choice']})")
    write(f"  palette: {' '.join(made['palette'])} ({made['palette_choice']})")
    write(f"  sha256: {made['sha256']}")
    write("  generator: local compositor, no remote inference")


def run_piece_session(closet, profile, read, write, *, wardrobe=None, store=None) -> dict:
    """Run the loop; an optional store registers saved pieces without changing library defaults."""
    pieces: list[dict] = []
    try:
        while True:
            pieces.append(_make_piece(closet, profile, read, write, store))
            if not ask_yes_no(read, write, "Create another piece?"):
                break
    except EndOfInput as exc:
        if pieces:
            kept = ", ".join(piece["id"] for piece in pieces)
            raise ValueError(f"{exc}; saved pieces are kept: {kept}") from exc
        raise
    result = {
        "generator": "local-compositor",
        "remote_inference": False,
        "country_code": profile.country_code,
        "pieces": pieces,
        "wardrobe": None,
    }
    if wardrobe is not None:
        saved = closet.save_wardrobe(wardrobe, [piece["id"] for piece in pieces])
        result["wardrobe"] = {**saved, "catalog": closet.build_catalog(wardrobe)}
    return result


def _register(closet, made, store, *, replaces=None):
    if store is None:
        return
    from mascot_wardrobe.application.inventory import register_closet_item

    try:
        kwargs = {"ranges": tuple(made["ranges"])}
        if replaces is not None:
            kwargs["replaces"] = replaces
        made["inventory"] = register_closet_item(closet, made["id"], store, **kwargs)
    except (ValueError, OSError, RuntimeError) as error:
        retry = (
            "mascot-wardrobe inventory import-closet --root "
            f"{shlex.quote(str(closet.root))} --item {made['id']}"
        )
        if replaces is not None:
            retry += f" --replaces {replaces}"
        retry += "".join(f" --range {category}" for category in made["ranges"])
        home = getattr(store, "home", None)
        if home is not None:
            retry = f"MASCOT_WARDROBE_HOME={shlex.quote(str(home))} {retry}"
        raise ValueError(f"{error}; saved piece {made['id']} is kept. Retry: {retry}") from error


def _make_piece(closet, profile, read, write, store=None) -> dict:
    write("--- New piece ---")
    ranges, temperature_c = ask_ranges(read, write, profile)
    category = ranges[0]
    if temperature_c is not None:
        write(
            f"  {temperature_c} °C is the {category.replace('_', ' ')} category for {profile.country_code}."
        )
    style = ask_style(read, write, allow_keep=False)
    palette = ask_palette(read, write, allow_keep=False)
    item_id = _ask_item_id(closet, read, write, _default_id(closet, category, store), store)
    record = closet.add_item(
        item_id,
        category,
        palette,
        style=style,
        description=_description(temperature_c, ranges, profile.country_code),
    )
    made = _summary(closet, item_id, record, temperature_c, ranges)
    _register(closet, made, store)
    _show(write, made)
    try:
        while ask_yes_no(read, write, "Revise this piece?"):
            _revise(closet, profile, read, write, made, store)
    except EndOfInput as error:
        raise ValueError(f"end of input; saved piece is kept: {item_id}") from error
    return made


def _revise(closet, profile, read, write, made: dict, store=None) -> None:
    """Change only ``made``: a blank or "keep" answer keeps the stored value."""
    new_ranges = ask_ranges(read, write, profile, allow_keep=True)
    style = ask_style(read, write, allow_keep=True)
    palette = ask_palette(read, write, allow_keep=True)
    template = description = None
    temperature_c, ranges = made["temperature_c"], tuple(made["ranges"])
    if new_ranges is not KEEP:
        ranges, temperature_c = new_ranges
        template = ranges[0]
        description = _description(temperature_c, ranges, profile.country_code)
    previous_sha256 = closet.items()[made["id"]]["sha256"]
    try:
        record = closet.edit_item(
            made["id"],
            template=template,
            style=None if style is KEEP else style,
            palette=None if palette is KEEP else palette,
            description=description,
        )
    except ValueError as error:
        if "Nothing to change" not in str(error):
            raise
        write("Nothing to change: the piece stays as it is.")
        return
    revisions = made["revisions"] + 1
    made.update(_summary(closet, made["id"], record, temperature_c, ranges), revisions=revisions)
    _register(closet, made, store, replaces=previous_sha256)
    _show(write, made)
