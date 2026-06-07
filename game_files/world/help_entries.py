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
        "key": "skills",
        "aliases": ["skill"],
        "category": "Character",
        "text": """
            The |wskills|n command displays all 18 skills and your current bonus for each.

            Usage:
              skills

            # subtopics

            ## Skill Bonuses

            Your bonus for a skill equals:
              |wAbility modifier|n + |wproficiency bonus|n (if proficient)

            If you are |wnot|n proficient, you only add the ability modifier.
            The proficiency bonus at level 1 is +2.

            ## Skill List

            |wAcrobatics|n (DEX)
              Your ability to stay on your feet and perform athletic feats of
              agility — tumbling, balancing, and diving out of harm's way.

            |wAnimal Handling|n (WIS)
              Calming domesticated animals, keeping a mount under control,
              and intuiting an animal's intentions.

            |wArcana|n (INT)
              Recalling lore about spells, magic items, eldritch symbols,
              magical traditions, and the planes of existence.

            |wAthletics|n (STR)
              Feats of physical exertion: climbing, jumping, swimming, and
              grappling foes.

            |wDeception|n (CHA)
              Convincingly hiding the truth, whether through misdirection,
              bluffing, or outright lying.

            |wHistory|n (INT)
              Recalling lore about historical events, legendary people, ancient
              kingdoms, past disputes, and recent wars.

            |wInsight|n (WIS)
              Determining the true intentions of a creature — reading body
              language, speech habits, and changes in mannerisms.

            |wIntimidation|n (CHA)
              Influencing someone through overt threats, hostile actions, and
              physical violence.

            |wInvestigation|n (INT)
              Looking for clues and deducing from evidence — noticing details
              others overlook and piecing together how something was done.

            |wMedicine|n (WIS)
              Stabilising a dying companion or diagnosing an illness.

            |wNature|n (INT)
              Recalling lore about terrain, plants and animals, the weather,
              and natural cycles.

            |wPerception|n (WIS)
              Spotting, hearing, or otherwise detecting the presence of
              something — the primary sense check for noticing hidden threats.

            |wPerformance|n (CHA)
              How well you entertain an audience with music, dance, acting,
              storytelling, or some other form of entertainment.

            |wPersuasion|n (CHA)
              Influencing someone or a group of people through tact, social
              graces, or good-natured requests.

            |wReligion|n (INT)
              Recalling lore about deities, rites, prayers, religious
              hierarchies, holy symbols, and the practices of secret cults.

            |wSleight of Hand|n (DEX)
              Legerdemain and manual trickery — pickpocketing, concealing an
              object, and other acts of manual deception.

            |wStealth|n (DEX)
              Concealing yourself from enemies, slipping past guards, and
              generally moving without being seen or heard.

            |wSurvival|n (WIS)
              Following tracks, hunting game, guiding the party through
              wilderness, predicting weather, and avoiding natural hazards.

        """,
    },
    {
        "key": "change language",
        "aliases": ["change"],
        "category": "Character",
        "text": """
            Switch the language your character speaks aloud.

            Usage:
              change language <language>

            Examples:
              change language common
              change language draconic

            Your character can only speak languages they know.  Attempting to
            switch to an unknown language will fail with a reminder.  Use
            |wchange language|n with no argument to see your current language and
            the full list of languages you know.

            Your active language is used by the |wsay|n command.  Other
            characters who know the same language will understand you normally;
            those who do not will hear only unintelligible speech.
        """,
    },
    {
        "key": "build",
        "aliases": ["building", "edit", "builder"],
        "category": "Building",
        "locks": "read:perm(Builder)",
        "text": """
            The |wbuild|n command is the unified, in-game world builder.  Rather
            than memorising a dozen separate builder verbs, you |wedit|n one
            object at a time and use a small set of flat verbs on it.

            Usage:
              build                 show what you can build / what you're editing
              edit here             edit the room you're standing in
              edit new [<name>]     create a fresh unlinked room and go to it
              edit <object>         edit an existing object by name or #dbref

            You must have |wBuilder|n permission.  The classic builder commands
            (dig, create, set, desc, spawn, ...) still work for expert use.

            # subtopics

            ## Editing

            |wedit here|n (or |wedit <object>|n) puts you in a sticky editing
            context bound to that object.  A header like |w[build: Town Square
            (#5)]|n reminds you what you're editing, and your input prompt
            changes to |weediting>|n for the whole session — so even if you walk
            away you can see you're still bound to a room.  |wdone|n clears it.
            While editing, these verbs act on the bound object:

              |wfields|n                 list the editable fields, with hints
              |wshow|n                   show current field values and exits
              |wset <field> <value>|n    set a field, e.g. |wset name The Plaza|n
              |wdesc|n                   open the multi-line description editor
              |wdesc <text>|n            set the description on one line
              |wdel|n                    delete this object (type |wdel|n twice)
              |wdone|n                   leave the editing context

            Values are validated — a bad value is rejected with a reason instead
            of being stored.  Type |wfields|n any time to see what you can set.

            ## Rooms and Exits

            To start a brand-new area in isolation — before it is linked into
            the live world — use |wedit new <name>|n.  That creates a standalone
            room with no exits and teleports you into it; from there you |wdig|n
            outward to grow the area and only |wlink|n it into the game once it
            has been built and reviewed.  (|wtel #<dbref>|n takes you back where
            you came from.)

            When editing a room you can shape the map without leaving the
            context:

              |wdig <dir> = <Room Name>|n   dig a new room with two-way exits
              |wdig <dir>|n                 same, naming it 'An Unnamed Room'
              |wlink <dir> = <room>|n       add an exit to an existing room
              |wunlink <dir>|n              remove an exit

            Standard directions (north/south/east/west/up/down/in/out and the
            diagonals) automatically get a reverse exit and short aliases
            (n, s, e, w, u, d).  Dug rooms inherit the current room's area.

            After digging you stay on the room you started from (handy for
            fanning several exits off a hub).  To work on the new room, type
            |wedit <direction>|n — editing an exit jumps you to the room it
            leads to — or walk there and |wedit here|n.

            ## Areas and Export

            Rooms are grouped into |yareas|n, which are the unit of saving.
            Browse what exists with:

              |wareas|n                  list every area and its room count
              |wrooms|n                  list the rooms in your current area
              |wrooms <area>|n           list the rooms in a named area (with #dbrefs)

            And group and save rooms with:

              |warea <name>|n            assign the room you're editing to an area
              |wexport|n                 save that area to a git-tracked file

            |wexport|n writes |wgame_files/world/areas/<area>.py|n — a readable
            file describing the rooms and the exit graph by stable keys (not
            dbrefs), so it can be reviewed in version control and re-loaded into
            any world.  To apply a saved area to the live game, sync it in,
            reload, then:

              |wloadarea <area>|n        spawn an area's rooms and exits

            Loading is idempotent: existing rooms and exits are reused, not
            duplicated, so you can safely re-run it after edits.
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
