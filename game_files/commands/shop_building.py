"""Sticky shop editor bound to one live NPC."""

from __future__ import annotations

import json
from typing import Any

from commands.building import (
    BuildModeCmdSet,
    CmdBuildDone,
    _BuildCommand,
    _enter_build_mode,
    _exit_build_mode,
)
from evennia import CmdSet
from systems.currency import balance
from systems.mobile_specials import mobile_specials, set_mobile_specials
from systems.shops import ShopError, set_shop_profile, shop_profile, shop_snapshot

SHOP_FIELDS = {
    "profile_key": "stable shop identity",
    "access_lock": "Evennia shop: access lock",
    "accepted_kinds": 'JSON item kinds, e.g. ["weapon", "armor"]',
    "buy_markup": "buy price percentage, rounded up (0–1000)",
    "sell_markdown": "sell price percentage, rounded down (0–buy markup)",
    "open_hour": "inclusive opening hour (0–23)",
    "close_hour": "exclusive closing hour (0–23); overnight hours supported",
    "wallet_opening_balance": "initial wallet only; editing never refills coins",
    "stock": 'JSON entries: [{"prototype_key": "blade", "target_quantity": 2}]',
}


def edit_shop(caller: Any, raw: str, *, create: bool = False) -> None:
    """Attach or edit a live NPC shop using the standard builder object search."""
    reference = raw.strip()
    if not reference:
        caller.msg("Usage: edit new shop <npc> / edit shop <npc>")
        return
    npc = caller.search(reference, global_search=True)
    if npc is None:
        return
    if (
        not npc.is_typeclass("typeclasses.characters.Character", exact=False)
        or npc.db.is_player_character is not False
    ):
        caller.msg("Shops must attach to an NPC.")
        return
    try:
        if create:
            assignment = mobile_specials(npc)
            if any(entry["key"] == "shopkeeper" for entry in assignment["behaviors"]):
                raise ShopError("That NPC already has a shop. Use edit shop <npc>.")
            set_shop_profile(
                npc,
                {
                    "version": 1,
                    "profile_key": f"shop_{npc.id}",
                    "access_lock": "shop:all()",
                    "accepted_kinds": [
                        "item",
                        "weapon",
                        "armor",
                        "container",
                        "key",
                    ],
                    "buy_markup": 125,
                    "sell_markdown": 50,
                    "open_hour": 0,
                    "close_hour": 23,
                    "wallet_opening_balance": 0,
                    "stock": [],
                },
            )
        shop_profile(npc)
    except ValueError as exc:
        caller.msg(str(exc))
        return
    _enter_build_mode(caller, npc)
    caller.cmdset.remove(BuildModeCmdSet)
    caller.cmdset.add(ShopBuildModeCmdSet, persistent=False)
    caller.msg(
        f"Editing shop on {npc.key} (#{npc.id}). Use show, fields, set, transactions, del, done."
    )


class CmdShopShow(_BuildCommand):
    """Display the shop definition separately from current inventory and wallet."""

    key = "show"

    def func(self) -> None:
        """Read current data rather than the editor's stale starting snapshot."""
        try:
            snapshot = shop_snapshot(self.target, shop_profile(self.target))
            self.msg(
                f"Shop on {self.target.key} (#{self.target.id})\nDefinition: {snapshot['definition']}\nLive stock: {snapshot['live_stock']}\nWallet: {balance(self.target)} coins"
            )
        except ValueError as exc:
            self.msg(str(exc))


class CmdShopFields(_BuildCommand):
    """List editable shop fields and their accepted syntax."""

    key = "fields"

    def func(self) -> None:
        """Describe validated configuration without exposing raw lock internals."""
        self.msg(
            "\n".join(
                f"{name}: {description}" for name, description in SHOP_FIELDS.items()
            )
        )


class CmdShopSet(_BuildCommand):
    """Set one shop field, validating the entire resulting profile before saving."""

    key = "set"

    def func(self) -> None:
        """Persist each edit immediately on the NPC and preserve its finite wallet."""
        field, _, raw = self.args.strip().partition(" ")
        field = field.casefold()
        if field not in SHOP_FIELDS:
            self.msg("Usage: set <field> <value>. Use fields for valid fields.")
            return
        try:
            profile = shop_profile(self.target)
            if field in {"profile_key", "access_lock"}:
                value = raw.strip()
            else:
                value = json.loads(raw)
            profile[field] = value
            set_shop_profile(self.target, profile)
        except (ValueError, TypeError) as exc:
            self.msg(f"Invalid shop field: {exc}")
            return
        self.msg(f"Set {field} to: {value}")


class CmdShopTransactions(_BuildCommand):
    """Inspect bounded currency audit entries associated with this shop."""

    key = "transactions"

    def func(self) -> None:
        """Keep staff diagnostics local to the edited shop."""
        entries = self.target.attributes.get("shop_transaction_history", default=[])
        self.msg(
            json.dumps(list(entries)[-20:], indent=2)
            if entries
            else "No shop transactions recorded."
        )


class CmdShopDel(_BuildCommand):
    """Detach this shop after two del commands, preserving the NPC and inventory."""

    key = "del"
    aliases = ["delete"]

    def func(self) -> None:
        """Remove only the shopkeeper behavior; never delete the attached NPC."""
        if self.caller.ndb._build_del_pending is not self.target:
            self.caller.ndb._build_del_pending = self.target
            self.msg("Detach this shop? Type del again to confirm.")
            return
        assignment = mobile_specials(self.target)
        assignment["behaviors"] = [
            entry for entry in assignment["behaviors"] if entry["key"] != "shopkeeper"
        ]
        set_mobile_specials(self.target, assignment)
        self.target.locks.remove("shop")
        _exit_build_mode(self.caller)
        self.msg("Shop detached. NPC, inventory, and wallet preserved.")


class ShopBuildModeCmdSet(CmdSet):
    """Use familiar editor verbs while isolating shop configuration from NPC fields."""

    key = "ShopBuildMode"
    priority = 110
    mergetype = "Union"

    def at_cmdset_creation(self) -> None:
        """Only expose shop verbs under the existing Builder command locks."""
        for command in (
            CmdShopShow,
            CmdShopFields,
            CmdShopSet,
            CmdShopTransactions,
            CmdShopDel,
            CmdBuildDone,
        ):
            self.add(command)
