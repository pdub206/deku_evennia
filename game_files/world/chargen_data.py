"""
SRD 5.2.1 character-generation data constants.

No game logic lives here — only data that the chargen menu and other systems
can import.  Add new backgrounds, species, or classes by extending the dicts;
the menu nodes read them dynamically.
"""

from systems.dice import roll

# ---------------------------------------------------------------------------
# Ability helpers
# ---------------------------------------------------------------------------

ABILITY_NAMES: list[str] = [
    "Strength",
    "Dexterity",
    "Constitution",
    "Intelligence",
    "Wisdom",
    "Charisma",
]

ABILITY_SHORT: dict[str, str] = {
    "Strength": "STR",
    "Dexterity": "DEX",
    "Constitution": "CON",
    "Intelligence": "INT",
    "Wisdom": "WIS",
    "Charisma": "CHA",
}


def ability_modifier(score: int) -> int:
    """Return the SRD ability modifier for a given score."""
    return (score - 10) // 2


def roll_4d6_drop_lowest() -> int:
    """Roll 4d6 and return the sum of the highest three dice."""
    rolls = sorted([roll(6) for _ in range(4)])
    return sum(rolls[1:])  # drop the lowest


# ---------------------------------------------------------------------------
# Ability score generation
# ---------------------------------------------------------------------------

STANDARD_ARRAY: list[int] = [15, 14, 13, 12, 10, 8]

# Point-buy cost table (SRD p.21).  Total budget = 27.
POINT_BUY_COSTS: dict[int, int] = {
    8: 0,
    9: 1,
    10: 2,
    11: 3,
    12: 4,
    13: 5,
    14: 7,
    15: 9,
}
POINT_BUY_TOTAL: int = 27
POINT_BUY_MIN: int = 8
POINT_BUY_MAX: int = 15

# Ability score modifier lookup table (SRD p.21).
ABILITY_SCORE_MODIFIERS: dict[int, int] = {
    3: -4,
    4: -3,
    5: -3,
    6: -2,
    7: -2,
    8: -1,
    9: -1,
    10: 0,
    11: 0,
    12: 1,
    13: 1,
    14: 2,
    15: 2,
    16: 3,
    17: 3,
    18: 4,
    19: 4,
    20: 5,
}

# ---------------------------------------------------------------------------
# Classes  (SRD p.19, p.21-22, p.28+)
# ---------------------------------------------------------------------------

# Each class entry:
#   likes           – flavour tagline (SRD Class Overview table)
#   primary_ability – str or list[str]
#   hit_die         – size of the hit die (int)
#   hp_base         – level-1 HP before adding CON modifier
#   complexity      – Low / Average / High
#   saving_throws   – two abilities the class is proficient in
#   skill_choices   – how many skills the player picks at level 1
#   skills_available – pool of skills to pick from
#   armor_training  – list of armor categories the class trains with
#   weapon_profs    – brief description of weapon proficiencies

