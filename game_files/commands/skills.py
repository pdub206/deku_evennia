"""
Skills command — displays all skill bonuses for the character.
"""

from commands.command import Command
from world.chargen_data import ABILITY_SHORT, SKILLS, ability_modifier

_SEP = "|x" + "─" * 60 + "|n"

# Ordered list for display (alphabetical, split into two columns of 9).
_SKILL_NAMES = list(SKILLS.keys())  # already defined in alphabetical order
_LEFT_COL = _SKILL_NAMES[:9]
_RIGHT_COL = _SKILL_NAMES[9:]

_ABILITY_DB = {
    "Strength": "strength",
    "Dexterity": "dexterity",
    "Constitution": "constitution",
    "Intelligence": "intelligence",
    "Wisdom": "wisdom",
    "Charisma": "charisma",
}


class CmdSkills(Command):
    """
    Display your skill bonuses.

    Usage:
      skills

    Shows all 18 skills, their governing ability, whether you are
    proficient, and your total bonus (ability modifier + proficiency
    bonus if applicable).
    """

    key = "skills"
    aliases = ["sk"]
    help_category = "Character"

    def func(self) -> None:
        char = self.caller
        proficiencies: list[str] = char.db.skill_proficiencies or []
        prof_bonus: int = char.db.proficiency_bonus or 2

        def _entry(skill: str) -> str:
            ability = SKILLS[skill]
            abbr = ABILITY_SHORT.get(ability, ability[:3].upper())
            score: int = char.attributes.get(_ABILITY_DB[ability]) or 8
            mod = ability_modifier(score)
            is_prof = skill in proficiencies
            bonus = mod + (prof_bonus if is_prof else 0)
            prof_marker = "|g[P]|n" if is_prof else "   "
            bonus_str = f"{bonus:+d}"
            return f"{skill:<18} {abbr}  {prof_marker}  {bonus_str:>3}"

        out = _SEP + "\n"
        out += f"  {'Skill':<18} {'Abl'}  {'[P]'}  {'Bon':>3}"
        out += f"    {'Skill':<18} {'Abl'}  {'[P]'}  {'Bon':>3}\n"
        out += _SEP + "\n"

        for left, right in zip(_LEFT_COL, _RIGHT_COL):
            out += f"  {_entry(left)}    {_entry(right)}\n"

        out += _SEP + "\n"
        out += f"  |y[P]|n = proficient  (proficiency bonus: |w+{prof_bonus}|n)\n"

        self.caller.msg(out)
