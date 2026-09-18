"""Staff diagnostics for the ITEM-07A starting-package registry."""

from __future__ import annotations

from commands.command import MuxCommand
from systems.starting_packages import (
    describe_package,
    reset_starting_package_registry,
    starting_package_registry,
)

_USAGE = "Usage: startpackages [<class or background>] | startpackages/check"


class CmdStartPackages(MuxCommand):
    """
    Check the starting-equipment registry while you author it.

    Usage:
      startpackages
      startpackages <class or background>
      startpackages/check

    With no argument, shows whether every class and background package
    validates, and lists each problem to fix. Naming a class or background
    previews its package text, even while other packages are incomplete.
    The /check switch revalidates now, for example after deleting a database
    prototype that shadowed a module prototype. Edits to the package or
    prototype modules need a server reload. See help building starting
    packages.
    """

    key = "startpackages"
    aliases = ["startpackage"]
    locks = "cmd:perm(Builder)"
    help_category = "Builder"

    def func(self) -> None:
        """Show registry status or one package preview."""
        if self.switches and self.switches != ["check"]:
            self.msg(_USAGE)
            return
        if self.switches:
            reset_starting_package_registry()
        registry = starting_package_registry()
        name = self.args.strip()
        if name:
            self.msg(self._preview(name))
            return
        status = "|gcomplete|n" if registry.complete else "|rincomplete|n"
        lines = [
            f"|wStarting packages|n v{registry.version} {status} "
            f"({registry.fingerprint[:12] or 'no fingerprint'})",
            f"Valid packages: {len(registry.classes)} class, "
            f"{len(registry.backgrounds)} background; "
            f"{len(registry.items)} validated item prototype(s).",
        ]
        if registry.diagnostics:
            lines.append(f"{len(registry.diagnostics)} problem(s):")
            lines.extend(f"  - {problem}" for problem in registry.diagnostics)
        self.msg("\n".join(lines))

    @staticmethod
    def _preview(name: str) -> str:
        """Match a class or background case-insensitively and describe it."""
        registry = starting_package_registry()
        for source, packages in (
            ("class", registry.classes),
            ("background", registry.backgrounds),
        ):
            for owner, package in packages.items():
                if owner.casefold() == name.casefold():
                    text = describe_package(
                        source, owner, registry=registry, require_complete=False
                    )
                    lines = [
                        f"|w{owner}|n ({source}) — {package.srd_reference}",
                        text,
                    ]
                    if package.adaptation:
                        lines.append(f"Adaptation: {package.adaptation}")
                    return "\n".join(lines)
        return f"No valid class or background package named '{name}'."