CLASSES: dict[str, dict] = {
    "Barbarian": {
        "likes": "Battle",
        "primary_ability": "Strength",
        "hit_die": 12,
        "hp_base": 12,
        "complexity": "Average",
        "saving_throws": ["Strength", "Constitution"],
        "skill_choices": 2,
        "skills_available": [
            "Animal Handling",
            "Athletics",
            "Intimidation",
            "Nature",
            "Perception",
            "Survival",
        ],
        "armor_training": ["Light", "Medium", "Shields"],
        "weapon_profs": "Simple and Martial weapons",
    },
    "Bard": {
        "likes": "Performing",
        "primary_ability": "Charisma",
        "hit_die": 8,
        "hp_base": 8,
        "complexity": "High",
        "saving_throws": ["Dexterity", "Charisma"],
        "skill_choices": 3,
        "skills_available": [
            "Acrobatics",
            "Animal Handling",
            "Arcana",
            "Athletics",
            "Deception",
            "History",
            "Insight",
            "Intimidation",
            "Investigation",
            "Medicine",
            "Nature",
            "Perception",
            "Performance",
            "Persuasion",
            "Religion",
            "Sleight of Hand",
            "Stealth",
            "Survival",
        ],
        "armor_training": ["Light"],
        "weapon_profs": "Simple weapons",
    },
    "Cleric": {
        "likes": "Gods",
        "primary_ability": "Wisdom",
        "hit_die": 8,
        "hp_base": 8,
        "complexity": "Average",
        "saving_throws": ["Wisdom", "Charisma"],
        "skill_choices": 2,
        "skills_available": [
            "History",
            "Insight",
            "Medicine",
            "Persuasion",
            "Religion",
        ],
        "armor_training": ["Light", "Medium", "Shields"],
        "weapon_profs": "Simple weapons",
    },
    "Druid": {
        "likes": "Nature",
        "primary_ability": "Wisdom",
        "hit_die": 8,
        "hp_base": 8,
        "complexity": "High",
        "saving_throws": ["Intelligence", "Wisdom"],
        "skill_choices": 2,
        "skills_available": [
            "Animal Handling",
            "Arcana",
            "Insight",
            "Medicine",
            "Nature",
            "Perception",
            "Religion",
            "Survival",
        ],
        "armor_training": ["Light", "Medium", "Shields"],
        "weapon_profs": "Simple weapons",
    },
    "Fighter": {
        "likes": "Weapons",
        "primary_ability": ["Strength", "Dexterity"],
        "hit_die": 10,
        "hp_base": 10,
        "complexity": "Low",
        "saving_throws": ["Strength", "Constitution"],
        "skill_choices": 2,
        "skills_available": [
            "Acrobatics",
            "Animal Handling",
            "Athletics",
            "History",
            "Insight",
            "Intimidation",
            "Perception",
            "Survival",
        ],
        "armor_training": ["Light", "Medium", "Heavy", "Shields"],
        "weapon_profs": "Simple and Martial weapons",
    },
    "Monk": {
        "likes": "Unarmed combat",
        "primary_ability": ["Dexterity", "Wisdom"],
        "hit_die": 8,
        "hp_base": 8,
        "complexity": "High",
        "saving_throws": ["Strength", "Dexterity"],
        "skill_choices": 2,
        "skills_available": [
            "Acrobatics",
            "Athletics",
            "History",
            "Insight",
            "Religion",
            "Stealth",
        ],
        "armor_training": [],
        "weapon_profs": "Simple weapons and Shortswords",
    },
    "Paladin": {
        "likes": "Defense",
        "primary_ability": ["Strength", "Charisma"],
        "hit_die": 10,
        "hp_base": 10,
        "complexity": "Average",
        "saving_throws": ["Wisdom", "Charisma"],
        "skill_choices": 2,
        "skills_available": [
            "Athletics",
            "Insight",
            "Intimidation",
            "Medicine",
            "Persuasion",
            "Religion",
        ],
        "armor_training": ["Light", "Medium", "Heavy", "Shields"],
        "weapon_profs": "Simple and Martial weapons",
    },
    "Ranger": {
        "likes": "Survival",
        "primary_ability": ["Dexterity", "Wisdom"],
        "hit_die": 10,
        "hp_base": 10,
        "complexity": "Average",
        "saving_throws": ["Strength", "Dexterity"],
        "skill_choices": 3,
        "skills_available": [
            "Animal Handling",
            "Athletics",
            "Insight",
            "Investigation",
            "Nature",
            "Perception",
            "Stealth",
            "Survival",
        ],
        "armor_training": ["Light", "Medium", "Shields"],
        "weapon_profs": "Simple and Martial weapons",
    },
    "Rogue": {
        "likes": "Stealth",
        "primary_ability": "Dexterity",
        "hit_die": 8,
        "hp_base": 8,
        "complexity": "Low",
        "saving_throws": ["Dexterity", "Intelligence"],
        "skill_choices": 4,
        "skills_available": [
            "Acrobatics",
            "Athletics",
            "Deception",
            "Insight",
            "Intimidation",
            "Investigation",
            "Perception",
            "Performance",
            "Persuasion",
            "Sleight of Hand",
            "Stealth",
        ],
        "armor_training": ["Light"],
        "weapon_profs": "Simple weapons, hand crossbows, longswords, rapiers, shortswords",
    },
    "Sorcerer": {
        "likes": "Power",
        "primary_ability": "Charisma",
        "hit_die": 6,
        "hp_base": 6,
        "complexity": "High",
        "saving_throws": ["Constitution", "Charisma"],
        "skill_choices": 2,
        "skills_available": [
            "Arcana",
            "Deception",
            "Insight",
            "Intimidation",
            "Persuasion",
            "Religion",
        ],
        "armor_training": [],
        "weapon_profs": "Daggers, darts, slings, quarterstaffs, light crossbows",
    },
    "Warlock": {
        "likes": "Occult lore",
        "primary_ability": "Charisma",
        "hit_die": 8,
        "hp_base": 8,
        "complexity": "High",
        "saving_throws": ["Wisdom", "Charisma"],
        "skill_choices": 2,
        "skills_available": [
            "Arcana",
            "Deception",
            "History",
            "Intimidation",
            "Investigation",
            "Nature",
            "Religion",
        ],
        "armor_training": ["Light"],
        "weapon_profs": "Simple weapons",
    },
    "Wizard": {
        "likes": "Spellbooks",
        "primary_ability": "Intelligence",
        "hit_die": 6,
        "hp_base": 6,
        "complexity": "Average",
        "saving_throws": ["Intelligence", "Wisdom"],
        "skill_choices": 2,
        "skills_available": [
            "Arcana",
            "History",
            "Insight",
            "Investigation",
            "Medicine",
            "Religion",
        ],
        "armor_training": [],
        "weapon_profs": "Daggers, darts, slings, quarterstaffs, light crossbows",
    },
}

