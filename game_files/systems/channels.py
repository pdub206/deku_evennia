"""COMM-01C's curated account-channel bootstrap and history policy."""

from __future__ import annotations

from typing import Any

from evennia.accounts.models import AccountDB
from evennia.comms.models import ChannelDB
from evennia.server.models import ServerConfig
from evennia.utils import create, logger
from systems.communication import ignored_by

CHANNEL_SPECS = {
    "OOC": {
        "aliases": ("ooc",),
        "desc": "Out-of-character discussion",
    },
    "Newbie": {
        "aliases": ("newbie", "new"),
        "desc": "Questions and help for new players",
    },
}
CHANNEL_LOCKS = "control:perm(Admin);listen:all();send:all()"
CHANNEL_HISTORY_LIMIT = 200
_SUBSCRIPTION_CONFIG_KEY = "comm_01c_existing_accounts_subscribed"


def released_channels() -> list[Any]:
    """Return the released channels in stable player-facing order."""
    channels: list[Any] = []
    for key in CHANNEL_SPECS:
        matches = list(ChannelDB.objects.filter(db_key__iexact=key))
        if len(matches) == 1:
            channels.append(matches[0])
    return channels


def _exact_channels(key: str) -> list[Any]:
    """Find key matches without treating aliases as canonical channels."""
    return list(ChannelDB.objects.filter(db_key__iexact=key).order_by("id"))


def _create_channel(key: str) -> Any:
    """Create one released channel with its fixed access policy."""
    spec = CHANNEL_SPECS[key]
    return create.create_channel(
        key,
        aliases=spec["aliases"],
        desc=spec["desc"],
        locks=CHANNEL_LOCKS,
        typeclass="typeclasses.channels.Channel",
    )


def reconcile_channels() -> list[str]:
    """Idempotently migrate stock Public and provision the two released channels.

    Collisions are reported, never merged or deleted: an Admin can repair the
    database deliberately instead of an automatic startup task guessing.
    """
    reports: list[str] = []
    ooc = _exact_channels("OOC")
    public = _exact_channels("Public")
    if len(ooc) > 1 or len(public) > 1 or (ooc and public):
        reports.append("COMM-01C channel collision; Admin repair is required.")
    elif not ooc and len(public) == 1:
        channel = public[0]
        channel.key = "OOC"
        channel.save(update_fields=["db_key"])
        channel.swap_typeclass(
            "typeclasses.channels.Channel", clean_attributes=False, run_start_hooks=None
        )
        reports.append("COMM-01C renamed stock Public channel to OOC.")

    for key in CHANNEL_SPECS:
        matches = _exact_channels(key)
        if len(matches) == 0:
            _create_channel(key)
            reports.append(f"COMM-01C created {key} channel.")
        elif len(matches) > 1:
            reports.append(f"COMM-01C {key} collision; Admin repair is required.")
        else:
            matches[0].swap_typeclass(
                "typeclasses.channels.Channel",
                clean_attributes=False,
                run_start_hooks=None,
            )

    channels = released_channels()
    if len(channels) == len(CHANNEL_SPECS) and not ServerConfig.objects.conf(
        _SUBSCRIPTION_CONFIG_KEY
    ):
        for account in AccountDB.objects.all():
            for channel in channels:
                if not channel.has_connection(account):
                    channel.connect(account)
        ServerConfig.objects.conf(_SUBSCRIPTION_CONFIG_KEY, True)
        reports.append("COMM-01C subscribed existing accounts to released channels.")

    for report in reports:
        logger.log_sec(report)
    return reports


def record_history(channel: Any, sender: Any, text: str) -> None:
    """Append one audited-format-independent entry and retain only 200."""
    history = list(channel.db.comm_history or [])
    history.append({"sender": str(sender.key), "text": text})
    channel.db.comm_history = history[-CHANNEL_HISTORY_LIMIT:]


def visible_to(recipient: Any, sender: Any) -> bool:
    """Keep an account's ignore list independent of its channel mute state."""
    return not ignored_by(recipient, sender)
