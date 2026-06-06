"""
File-based help entries. These complements command-based help and help entries
added in the database using the `sethelp` command in-game.

Control where Evennia reads these entries with `settings.FILE_HELP_ENTRY_MODULES`,
which is a list of python-paths to modules to read.

A module like this should hold a global `HELP_ENTRY_DICTS` list, containing
dicts that each represent a help entry. If no `HELP_ENTRY_DICTS` variable is
given, all top-level variables that are dicts in the module are read as help
entries.

Each dict is on the form
::

    {'key': <str>,
     'text': <str>}``     # the actual help text. Can contain # subtopic sections
     'category': <str>,   # optional, otherwise settings.DEFAULT_HELP_CATEGORY
     'aliases': <list>,   # optional
     'locks': <str>       # optional, 'view' controls seeing in help index, 'read'
                          #           if the entry can be read. If 'view' is unset,
                          #           'read' is used for the index. If unset, everyone
                          #           can read/view the entry.

"""

HELP_ENTRY_DICTS = [
    {
        "key": "charcreate",
        "aliases": ["character creation", "chargen"],
        "category": "Character",
        "text": """
            The |wcharcreate|n command opens the character-creation wizard.

            Usage:
              charcreate

            You must be logged in (Out-of-Character) to use this command.  If you
            already have a character in progress, |wcharcreate|n resumes where you
            left off — you can exit at any time and come back later.

            # subtopics

            ## Steps

            Character creation follows five steps from the SRD 5.2.1:

            1. |yChoose a Class|n — Your class defines your vocation, talents, and
               fighting style.  Available classes: Barbarian, Bard, Cleric, Druid,
               Fighter, Monk, Paladin, Ranger, Rogue, Sorcerer, Warlock, Wizard.

            2. |yChoose Your Origin|n — Your origin has two parts:
               - Background: represents your pre-adventuring occupation and gives
                 skill proficiencies, a tool proficiency, a feat, and starting gear.
               - Species: your ancestral heritage, determining size and speed.
               - Languages: your character automatically knows Common plus 2 more.

            3. |yDetermine Ability Scores|n — Six scores power your character:
               Strength, Dexterity, Constitution, Intelligence, Wisdom, Charisma.
               Choose Standard Array (15/14/13/12/10/8), Point Buy (27 pts), or
               Random Roll (4d6 drop lowest, six times).  Your background then
               adds +2/+1 or +1/+1/+1 to three abilities.

            4. |yChoose Alignment|n — A shorthand for your character's ethical
               outlook: one of nine combinations of Lawful/Neutral/Chaotic and
               Good/Neutral/Evil.

            5. |yChoose a Name|n — Pick a unique name for your character.  Once
               confirmed, your character is created and you enter the world.

            ## Resuming

            If you quit chargen mid-way (type |wq|n or |wquit|n inside the menu),
            your progress is saved.  Type |wcharcreate|n again to continue.

        """,
    },
    {
        "key": "character sheet",
        "aliases": ["score", "sheet"],
        "category": "Character",
        "text": """
            Your character sheet tracks all the key numbers that define your
            adventurer in the game world.

            # subtopics

            ## Ability Scores

            Six core abilities describe your character's natural talents:

              |wStrength (STR)|n     — Physical power; melee attacks, lifting, climbing.
              |wDexterity (DEX)|n    — Agility; ranged attacks, AC, Stealth, Initiative.
              |wConstitution (CON)|n — Endurance; Hit Points, concentration.
              |wIntelligence (INT)|n — Memory and reasoning; Arcana, History, Investigation.
              |wWisdom (WIS)|n       — Perception and intuition; Insight, Perception, Medicine.
              |wCharisma (CHA)|n     — Force of personality; Deception, Persuasion, Performance.

            Each score has a modifier: (score − 10) / 2, rounded down.

            ## Combat Stats

              |wHit Points (HP)|n     — Determined at level 1 by class hit die + CON modifier.
              |wArmor Class (AC)|n    — 10 + DEX modifier (base, no armor).
              |wInitiative|n          — DEX modifier; used to order combat turns.
              |wProficiency Bonus|n   — +2 at level 1; increases as you gain levels.
              |wPassive Perception|n  — 10 + WIS (Perception) modifier.

            ## Identity

              |wClass|n, |wBackground|n, |wSpecies|n, |wAlignment|n, |wLanguages|n

              Level 1 characters start with 0 XP and advance by earning Experience Points.

        """,
    },
    {
        "key": "evennia",
        "aliases": ["ev"],
        "category": "General",
        "locks": "read:perm(Developer)",
        "text": """
            Evennia is a MU-game server and framework written in Python. You can read more
            on https://www.evennia.com.

            # subtopics

            ## Installation

            You'll find installation instructions on https://www.evennia.com.

            ## Community

            There are many ways to get help and communicate with other devs!

            ### Discussions

            The Discussions forum is found at https://github.com/evennia/evennia/discussions.

            ### Discord

            There is also a discord channel for chatting - connect using the
            following link: https://discord.gg/AJJpcRUhtF

        """,
    },
]