# Suggested standard-array score assignments per class (SRD p.21).
# Keys are ability names; values are the suggested score from [15,14,13,12,10,8].
STANDARD_ARRAY_BY_CLASS: dict[str, dict[str, int]] = {
    "Barbarian": {
        "Strength": 15,
        "Dexterity": 13,
        "Constitution": 14,
        "Intelligence": 10,
        "Wisdom": 12,
        "Charisma": 8,
    },
    "Bard": {
        "Strength": 8,
        "Dexterity": 14,
        "Constitution": 12,
        "Intelligence": 13,
        "Wisdom": 10,
        "Charisma": 15,
    },
    "Cleric": {
        "Strength": 14,
        "Dexterity": 8,
        "Constitution": 13,
        "Intelligence": 10,
        "Wisdom": 15,
        "Charisma": 12,
    },
    "Druid": {
        "Strength": 8,
        "Dexterity": 12,
        "Constitution": 14,
        "Intelligence": 13,
        "Wisdom": 15,
        "Charisma": 10,
    },
    "Fighter": {
        "Strength": 15,
        "Dexterity": 14,
        "Constitution": 13,
        "Intelligence": 8,
        "Wisdom": 10,
        "Charisma": 12,
    },
    "Monk": {
        "Strength": 12,
        "Dexterity": 15,
        "Constitution": 13,
        "Intelligence": 10,
        "Wisdom": 14,
        "Charisma": 8,
    },
    "Paladin": {
        "Strength": 15,
        "Dexterity": 10,
        "Constitution": 13,
        "Intelligence": 8,
        "Wisdom": 12,
        "Charisma": 14,
    },
    "Ranger": {
        "Strength": 12,
        "Dexterity": 15,
        "Constitution": 13,
        "Intelligence": 8,
        "Wisdom": 14,
        "Charisma": 10,
    },
    "Rogue": {
        "Strength": 12,
        "Dexterity": 15,
        "Constitution": 13,
        "Intelligence": 14,
        "Wisdom": 10,
        "Charisma": 8,
    },
    "Sorcerer": {
        "Strength": 10,
        "Dexterity": 13,
        "Constitution": 14,
        "Intelligence": 8,
        "Wisdom": 12,
        "Charisma": 15,
    },
    "Warlock": {
        "Strength": 8,
        "Dexterity": 14,
        "Constitution": 13,
        "Intelligence": 12,
        "Wisdom": 10,
        "Charisma": 15,
    },
    "Wizard": {
        "Strength": 8,
        "Dexterity": 12,
        "Constitution": 13,
        "Intelligence": 15,
        "Wisdom": 14,
        "Charisma": 10,
    },
}

