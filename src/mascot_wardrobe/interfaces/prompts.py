"""Interactive style and palette prompts.

Prompts and their echo go through the injected ``write`` (stderr in the CLI), so stdout stays
machine-readable JSON. Input comes from the injected ``read``. Nothing here uses the network or a
model; "let the Wardrobe generator choose" only returns ``"auto"``, resolved later by a
deterministic local function.
"""

from __future__ import annotations

from mascot_wardrobe.domain.models import Category, validate_temperature
from mascot_wardrobe.wardrobe.styles import AUTO, STYLES, validate_palette, validate_style

KEEP = object()  # sentinel: keep the stored value (edits only)
ATTEMPTS = 3


class EndOfInput(ValueError):
    """The input stream ended during a prompt; never treated as a default."""


def is_interactive(no_input, stdin, stderr) -> bool:
    """Interactive only when stdin and stderr are both terminals and ``--no-input`` is absent."""
    if no_input:
        return False
    return all(callable(getattr(stream, "isatty", None)) and stream.isatty() for stream in (stdin, stderr))


def _read(read, prompt):
    try:
        return read(prompt)
    except EOFError as exc:
        raise EndOfInput("end of input while waiting for an answer; nothing was written") from exc


def _ask(read, write, noun, hint, parse, allow_keep):
    write(f"Choose the {noun}:")
    write(f"  1) supply my own ({hint})")
    write(f"  2) let the Wardrobe generator choose ({AUTO}: deterministic, local)")
    if allow_keep:
        write(f"  3) keep current {noun}")
    for _ in range(ATTEMPTS):
        answer = _read(read, f"{noun}> ").strip()
        if answer == "1":
            for _ in range(ATTEMPTS):
                text = _read(read, f"{noun} value> ")
                try:
                    return parse(text)
                except ValueError as error:
                    write(f"  {error}")
            raise ValueError(f"No valid {noun} after {ATTEMPTS} attempts.")
        if answer == "2":
            return AUTO
        if answer == "3" and allow_keep:
            return KEEP
        write(f"  Answer 1, 2{' or 3' if allow_keep else ''}.")
    raise ValueError(f"No valid answer for the {noun} after {ATTEMPTS} attempts.")


def ask_style(read, write, *, allow_keep):
    """Return a style name, ``"auto"`` or ``KEEP``."""
    return _ask(
        read, write, "style", ", ".join(STYLES), lambda text: validate_style(text.strip()), allow_keep
    )


def ask_palette(read, write, *, allow_keep):
    """Return a list of 1 to 6 hex colors, ``"auto"`` or ``KEEP``."""
    return _ask(
        read,
        write,
        "palette",
        "1 to 6 #RRGGBB colors separated by spaces",
        lambda text: list(validate_palette(text.split())),
        allow_keep,
    )


def ask_temperature(read, write, *, allow_keep=False):
    """Return the intended use temperature in Celsius, or ``KEEP`` on a blank answer (edits only)."""
    write(
        "Intended use temperature in Celsius (for example 12 or -3.5)"
        + (", blank keeps it:" if allow_keep else ":")
    )
    for _ in range(ATTEMPTS):
        text = _read(read, "temperature> ").strip()
        if allow_keep and not text:
            return KEEP
        try:
            return validate_temperature(float(text))
        except ValueError as error:
            write(f"  Enter a number of degrees Celsius ({error}).")
    raise ValueError(f"No valid temperature after {ATTEMPTS} attempts.")


def ask_ranges(read, write, profile, *, allow_keep=False):
    """Return named suitability ranges and optional Celsius input, or ``KEEP`` for edits."""
    write(
        "Intended use: Celsius or comma-separated named ranges" + ("; blank keeps it:" if allow_keep else ":")
    )
    categories = tuple(category.value for category in Category)
    for index, category in enumerate(categories):
        lower = None if index == 0 else profile.thresholds_c[index - 1]
        upper = None if index == len(categories) - 1 else profile.thresholds_c[index]
        boundary = f"< {upper:g}" if lower is None else f">= {lower:g}"
        if lower is not None and upper is not None:
            boundary = f"{lower:g} to < {upper:g}"
        write(f"  {category}: {boundary} C")
    for _ in range(ATTEMPTS):
        text = _read(read, "temperature> ").strip()
        if allow_keep and not text:
            return KEEP
        tokens = tuple(token.strip() for token in text.split(","))
        try:
            if len(tokens) == 1:
                try:
                    numeric = float(text)
                except ValueError:
                    pass
                else:
                    temperature = validate_temperature(numeric)
                    return (profile.classify(temperature).value,), temperature
            if tokens and all(token in categories for token in tokens):
                return tuple(dict.fromkeys(tokens)), None
            if len(tokens) > 1 and any(token in categories for token in tokens):
                raise ValueError("Mixing Celsius values and named ranges is not allowed.")
            raise ValueError("Enter Celsius or valid named ranges; no numeric indices or unit suffixes.")
        except ValueError as error:
            write(f"  {error}")
    raise ValueError(f"No valid temperature or named range after {ATTEMPTS} attempts.")


def ask_range_name(read, write, ranges):
    """Ask for exactly one named range from the offered categories."""
    choices = tuple(row["range"] if isinstance(row, dict) else row for row in ranges)
    for _ in range(ATTEMPTS):
        answer = _read(read, "range> ").strip()
        if answer in choices:
            return answer
        write("  Enter one named range: " + ", ".join(choices))
    raise ValueError(f"No valid named range after {ATTEMPTS} attempts.")


def ask_number(read, write, valid_numbers):
    """Ask for the visible garment number; never infer or choose one."""
    valid = set(valid_numbers)
    for _ in range(ATTEMPTS):
        text = _read(read, "number> ").strip()
        try:
            number = int(text) if text.isascii() and text.isdecimal() else None
        except ValueError:
            number = None
        if number in valid:
            return number
        write("  Enter an existing garment number from the full list.")
    raise ValueError(f"No valid garment number after {ATTEMPTS} attempts.")


def ask_yes_no(read, write, question, *, default=False):
    """Ask a yes/no question; a blank answer is ``default``, end of input never is."""
    for _ in range(ATTEMPTS):
        answer = _read(read, f"{question} [{'Y/n' if default else 'y/N'}] ").strip().lower()
        if not answer:
            return default
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        write("  Answer yes or no.")
    raise ValueError(f"No valid answer (yes or no) after {ATTEMPTS} attempts.")
