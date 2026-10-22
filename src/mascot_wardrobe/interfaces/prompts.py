"""Interactive style and palette prompts.

Prompts and their echo go through the injected ``write`` (stderr in the CLI), so stdout stays
machine-readable JSON. Input comes from the injected ``read``. Nothing here uses the network or a
model; "let the Wardrobe generator choose" only returns ``"auto"``, resolved later by a
deterministic local function.
"""

from __future__ import annotations

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
