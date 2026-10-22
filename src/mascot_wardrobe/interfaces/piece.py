"""Interactive one-piece-at-a-time session.

Every piece asks its own temperature, style and palette (never inherited from the previous piece),
renders and saves only that item, lets the user revise it, then asks whether to make another. Prompts
and their echo go through ``write`` (stderr in the CLI). Rendering is the local deterministic
compositor: no network, no model.
"""

from __future__ import annotations

from mascot_wardrobe.closet import _name
from mascot_wardrobe.interfaces.prompts import (
    KEEP,
    EndOfInput,
    ask_palette,
    ask_style,
    ask_temperature,
    ask_yes_no,
)

ATTEMPTS = 3


def _slug(category: str) -> str:
    return category.replace("_", "-")


def _default_id(closet, category: str) -> str:
    taken = closet.items()
    number = 1
    while f"{_slug(category)}-{number}" in taken:
        number += 1
    return f"{_slug(category)}-{number}"


def _ask_item_id(closet, read, write, default: str) -> str:
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
        return answer
    raise ValueError(f"No valid item id after {ATTEMPTS} attempts.")


def _description(temperature_c: float, category: str, country: str) -> str:
    return f"Intended for {temperature_c} °C ({category.replace('_', ' ')}, {country} profile)."


def _summary(closet, item_id: str, record: dict, temperature_c: float, category: str) -> dict:
    return {
        "id": item_id,
        "path": str(closet.item_path(item_id)),
        "template": record["template"],
        "category": category,
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
    write(f"  preview: {made['path']}")
    write(f"  temperature: {made['temperature_c']} °C -> {made['category'].replace('_', ' ')}")
    write(f"  style: {made['style']} ({made['style_choice']})")
    write(f"  palette: {' '.join(made['palette'])} ({made['palette_choice']})")
    write(f"  sha256: {made['sha256']}")
    write("  generator: local compositor, no remote inference")


def run_piece_session(closet, profile, read, write, *, wardrobe=None) -> dict:
    """Run the loop and return the JSON-ready result; ``wardrobe`` names an optional final wardrobe."""
    pieces: list[dict] = []
    try:
        while True:
            pieces.append(_make_piece(closet, profile, read, write))
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


def _make_piece(closet, profile, read, write) -> dict:
    write("--- New piece ---")
    temperature_c = ask_temperature(read, write)
    category = profile.classify(temperature_c).value
    write(f"  {temperature_c} °C is the {category.replace('_', ' ')} category for {profile.country_code}.")
    style = ask_style(read, write, allow_keep=False)
    palette = ask_palette(read, write, allow_keep=False)
    item_id = _ask_item_id(closet, read, write, _default_id(closet, category))
    record = closet.add_item(
        item_id,
        category,
        palette,
        style=style,
        description=_description(temperature_c, category, profile.country_code),
    )
    made = _summary(closet, item_id, record, temperature_c, category)
    _show(write, made)
    while ask_yes_no(read, write, "Revise this piece?"):
        _revise(closet, profile, read, write, made)
    return made


def _revise(closet, profile, read, write, made: dict) -> None:
    """Change only ``made``: a blank or "keep" answer keeps the stored value."""
    new_temperature = ask_temperature(read, write, allow_keep=True)
    style = ask_style(read, write, allow_keep=True)
    palette = ask_palette(read, write, allow_keep=True)
    template = description = None
    temperature_c, category = made["temperature_c"], made["category"]
    if new_temperature is not KEEP:
        temperature_c = new_temperature
        category = profile.classify(temperature_c).value
        template = category
        description = _description(temperature_c, category, profile.country_code)
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
    made.update(_summary(closet, made["id"], record, temperature_c, category), revisions=revisions)
    _show(write, made)