# ---------------------------------------------------------------------------
# Backgrounds  (SRD p.19-20)
# ---------------------------------------------------------------------------

# ability_options – three abilities from which the player distributes bonuses
#   (+2 to one and +1 to another, OR +1 to all three).
# feat – the background feat (not yet enforced mechanically; stored for reference).

BACKGROUNDS: dict[str, dict] = {
    "Acolyte": {
        "description": (
            "You have spent your life in the service of a temple, learning sacred rites "
            "and mastering the basics of a sacred tradition."
        ),
        "ability_options": ["Intelligence", "Wisdom", "Charisma"],
        "skill_proficiencies": ["Insight", "Religion"],
        "tool_proficiency": "Calligrapher's Supplies",
        "feat": "Magic Initiate (Cleric)",
        "equipment": "Calligrapher's Supplies, Book (prayers), Holy Symbol, Parchment, Robe, 8 GP",
    },
    "Artisan": {
        "description": (
            "You began your career as an apprentice in a craft or trade, learning the tools "
            "and techniques of your guild or master."
        ),
        "ability_options": ["Strength", "Dexterity", "Intelligence"],
        "skill_proficiencies": ["Investigation", "Persuasion"],
        "tool_proficiency": "Artisan's Tools (one kind)",
        "feat": "Crafter",
        "equipment": "Artisan's Tools (one kind), 2 Pouches, Traveler's Clothes, 32 GP",
    },
    "Charlatan": {
        "description": (
            "You have always had a way with people. You know what makes them tick, you can "
            "tease out their heart's desires after a few minutes of conversation."
        ),
        "ability_options": ["Dexterity", "Constitution", "Charisma"],
        "skill_proficiencies": ["Deception", "Sleight of Hand"],
        "tool_proficiency": "Forgery Kit",
        "feat": "Skilled",
        "equipment": "Forgery Kit, Costume, Fine Clothes, 15 GP",
    },
    "Criminal": {
        "description": (
            "You are an experienced criminal with a history of breaking the law. You have "
            "spent a lot of time among other criminals and still have contacts within the "
            "criminal underworld."
        ),
        "ability_options": ["Dexterity", "Constitution", "Intelligence"],
        "skill_proficiencies": ["Sleight of Hand", "Stealth"],
        "tool_proficiency": "Thieves' Tools",
        "feat": "Alert",
        "equipment": "Thieves' Tools, Crowbar, 2 Daggers, Traveler's Clothes, 16 GP",
    },
    "Entertainer": {
        "description": (
            "You thrive in front of an audience. You know how to entrance them, entertain "
            "them, and even inspire them."
        ),
        "ability_options": ["Strength", "Dexterity", "Charisma"],
        "skill_proficiencies": ["Acrobatics", "Performance"],
        "tool_proficiency": "Musical Instrument (one kind)",
        "feat": "Musician",
        "equipment": "Musical Instrument (one kind), 2 Costumes, Mirror, Perfume, Traveler's Clothes, 11 GP",
    },
    "Farmer": {
        "description": (
            "You grew up close to the land, working long hours in the fields and learning "
            "how to provide for a community."
        ),
        "ability_options": ["Strength", "Constitution", "Wisdom"],
        "skill_proficiencies": ["Animal Handling", "Nature"],
        "tool_proficiency": "Carpenter's Tools",
        "feat": "Tough",
        "equipment": "Carpenter's Tools, Healer's Kit, Iron Pot, Shovel, Traveler's Clothes, 30 GP",
    },
    "Guard": {
        "description": (
            "Your training in a town watch, a noble's guard, or some other security force "
            "has made you vigilant and capable of protecting others."
        ),
        "ability_options": ["Strength", "Intelligence", "Wisdom"],
        "skill_proficiencies": ["Athletics", "Perception"],
        "tool_proficiency": "Gaming Set (one kind)",
        "feat": "Alert",
        "equipment": "Gaming Set (one kind), Hooded Lantern, Manacles, Quiver with 20 Arrows, Spear, Traveler's Clothes, 11 GP",
    },
    "Guide": {
        "description": (
            "You grew up in the wilderness, learning to read the land, track prey, and "
            "survive in the harshest environments."
        ),
        "ability_options": ["Dexterity", "Constitution", "Wisdom"],
        "skill_proficiencies": ["Stealth", "Survival"],
        "tool_proficiency": "Cartographer's Tools",
        "feat": "Magic Initiate (Druid)",
        "equipment": "Cartographer's Tools, Bedroll, Quiver with 20 Arrows, Shortbow, Traveler's Clothes, 3 GP",
    },
    "Hermit": {
        "description": (
            "You lived in seclusion—either in a sheltered community such as a monastery, "
            "or entirely alone—for a formative part of your life."
        ),
        "ability_options": ["Constitution", "Wisdom", "Charisma"],
        "skill_proficiencies": ["Medicine", "Religion"],
        "tool_proficiency": "Herbalism Kit",
        "feat": "Magic Initiate (Druid)",
        "equipment": "Herbalism Kit, Bedroll, Book (philosophy), Blanket, Traveler's Clothes, 16 GP",
    },
    "Merchant": {
        "description": (
            "You grew up in a mercantile family or worked in a trading company, developing "
            "a head for numbers and a silver tongue for negotiations."
        ),
        "ability_options": ["Constitution", "Intelligence", "Charisma"],
        "skill_proficiencies": ["Animal Handling", "Persuasion"],
        "tool_proficiency": "Navigator's Tools",
        "feat": "Lucky",
        "equipment": "Navigator's Tools, 2 Pouches, Traveler's Clothes, 22 GP",
    },
    "Noble": {
        "description": (
            "You understand wealth, power, and privilege. You carry a noble title, and your "
            "family owns land, collects taxes, and wields significant political influence."
        ),
        "ability_options": ["Strength", "Intelligence", "Charisma"],
        "skill_proficiencies": ["History", "Persuasion"],
        "tool_proficiency": "Gaming Set (one kind)",
        "feat": "Skilled",
        "equipment": "Gaming Set (one kind), Fine Clothes, Perfume, Signet Ring, 30 GP",
    },
    "Sage": {
        "description": (
            "You spent years learning the lore of the multiverse. You scoured manuscripts, "
            "studied scrolls, and listened to the greatest experts on the subjects that "
            "interest you."
        ),
        "ability_options": ["Constitution", "Intelligence", "Wisdom"],
        "skill_proficiencies": ["Arcana", "History"],
        "tool_proficiency": "Calligrapher's Supplies",
        "feat": "Magic Initiate (Wizard)",
        "equipment": "Calligrapher's Supplies, Book (history), Parchment (8 sheets), Traveler's Clothes, 8 GP",
    },
    "Sailor": {
        "description": (
            "You sailed on a seagoing vessel for years. In that time, you faced down mighty "
            "storms, monsters of the deep, and those who wanted to sink your craft to the "
            "bottomless depths."
        ),
        "ability_options": ["Strength", "Dexterity", "Wisdom"],
        "skill_proficiencies": ["Acrobatics", "Perception"],
        "tool_proficiency": "Navigator's Tools",
        "feat": "Tavern Brawler",
        "equipment": "Navigator's Tools, Dagger, Rope (50 ft.), Traveler's Clothes, 20 GP",
    },
    "Scribe": {
        "description": (
            "You spent formative years in a scriptorium, library, or government office, "
            "copying documents and keeping records with painstaking accuracy."
        ),
        "ability_options": ["Dexterity", "Intelligence", "Wisdom"],
        "skill_proficiencies": ["Investigation", "Perception"],
        "tool_proficiency": "Calligrapher's Supplies",
        "feat": "Skilled",
        "equipment": "Calligrapher's Supplies, Fine Clothes, Parchment (12 sheets), 23 GP",
    },
    "Soldier": {
        "description": (
            "War has been your life for as long as you care to remember. You trained as a "
            "youth, studied the use of weapons and armor, learned basic survival techniques, "
            "including how to stay alive on the battlefield."
        ),
        "ability_options": ["Strength", "Dexterity", "Constitution"],
        "skill_proficiencies": ["Athletics", "Intimidation"],
        "tool_proficiency": "Gaming Set (one kind)",
        "feat": "Savage Attacker",
        "equipment": "Gaming Set (one kind), Healer's Kit, Quiver with 20 Arrows, Shortbow, Traveler's Clothes, Spear, 14 GP",
    },
    "Wayfarer": {
        "description": (
            "You grew up on the road, always moving from place to place. Whether as a "
            "member of a traveling caravan, a family of wanderers, or alone, you learned "
            "to fend for yourself in a variety of environments."
        ),
        "ability_options": ["Dexterity", "Wisdom", "Charisma"],
        "skill_proficiencies": ["Insight", "Stealth"],
        "tool_proficiency": "Thieves' Tools",
        "feat": "Lucky",
        "equipment": "Thieves' Tools, Bedroll, 2 Daggers, Gaming Set (one kind), Traveler's Clothes, 16 GP",
    },
}

