"""Canonical, on-demand character statistics for PCs and NPCs.

Only base inputs and mutable resources belong in Evennia Attributes. Derived
values are calculated here so commands, combat, equipment, and effects cannot
develop competing versions of the same rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from numbers import Real
from typing import Any, Mapping

from systems.equipment import DamageMitigation
from world.chargen_data import (
    ABILITY_NAMES,
    ABILITY_SHORT,
    CARRY_CAPACITY_MULTIPLIER,
    CLASSES,
    SKILLS,
    SPECIES,
    ability_modifier,
)

NORMAL_SPEED = 30
REACTION_DELAY_STEP = 0.02
MIN_REACTION_DELAY_MULTIPLIER = 0.80
MAX_REACTION_DELAY_MULTIPLIER = 1.20

_ABILITY_ATTRIBUTES = {name: name.lower() for name in ABILITY_NAMES}
_ABILITY_ALIASES = {
    **{name.lower(): name for name in ABILITY_NAMES},
    **{short.lower(): name for name, short in ABILITY_SHORT.items()},
}


def calculate_max_hp(hp_base: int, level: int, constitution: int) -> int:
    """Calculate maximum HP before equipment and effect modifiers."""
    return max(1, hp_base + level * ability_modifier(constitution))


@dataclass(frozen=True)
class AttackProfile:
    """Numbers needed to resolve one attack without performing its roll."""

    name: str
    ability: str
    attack_bonus: int
    damage_dice: str | None
    damage_base: int
    damage_bonus: int
    damage_type: str
    proficient: bool


class CharacterStats:
    """Expose persistent resources and derived statistics for one character."""

    def __init__(self, owner: Any):
        self.owner = owner

    def _attribute(self, name: str, default: Any = None) -> Any:
        """Read an Attribute while preserving meaningful falsey values."""
        value = self.owner.attributes.get(name)
        return default if value is None else value

    def _modifier_total(self, *names: str) -> int:
        """Add named contributions from every current modifier source."""
        total = 0
        for modifiers in self.owner.get_stat_modifier_sources():
            if not isinstance(modifiers, Mapping):
                continue
            for name in names:
                value = modifiers.get(name, 0)
                if isinstance(value, Real):
                    total += int(value)
        return total

    @staticmethod
    def _ability_name(ability: str) -> str:
        """Return a canonical ability name or raise for an unknown ability."""
        canonical = _ABILITY_ALIASES.get(str(ability).strip().lower())
        if canonical is None:
            raise ValueError(f"Unknown ability: {ability}")
        return canonical

    def _base_ability_score(self, ability: str) -> int:
        canonical = self._ability_name(ability)
        return int(self._attribute(_ABILITY_ATTRIBUTES[canonical], 8))

    def ability_score(self, ability: str) -> int:
        """Return an ability score including equipment and effect modifiers."""
        canonical = self._ability_name(ability)
        return self._base_ability_score(canonical) + self._modifier_total(
            f"ability:{canonical.lower()}"
        )

    def set_ability_score(self, ability: str, score: int) -> None:
        """Persist a base ability score; temporary changes are modifiers."""
        canonical = self._ability_name(ability)
        if not 1 <= score <= 30:
            raise ValueError("Ability scores must be between 1 and 30.")
        setattr(self.owner.db, _ABILITY_ATTRIBUTES[canonical], score)
        self._clamp_current_hp()

    def ability_modifier(self, ability: str) -> int:
        """Return the modifier for an effective ability score."""
        return ability_modifier(self.ability_score(ability))

    @property
    def level(self) -> int:
        """Return character level, constrained to the supported 1-20 range."""
        return max(1, min(20, int(self._attribute("level", 1))))

    def set_level(self, level: int) -> None:
        """Persist a level without awarding level-up benefits."""
        if not 1 <= level <= 20:
            raise ValueError("Level must be between 1 and 20.")
        # TODO(ADV-01): The level-up transaction must add each earned level's
        # class HP contribution to hp_base before calling this mutator.
        self.owner.db.level = level
        self._clamp_current_hp()

    @property
    def xp(self) -> int:
        """Return accumulated experience points."""
        return max(0, int(self._attribute("xp", 0)))

    def set_xp(self, xp: int) -> None:
        """Persist experience without applying advancement thresholds."""
        if xp < 0:
            raise ValueError("XP cannot be negative.")
        self.owner.db.xp = xp

    @property
    def proficiency_bonus(self) -> int:
        """Return the SRD proficiency bonus for the current level."""
        override = self._attribute("proficiency_bonus_override")
        base = int(override) if override is not None else 2 + (self.level - 1) // 4
        return max(0, base + self._modifier_total("proficiency_bonus"))

    @property
    def hit_die(self) -> int:
        """Return the configured or class-default hit-die size."""
        configured = self._attribute("hit_die")
        if configured is not None:
            return max(1, int(configured))
        class_data = CLASSES.get(
            self._attribute("char_class", "Fighter"), CLASSES["Fighter"]
        )
        return int(class_data["hit_die"])

    @property
    def hp_base(self) -> int:
        """Return accumulated HP before Constitution and temporary modifiers."""
        configured = self._attribute("hp_base")
        if configured is not None:
            return max(1, int(configured))

        # Compatibility for characters created before RULES-01. Their hp_max
        # snapshot already included Constitution once per existing level.
        legacy_max = self._attribute("hp_max")
        if legacy_max is not None:
            base_con = ability_modifier(self._base_ability_score("Constitution"))
            return max(1, int(legacy_max) - self.level * base_con)

        class_data = CLASSES.get(
            self._attribute("char_class", "Fighter"), CLASSES["Fighter"]
        )
        return int(class_data["hp_base"])

    @property
    def hp_max(self) -> int:
        """Return maximum HP after Constitution and current modifiers."""
        override = self._attribute("hp_max_override")
        if override is None:
            base = calculate_max_hp(
                self.hp_base,
                self.level,
                self.ability_score("Constitution"),
            )
        else:
            base = int(override)
        return max(1, base + self._modifier_total("hp_max"))

    @property
    def hp_current(self) -> int:
        """Return current HP, repairing values outside the current bounds."""
        stored = int(self._attribute("hp_current", self.hp_max))
        current = max(0, min(self.hp_max, stored))
        if current != stored:
            self.owner.db.hp_current = current
        return current

    def _clamp_current_hp(self) -> None:
        """Persist current HP within its newly derived maximum."""
        current = self._attribute("hp_current")
        if current is not None:
            self.owner.db.hp_current = max(0, min(self.hp_max, int(current)))

    def set_hp(self, hp: int) -> int:
        """Set and return current HP after clamping it to valid bounds."""
        self.owner.db.hp_current = max(0, min(self.hp_max, int(hp)))
        return self.hp_current

    def take_damage(self, amount: int) -> int:
        """Apply non-negative damage and return the resulting current HP."""
        if amount < 0:
            raise ValueError("Damage cannot be negative.")
        return self.set_hp(self.hp_current - amount)

    def heal(self, amount: int) -> int:
        """Apply non-negative healing and return the resulting current HP."""
        if amount < 0:
            raise ValueError("Healing cannot be negative.")
        return self.set_hp(self.hp_current + amount)

    @property
    def armor_class(self) -> int:
        """Return effective Armor Class."""
        armor = self.owner.equipment.primary_armor
        dexterity = self.ability_modifier("Dexterity")
        if armor is None or armor.attributes.get("base_ac") is None:
            override = self._attribute("armor_class_override")
            base = int(override) if override is not None else 10 + dexterity
        else:
            base = int(armor.db.base_ac)
            category = str(armor.db.subtype).lower()
            if category == "light":
                base += dexterity
            elif category == "medium":
                base += min(dexterity, 2)

        shield = self.owner.equipment.shield
        if shield is not None and shield.attributes.get("base_ac") is not None:
            base += int(shield.db.base_ac)
        return max(0, base + self._modifier_total("armor_class"))

    @property
    def reaction_modifier(self) -> int:
        """Return the modifier that slightly changes combat action cadence."""
        override = self._attribute("reaction_modifier_override")
        base = (
            int(override)
            if override is not None
            else self.ability_modifier("Dexterity")
        )
        return base + self._modifier_total("reaction")

    def combat_delay(self, base_delay: float) -> float:
        """Scale an attack delay by Reaction, clamped to a subtle +/-20%."""
        # TODO(COMBAT-01): Use this result as the participant's recurring
        # attack cadence once the live combat handler schedules actions.
        if base_delay < 0:
            raise ValueError("A combat delay cannot be negative.")
        multiplier = 1.0 - self.reaction_modifier * REACTION_DELAY_STEP
        multiplier = max(
            MIN_REACTION_DELAY_MULTIPLIER,
            min(MAX_REACTION_DELAY_MULTIPLIER, multiplier),
        )
        return base_delay * multiplier

    @property
    def passive_perception(self) -> int:
        """Return passive Perception, including skill proficiency."""
        override = self._attribute("passive_perception_override")
        if override is not None:
            base = int(override)
        else:
            base = 10 + self.skill_bonus("Perception")
        return base + self._modifier_total("passive_perception")

    def skill_bonus(self, skill: str) -> int:
        """Return the effective bonus for a named skill."""
        canonical = next(
            (name for name in SKILLS if name.lower() == str(skill).strip().lower()),
            None,
        )
        if canonical is None:
            raise ValueError(f"Unknown skill: {skill}")
        bonus = self.ability_modifier(SKILLS[canonical])
        proficiencies = self._attribute("skill_proficiencies", [])
        if canonical in proficiencies:
            bonus += self.proficiency_bonus
        return bonus + self._modifier_total("skill_bonus", f"skill:{canonical.lower()}")

    def saving_throw_bonus(self, ability: str) -> int:
        """Return a saving-throw bonus, including class proficiency."""
        canonical = self._ability_name(ability)
        configured = self._attribute("saving_throw_proficiencies")
        if configured is None:
            class_data = CLASSES.get(
                self._attribute("char_class", "Fighter"), CLASSES["Fighter"]
            )
            configured = class_data["saving_throws"]
        bonus = self.ability_modifier(canonical)
        if canonical in configured:
            bonus += self.proficiency_bonus
        return bonus + self._modifier_total(
            "saving_throw", f"saving_throw:{canonical.lower()}"
        )

    def attack_profile(self) -> AttackProfile:
        """Build the current wielded-weapon or unarmed attack profile."""
        weapon = self.owner.equipment.wielded_weapon
        if weapon is None:
            name = "unarmed strike"
            ability = "Strength"
            proficient = True
            damage_dice = None
            damage_base = 1
            damage_type = "bludgeoning"
        else:
            name = weapon.key
            ability = weapon.db.attack_ability or "Strength"
            proficient = self.owner.equipment.is_weapon_proficient(weapon)
            damage_dice = weapon.db.damage
            damage_base = 0
            damage_type = weapon.db.subtype or "bludgeoning"

        canonical = self._ability_name(ability)
        attack_bonus = self.ability_modifier(canonical)
        if proficient:
            attack_bonus += self.proficiency_bonus
        attack_bonus += self._modifier_total("attack_bonus")
        damage_bonus = self.ability_modifier(canonical) + self._modifier_total(
            "damage_bonus"
        )
        return AttackProfile(
            name=name,
            ability=canonical,
            attack_bonus=attack_bonus,
            damage_dice=damage_dice,
            damage_base=damage_base,
            damage_bonus=damage_bonus,
            damage_type=damage_type,
            proficient=proficient,
        )

    @property
    def has_untrained_armor(self) -> bool:
        """Return whether equipped armor should trigger training penalties."""
        return self.owner.equipment.has_untrained_armor

    def mitigate_damage(
        self, amount: int, hit_location: str, damage_type: str
    ) -> DamageMitigation:
        """Apply current locational armor without changing hit points."""
        return self.owner.equipment.mitigate_damage(amount, hit_location, damage_type)

    @property
    def speed(self) -> int:
        """Return effective movement speed in feet."""
        configured = self._attribute("speed")
        if configured is None:
            species_data = SPECIES.get(
                self._attribute("species", "Human"), SPECIES["Human"]
            )
            configured = species_data["speed"]
        return max(0, int(configured) + self._modifier_total("speed"))

    def movement_delay(self, base_delay: float) -> float | None:
        """Scale traversal delay around speed 30; return None when immobilized."""
        if base_delay < 0:
            raise ValueError("A movement delay cannot be negative.")
        if self.speed == 0:
            return None
        return base_delay * NORMAL_SPEED / self.speed

    @property
    def carry_capacity(self) -> int:
        """Return carrying capacity in pounds for effective Strength and size."""
        size = self._attribute("size", "Medium")
        multiplier = CARRY_CAPACITY_MULTIPLIER.get(size, 15.0)
        return max(0, int(self.ability_score("Strength") * multiplier))
