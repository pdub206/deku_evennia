"""Reviewed SRD 5.2.1 spell-list membership data for P04-A01.

Rows here are source citations and class-list membership only.  They never
register a spell with the runtime magic registry; P04-S00--P04-S09 own that
mechanical promotion after the relevant handler and help are complete.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True)
class SRDSpellListEntry:
    """One spell level/list occurrence with its precise source citation."""

    class_key: str
    spell_name: str
    spell_level: int
    srd_reference: str


def _entries(
    class_key: str, page: int, spell_level: int, names: tuple[str, ...]
) -> tuple[SRDSpellListEntry, ...]:
    """Build one cited alphabetical spell-level membership set."""
    return tuple(
        SRDSpellListEntry(
            class_key,
            name,
            spell_level,
            f"SRD 5.2.1 p.{page}: Level {spell_level} {class_key} Spells",
        )
        for name in names
    )


# P04-S00's complete level-zero membership rows.  A shared spell appears once
# per SRD class list, preserving the class-access evidence P04-A09 will need.
SRD_CANTRIP_LISTS: Mapping[str, tuple[SRDSpellListEntry, ...]] = MappingProxyType(
    {
        "Bard": _entries(
            "Bard",
            33, 0,
            (
                "Dancing Lights",
                "Light",
                "Mage Hand",
                "Mending",
                "Message",
                "Minor Illusion",
                "Starry Wisp",
                "True Strike",
                "Vicious Mockery",
            ),
        ),
        "Cleric": _entries(
            "Cleric",
            38, 0,
            (
                "Guidance",
                "Light",
                "Mending",
                "Resistance",
                "Sacred Flame",
                "Spare the Dying",
                "Thaumaturgy",
            ),
        ),
        "Druid": _entries(
            "Druid",
            44, 0,
            (
                "Druidcraft",
                "Elementalism",
                "Guidance",
                "Mending",
                "Message",
                "Poison Spray",
                "Produce Flame",
                "Resistance",
                "Shillelagh",
                "Spare the Dying",
                "Starry Wisp",
            ),
        ),
        "Paladin": (),
        "Ranger": (),
        "Sorcerer": _entries(
            "Sorcerer",
            67, 0,
            (
                "Acid Splash",
                "Chill Touch",
                "Dancing Lights",
                "Elementalism",
                "Fire Bolt",
                "Light",
                "Mage Hand",
                "Mending",
                "Message",
                "Minor Illusion",
                "Poison Spray",
                "Prestidigitation",
                "Ray of Frost",
                "Shocking Grasp",
                "Sorcerous Burst",
                "True Strike",
            ),
        ),
        "Warlock": _entries(
            "Warlock",
            74, 0,
            (
                "Chill Touch",
                "Eldritch Blast",
                "Mage Hand",
                "Minor Illusion",
                "Poison Spray",
                "Prestidigitation",
                "True Strike",
            ),
        ),
        "Wizard": _entries(
            "Wizard",
            79, 0,
            (
                "Acid Splash",
                "Chill Touch",
                "Dancing Lights",
                "Elementalism",
                "Fire Bolt",
                "Light",
                "Mage Hand",
                "Mending",
                "Message",
                "Minor Illusion",
                "Poison Spray",
                "Prestidigitation",
                "Ray of Frost",
                "Shocking Grasp",
                "True Strike",
            ),
        ),
    }
)


SRD_LEVEL_ONE_SPELL_LISTS: Mapping[str, tuple[SRDSpellListEntry, ...]] = MappingProxyType(
    {
        "Bard": _entries("Bard", 33, 1, ("Animal Friendship", "Bane", "Charm Person", "Color Spray", "Command", "Comprehend Languages", "Cure Wounds", "Detect Magic", "Disguise Self", "Dissonant Whispers", "Faerie Fire", "Feather Fall", "Healing Word", "Heroism", "Hideous Laughter", "Identify", "Illusory Script", "Longstrider", "Silent Image", "Sleep", "Speak with Animals", "Thunderwave", "Unseen Servant")),
        "Cleric": _entries("Cleric", 38, 1, ("Bane", "Bless", "Command", "Create or Destroy Water", "Cure Wounds", "Detect Evil and Good", "Detect Magic", "Detect Poison and Disease", "Guiding Bolt", "Healing Word", "Inflict Wounds", "Protection from Evil and Good", "Purify Food and Drink", "Sanctuary", "Shield of Faith")),
        "Druid": _entries("Druid", 44, 1, ("Animal Friendship", "Charm Person", "Create or Destroy Water", "Cure Wounds", "Detect Magic", "Detect Poison and Disease", "Entangle", "Faerie Fire", "Fog Cloud", "Goodberry", "Healing Word", "Ice Knife", "Jump", "Longstrider", "Protection from Evil and Good", "Purify Food and Drink", "Speak with Animals", "Thunderwave")),
        "Paladin": _entries("Paladin", 55, 1, ("Bless", "Command", "Cure Wounds", "Detect Evil and Good", "Detect Magic", "Detect Poison and Disease", "Divine Favor", "Divine Smite", "Heroism", "Protection from Evil and Good", "Purify Food and Drink", "Searing Smite", "Shield of Faith")),
        "Ranger": _entries("Ranger", 60, 1, ("Alarm", "Animal Friendship", "Cure Wounds", "Detect Magic", "Detect Poison and Disease", "Ensnaring Strike", "Entangle", "Fog Cloud", "Goodberry", "Hunter’s Mark", "Jump", "Longstrider", "Speak with Animals")),
        "Sorcerer": _entries("Sorcerer", 67, 1, ("Burning Hands", "Charm Person", "Chromatic Orb", "Color Spray", "Comprehend Languages", "Detect Magic", "Disguise Self", "Expeditious Retreat", "False Life", "Feather Fall", "Fog Cloud", "Grease", "Ice Knife", "Jump", "Mage Armor", "Magic Missile", "Ray of Sickness", "Shield", "Silent Image", "Sleep", "Thunderwave")),
        "Warlock": _entries("Warlock", 74, 1, ("Bane", "Charm Person", "Comprehend Languages", "Detect Magic", "Expeditious Retreat", "Hellish Rebuke", "Hex", "Hideous Laughter", "Illusory Script", "Protection from Evil and Good", "Speak with Animals", "Unseen Servant")),
        "Wizard": _entries("Wizard", 79, 1, ("Alarm", "Burning Hands", "Charm Person", "Chromatic Orb", "Color Spray", "Comprehend Languages", "Detect Magic", "Disguise Self", "Expeditious Retreat", "False Life", "Feather Fall", "Find Familiar", "Floating Disk", "Fog Cloud", "Grease", "Hideous Laughter", "Ice Knife", "Identify", "Illusory Script", "Jump", "Longstrider", "Mage Armor", "Magic Missile", "Protection from Evil and Good", "Ray of Sickness", "Shield", "Silent Image", "Sleep", "Thunderwave", "Unseen Servant")),
    }
)


SRD_LEVEL_TWO_SPELL_LISTS: Mapping[str, tuple[SRDSpellListEntry, ...]] = MappingProxyType(
    {
        "Bard": _entries("Bard", 33, 2, ("Aid", "Animal Messenger", "Blindness/Deafness", "Calm Emotions", "Detect Thoughts", "Enhance Ability", "Enlarge/Reduce", "Enthrall", "Heat Metal", "Hold Person", "Invisibility", "Knock", "Lesser Restoration", "Locate Animals or Plants", "Locate Object", "Magic Mouth", "Mirror Image", "See Invisibility", "Shatter", "Silence", "Suggestion", "Zone of Truth")),
        "Cleric": _entries("Cleric", 38, 2, ("Aid", "Augury", "Blindness/Deafness", "Calm Emotions", "Continual Flame", "Enhance Ability", "Find Traps", "Gentle Repose", "Hold Person", "Lesser Restoration", "Locate Object", "Prayer of Healing", "Protection from Poison", "Silence", "Spiritual Weapon", "Warding Bond", "Zone of Truth")),
        "Druid": _entries("Druid", 43, 2, ("Aid", "Animal Messenger", "Augury", "Barkskin", "Continual Flame", "Darkvision", "Enhance Ability", "Enlarge/Reduce", "Find Traps", "Flame Blade", "Flaming Sphere", "Gust of Wind", "Heat Metal", "Hold Person", "Lesser Restoration", "Locate Animals or Plants", "Locate Object", "Moonbeam", "Pass without Trace", "Protection from Poison", "Spike Growth")),
        "Paladin": _entries("Paladin", 55, 2, ("Aid", "Find Steed", "Gentle Repose", "Lesser Restoration", "Locate Object", "Magic Weapon", "Prayer of Healing", "Protection from Poison", "Shining Smite", "Warding Bond", "Zone of Truth")),
        "Ranger": _entries("Ranger", 60, 2, ("Aid", "Animal Messenger", "Barkskin", "Darkvision", "Enhance Ability", "Find Traps", "Gust of Wind", "Lesser Restoration", "Locate Animals or Plants", "Locate Object", "Magic Weapon", "Pass without Trace", "Protection from Poison", "Silence", "Spike Growth")),
        "Sorcerer": _entries("Sorcerer", 67, 2, ("Alter Self", "Blindness/Deafness", "Blur", "Darkness", "Darkvision", "Detect Thoughts", "Dragon’s Breath", "Enhance Ability", "Enlarge/Reduce", "Flame Blade", "Flaming Sphere", "Gust of Wind", "Hold Person", "Invisibility", "Knock", "Levitate", "Magic Weapon", "Mirror Image", "Misty Step", "Scorching Ray", "See Invisibility", "Shatter", "Spider Climb", "Suggestion", "Web")),
        "Warlock": _entries("Warlock", 74, 2, ("Darkness", "Enthrall", "Hold Person", "Invisibility", "Mind Spike", "Mirror Image", "Misty Step", "Ray of Enfeeblement", "Spider Climb", "Suggestion")),
        "Wizard": _entries("Wizard", 79, 2, ("Acid Arrow", "Alter Self", "Arcane Lock", "Arcanist’s Magic Aura", "Augury", "Blindness/Deafness", "Blur", "Continual Flame", "Darkness", "Darkvision", "Detect Thoughts", "Dragon’s Breath", "Enhance Ability", "Enlarge/Reduce", "Flaming Sphere", "Gentle Repose", "Gust of Wind", "Hold Person", "Invisibility", "Knock", "Levitate", "Locate Object", "Magic Mouth", "Magic Weapon", "Mind Spike", "Mirror Image", "Misty Step", "Ray of Enfeeblement", "Rope Trick", "Scorching Ray", "See Invisibility", "Shatter", "Spider Climb", "Suggestion", "Web")),
    }
)


SRD_LEVEL_THREE_SPELL_LISTS: Mapping[str, tuple[SRDSpellListEntry, ...]] = MappingProxyType(
    {
        "Bard": _entries("Bard", 34, 3, ("Bestow Curse", "Clairvoyance", "Dispel Magic", "Fear", "Glyph of Warding", "Hypnotic Pattern", "Major Image", "Mass Healing Word", "Nondetection", "Plant Growth", "Sending", "Slow", "Speak with Dead", "Speak with Plants", "Stinking Cloud", "Tiny Hut", "Tongues")),
        "Cleric": _entries("Cleric", 39, 3, ("Animate Dead", "Beacon of Hope", "Bestow Curse", "Clairvoyance", "Create Food and Water", "Daylight", "Dispel Magic", "Glyph of Warding", "Magic Circle", "Mass Healing Word", "Meld into Stone", "Protection from Energy", "Remove Curse", "Revivify", "Sending", "Speak with Dead", "Spirit Guardians", "Tongues", "Water Walk")),
        "Druid": _entries("Druid", 43, 3, ("Call Lightning", "Conjure Animals", "Daylight", "Dispel Magic", "Meld into Stone", "Plant Growth", "Protection from Energy", "Revivify", "Sleet Storm", "Speak with Plants", "Water Breathing", "Water Walk", "Wind Wall")),
        "Paladin": _entries("Paladin", 56, 3, ("Create Food and Water", "Daylight", "Dispel Magic", "Magic Circle", "Remove Curse", "Revivify")),
        "Ranger": _entries("Ranger", 60, 3, ("Conjure Animals", "Daylight", "Dispel Magic", "Meld into Stone", "Nondetection", "Plant Growth", "Protection from Energy", "Revivify", "Speak with Plants", "Water Breathing", "Water Walk", "Wind Wall")),
        "Sorcerer": _entries("Sorcerer", 67, 3, ("Blink", "Clairvoyance", "Counterspell", "Daylight", "Dispel Magic", "Fear", "Fireball", "Fly", "Gaseous Form", "Haste", "Hypnotic Pattern", "Lightning Bolt", "Major Image", "Protection from Energy", "Sleet Storm", "Slow", "Stinking Cloud", "Tongues", "Vampiric Touch", "Water Breathing", "Water Walk")),
        "Warlock": _entries("Warlock", 74, 3, ("Counterspell", "Dispel Magic", "Fear", "Fly", "Gaseous Form", "Hypnotic Pattern", "Magic Circle", "Major Image", "Remove Curse", "Tongues", "Vampiric Touch")),
        "Wizard": _entries("Wizard", 79, 3, ("Animate Dead", "Bestow Curse", "Blink", "Clairvoyance", "Counterspell", "Dispel Magic", "Fear", "Fireball", "Fly", "Gaseous Form", "Glyph of Warding", "Haste", "Hypnotic Pattern", "Lightning Bolt", "Magic Circle", "Major Image", "Nondetection", "Phantom Steed", "Protection from Energy", "Remove Curse", "Sending", "Sleet Storm", "Slow", "Speak with Dead", "Stinking Cloud", "Tiny Hut", "Tongues", "Vampiric Touch", "Water Breathing")),
    }
)


SRD_LEVEL_FOUR_SPELL_LISTS: Mapping[str, tuple[SRDSpellListEntry, ...]] = MappingProxyType(
    {
        "Bard": _entries("Bard", 34, 4, ("Charm Monster", "Compulsion", "Confusion", "Dimension Door", "Freedom of Movement", "Greater Invisibility", "Hallucinatory Terrain", "Locate Creature", "Phantasmal Killer", "Polymorph")),
        "Cleric": _entries("Cleric", 39, 4, ("Aura of Life", "Banishment", "Control Water", "Death Ward", "Divination", "Freedom of Movement", "Guardian of Faith", "Locate Creature", "Stone Shape")),
        "Druid": _entries("Druid", 44, 4, ("Blight", "Charm Monster", "Confusion", "Conjure Minor Elementals", "Conjure Woodland Beings", "Control Water", "Divination", "Dominate Beast", "Fire Shield", "Freedom of Movement", "Giant Insect", "Hallucinatory Terrain", "Ice Storm", "Locate Creature", "Polymorph", "Stone Shape", "Stoneskin", "Wall of Fire")),
        "Paladin": _entries("Paladin", 56, 4, ("Aura of Life", "Banishment", "Death Ward", "Locate Creature")),
        "Ranger": _entries("Ranger", 60, 4, ("Conjure Woodland Beings", "Dominate Beast", "Freedom of Movement", "Locate Creature", "Stoneskin")),
        "Sorcerer": _entries("Sorcerer", 68, 4, ("Banishment", "Blight", "Charm Monster", "Confusion", "Dimension Door", "Dominate Beast", "Fire Shield", "Greater Invisibility", "Ice Storm", "Polymorph", "Stoneskin", "Vitriolic Sphere", "Wall of Fire")),
        "Warlock": _entries("Warlock", 74, 4, ("Banishment", "Blight", "Charm Monster", "Dimension Door", "Hallucinatory Terrain")),
        "Wizard": _entries("Wizard", 80, 4, ("Arcane Eye", "Banishment", "Black Tentacles", "Blight", "Charm Monster", "Confusion", "Conjure Minor Elementals", "Control Water", "Dimension Door", "Divination", "Fabricate", "Faithful Hound", "Fire Shield", "Greater Invisibility", "Hallucinatory Terrain", "Ice Storm", "Locate Creature", "Phantasmal Killer", "Polymorph", "Private Sanctum", "Resilient Sphere", "Secret Chest", "Stone Shape", "Stoneskin", "Vitriolic Sphere", "Wall of Fire")),
    }
)


SRD_LEVEL_FIVE_SPELL_LISTS: Mapping[str, tuple[SRDSpellListEntry, ...]] = MappingProxyType(
    {
        "Bard": _entries("Bard", 34, 5, ("Animate Objects", "Awaken", "Dominate Person", "Dream", "Geas", "Greater Restoration", "Hold Monster", "Legend Lore", "Mass Cure Wounds", "Mislead", "Modify Memory", "Planar Binding", "Raise Dead", "Scrying", "Seeming", "Telepathic Bond", "Teleportation Circle")),
        "Cleric": _entries("Cleric", 39, 5, ("Commune", "Contagion", "Dispel Evil and Good", "Flame Strike", "Geas", "Greater Restoration", "Hallow", "Insect Plague", "Legend Lore", "Mass Cure Wounds", "Planar Binding", "Raise Dead", "Scrying")),
        "Druid": _entries("Druid", 44, 5, ("Antilife Shell", "Awaken", "Commune with Nature", "Cone of Cold", "Conjure Elemental", "Contagion", "Geas", "Greater Restoration", "Insect Plague", "Mass Cure Wounds", "Planar Binding", "Reincarnate", "Scrying", "Tree Stride", "Wall of Stone")),
        "Paladin": _entries("Paladin", 56, 5, ("Dispel Evil and Good", "Geas", "Greater Restoration", "Raise Dead")),
        "Ranger": _entries("Ranger", 60, 5, ("Commune with Nature", "Greater Restoration", "Tree Stride")),
        "Sorcerer": _entries("Sorcerer", 68, 5, ("Animate Objects", "Arcane Hand", "Cloudkill", "Cone of Cold", "Creation", "Dominate Person", "Hold Monster", "Insect Plague", "Seeming", "Telekinesis", "Teleportation Circle", "Wall of Stone")),
        "Warlock": _entries("Warlock", 74, 5, ("Contact Other Plane", "Dream", "Hold Monster", "Mislead", "Planar Binding", "Scrying", "Teleportation Circle")),
        "Wizard": _entries("Wizard", 80, 5, ("Animate Objects", "Arcane Hand", "Cloudkill", "Cone of Cold", "Conjure Elemental", "Contact Other Plane", "Creation", "Dominate Person", "Dream", "Geas", "Hold Monster", "Legend Lore", "Mislead", "Modify Memory", "Passwall", "Planar Binding", "Scrying", "Seeming", "Summon Dragon", "Telekinesis", "Telepathic Bond", "Teleportation Circle", "Wall of Force", "Wall of Stone")),
    }
)


SRD_LEVEL_SIX_SPELL_LISTS: Mapping[str, tuple[SRDSpellListEntry, ...]] = MappingProxyType(
    {
        "Bard": _entries("Bard", 34, 6, ("Eyebite", "Find the Path", "Guards and Wards", "Heroes’ Feast", "Irresistible Dance", "Mass Suggestion", "Programmed Illusion", "True Seeing")),
        "Cleric": _entries("Cleric", 39, 6, ("Blade Barrier", "Create Undead", "Find the Path", "Forbiddance", "Harm", "Heal", "Heroes’ Feast", "Planar Ally", "Sunbeam", "True Seeing", "Word of Recall")),
        "Druid": _entries("Druid", 44, 6, ("Conjure Fey", "Find the Path", "Flesh to Stone", "Heal", "Heroes’ Feast", "Move Earth", "Sunbeam", "Transport via Plants", "Wall of Thorns", "Wind Walk")),
        "Paladin": (),
        "Ranger": (),
        "Sorcerer": _entries("Sorcerer", 68, 6, ("Chain Lightning", "Circle of Death", "Disintegrate", "Eyebite", "Flesh to Stone", "Freezing Sphere", "Globe of Invulnerability", "Mass Suggestion", "Move Earth", "Sunbeam", "True Seeing")),
        "Warlock": _entries("Warlock", 74, 6, ("Circle of Death", "Create Undead", "Eyebite", "True Seeing")),
        "Wizard": _entries("Wizard", 80, 6, ("Chain Lightning", "Circle of Death", "Contingency", "Create Undead", "Disintegrate", "Eyebite", "Flesh to Stone", "Freezing Sphere", "Globe of Invulnerability", "Guards and Wards", "Instant Summons", "Irresistible Dance", "Magic Jar", "Mass Suggestion", "Move Earth", "Programmed Illusion", "Sunbeam", "True Seeing", "Wall of Ice")),
    }
)


SRD_LEVEL_SEVEN_SPELL_LISTS: Mapping[str, tuple[SRDSpellListEntry, ...]] = MappingProxyType(
    {
        "Bard": _entries("Bard", 34, 7, ("Arcane Sword", "Etherealness", "Forcecage", "Magnificent Mansion", "Mirage Arcane", "Prismatic Spray", "Project Image", "Regenerate", "Resurrection", "Symbol", "Teleport")),
        "Cleric": _entries("Cleric", 39, 7, ("Conjure Celestial", "Divine Word", "Etherealness", "Fire Storm", "Plane Shift", "Regenerate", "Resurrection", "Symbol")),
        "Druid": _entries("Druid", 44, 7, ("Fire Storm", "Mirage Arcane", "Plane Shift", "Regenerate", "Reverse Gravity", "Symbol")),
        "Paladin": (),
        "Ranger": (),
        "Sorcerer": _entries("Sorcerer", 68, 7, ("Delayed Blast Fireball", "Etherealness", "Finger of Death", "Fire Storm", "Plane Shift", "Prismatic Spray", "Reverse Gravity", "Teleport")),
        "Warlock": _entries("Warlock", 74, 7, ("Etherealness", "Finger of Death", "Forcecage", "Plane Shift")),
        "Wizard": _entries("Wizard", 80, 7, ("Arcane Sword", "Delayed Blast Fireball", "Etherealness", "Finger of Death", "Forcecage", "Magnificent Mansion", "Mirage Arcane", "Plane Shift", "Prismatic Spray", "Project Image", "Reverse Gravity", "Sequester", "Simulacrum", "Symbol", "Teleport")),
    }
)


SRD_LEVEL_EIGHT_SPELL_LISTS: Mapping[str, tuple[SRDSpellListEntry, ...]] = MappingProxyType(
    {
        "Bard": _entries("Bard", 34, 8, ("Antipathy/Sympathy", "Befuddlement", "Dominate Monster", "Glibness", "Mind Blank", "Power Word Stun")),
        "Cleric": _entries("Cleric", 39, 8, ("Antimagic Field", "Control Weather", "Earthquake", "Holy Aura", "Sunburst")),
        "Druid": _entries("Druid", 44, 8, ("Animal Shapes", "Antipathy/Sympathy", "Befuddlement", "Control Weather", "Earthquake", "Incendiary Cloud", "Sunburst", "Tsunami")),
        "Paladin": (),
        "Ranger": (),
        "Sorcerer": _entries("Sorcerer", 68, 8, ("Demiplane", "Dominate Monster", "Earthquake", "Incendiary Cloud", "Power Word Stun", "Sunburst")),
        "Warlock": _entries("Warlock", 75, 8, ("Befuddlement", "Demiplane", "Dominate Monster", "Glibness", "Power Word Stun")),
        "Wizard": _entries("Wizard", 81, 8, ("Antimagic Field", "Antipathy/Sympathy", "Befuddlement", "Clone", "Control Weather", "Demiplane", "Dominate Monster", "Incendiary Cloud", "Maze", "Mind Blank", "Power Word Stun", "Sunburst")),
    }
)


SRD_LEVEL_NINE_SPELL_LISTS: Mapping[str, tuple[SRDSpellListEntry, ...]] = MappingProxyType(
    {
        "Bard": _entries("Bard", 34, 9, ("Foresight", "Power Word Heal", "Power Word Kill", "Prismatic Wall", "True Polymorph")),
        "Cleric": _entries("Cleric", 39, 9, ("Astral Projection", "Gate", "Mass Heal", "Power Word Heal", "True Resurrection")),
        "Druid": _entries("Druid", 44, 9, ("Foresight", "Shapechange", "Storm of Vengeance", "True Resurrection")),
        "Paladin": (),
        "Ranger": (),
        "Sorcerer": _entries("Sorcerer", 68, 9, ("Gate", "Meteor Swarm", "Power Word Kill", "Time Stop", "Wish")),
        "Warlock": _entries("Warlock", 75, 9, ("Astral Projection", "Foresight", "Gate", "Imprisonment", "Power Word Kill", "True Polymorph", "Weird")),
        "Wizard": _entries("Wizard", 81, 9, ("Astral Projection", "Foresight", "Gate", "Imprisonment", "Meteor Swarm", "Power Word Kill", "Prismatic Wall", "Shapechange", "Time Stop", "True Polymorph", "Weird", "Wish")),
    }
)
