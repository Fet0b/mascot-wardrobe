"""Active local Hermes PetDex adapter. No runtime import, CLI call or file write until preflight/apply."""

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from mascot_wardrobe.infrastructure.inventory_store import _safe_path
from mascot_wardrobe.wardrobe.petdex import CODEX_STATE_ROWS, validate_atlas


def _slug(value):
    if not isinstance(value, str) or re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,255}", value) is None:
        raise ValueError("Invalid previous or intended Hermes pet slug")
    return value


def _runtime_store(home):
    """Load only on apply, inside the Hermes Python environment; never call pets_dir/load_pet.

    CLI config path identifies the active profile. Scope store registration to that same home.
    Older Hermes releases without a context override must already have HERMES_HOME pinned.
    """
    try:
        import hermes_constants
        from agent.pet.constants import CODEX_STATE_ROWS as runtime_rows
        from agent.pet.store import register_local_pet
    except ImportError as error:
        raise ValueError("Hermes apply needs the Hermes Agent Python environment and pet store") from error

    def register(sheet, **kwargs):
        setter = getattr(hermes_constants, "set_hermes_home_override", None)
        if setter is None:
            if Path(hermes_constants.get_hermes_home()).resolve() != home:
                raise ValueError("Pin HERMES_HOME to the active Hermes profile before applying")
            return register_local_pet(sheet, **kwargs)
        token = setter(home)
        try:
            return register_local_pet(sheet, **kwargs)
        finally:
            hermes_constants.reset_hermes_home_override(token)

    # Fail before reserving directories on older releases with a different profile.
    if not hasattr(hermes_constants, "set_hermes_home_override") and (
        Path(hermes_constants.get_hermes_home()).resolve() != home
    ):
        raise ValueError("Pin HERMES_HOME to the active Hermes profile before applying")
    return register, tuple(runtime_rows)


@dataclass(frozen=True)
class PreparedPet:
    home: Path
    previous: str
    slug: str
    sha256: str
    existing: bool
    environment: dict
    register: object


class HermesPetAdapter:
    def __init__(self, *, runner=None, register=None, state_rows=None, executable="hermes"):
        self.runner = runner or subprocess.run
        self.register = register
        self.state_rows = state_rows
        self.executable = str(executable)

    def _command(self, *arguments, environment=None):
        try:
            result = self.runner(
                [self.executable, *arguments],
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
                env=environment,
            )
            if result.returncode != 0:
                raise RuntimeError(f"Hermes {' '.join(arguments[:2])} failed (exit {result.returncode})")
        except (OSError, subprocess.SubprocessError) as error:
            # Avoid echoing CLI stdout/stderr, which could contain private runtime data.
            raise RuntimeError(f"Hermes {' '.join(arguments[:2])} failed ({type(error).__name__})") from error
        return result.stdout.strip()

    def _selected(self, environment):
        value = self._command("config", "get", "display.pet.slug", environment=environment)
        if value.startswith('"'):
            try:
                value = json.loads(value)
            except json.JSONDecodeError as error:
                raise ValueError("Invalid previous Hermes slug readback") from error
        return _slug(value)

    @staticmethod
    def _sheet(home, slug):
        folder = _safe_path(home / "pets" / _slug(slug))
        if not folder.is_dir():
            raise ValueError("Hermes pet is not installed; previous slug must be restorable")
        metadata = _safe_path(folder / "pet.json")
        if not metadata.is_file() or metadata.stat().st_size > 65536:
            raise ValueError("Occupied existing Hermes pet has missing/invalid metadata")
        try:
            data = json.loads(metadata.read_text(encoding="utf-8"))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise ValueError("Occupied existing Hermes pet has invalid metadata") from error
        name = data.get("spritesheetPath") if isinstance(data, dict) else None
        if not isinstance(name, str) or Path(name).name != name or name in (".", ".."):
            raise ValueError("Occupied existing Hermes pet has an unsafe sheet path")
        sheet = _safe_path(folder / name)
        if not sheet.is_file() or sheet.stat().st_size > 16 * 1024 * 1024:
            raise ValueError("Occupied existing Hermes pet has no valid sheet")
        return sheet

    def preflight(self, plan):
        """Read-only checks before *any* gateway writes; dry-run must never call this method."""
        validate_atlas(plan.png, rows=plan.rows)
        config = Path(self._command("config", "path"))
        if not config.is_absolute() or config.name != "config.yaml":
            raise ValueError("Hermes config path did not identify an absolute profile path")
        home = _safe_path(config.parent)
        if not home.is_dir():
            raise ValueError("Hermes profile home must already exist")
        environment = {**os.environ, "HERMES_HOME": str(home)}
        previous = self._selected(environment)
        self._sheet(home, previous)
        register, rows = (
            (self.register, self.state_rows) if self.register is not None else _runtime_store(home)
        )
        if tuple(rows or ()) != CODEX_STATE_ROWS:
            raise ValueError("Installed Hermes PetDex row taxonomy mismatch")
        folder = _safe_path(home / "pets" / plan.slug)
        existing = folder.exists()
        if existing and self._sheet(home, plan.slug).read_bytes() != plan.png:
            raise ValueError("Refusing to overwrite a different existing Hermes pet at the digest slug")
        return PreparedPet(home, previous, plan.slug, plan.sha256, existing, environment, register)

    def apply(self, plan, *, prepared=None):
        prepared = prepared or self.preflight(plan)
        if (prepared.slug, prepared.sha256) != (plan.slug, plan.sha256):
            raise ValueError("Hermes plan changed after preflight")
        folder = _safe_path(prepared.home / "pets" / plan.slug)
        # Recheck immediately before use. Exclusive reservation prevents two cooperating publishers
        # from invoking Hermes' overwrite-capable registration on the same slug.
        if prepared.existing:
            if self._sheet(prepared.home, plan.slug).read_bytes() != plan.png:
                raise ValueError("Occupied existing Hermes pet changed after preflight")
        else:
            folder.parent.mkdir(parents=True, exist_ok=True)
            folder.mkdir(exist_ok=False)
            prepared.register(
                plan.png,
                slug=plan.slug,
                display_name="Closet weather look",
                description=f"Saved closet item sha256:{plan.source_sha256}",
            )
            if self._sheet(prepared.home, plan.slug).read_bytes() != plan.png:
                raise RuntimeError("Hermes registration readback differs from the planned atlas")
        if prepared.previous == plan.slug:
            return {"action": "unchanged", "slug": plan.slug, "verified": True}
        try:
            self._command("pets", "select", plan.slug, environment=prepared.environment)
            if self._selected(prepared.environment) != plan.slug:
                raise RuntimeError("Hermes selection readback mismatch")
        except (ValueError, RuntimeError) as error:
            try:
                self._command("pets", "select", prepared.previous, environment=prepared.environment)
                if self._selected(prepared.environment) != prepared.previous:
                    raise RuntimeError("previous slug readback mismatch")
            except (ValueError, RuntimeError) as rollback:
                raise RuntimeError(f"Hermes selection failed; rollback failed: {rollback}") from error
            raise RuntimeError("Hermes selection failed; previous slug restored and verified") from error
        return {"action": "selected", "slug": plan.slug, "previous_slug": prepared.previous, "verified": True}

    def rollback(self, prepared):
        """Restore and verify the selection saved during preflight; preserve all pet files."""
        self._sheet(prepared.home, prepared.previous)
        self._command("pets", "select", prepared.previous, environment=prepared.environment)
        if self._selected(prepared.environment) != prepared.previous:
            raise RuntimeError("Hermes rollback readback mismatch")
        return {"verified": True, "slug": prepared.previous}
