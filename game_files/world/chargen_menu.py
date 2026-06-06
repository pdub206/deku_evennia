"""
SRD 5.2.1 character-generation EvMenu.

Registered as CHARGEN_MENU in settings.py.  The contrib's ContribCmdCharCreate
creates a temporary character object, sets char.db.chargen_step to
"menunode_welcome", and opens this menu.  Each decision node writes its own
name back to chargen_step so the player can exit and resume later.

Temporary state is stored on the character as db.chargen_* attributes; they
are all removed when menunode_end finalises the character.
"""

from __future__ import annotations

from typing import Any

from evennia.utils import dedent
from world.chargen_data import (
    ABILITY_NAMES,
    ABILITY_SHORT,
    ALIGNMENTS,
    BACKGROUNDS,
    CLASSES,
    MAX_AGE,
    MIN_AGE,
    POINT_BUY_COSTS,
    POINT_BUY_MAX,
    POINT_BUY_MIN,
    POINT_BUY_TOTAL,
    SKILLS,
    SPECIES,
    STANDARD_ARRAY,
    STANDARD_ARRAY_BY_CLASS,
    STANDARD_LANGUAGES,
    ability_modifier,
    roll_4d6_drop_lowest,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _char(caller: Any):
    """Return the in-progress character attached to the session."""
    return caller.new_char


def _fmt_ability_block(scores: dict[str, int]) -> str:
    """Return a formatted two-column ability score block."""
    lines = []
    abilities = ABILITY_NAMES
    for i in range(0, len(abilities), 2):
        left = abilities[i]
        right = abilities[i + 1] if i + 1 < len(abilities) else None
        lscore = scores.get(left, 8)
        lmod = ability_modifier(lscore)
        lmod_str = f"+{lmod}" if lmod >= 0 else str(lmod)
        left_col = f"|w{ABILITY_SHORT[left]}|n {lscore:2d} ({lmod_str:3s})"
        if right:
            rscore = scores.get(right, 8)
            rmod = ability_modifier(rscore)
            rmod_str = f"+{rmod}" if rmod >= 0 else str(rmod)
            right_col = f"|w{ABILITY_SHORT[right]}|n {rscore:2d} ({rmod_str:3s})"
            lines.append(f"  {left_col:<22}{right_col}")
        else:
            lines.append(f"  {left_col}")
    return "\n".join(lines)


def _primary_ability_str(cls_data: dict) -> str:
    pa = cls_data["primary_ability"]
    return pa if isinstance(pa, str) else " / ".join(pa)


# ---------------------------------------------------------------------------
# Step 0: Welcome
# ---------------------------------------------------------------------------


def menunode_welcome(caller: Any, **kwargs):
    char = _char(caller)
    char.db.chargen_step = "menunode_welcome"
    text = dedent("""\
        |wWelcome to Character Creation!|n

        You are about to create your adventurer using the rules from the
        |cSystem Reference Document 5.2.1|n.

        The process has nine steps:
          |w1.|n Choose your |yName|n
          |w2.|n Choose your |yGender|n
          |w3.|n Choose a |yClass|n
          |w4.|n Choose your |yOrigin|n (background, skills & species)
          |w5.|n Choose your |yAge|n
          |w6.|n Choose |yLanguages|n
          |w7.|n Determine |yAbility Scores|n
          |w8.|n Choose your |yAlignment|n
          |w9.|n Review and finish

        You can exit at any time (|wq|n or |wquit|n) and resume later by
        typing |wcharcreate|n again.
    """)
    options = {"desc": "Begin character creation", "goto": "menunode_choose_name"}
    return text, options


# ---------------------------------------------------------------------------
# Step 1: Class
# ---------------------------------------------------------------------------


def menunode_choose_class(caller: Any, **kwargs):
    char = _char(caller)
    char.db.chargen_step = "menunode_choose_class"

    header = "|wStep 3 — Choose a Class|n\n\n"
    header += f"{'Class':<12} {'Likes':<16} {'Primary Ability'}\n" + "-" * 50 + "\n"
    for name, data in CLASSES.items():
        pa = _primary_ability_str(data)
        header += f"{name:<12} {data['likes']:<16} {pa}\n"
    header += "\nSelect a class to read more about it before choosing."

    options = []
    for name in CLASSES:
        options.append(
            {
                "desc": name,
                "goto": ("menunode_class_detail", {"selected_class": name}),
            }
        )
    return header, options


def menunode_class_detail(
    caller: Any, raw_string: str = "", selected_class: str = "", **kwargs
):
    if not selected_class:
        return "menunode_choose_class"

    data = CLASSES[selected_class]
    pa = _primary_ability_str(data)
    suggestion = STANDARD_ARRAY_BY_CLASS.get(selected_class, {})
    sug_line = "  " + ", ".join(
        f"{ABILITY_SHORT[a]}: {suggestion[a]}" for a in ABILITY_NAMES if a in suggestion
    )

    text = dedent(f"""\
        |w{selected_class}|n

        |yPrimary Ability:|n  {pa}
        |yHit Die:|n         d{data['hit_die']}
        |yLevel-1 HP:|n      {data['hp_base']} + CON modifier
        |ySaving Throws:|n   {', '.join(data['saving_throws'])}
        |yArmor Training:|n  {', '.join(data['armor_training']) or 'None'}
        |yWeapons:|n         {data['weapon_profs']}
        |ySkill Picks:|n     {data['skill_choices']} from: {', '.join(data['skills_available'])}

        |ySuggested Standard Array:|n
{sug_line}

        Do you want to play a |w{selected_class}|n?
    """)
    options = [
        {
            "key": ("Yes", "y"),
            "desc": f"Play as a {selected_class}",
            "goto": (_set_class, {"selected_class": selected_class}),
        },
        {
            "key": ("No", "n"),
            "desc": "Return to class list",
            "goto": "menunode_choose_class",
        },
    ]
    return text, options


def _set_class(caller: Any, raw_string: str = "", selected_class: str = "", **kwargs):
    if not selected_class:
        return "menunode_choose_class"
    char = _char(caller)
    char.db.chargen_class = selected_class
    # Clear any previously chosen class skills; the pool may have changed.
    char.attributes.remove("chargen_skill_proficiencies")
    if char.db.chargen_from_review:
        # Don't clear from_review — skills step needs it to route back to review.
        return "menunode_choose_skills"
    return "menunode_choose_background"


# ---------------------------------------------------------------------------
# Step 2a: Background
# ---------------------------------------------------------------------------


def menunode_choose_background(caller: Any, **kwargs):
    char = _char(caller)
    char.db.chargen_step = "menunode_choose_background"

    text = dedent("""\
        |wStep 4 — Choose a Background|n

        Your background represents the place and occupation most formative
        for your character.  It grants skill proficiencies, a tool
        proficiency, starting equipment, and a feat.

        Select a background to learn more.
    """)
    options = []
    for name in BACKGROUNDS:
        options.append(
            {
                "desc": name,
                "goto": ("menunode_background_detail", {"selected_bg": name}),
            }
        )
    options.append(
        {
            "key": ("Back", "back", "b"),
            "desc": "Go back to class selection",
            "goto": "menunode_choose_class",
        }
    )
    return text, options


def menunode_background_detail(
    caller: Any, raw_string: str = "", selected_bg: str = "", **kwargs
):
    if not selected_bg:
        return "menunode_choose_background"

    data = BACKGROUNDS[selected_bg]
    text = dedent(f"""\
        |w{selected_bg}|n

        {data['description']}

        |yAbility Bonuses:|n  Choose from {', '.join(data['ability_options'])}
                          (+2 one / +1 another, or +1 all three)
        |ySkill Proficiencies:|n  {', '.join(data['skill_proficiencies'])}
        |yTool Proficiency:|n     {data['tool_proficiency']}
        |yFeat:|n                 {data['feat']}
        |yEquipment:|n            {data['equipment']}

        Do you want to take the |w{selected_bg}|n background?
    """)
    options = [
        {
            "key": ("Yes", "y"),
            "desc": f"Take the {selected_bg} background",
            "goto": (_set_background, {"selected_bg": selected_bg}),
        },
        {
            "key": ("No", "n"),
            "desc": "Return to background list",
            "goto": "menunode_choose_background",
        },
    ]
    return text, options


def _set_background(caller: Any, raw_string: str = "", selected_bg: str = "", **kwargs):
    if not selected_bg:
        return "menunode_choose_background"
    char = _char(caller)
    char.db.chargen_background = selected_bg
    if char.db.chargen_from_review:
        char.db.chargen_from_review = False
        return "menunode_review"
    return "menunode_choose_skills"


# ---------------------------------------------------------------------------
# Step 2b: Species
# ---------------------------------------------------------------------------


def menunode_choose_species(caller: Any, **kwargs):
    char = _char(caller)
    char.db.chargen_step = "menunode_choose_species"

    text = dedent("""\
        |wStep 4 — Choose a Species|n

        Your species determines your character's ancestry.  Each species
        provides traits, a size, and a base movement speed.

        Select a species to learn more.
    """)
    options = []
    for name in SPECIES:
        data = SPECIES[name]
        options.append(
            {
                "desc": f"{name} ({data['size']}, Speed {data['speed']} ft.)",
                "goto": ("menunode_species_detail", {"selected_species": name}),
            }
        )
    options.append(
        {
            "key": ("Back", "back", "b"),
            "desc": "Go back to background selection",
            "goto": "menunode_choose_background",
        }
    )
    return text, options


def menunode_species_detail(
    caller: Any, raw_string: str = "", selected_species: str = "", **kwargs
):
    if not selected_species:
        return "menunode_choose_species"

    data = SPECIES[selected_species]
    traits_str = ", ".join(data["traits"])

    if data.get("size_choice"):
        size_line = (
            f"|ySize:|n   Medium or Small (chosen at creation)\n"
            f"         Medium: {data['height_medium']}\n"
            f"         Small:  {data['height_small']}"
        )
    else:
        size_line = f"|ySize:|n   {data['size']} ({data['height']})"

    text = dedent(f"""\
        |w{selected_species}|n

        {data['description']}

        {size_line}
        |ySpeed:|n  {data['speed']} ft.
        |yTraits:|n {traits_str}

        Do you want to play as a |w{selected_species}|n?
    """)
    options = [
        {
            "key": ("Yes", "y"),
            "desc": f"Play as a {selected_species}",
            "goto": (_set_species, {"selected_species": selected_species}),
        },
        {
            "key": ("No", "n"),
            "desc": "Return to species list",
            "goto": "menunode_choose_species",
        },
    ]
    return text, options


def _set_species(
    caller: Any, raw_string: str = "", selected_species: str = "", **kwargs
):
    if not selected_species:
        return "menunode_choose_species"
    char = _char(caller)
    char.db.chargen_species = selected_species
    # Species with a size choice require an extra step before continuing.
    # chargen_from_review is preserved so _set_size can handle the redirect.
    if SPECIES.get(selected_species, {}).get("size_choice"):
        return "menunode_choose_size"
    if char.db.chargen_from_review:
        char.db.chargen_from_review = False
        return "menunode_review"
    return "menunode_choose_age"


def menunode_choose_size(caller: Any, **kwargs):
    """Prompt the player to pick Medium or Small for species that allow it."""
    char = _char(caller)
    species = char.db.chargen_species or "Human"
    data = SPECIES.get(species, {})
    text = dedent(f"""\
        |wChoose Your Size — {species}|n

        As a {species}, you may be Medium or Small.

        |yMedium:|n  {data.get('height_medium', 'standard humanoid height')}
        |ySmall:|n   {data.get('height_small', 'shorter than most humanoids')}

        Size has no mechanical effect at this time, but affects how your
        character is described in the world.
    """)
    options = [
        {
            "key": ("Medium", "m"),
            "desc": "Medium",
            "goto": (_set_size, {"size": "Medium"}),
        },
        {
            "key": ("Small", "s"),
            "desc": "Small",
            "goto": (_set_size, {"size": "Small"}),
        },
    ]
    return text, options


def _set_size(caller: Any, raw_string: str = "", size: str = "Medium", **kwargs):
    char = _char(caller)
    char.db.chargen_size = size
    if char.db.chargen_from_review:
        char.db.chargen_from_review = False
        return "menunode_review"
    return "menunode_choose_age"


# ---------------------------------------------------------------------------
# Step 2c: Skills
# ---------------------------------------------------------------------------


def menunode_choose_skills(caller: Any, raw_string: str = "", **kwargs):
    char = _char(caller)
    char.db.chargen_step = "menunode_choose_skills"

    cls_name = char.db.chargen_class or ""
    bg_name = char.db.chargen_background or ""
    cls_data = CLASSES.get(cls_name, {})
    num_picks: int = cls_data.get("skill_choices", 2)
    available: list[str] = cls_data.get("skills_available", [])
    bg_skills: list[str] = BACKGROUNDS.get(bg_name, {}).get("skill_proficiencies", [])

    # Class skills the player has already chosen this session.
    class_selected: list[str] = (
        kwargs.get("class_selected") or list(char.db.chargen_skill_proficiencies or [])
    )
    # Filter available pool to exclude skills already granted by background.
    pickable = [s for s in available if s not in bg_skills]

    if len(class_selected) == num_picks:
        # Confirmation screen.
        all_profs = sorted(set(bg_skills + class_selected))
        text = dedent(f"""\
            |wStep 4 — Choose Skills|n

            Your skill proficiencies:
              |yBackground ({bg_name}):|n  {', '.join(bg_skills) or 'none'}
              |yClass ({cls_name}):|n       {', '.join(sorted(class_selected))}

            All proficient skills: {', '.join(all_profs)}

            Confirm these skills?
        """)
        options = [
            {
                "key": ("Yes", "y"),
                "desc": "Confirm and continue",
                "goto": (_confirm_skills, {"class_selected": list(class_selected)}),
            },
            {
                "key": ("No", "n"),
                "desc": "Clear class skill choices and reselect",
                "goto": (_reset_skills, {}),
            },
        ]
        return text, options

    text = dedent(f"""\
        |wStep 4 — Choose Skills|n

        Your background (|w{bg_name}|n) grants: |w{', '.join(bg_skills) or 'none'}|n

        Choose |w{num_picks}|n skill{'' if num_picks == 1 else 's'} from your \
|w{cls_name}|n class.
        Selecting a skill again deselects it.

        Currently selected ({len(class_selected)}/{num_picks}): \
{', '.join(class_selected) if class_selected else '(none)'}
    """)

    options = []
    for skill in pickable:
        ability = SKILLS.get(skill, "")
        abbr = ABILITY_SHORT.get(ability, ability[:3].upper())
        marker = " |g[✓]|n" if skill in class_selected else ""
        options.append(
            {
                "desc": f"{skill} ({abbr}){marker}",
                "goto": (
                    _toggle_skill,
                    {"class_selected": list(class_selected), "skill": skill,
                     "num_picks": num_picks},
                ),
            }
        )
    options.append(
        {
            "key": ("Back", "back", "b"),
            "desc": "Go back to background selection",
            "goto": "menunode_choose_background",
        }
    )
    return text, options


def _toggle_skill(
    caller: Any,
    raw_string: str = "",
    class_selected: list | None = None,
    skill: str = "",
    num_picks: int = 2,
    **kwargs,
):
    if class_selected is None:
        class_selected = []
    if skill in class_selected:
        class_selected.remove(skill)
    elif len(class_selected) < num_picks:
        class_selected.append(skill)
    _char(caller).db.chargen_skill_proficiencies = list(class_selected)
    return ("menunode_choose_skills", {"class_selected": list(class_selected)})


def _confirm_skills(
    caller: Any,
    raw_string: str = "",
    class_selected: list | None = None,
    **kwargs,
):
    char = _char(caller)
    if class_selected is not None:
        char.db.chargen_skill_proficiencies = list(class_selected)
    if char.db.chargen_from_review:
        char.db.chargen_from_review = False
        return "menunode_review"
    return "menunode_choose_species"


def _reset_skills(caller: Any, raw_string: str = "", **kwargs):
    _char(caller).db.chargen_skill_proficiencies = []
    return ("menunode_choose_skills", {"class_selected": []})


# ---------------------------------------------------------------------------
# Step 2d: Languages
# ---------------------------------------------------------------------------


def menunode_choose_languages(caller: Any, raw_string: str = "", **kwargs):
    char = _char(caller)
    char.db.chargen_step = "menunode_choose_languages"

    # Retrieve current selections, preserving across re-entries.
    selected: list[str] = kwargs.get("selected") or char.db.chargen_languages or []

    if len(selected) == 2:
        # Show inline confirmation rather than the selection list.
        text = dedent(f"""\
            |wStep 6 — Choose Languages|n

            You have selected: |wCommon|n (automatic), |w{selected[0]}|n, and |w{selected[1]}|n.

            Proceed with these languages?
        """)
        options = [
            {
                "key": ("Yes", "y"),
                "desc": "Proceed to ability scores",
                "goto": _confirm_languages,
            },
            {
                "key": ("No", "n"),
                "desc": "Start over — clear both and reselect",
                "goto": _reset_languages,
            },
        ]
        return text, options

    text = dedent("""\
        |wStep 6 — Choose Languages|n

        Your character automatically speaks and reads |wCommon|n.
        Choose |w2|n additional languages from the list below.
        Selecting a language a second time will deselect it.
    """)
    text += f"\n  Currently selected: {', '.join(selected) if selected else '(none)'}\n"

    options = []
    for lang in STANDARD_LANGUAGES:
        marker = " |g[✓]|n" if lang in selected else ""
        options.append(
            {
                "desc": f"{lang}{marker}",
                "goto": (_toggle_language, {"selected": list(selected), "lang": lang}),
            }
        )
    options.append(
        {
            "key": ("Back", "back", "b"),
            "desc": "Go back to species selection",
            "goto": "menunode_choose_species",
        }
    )
    return text, options


def _toggle_language(
    caller: Any,
    raw_string: str = "",
    selected: list | None = None,
    lang: str = "",
    **kwargs,
):
    if selected is None:
        selected = []
    if lang in selected:
        selected.remove(lang)
    else:
        if len(selected) < 2:
            selected.append(lang)
    _char(caller).db.chargen_languages = selected
    return ("menunode_choose_languages", {"selected": selected})


def _reset_languages(caller: Any, raw_string: str = "", **kwargs):
    _char(caller).db.chargen_languages = []
    return ("menunode_choose_languages", {"selected": []})


def _confirm_languages(caller: Any, raw_string: str = "", **kwargs):
    char = _char(caller)
    if char.db.chargen_from_review:
        char.db.chargen_from_review = False
        return "menunode_review"
    return "menunode_ability_method"


# ---------------------------------------------------------------------------
# Step 3: Ability Scores — method selection
# ---------------------------------------------------------------------------


def menunode_ability_method(caller: Any, **kwargs):
    char = _char(caller)
    char.db.chargen_step = "menunode_ability_method"
    cls_name = char.db.chargen_class or "your class"
    suggestion = STANDARD_ARRAY_BY_CLASS.get(cls_name, {})
    sug_line = ""
    if suggestion:
        sug_line = "\n  Suggestion for {}: {}".format(
            cls_name,
            ", ".join(f"{ABILITY_SHORT[a]}: {suggestion[a]}" for a in ABILITY_NAMES),
        )

    text = dedent(f"""\
        |wStep 7 — Determine Ability Scores|n

        Choose how to generate your six ability scores
        (Strength, Dexterity, Constitution, Intelligence, Wisdom, Charisma).

        |wA) Standard Array|n  — Use the fixed set: {', '.join(str(s) for s in STANDARD_ARRAY)}{sug_line}
        |wB) Point Buy|n       — Spend 27 points; scores range from 8–15.
        |wC) Random Roll|n     — Roll 4d6, drop the lowest, six times.
    """)
    options = [
        {
            "desc": "Standard Array",
            "goto": "menunode_assign_scores",
        },
        {
            "desc": "Point Buy",
            "goto": "menunode_point_buy",
        },
        {
            "desc": "Random Roll",
            "goto": "menunode_random_roll",
        },
        {
            "key": ("Back", "back", "b"),
            "desc": "Go back to language selection",
            "goto": "menunode_choose_languages",
        },
    ]
    return text, options


# ---------------------------------------------------------------------------
# Step 3A: Standard Array assignment
# ---------------------------------------------------------------------------


def menunode_assign_scores(caller: Any, raw_string: str = "", **kwargs):
    """Assign the standard-array values (or rolled values) to abilities one at a time."""
    char = _char(caller)
    char.db.chargen_step = "menunode_assign_scores"

    assigned: dict[str, int] = char.db.chargen_scores_assigned or {}

    # Determine pool: rolled scores or standard array.
    if char.db.chargen_rolled_scores:
        pool = sorted(list(char.db.chargen_rolled_scores), reverse=True)
        method_label = "Rolled"
    else:
        pool = list(STANDARD_ARRAY)
        method_label = "Standard Array"

    # Remove already-assigned scores from the pool.
    remaining = list(pool)
    for score in assigned.values():
        if score in remaining:
            remaining.remove(score)

    # Find next unassigned ability.
    unassigned = [a for a in ABILITY_NAMES if a not in assigned]

    if not unassigned:
        # All assigned — show summary for confirmation. (Reached on resume; normally
        # _assign_score routes directly to menunode_review_scores when last score lands.)
        text = "|wAll abilities assigned.|n\n\n"
        text += _fmt_ability_block(assigned)
        text += "\n"
        return text, [
            {"desc": "Review and confirm scores", "goto": "menunode_review_scores"},
            {
                "key": ("Back", "back", "b"),
                "desc": "Restart score assignment",
                "goto": (_reset_scores, {}),
            },
        ]

    next_ability = unassigned[0]
    cls_name = char.db.chargen_class or ""
    suggestion = STANDARD_ARRAY_BY_CLASS.get(cls_name, {})

    text = f"|wAssign Scores ({method_label})|n\n\n"
    text += "Assigned so far:\n"
    for ability in ABILITY_NAMES:
        if ability in assigned:
            sc = assigned[ability]
            mod = ability_modifier(sc)
            mod_str = f"+{mod}" if mod >= 0 else str(mod)
            text += f"  {ABILITY_SHORT[ability]}: {sc:2d} ({mod_str})\n"
        elif ability == next_ability:
            text += f"  {ABILITY_SHORT[ability]}: |y(choosing now)|n\n"
        else:
            text += f"  {ABILITY_SHORT[ability]}: |x------|n\n"

    if suggestion and next_ability in suggestion:
        text += f"\n  Suggestion for {cls_name}: assign |w{suggestion[next_ability]}|n to {next_ability}."

    text += f"\n\nRemaining scores: {remaining}\nChoose a score to assign to |w{next_ability}|n:"

    seen = set()
    options = []
    for score in sorted(remaining, reverse=True):
        if score in seen:
            continue
        seen.add(score)
        mod = ability_modifier(score)
        mod_str = f"+{mod}" if mod >= 0 else str(mod)
        options.append(
            {
                "desc": f"{score} ({mod_str})",
                "goto": (
                    _assign_score,
                    {
                        "ability": next_ability,
                        "score": score,
                        "assigned": dict(assigned),
                    },
                ),
            }
        )
    options.append(
        {
            "key": ("Back", "back", "b"),
            "desc": "Restart score assignment",
            "goto": (_reset_scores, {}),
        }
    )
    return text, options


def _assign_score(
    caller: Any,
    raw_string: str = "",
    ability: str = "",
    score: int = 0,
    assigned: dict | None = None,
    **kwargs,
):
    if assigned is None:
        assigned = {}
    assigned[ability] = score
    _char(caller).db.chargen_scores_assigned = assigned
    if len(assigned) >= len(ABILITY_NAMES):
        return "menunode_review_scores"
    return "menunode_assign_scores"


def _reset_scores(caller: Any, raw_string: str = "", **kwargs):
    char = _char(caller)
    char.db.chargen_scores_assigned = {}
    char.db.chargen_rolled_scores = None
    return "menunode_ability_method"


def menunode_review_scores(caller: Any, **kwargs):
    """Show the fully-assigned scores and ask the player to confirm or restart."""
    char = _char(caller)
    char.db.chargen_step = "menunode_review_scores"

    scores: dict[str, int] = char.db.chargen_scores_assigned or {
        a: 8 for a in ABILITY_NAMES
    }
    text = "|wStep 7 — Confirm Ability Scores|n\n\n"
    text += "Your ability scores:\n"
    text += _fmt_ability_block(scores)
    text += "\n\nConfirm these scores?"

    options = [
        {
            "key": ("Yes", "y"),
            "desc": "Confirm and continue",
            "goto": _confirm_scores,
        },
        {
            "key": ("No", "n"),
            "desc": "Go back and reassign",
            "goto": _reset_scores,
        },
    ]
    return text, options


def _confirm_scores(caller: Any, raw_string: str = "", **kwargs):
    char = _char(caller)
    if char.db.chargen_from_review:
        char.db.chargen_from_review = False
        return "menunode_review"
    return "menunode_background_bonus"


# ---------------------------------------------------------------------------
# Step 3B: Point Buy
# ---------------------------------------------------------------------------


def menunode_point_buy(caller: Any, raw_string: str = "", **kwargs):
    char = _char(caller)
    char.db.chargen_step = "menunode_point_buy"

    scores: dict[str, int] = char.db.chargen_scores_assigned or {
        a: 8 for a in ABILITY_NAMES
    }
    # Ensure all abilities are present.
    for a in ABILITY_NAMES:
        scores.setdefault(a, 8)

    spent = sum(POINT_BUY_COSTS.get(v, 0) for v in scores.values())
    remaining_pts = POINT_BUY_TOTAL - spent

    text = "|wStep 7 — Point Buy|n\n\n"
    text += f"Points remaining: |w{remaining_pts}|n / {POINT_BUY_TOTAL}\n\n"
    text += "Current scores:\n"
    for ability in ABILITY_NAMES:
        sc = scores[ability]
        mod = ability_modifier(sc)
        mod_str = f"+{mod}" if mod >= 0 else str(mod)
        cost = POINT_BUY_COSTS.get(sc, 0)
        text += f"  {ABILITY_SHORT[ability]}: {sc:2d} ({mod_str})  [cost: {cost}]\n"
    text += "\nSelect an ability to increase or decrease it."

    options = []
    for ability in ABILITY_NAMES:
        sc = scores[ability]
        # Increase option.
        if sc < POINT_BUY_MAX:
            next_cost = POINT_BUY_COSTS.get(sc + 1, 99) - POINT_BUY_COSTS.get(sc, 0)
            if next_cost <= remaining_pts:
                options.append(
                    {
                        "desc": f"Increase {ABILITY_SHORT[ability]} ({sc} → {sc + 1}, costs {next_cost}pt)",
                        "goto": (
                            _point_buy_adjust,
                            {"ability": ability, "delta": 1, "scores": dict(scores)},
                        ),
                    }
                )
        # Decrease option.
        if sc > POINT_BUY_MIN:
            options.append(
                {
                    "desc": f"Decrease {ABILITY_SHORT[ability]} ({sc} → {sc - 1})",
                    "goto": (
                        _point_buy_adjust,
                        {"ability": ability, "delta": -1, "scores": dict(scores)},
                    ),
                }
            )

    # Always allow confirming current scores.
    options.append(
        {
            "key": ("Confirm scores", "confirm", "done"),
            "desc": f"Confirm these scores ({remaining_pts}pt remaining will be lost)",
            "goto": (_confirm_point_buy, {"scores": dict(scores)}),
        }
    )
    options.append(
        {
            "key": ("Back", "back", "b"),
            "desc": "Go back to score method selection",
            "goto": (_reset_scores, {}),
        }
    )
    return text, options


def _point_buy_adjust(
    caller: Any,
    raw_string: str = "",
    ability: str = "",
    delta: int = 0,
    scores: dict | None = None,
    **kwargs,
):
    if scores is None:
        scores = {a: 8 for a in ABILITY_NAMES}
    scores[ability] = max(POINT_BUY_MIN, min(POINT_BUY_MAX, scores[ability] + delta))
    _char(caller).db.chargen_scores_assigned = scores
    return ("menunode_point_buy",)


def _confirm_point_buy(
    caller: Any, raw_string: str = "", scores: dict | None = None, **kwargs
):
    if scores is None:
        scores = {a: 8 for a in ABILITY_NAMES}
    _char(caller).db.chargen_scores_assigned = scores
    return "menunode_review_scores"


# ---------------------------------------------------------------------------
# Step 3C: Random Roll
# ---------------------------------------------------------------------------


def menunode_random_roll(caller: Any, raw_string: str = "", **kwargs):
    char = _char(caller)
    char.db.chargen_step = "menunode_random_roll"

    # Roll only once; re-entering the node shows the same rolls.
    if not char.db.chargen_rolled_scores:
        rolls = sorted([roll_4d6_drop_lowest() for _ in range(6)], reverse=True)
        char.db.chargen_rolled_scores = rolls
    else:
        rolls = list(char.db.chargen_rolled_scores)

    total = sum(rolls)
    text = dedent(f"""\
        |wStep 7 — Random Roll|n

        You rolled: {rolls}
        Total: {total}

        These will be assigned to your abilities in the next step.
        You may re-roll, or proceed to assign these scores.
    """)
    options = [
        {
            "desc": "Assign these scores to abilities",
            "goto": "menunode_assign_scores",
        },
        {
            "desc": "Re-roll (discards current rolls)",
            "goto": _reroll_scores,
        },
        {
            "key": ("Back", "back", "b"),
            "desc": "Go back to score method selection",
            "goto": (_reset_scores, {}),
        },
    ]
    return text, options


def _reroll_scores(caller: Any, raw_string: str = "", **kwargs):
    _char(caller).db.chargen_rolled_scores = None
    _char(caller).db.chargen_scores_assigned = {}
    return "menunode_random_roll"


# ---------------------------------------------------------------------------
# Step 3 post-assignment: Background ability bonus
# ---------------------------------------------------------------------------


def menunode_background_bonus(caller: Any, raw_string: str = "", **kwargs):
    char = _char(caller)
    char.db.chargen_step = "menunode_background_bonus"

    bg_name = char.db.chargen_background or ""
    bg_data = BACKGROUNDS.get(bg_name, {})
    options_list: list[str] = bg_data.get("ability_options", [])

    # Read current bonus state.
    bonus: dict[str, int] = kwargs.get("bonus") or char.db.chargen_bg_bonus or {}
    mode: str = kwargs.get("mode") or char.db.chargen_bg_bonus_mode or ""

    scores: dict[str, int] = char.db.chargen_scores_assigned or {
        a: 8 for a in ABILITY_NAMES
    }

    text = f"|wBackground Ability Bonus ({bg_name})|n\n\n"
    text += (
        "Your background lets you adjust three ability scores.\n"
        "Choose one of the two distribution options:\n\n"
        "  |wA)|n +2 to one ability and +1 to a different ability\n"
        "  |wB)|n +1 to all three abilities\n\n"
        f"Eligible abilities: {', '.join(options_list)}\n"
    )
    if bonus:
        text += "\nCurrent bonus allocations:\n"
        for ab, val in bonus.items():
            text += f"  {ab}: +{val}\n"

    menu_options = []

    if not mode:
        menu_options.append(
            {
                "desc": "Option A: +2 to one ability, +1 to another",
                "goto": (
                    _bg_bonus_set_mode,
                    {"mode": "A", "options_list": options_list},
                ),
            }
        )
        menu_options.append(
            {
                "desc": "Option B: +1 to all three abilities",
                "goto": (_bg_bonus_option_b, {"options_list": options_list}),
            }
        )
    elif mode == "A_pick_two":
        two_pts: str = (
            kwargs.get("two_pts_ability") or char.db.chargen_bg_bonus_two_pts or ""
        )
        if not two_pts:
            text += "\nChoose which ability receives |w+2|n:"
            for ab in options_list:
                menu_options.append(
                    {
                        "desc": f"{ab} (currently {scores.get(ab, 8)})",
                        "goto": (
                            _bg_bonus_pick_two,
                            {"ability": ab, "options_list": options_list},
                        ),
                    }
                )
        else:
            text += f"\n{two_pts} gets +2.  Choose which ability receives |w+1|n:"
            for ab in options_list:
                if ab != two_pts:
                    menu_options.append(
                        {
                            "desc": f"{ab} (currently {scores.get(ab, 8)})",
                            "goto": (
                                _bg_bonus_pick_one,
                                {
                                    "two_pts_ability": two_pts,
                                    "one_pt_ability": ab,
                                    "options_list": options_list,
                                },
                            ),
                        }
                    )

    if mode in ("A_done", "B") and bonus:
        menu_options.append(
            {
                "key": ("Yes", "y"),
                "desc": "Confirm these bonuses and continue",
                "goto": (_confirm_bg_bonus, {"bonus": dict(bonus)}),
            }
        )
        menu_options.append(
            {
                "key": ("No", "n"),
                "desc": "Clear bonus choices and start over",
                "goto": (_reset_bg_bonus, {}),
            }
        )
    else:
        menu_options.append(
            {
                "key": ("Restart", "restart", "r"),
                "desc": "Clear bonus choices and start over",
                "goto": (_reset_bg_bonus, {}),
            }
        )
    return text, menu_options


def _bg_bonus_set_mode(
    caller: Any,
    raw_string: str = "",
    mode: str = "",
    options_list: list | None = None,
    **kwargs,
):
    _char(caller).db.chargen_bg_bonus_mode = "A_pick_two"
    _char(caller).db.chargen_bg_bonus = {}
    _char(caller).db.chargen_bg_bonus_two_pts = ""
    return ("menunode_background_bonus", {"mode": "A_pick_two", "bonus": {}})


def _bg_bonus_pick_two(
    caller: Any,
    raw_string: str = "",
    ability: str = "",
    options_list: list | None = None,
    **kwargs,
):
    _char(caller).db.chargen_bg_bonus_two_pts = ability
    return (
        "menunode_background_bonus",
        {"mode": "A_pick_two", "two_pts_ability": ability, "bonus": {ability: 2}},
    )


def _bg_bonus_pick_one(
    caller: Any,
    raw_string: str = "",
    two_pts_ability: str = "",
    one_pt_ability: str = "",
    options_list: list | None = None,
    **kwargs,
):
    bonus = {two_pts_ability: 2, one_pt_ability: 1}
    _char(caller).db.chargen_bg_bonus = bonus
    _char(caller).db.chargen_bg_bonus_mode = "A_done"
    return ("menunode_background_bonus", {"mode": "A_done", "bonus": bonus})


def _bg_bonus_option_b(
    caller: Any, raw_string: str = "", options_list: list | None = None, **kwargs
):
    if options_list is None:
        options_list = []
    bonus = {ab: 1 for ab in options_list}
    _char(caller).db.chargen_bg_bonus = bonus
    _char(caller).db.chargen_bg_bonus_mode = "B"
    return ("menunode_background_bonus", {"mode": "B", "bonus": bonus})


def _confirm_bg_bonus(
    caller: Any, raw_string: str = "", bonus: dict | None = None, **kwargs
):
    char = _char(caller)
    if bonus:
        char.db.chargen_bg_bonus = bonus
    if char.db.chargen_from_review:
        char.db.chargen_from_review = False
        return "menunode_review"
    return "menunode_choose_alignment"


def _reset_bg_bonus(caller: Any, raw_string: str = "", **kwargs):
    char = _char(caller)
    char.db.chargen_bg_bonus = {}
    char.db.chargen_bg_bonus_mode = ""
    char.db.chargen_bg_bonus_two_pts = ""
    return "menunode_background_bonus"


# ---------------------------------------------------------------------------
# Step 4: Alignment
# ---------------------------------------------------------------------------


def menunode_choose_alignment(caller: Any, **kwargs):
    char = _char(caller)
    char.db.chargen_step = "menunode_choose_alignment"

    text = dedent("""\
        |wStep 8 — Choose an Alignment|n

        Alignment describes your character's ethical attitudes and ideals.
        It combines a moral axis (Good, Neutral, Evil) with an order axis
        (Lawful, Neutral, Chaotic).

        Alignment is a |yroleplay detail only|n — it has no effect on
        gameplay or stats.  Select an alignment to read more about it.
    """)
    options = []
    for full_name, abbr, desc in ALIGNMENTS:
        options.append(
            {
                "desc": f"|w{abbr}|n  {full_name}",
                "goto": (
                    "menunode_alignment_detail",
                    {"alignment": full_name, "abbr": abbr},
                ),
            }
        )
    options.append(
        {
            "key": ("Back", "back", "b"),
            "desc": "Go back to background bonus",
            "goto": "menunode_background_bonus",
        }
    )
    return text, options


def menunode_alignment_detail(
    caller: Any, raw_string: str = "", alignment: str = "", abbr: str = "", **kwargs
):
    if not alignment:
        return "menunode_choose_alignment"

    # Look up the full description from ALIGNMENTS.
    desc = next((d for n, a, d in ALIGNMENTS if n == alignment), "")
    text = dedent(f"""\
        |w{alignment}|n (|w{abbr}|n)

        {desc}

        Do you want to be |w{alignment}|n?
    """)
    options = [
        {
            "key": ("Yes", "y"),
            "desc": f"Choose {alignment}",
            "goto": (_set_alignment, {"alignment": alignment}),
        },
        {
            "key": ("No", "n"),
            "desc": "Return to alignment list",
            "goto": "menunode_choose_alignment",
        },
    ]
    return text, options


def _set_alignment(caller: Any, raw_string: str = "", alignment: str = "", **kwargs):
    _char(caller).db.chargen_alignment = alignment
    return "menunode_review"


# ---------------------------------------------------------------------------
# Step 1: Name, Gender, Age  (asked first so the character has an identity early)
# ---------------------------------------------------------------------------


def menunode_choose_name(caller: Any, raw_string: str = "", **kwargs):
    char = _char(caller)
    char.db.chargen_step = "menunode_choose_name"

    if error := kwargs.get("error"):
        prompt = f"|r{error}|n\nEnter a different name:"
    else:
        prompt = "Enter your character's name:"

    text = dedent(f"""\
        |wStep 1 — Name|n

        Choose a name for your adventurer.  Names must be unique and may
        not contain spaces or special characters.

        {prompt}
    """)
    return text, {"key": "_default", "goto": _check_name}


def _check_name(caller: Any, raw_string: str = "", **kwargs):
    from typeclasses.characters import Character

    name = raw_string.strip()
    name = caller.account.normalize_username(name)
    name = name.capitalize()

    if not name:
        return ("menunode_choose_name", {"error": "Name cannot be empty."})

    if len(name) < 2:
        return (
            "menunode_choose_name",
            {"error": "Name must be at least 2 characters."},
        )

    if Character.objects.filter_family(db_key__iexact=name).exists():
        return ("menunode_choose_name", {"error": f"|w{name}|n is already taken."})

    _char(caller).key = name
    return "menunode_confirm_name"


def menunode_confirm_name(caller: Any, **kwargs):
    char = _char(caller)
    name = char.key
    text = f"Your character's name will be |w{name}|n.  Confirm?"
    next_node = "menunode_review" if char.db.chargen_from_review else "menunode_choose_gender"
    options = [
        {"key": ("Yes", "y"), "goto": (_confirm_name, {"next_node": next_node})},
        {"key": ("No", "n"), "goto": "menunode_choose_name"},
    ]
    return text, options


def _confirm_name(caller: Any, raw_string: str = "", next_node: str = "", **kwargs):
    char = _char(caller)
    char.db.chargen_from_review = False
    return next_node or "menunode_choose_gender"


def menunode_choose_gender(caller: Any, **kwargs):
    char = _char(caller)
    char.db.chargen_step = "menunode_choose_gender"

    text = dedent("""\
        |wStep 2 — Gender|n

        What is your character's gender?
    """)
    options = [
        {"desc": "Male", "goto": (_set_gender, {"gender": "male"})},
        {"desc": "Female", "goto": (_set_gender, {"gender": "female"})},
        {"desc": "Nonbinary", "goto": (_set_gender, {"gender": "nonbinary"})},
        {"desc": "Prefer not to say", "goto": (_set_gender, {"gender": "unspecified"})},
    ]
    return text, options


def _set_gender(caller: Any, raw_string: str = "", gender: str = "", **kwargs):
    char = _char(caller)
    char.db.gender = gender
    if char.db.chargen_from_review:
        char.db.chargen_from_review = False
        return "menunode_review"
    return "menunode_choose_class"


def menunode_choose_age(caller: Any, raw_string: str = "", **kwargs):
    char = _char(caller)
    char.db.chargen_step = "menunode_choose_age"

    species_name = char.db.chargen_species or ""
    species_max = (
        SPECIES[species_name]["max_age"] if species_name in SPECIES else MAX_AGE
    )

    if error := kwargs.get("error"):
        prompt = f"|r{error}|n\nEnter a different age:"
    else:
        prompt = f"Enter your character's age ({MIN_AGE}–{species_max}):"

    text = dedent(f"""\
        |wStep 5 — Age|n

        How old is your character?

        Age is a |yroleplay detail only|n — it has no effect on gameplay or
        stats.  As a |w{species_name}|n your character must be at least
        |w{MIN_AGE}|n and no older than |w{species_max}|n.

        {prompt}
    """)
    return text, {"key": "_default", "goto": _check_age}


def _check_age(caller: Any, raw_string: str = "", **kwargs):
    char = _char(caller)
    species_name = char.db.chargen_species or ""
    species_max = (
        SPECIES[species_name]["max_age"] if species_name in SPECIES else MAX_AGE
    )

    age_str = raw_string.strip()
    if not age_str.isdigit() or int(age_str) < MIN_AGE:
        return (
            "menunode_choose_age",
            {"error": f"Age must be at least {MIN_AGE}."},
        )
    age = int(age_str)
    if age > species_max:
        return (
            "menunode_choose_age",
            {"error": f"{species_name} characters cannot be older than {species_max}."},
        )
    char.db.age = age
    if char.db.chargen_from_review:
        char.db.chargen_from_review = False
        return "menunode_review"
    return "menunode_choose_languages"


# ---------------------------------------------------------------------------
# Review & End
# ---------------------------------------------------------------------------


def _edit_from_review(caller: Any, raw_string: str = "", goto_node: str = "", **kwargs):
    char = _char(caller)
    char.db.chargen_from_review = True
    return goto_node


def menunode_review(caller: Any, **kwargs):
    char = _char(caller)
    char.db.chargen_step = "menunode_review"

    cls_name = char.db.chargen_class or "|runset|n"
    bg = char.db.chargen_background or "|runset|n"
    species = char.db.chargen_species or "|runset|n"
    # Resolve displayed size: fixed from species data, or player's choice.
    species_data = SPECIES.get(species, {})
    if species_data.get("size_choice"):
        size_display = char.db.chargen_size or "|runset|n"
    else:
        size_display = species_data.get("size", "Medium")
    langs = char.db.chargen_languages or []
    alignment = char.db.chargen_alignment or "|runset|n"
    scores: dict[str, int] = char.db.chargen_scores_assigned or {}
    bg_bonus: dict[str, int] = char.db.chargen_bg_bonus or {}

    # Apply background bonus to display final scores.
    final_scores = {}
    for ab in ABILITY_NAMES:
        base = scores.get(ab, 8)
        bonus = bg_bonus.get(ab, 0)
        final_scores[ab] = base + bonus

    cls_data = CLASSES.get(cls_name, {})
    con_mod = ability_modifier(final_scores.get("Constitution", 8))
    hp_max = cls_data.get("hp_base", 8) + con_mod

    gender_display = {
        "male": "Male",
        "female": "Female",
        "nonbinary": "Nonbinary",
        "unspecified": "Prefer not to say",
    }.get(char.db.gender or "", char.db.gender or "|xunset|n")
    age_display = str(char.db.age) if char.db.age else "|xunset|n"

    # Skill proficiencies for review.
    bg_skills: list[str] = BACKGROUNDS.get(bg, {}).get("skill_proficiencies", [])
    class_skills: list[str] = list(char.db.chargen_skill_proficiencies or [])
    all_skills = sorted(set(bg_skills + class_skills))
    skills_display = ", ".join(all_skills) if all_skills else "|xunset|n"

    text = "|wCharacter Review|n\n\n"
    text += f"  |yName:|n       {char.key}\n"
    text += f"  |yGender:|n     {gender_display}\n"
    text += f"  |yAge:|n        {age_display}\n"
    text += f"  |yClass:|n      {cls_name}\n"
    text += f"  |yBackground:|n {bg}\n"
    text += f"  |ySpecies:|n    {species} ({size_display})\n"
    text += f"  |ySkills:|n     {skills_display}\n"
    text += (
        f"  |yLanguages:|n  Common, {', '.join(langs)}\n"
        if langs
        else "  |yLanguages:|n  Common\n"
    )
    text += f"  |yAlignment:|n  {alignment}\n"
    text += "  |yLevel:|n      1   |yXP:|n 0   |yProf. Bonus:|n +2\n"
    text += "\n|yAbility Scores|n (base + background bonus):\n"
    text += _fmt_ability_block(final_scores)
    text += f"\n\n  |yHP (max):|n   {hp_max}  (base {cls_data.get('hp_base', 8)} + CON {con_mod:+d})\n"
    text += "  |yArmor Class:|n 10 + DEX modifier\n"

    options = [
        {
            "key": ("0", "play", "yes", "y"),
            "desc": "Start playing — enter the world",
            "goto": "menunode_end",
        },
        {
            "key": "1",
            "desc": f"Edit Name (currently: {char.key})",
            "goto": (_edit_from_review, {"goto_node": "menunode_choose_name"}),
        },
        {
            "key": "2",
            "desc": f"Edit Gender (currently: {gender_display})",
            "goto": (_edit_from_review, {"goto_node": "menunode_choose_gender"}),
        },
        {
            "key": "3",
            "desc": f"Edit Age (currently: {age_display})",
            "goto": (_edit_from_review, {"goto_node": "menunode_choose_age"}),
        },
        {
            "key": "4",
            "desc": f"Edit Class (currently: {cls_name})",
            "goto": (_edit_from_review, {"goto_node": "menunode_choose_class"}),
        },
        {
            "key": "5",
            "desc": f"Edit Background (currently: {bg})",
            "goto": (_edit_from_review, {"goto_node": "menunode_choose_background"}),
        },
        {
            "key": "6",
            "desc": f"Edit Species (currently: {species})",
            "goto": (_edit_from_review, {"goto_node": "menunode_choose_species"}),
        },
        {
            "key": "7",
            "desc": "Edit Languages",
            "goto": (_edit_from_review, {"goto_node": "menunode_choose_languages"}),
        },
        {
            "key": "8",
            "desc": "Edit Ability Scores",
            "goto": (_edit_from_review, {"goto_node": "menunode_ability_method"}),
        },
        {
            "key": "9",
            "desc": "Edit Background Bonus",
            "goto": (_edit_from_review, {"goto_node": "menunode_background_bonus"}),
        },
        {
            "key": "10",
            "desc": f"Edit Alignment (currently: {alignment})",
            "goto": (_edit_from_review, {"goto_node": "menunode_choose_alignment"}),
        },
        {
            "key": "11",
            "desc": "Edit Class Skills",
            "goto": (_edit_from_review, {"goto_node": "menunode_choose_skills"}),
        },
        {
            "key": ("R", "restart"),
            "desc": "Start over — go back to class selection",
            "goto": _restart_chargen,
        },
    ]
    return text, options


def _restart_chargen(caller: Any, raw_string: str = "", **kwargs):
    char = _char(caller)
    for attr in [
        "chargen_class",
        "chargen_background",
        "chargen_skill_proficiencies",
        "chargen_species",
        "chargen_size",
        "chargen_languages",
        "chargen_scores_assigned",
        "chargen_rolled_scores",
        "chargen_bg_bonus",
        "chargen_bg_bonus_mode",
        "chargen_bg_bonus_two_pts",
        "chargen_alignment",
        "chargen_from_review",
    ]:
        char.attributes.remove(attr)
    return "menunode_choose_class"


def menunode_end(caller: Any, **kwargs):
    """Finalise all choices and write canonical character attributes."""
    char = _char(caller)

    cls_name: str = char.db.chargen_class or "Fighter"
    bg: str = char.db.chargen_background or ""
    species: str = char.db.chargen_species or "Human"
    species_data = SPECIES.get(species, {})
    # For species with a player-chosen size (Human, Tiefling), use the stored
    # choice; for all others, derive it directly from the species data.
    if species_data.get("size_choice"):
        size: str = char.db.chargen_size or "Medium"
    else:
        size = species_data.get("size", "Medium")
    langs: list[str] = char.db.chargen_languages or []
    alignment: str = char.db.chargen_alignment or "Neutral"
    scores: dict[str, int] = char.db.chargen_scores_assigned or {
        a: 8 for a in ABILITY_NAMES
    }
    bg_bonus: dict[str, int] = char.db.chargen_bg_bonus or {}

    # Merge background bonus into final scores.
    final: dict[str, int] = {}
    for ab in ABILITY_NAMES:
        final[ab] = scores.get(ab, 8) + bg_bonus.get(ab, 0)

    # Core ability scores.
    char.db.strength = final["Strength"]
    char.db.dexterity = final["Dexterity"]
    char.db.constitution = final["Constitution"]
    char.db.intelligence = final["Intelligence"]
    char.db.wisdom = final["Wisdom"]
    char.db.charisma = final["Charisma"]

    # Derived stats.
    cls_data = CLASSES.get(cls_name, CLASSES["Fighter"])
    con_mod = ability_modifier(final["Constitution"])
    dex_mod = ability_modifier(final["Dexterity"])
    wis_mod = ability_modifier(final["Wisdom"])

    char.db.level = 1
    char.db.xp = 0
    char.db.proficiency_bonus = 2
    char.db.hp_max = cls_data["hp_base"] + con_mod
    char.db.hp_current = char.db.hp_max
    char.db.hit_die = cls_data["hit_die"]
    char.db.initiative = dex_mod
    char.db.armor_class = 10 + dex_mod
    char.db.passive_perception = 10 + wis_mod
    char.db.speed = SPECIES.get(species, {}).get("speed", 30)

    # Identity / origin.
    char.db.char_class = cls_name
    char.db.background = bg
    char.db.species = species
    char.db.size = size
    char.db.alignment = alignment
    char.db.languages = ["Common"] + list(langs)
    char.db.active_language = char.db.languages[0]

    # Skill proficiencies: background grants fixed skills; class grants chosen skills.
    bg_data = BACKGROUNDS.get(bg, {})
    bg_skill_profs: list[str] = bg_data.get("skill_proficiencies", [])
    class_skill_profs: list[str] = list(char.db.chargen_skill_proficiencies or [])
    char.db.skill_proficiencies = sorted(set(bg_skill_profs + class_skill_profs))

    # Clean up all temporary chargen attributes.
    for attr in [
        "chargen_class",
        "chargen_background",
        "chargen_skill_proficiencies",
        "chargen_species",
        "chargen_size",
        "chargen_languages",
        "chargen_scores_assigned",
        "chargen_rolled_scores",
        "chargen_bg_bonus",
        "chargen_bg_bonus_mode",
        "chargen_bg_bonus_two_pts",
        "chargen_alignment",
        "chargen_from_review",
        "chargen_step",  # clears it — signals the contrib callback to puppet the char
    ]:
        char.attributes.remove(attr)

    text = dedent(f"""\
        |gCongratulations, {char.key}!|n

        Your character has been created.  Welcome to the world, adventurer.

          Class: {cls_name}  |  Background: {bg}  |  Species: {species}
          Alignment: {alignment}
          HP: {char.db.hp_max}  |  AC: {char.db.armor_class}  |  Initiative: {char.db.initiative:+d}

        Entering the world now…
    """)
    return text, None
