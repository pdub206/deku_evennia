"""Player commands for local, finite-wallet shops."""

from __future__ import annotations

import re
from typing import Any
from uuid import uuid4

from commands.command import Command
from systems.shops import (
    ShopError,
    current_shop_hour,
    shop_availability,
    shop_price,
    shop_profile,
    trade_eligible,
    trade_shop,
    visible_stock,
)
from systems.visibility import room_visibility, target_visibility


def resolve_shop(actor: Any, keyword: str = "") -> Any:
    """Resolve one visible eligible local shop using Evennia names and aliases."""
    if actor.location is None or not room_visibility(actor, actor.location).visible:
        raise ShopError("You cannot see a shop here.")
    shops = []
    for npc in actor.location.contents:
        try:
            profile = shop_profile(npc)
            if (
                target_visibility(actor, npc).visible
                and shop_availability(
                    npc, actor, profile, current_shop_hour()
                ).available
            ):
                shops.append(npc)
        except (ValueError, TypeError):
            continue
    if keyword:
        shops = actor.search(keyword, candidates=shops, quiet=True)
    if not shops:
        raise ShopError("No matching open shop is available here.")
    if len(shops) != 1:
        raise ShopError("Which shopkeeper? Use their name or keywords.")
    return shops[0]


class _ShopCommand(Command):
    """Parse optional service-specific prepositions and share safe failures."""

    help_category = "Shops"
    preposition = "at"

    def parse(self) -> None:
        """Keep item keywords intact and bound hostile command input."""
        raw = self.args.strip()
        self.item_query = ""
        self.shop_query = ""
        self.invalid = len(raw) > 200 or not raw
        if not self.invalid:
            parts = re.split(
                rf"\s+(?:{self.preposition}|at)(?:\s+|$)",
                raw,
                maxsplit=1,
                flags=re.IGNORECASE,
            )
            self.item_query = parts[0].strip()
            self.shop_query = parts[1].strip() if len(parts) == 2 else ""
            self.invalid = not self.item_query or (
                len(parts) == 2 and not self.shop_query
            )
        if not getattr(self, "transaction_id", None):
            self.transaction_id = str(uuid4())

    def func(self) -> None:
        """Resolve the exact live item; quotes never reserve stock or wallets."""
        if self.invalid or self.item_query.casefold() == "all":
            self.msg(f"Usage: {self.key} <item> [{self.preposition} shopkeeper]")
            return
        try:
            npc = resolve_shop(self.caller, self.shop_query)
            buying = self.key == "buy"
            candidates = (
                visible_stock(npc, self.caller)
                if buying
                else [
                    obj
                    for obj in self.caller.contents
                    if target_visibility(self.caller, obj, source=self.caller).visible
                ]
            )
            matches = self.caller.search(
                self.item_query, candidates=candidates, quiet=True
            )
            if not matches:
                raise ShopError("That item is unavailable.")
            if len(matches) > 1:
                # Identical shop copies are fungible for selection, but the service
                # still reserves and transfers exactly the selected live object.
                if (
                    not buying
                    or len(
                        {
                            (obj.key, shop_price(obj, shop_profile(npc), buying=True))
                            for obj in matches
                        }
                    )
                    > 1
                ):
                    raise ShopError("Which item? Use a more specific keyword.")
            item = sorted(matches, key=lambda obj: obj.id)[0]
            profile = shop_profile(npc)
            trade_eligible(item, self.caller, profile, buying=buying)
            if self.key == "value":
                price = shop_price(item, profile, buying=False)
                self.msg(
                    f"{npc.get_display_name(self.caller)} offers {price} coins for {item.get_display_name(self.caller)}."
                )
                return
            result = trade_shop(
                self.caller,
                npc,
                item,
                buying=buying,
                transaction_id=self.transaction_id,
            )
            if not result.success and not result.repeated:
                self.msg(result.reason)
        except ShopError as exc:
            self.msg(str(exc))


class CmdList(Command):
    """List actual visible stock and its final price: list [shopkeeper]."""

    key = "list"
    help_category = "Shops"

    def func(self) -> None:
        """Group stock by visible name and price without spawning or reserving it."""
        try:
            if len(self.args) > 200:
                raise ShopError("Usage: list [shopkeeper]")
            npc = resolve_shop(self.caller, self.args.strip())
            profile = shop_profile(npc)
            rows = {}
            for item in visible_stock(npc, self.caller):
                row = (
                    item.get_display_name(self.caller),
                    shop_price(item, profile, buying=True),
                )
                rows[row] = rows.get(row, 0) + 1
            text = [f"{npc.get_display_name(self.caller)}'s stock:"]
            text.extend(
                f"{name} — {quantity} available — {price} coins"
                for (name, price), quantity in rows.items()
            )
            self.msg("\n".join(text) if rows else "That shop has no stock available.")
        except ShopError as exc:
            self.msg(str(exc))


class CmdValue(_ShopCommand):
    """Quote a carried item without reserving a sale: value <item> [at shopkeeper]."""

    key = "value"


class CmdBuy(_ShopCommand):
    """Buy one live stock item: buy <item> [from shopkeeper]."""

    key = "buy"
    preposition = "from"


class CmdSell(_ShopCommand):
    """Sell one eligible carried item: sell <item> [to shopkeeper]."""

    key = "sell"
    preposition = "to"