# ---------------------------------------------------------------------------
# Species  (SRD p.20)
# ---------------------------------------------------------------------------

# Full species traits will be expanded when the species chapter is implemented.
# For chargen we store name, size, and speed so derived stats can be set.

SPECIES: dict[str, dict] = {
    "Dragonborn": {
        "description": "Born of dragons, dragonborn walk proudly through a world that greets them with fearful incomprehension.",
        "size": "Medium",
        "height": "about 5–7 feet tall",
        "speed": 30,
        "max_age": 80,
        "traits": ["Draconic Ancestry", "Breath Weapon", "Damage Resistance"],
    },
    "Dwarf": {
        "description": "Bold and hardy, dwarves are known as skilled warriors, miners, and workers of stone and metal.",
        "size": "Medium",
        "height": "about 4–5 feet tall",
        "speed": 30,
        "max_age": 350,
        "traits": [
            "Darkvision",
            "Dwarven Resilience",
            "Dwarven Toughness",
            "Stonecunning",
        ],
    },
    "Elf": {
        "description": "Elves are a magical people of otherworldly grace, living in the world but not entirely part of it.",
        "size": "Medium",
        "height": "about 5–6 feet tall",
        "speed": 30,
        "max_age": 750,
        "traits": [
            "Darkvision",
            "Elven Lineage",
            "Fey Ancestry",
            "Keen Senses",
            "Trance",
        ],
    },
    "Gnome": {
        "description": "A gnome's energy and enthusiasm for living shines through every inch of their tiny bodies.",
        "size": "Small",
        "height": "about 3–4 feet tall",
        "speed": 30,
        "max_age": 500,
        "traits": ["Darkvision", "Gnomish Cunning", "Gnomish Lineage"],
    },
    "Goliath": {
        "description": "Goliaths are massive, distantly related to giants, and feel most at home amid the highest mountain peaks.",
        "size": "Medium",
        "height": "about 7–8 feet tall",
        "speed": 35,
        "max_age": 95,
        "traits": ["Giant Ancestry", "Large Form", "Powerful Build"],
    },
    "Halfling": {
        "description": "The comforts of home are the goals of most halflings' lives: a place to settle in peace and quiet.",
        "size": "Small",
        "height": "about 2–3 feet tall",
        "speed": 30,
        "max_age": 250,
        "traits": ["Brave", "Halfling Nimbleness", "Luck", "Naturally Stealthy"],
    },
    "Human": {
        "description": "Humans are the most adaptable and ambitious people among the common races.",
        # Size is chosen by the player at character creation (SRD p.86).
        "size": "Medium or Small",
        "size_choice": True,
        "height_medium": "about 4–7 feet tall",
        "height_small": "about 2–4 feet tall",
        "speed": 30,
        "max_age": 100,
        "traits": ["Resourceful", "Skillful", "Versatile"],
    },
    "Orc": {
        "description": "Orcs are a fierce and enduring people with a rich history of struggle against the forces that shaped them.",
        "size": "Medium",
        "height": "about 6–7 feet tall",
        "speed": 30,
        "max_age": 80,
        "traits": [
            "Adrenaline Rush",
            "Darkvision",
            "Powerful Build",
            "Relentless Endurance",
        ],
    },
    "Tiefling": {
        "description": "Tieflings are derived from human bloodlines, carrying infernal power that erupted through generations past.",
        # Size is chosen by the player at character creation (SRD p.86).
        "size": "Medium or Small",
        "size_choice": True,
        "height_medium": "about 4–7 feet tall",
        "height_small": "about 3–4 feet tall",
        "speed": 30,
        "max_age": 115,
        "traits": ["Darkvision", "Fiendish Legacy", "Otherworldly Presence"],
    },
}

