r"""
Evennia settings file.

The available options are found in the default settings file found
here:

https://www.evennia.com/docs/latest/Setup/Settings-Default.html

Remember:

Don't copy more from the default file than you actually intend to
change; this will make sure that you don't overload upstream updates
unnecessarily.

When changing a setting requiring a file system path (like
path/to/actual/file.py), use GAME_DIR and EVENNIA_DIR to reference
your game folder and the Evennia library folders respectively. Python
paths (path.to.module) should be given relative to the game's root
folder (typeclasses.foo) whereas paths within the Evennia library
needs to be given explicitly (evennia.foo).

If you want to share your game dir, including its settings, you can
put secret game- or server-specific settings in secret_settings.py.

"""

# Use the defaults from Evennia unless explicitly overridden
from evennia.settings_default import *

######################################################################
# Evennia base server config
######################################################################

# This is the name of your game. Make it catchy!
SERVERNAME = "game"
# COMM-04A records this configured deployment label, never environment details.
# Production deployments should override it in secret_settings.py or deployment
# configuration with a release identifier such as ``2026.09.21``.
GAME_BUILD_ID = "development"
# COMM-05's ``info`` and ``credits`` expose only this explicitly public,
# source-controlled metadata. Deployments may override wording, never secrets.
GAME_PUBLIC_INFO = {
    "game": SERVERNAME,
    "release": GAME_BUILD_ID,
    "version": "Evennia",
    "transports": "telnet and web client",
    "rules": "SRD-inspired fantasy adventure",
    "help": "help <topic>",
    "contact": "Contact a staff member in game.",
}
GAME_CREDITS = (
    "This game is built with Evennia.\n"
    "Rules inspiration includes the System Reference Document (SRD).\n"
    "See the project source and in-game staff for additional credits."
)

# Character creation: new accounts go to OOC screen; charcreate runs the EvMenu wizard.
# Account #1 (superuser) still gets a character via initial_setup.py regardless of this flag.
AUTO_CREATE_CHARACTER_WITH_ACCOUNT = False
AUTO_PUPPET_ON_LOGIN = False
MAX_NR_CHARACTERS = 1
# Accounts may have several connected clients, but WORLD-04 permits only one
# of them to control the account's sole character at a time.
MULTISESSION_MODE = 2
MAX_NR_SIMULTANEOUS_PUPPETS = 1
# COMM-01C deliberately replaces Evennia's catch-all Public channel with the
# two curated account channels reconciled at server start.
BASE_CHANNEL_TYPECLASS = "typeclasses.channels.Channel"
DEFAULT_CHANNELS = [
    {
        "key": "OOC",
        "aliases": ("ooc",),
        "desc": "Out-of-character discussion",
        "locks": "control:perm(Admin);listen:all();send:all()",
    },
    {
        "key": "Newbie",
        "aliases": ("newbie", "new"),
        "desc": "Questions and help for new players",
        "locks": "control:perm(Admin);listen:all();send:all()",
    },
]
CHARGEN_MENU = "world.chargen_menu"
SERVER_SESSION_CLASS = "server.conf.serversession.ServerSession"

# Base class for Evennia's default and auto-generated (exit/movement) commands.
# Our MuxCommand adds the persistent-prompt hook so the build editor's prompt
# stays visible after every command, not just our own. See commands/command.py.
COMMAND_DEFAULT_CLASS = "commands.command.MuxCommand"

# One one-second heartbeat owns all recurring live-world work. Lane cadences
# are heartbeat counts, so the effect lane is currently one six-second SRD
# round while slower systems remain independently tunable.
GAME_PULSE_INTERVAL_SECONDS = 1
GAME_PULSE_CADENCES = {
    "combat": 2,
    "recovery": 60,
    "mobiles": 10,
    "effects": 6,
    "corpses": 60,
    "world_time": 60,
    "weather": 300,
    "resets": 60,
    "actions": 1,
    "objects": 60,
}
# ENV-01 starts at noon, year 1. Scale changes require a new version and
# systems.world_clock.reconcile_clock() from the staff shell.
GAME_CLOCK_EPOCH_MINUTE = 720
GAME_CLOCK_REAL_SECONDS_PER_HOUR = 600
GAME_CLOCK_SCALE_VERSION = 1

