"""
Character sheet command — displays identity, ability scores, and combat stats.
"""

import time

from commands.command import Command
from world.chargen_data import (
    ABILITY_NAMES,
    ABILITY_SHORT,
    CARRY_CAPACITY_MULTIPLIER,
    ability_modifier,
)

_GENDER_LABELS = {
    "male": "Male",
    "female": "Female",
    "nonbinary": "Nonbinary",
    "unspecified": "Prefer not to say",
}

_SEP = "|x" + "─" * 60 + "|n"


def _format_time_played(seconds: float) -> str:
    total = int(seconds)
    days, remainder = divmod(total, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return ", ".join(parts)


class CmdSheet(Command):
    """
    Display your character sheet.

    Usage:
      score
      sheet
      stats
      attributes

    Shows your character's identity, ability scores, and combat stats.
    For skills, proficiencies, and spells use the relevant commands.
    """

    key = "score"
    aliases = ["sheet", "sc"]
    help_category = "Character"

    def func(self) -> None:
        char = self.caller

        # --- Identity ---
        name = char.key
        gender = _GENDER_LABELS.get(char.db.gender or "", char.db.gender or "Unknown")
        age = str(char.db.age) if char.db.age else "Unknown"
        species = char.db.species or "Unknown"
        size = char.db.size or "Medium"
        char_class = char.db.char_class or "Unknown"
        background = char.db.background or "Unknown"
        alignment = char.db.alignment or "Unknown"
        level = char.db.level or 1
        xp = char.db.xp or 0
        prof_bonus = char.db.proficiency_bonus or 2

        # --- Time played (accumulated + live current session) ---
        total_seconds: float = char.db.time_played or 0.0
        login_time = char.db.session_login_time
        if login_time is None and char.account:
            # at_post_puppet didn't fire for this session (e.g. server hot-reload
            # while already logged in) — start tracking from now.
            char.db.session_login_time = time.time()
            login_time = char.db.session_login_time
        if login_time:
            total_seconds += time.time() - login_time
        time_str = _format_time_played(total_seconds)

        # --- Ability scores ---
        ability_db_names = {
            "Strength": "strength",
            "Dexterity": "dexterity",
            "Constitution": "constitution",
            "Intelligence": "intelligence",
            "Wisdom": "wisdom",
            "Charisma": "charisma",
        }
        scores: dict[str, int] = {
            ab: (char.attributes.get(ability_db_names[ab]) or 8) for ab in ABILITY_NAMES
        }

        # --- Combat stats ---
        hp_cur = char.db.hp_current or 0
        hp_max = char.db.hp_max or 0
        ac = char.db.armor_class or 10
        initiative = char.db.initiative or 0
        speed = char.db.speed or 30
        hit_die = char.db.hit_die or 8
        passive_perc = char.db.passive_perception or 10

        languages = char.db.languages or ["Common"]

        # --- Carry capacity (SRD p.178): STR × multiplier based on size ---
        carry_mult = CARRY_CAPACITY_MULTIPLIER.get(size, 15.0)
        carry_capacity = int(scores["Strength"] * carry_mult)

        # --- Render ---
        out = _SEP + "\n"

        out += f"  |yName:|n       {name:<18}  |ySpecies:|n    {species}\n"
        out += f"  |yGender:|n     {gender:<18}  |yClass:|n      {char_class}\n"
        out += f"  |yAge:|n        {age:<18}  |yBackground:|n {background}\n"
        out += f"  |yAlignment:|n  {alignment:<18}  |ySize:|n       {size}\n"
        out += (
            f"  |yLevel:|n  {level}    |yXP:|n  {xp}"
            f"    |yProficiency Bonus:|n  +{prof_bonus}\n"
        )
        out += f"  |yTime Played:|n  {time_str}\n"

        out += _SEP + "\n"
        out += "  |yAbility Scores|n                   |yCombat|n\n"

        combat_lines = [
            f"|yHP:|n           {hp_cur}/{hp_max}",
            f"|yArmor Class:|n   {ac}",
            f"|yInitiative:|n    {initiative:+d}",
            f"|ySpeed:|n         {speed} ft.",
            f"|yHit Die:|n       d{hit_die}",
            f"|yPassive Perc:|n  {passive_perc}",
        ]
        for i, ab in enumerate(ABILITY_NAMES):
            sc = scores[ab]
            mod = ability_modifier(sc)
            left = f"  {ABILITY_SHORT[ab]}: {sc:2d}  ({mod:+d})"
            right = combat_lines[i] if i < len(combat_lines) else ""
            out += f"{left:<28}  {right}\n"

        out += f"  |yCarry Capacity:|n  {carry_capacity} lb.\n"
        out += f"  |yLanguages:|n      {', '.join(languages)}\n"
        out += _SEP

        self.caller.msg(out)