# Age constraints — all characters must be adults (18+); upper bound is the
# maximum lifespan of the longest-lived playable species (Elf, 750).
MIN_AGE: int = 18
MAX_AGE: int = max(data["max_age"] for data in SPECIES.values())

# ---------------------------------------------------------------------------
# Languages  (SRD p.20)
# ---------------------------------------------------------------------------

# Common is automatic for all player characters and not in the choosable list.
STANDARD_LANGUAGES: list[str] = [
    "Common Sign Language",
    "Draconic",
    "Dwarvish",
    "Elvish",
    "Giant",
    "Gnomish",
    "Goblin",
    "Halfling",
    "Orc",
]

RARE_LANGUAGES: list[str] = [
    "Abyssal",
    "Celestial",
    "Deep Speech",
    "Druidic",
    "Infernal",
    "Primordial",
    "Sylvan",
    "Thieves' Cant",
    "Undercommon",
]

# ---------------------------------------------------------------------------
# Skills  (SRD p.9)
# ---------------------------------------------------------------------------

# Maps each skill name to its governing ability score.
SKILLS: dict[str, str] = {
    "Acrobatics": "Dexterity",
    "Animal Handling": "Wisdom",
    "Arcana": "Intelligence",
    "Athletics": "Strength",
    "Deception": "Charisma",
    "History": "Intelligence",
    "Insight": "Wisdom",
    "Intimidation": "Charisma",
    "Investigation": "Intelligence",
    "Medicine": "Wisdom",
    "Nature": "Intelligence",
    "Perception": "Wisdom",
    "Performance": "Charisma",
    "Persuasion": "Charisma",
    "Religion": "Intelligence",
    "Sleight of Hand": "Dexterity",
    "Stealth": "Dexterity",
    "Survival": "Wisdom",
}

