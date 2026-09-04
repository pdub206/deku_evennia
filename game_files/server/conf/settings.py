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

# Character creation: new accounts go to OOC screen; charcreate runs the EvMenu wizard.
# Account #1 (superuser) still gets a character via initial_setup.py regardless of this flag.
AUTO_CREATE_CHARACTER_WITH_ACCOUNT = False
AUTO_PUPPET_ON_LOGIN = False
MAX_NR_CHARACTERS = 5
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
}
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