# ENV-02's bounded temperate profile. Values are consumed only by weather.py.
GAME_WEATHER_TRANSITION_TOKENS = 1
GAME_WEATHER_RAIN_PERCEPTION_PENALTY = 2
GAME_WEATHER_SEVERE_PERCEPTION_PENALTY = 5
GAME_WEATHER_RAIN_TRAVEL_MULTIPLIER = 1.25
GAME_WEATHER_STORM_TRAVEL_MULTIPLIER = 1.5

# INTERACT-06's independently configurable delayed-interaction heartbeat.
GAME_ACTION_AUDIT_LIMIT = 20
# MAGIC-03 uses the one-minute recovery lane as its real-time clock. These
# compressed durations represent one in-world hour per ten real minutes.
MAGIC_SHORT_REST_RECOVERY_PULSES = 10
MAGIC_LONG_REST_RECOVERY_PULSES = 80
MAGIC_LONG_REST_SLEEP_PULSES = 60
# Combat action clocks are measured in combat-pulse tokens. CharacterStats
# adjusts this base cadence through its Reaction-derived ``combat_delay`` API.
GAME_COMBAT_BASE_DELAY = 1.0
# Ordered upper bounds for COMBAT-09's target-vs-observer live threat ratio.
# COMBAT-10 calibration targets the upper bounds in the paired simulated target
# win-rate bands below: trivial <=10%, easy <=25%, even <=55%, dangerous <=75%,
# deadly <=90%, and overwhelming above 90%. They remain server policy, not
# player-visible target statistics.
COMBAT_CONSIDER_THRESHOLDS = (0.25, 0.5, 1.25, 2.0, 4.0)
COMBAT_CONSIDER_WIN_RATE_BOUNDS = (0.10, 0.25, 0.55, 0.75, 0.90)
# COMBAT-06 resolves this stable ``area:room_key`` pair at entry. Set it in
# secret_settings.py for each deployed world; an unset/ambiguous value fails
# closed for dead-character entry rather than guessing from a mutable home.
COMBAT_RESPAWN_SANCTUARY = None
# INTERACT-02C keeps these roles independent even when both reference one room.
CHARACTER_START_ROOM = None
RECALL_DESTINATION = None
INTERACTION_INTERVAL_ACTIONS = 1
COMBAT_LINKDEAD_MINUTES = 30

# RULES-05 limits recursive carried objects independently of weight.  Builders
# may override this per character/NPC with ``carry_item_limit`` when needed.
CARRIED_ITEM_LIMIT = 100
MAX_CONTAINER_NESTING = 20
# ITEM-02 wallet and per-owner idempotency/audit policy.
GAME_MAX_CURRENCY = 2_000_000_000
GAME_CURRENCY_LEDGER_LIMIT = 100
# ITEM-06 note limits, in characters after sanitization and normalization.
NOTE_TITLE_MAX_LENGTH = 80
NOTE_BODY_MAX_LENGTH = 2000
# COMBAT-05 converts these policy durations to durable corpse-lane pulse
# counts at creation. NPC prototypes may set ``corpse_decay_minutes``; PC
# duration is deliberately global policy.
NPC_CORPSE_DECAY_MINUTES = 10
PC_CORPSE_DECAY_MINUTES = 30
# GROUP-03B protects an NPC's death-time credited roster before its ordinary
# corpse lifetime and decay policy take over.
NPC_CORPSE_LOOT_RESERVATION_MINUTES = 2
GLOBAL_SCRIPTS = {
    "game_pulse": {
        "typeclass": "typeclasses.scripts.GamePulseScript",
        "interval": GAME_PULSE_INTERVAL_SECONDS,
        "repeats": 0,
        "start_delay": True,
        "persistent": True,
        "desc": "Central scheduler for recurring live-world systems.",
    }
}


######################################################################
# Settings given in secret_settings.py override those in this file.
######################################################################
try:
    from server.conf.secret_settings import *
except ImportError:
    print("secret_settings.py file not found or failed to import.")