# ---------------------------------------------------------------------------
# Alignments  (SRD p.21-22)
# ---------------------------------------------------------------------------

# Each tuple: (full_name, abbreviation, brief_description)
ALIGNMENTS: list[tuple[str, str, str]] = [
    (
        "Lawful Good",
        "LG",
        "Endeavors to do the right thing as expected by society. Fights injustice and protects the innocent.",
    ),
    (
        "Neutral Good",
        "NG",
        "Does the best they can, working within rules but not feeling bound by them. Helps others according to their needs.",
    ),
    (
        "Chaotic Good",
        "CG",
        "Acts as their conscience directs with little regard for what others expect. Follows their heart over tradition.",
    ),
    (
        "Lawful Neutral",
        "LN",
        "Acts in accordance with law, tradition, or personal codes. Follows a disciplined rule of life.",
    ),
    (
        "Neutral",
        "N",
        "Prefers to avoid moral questions and doesn't take sides, doing what seems best at the time.",
    ),
    (
        "Chaotic Neutral",
        "CN",
        "Follows their whims, valuing personal freedom above all else. Avoids commitments and obligations.",
    ),
    (
        "Lawful Evil",
        "LE",
        "Methodically takes what they want within the limits of a code of tradition, loyalty, or order.",
    ),
    (
        "Neutral Evil",
        "NE",
        "Is untroubled by the harm they cause as they pursue their desires. Does whatever they can get away with.",
    ),
    (
        "Chaotic Evil",
        "CE",
        "Acts with arbitrary violence, spurred by hatred or bloodlust. Destroys without remorse.",
    ),
]
