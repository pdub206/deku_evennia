"""
Prototypes

A prototype is a simple way to create individualized instances of a
given typeclass. It is dictionary with specific key names.

For example, you might have a Sword typeclass that implements everything a
Sword would need to do. The only difference between different individual Swords
would be their key, description and some Attributes. The Prototype system
allows to create a range of such Swords with only minor variations. Prototypes
can also inherit and combine together to form entire hierarchies (such as
giving all Sabres and all Broadswords some common properties). Note that bigger
variations, such as custom commands or functionality belong in a hierarchy of
typeclasses instead.

A prototype can either be a dictionary placed into a global variable in a
python module (a 'module-prototype') or stored in the database as a dict on a
special Script (a db-prototype). The former can be created just by adding dicts
to modules Evennia looks at for prototypes, the latter is easiest created
in-game via the `olc` command/menu.

Prototypes are read and used to create new objects with the `spawn` command
or directly via `evennia.spawn` or the full path `evennia.prototypes.spawner.spawn`.

A prototype dictionary have the following keywords:

Possible keywords are:
- `prototype_key` - the name of the prototype. This is required for db-prototypes,
  for module-prototypes, the global variable name of the dict is used instead
- `prototype_parent` - string pointing to parent prototype if any. Prototype inherits
  in a similar way as classes, with children overriding values in their parents.
- `key` - string, the main object identifier.
- `typeclass` - string, if not set, will use `settings.BASE_OBJECT_TYPECLASS`.
- `location` - this should be a valid object or #dbref.
- `home` - valid object or #dbref.
- `destination` - only valid for exits (object or #dbref).
- `permissions` - string or list of permission strings.
- `locks` - a lock-string to use for the spawned object.
- `aliases` - string or list of strings.
- `attrs` - Attributes, expressed as a list of tuples on the form `(attrname, value)`,
  `(attrname, value, category)`, or `(attrname, value, category, locks)`. If using one
   of the shorter forms, defaults are used for the rest.
- `tags` - Tags, as a list of tuples `(tag,)`, `(tag, category)` or `(tag, category, data)`.
-  Any other keywords are interpreted as Attributes with no category or lock.
   These will internally be added to `attrs` (equivalent to `(attrname, value)`.

See the `spawn` command and `evennia.prototypes.spawner.spawn` for more info.

"""

## example of module-based prototypes using
## the variable name as `prototype_key` and
## simple Attributes

# from random import randint
#
# GOBLIN = {
# "key": "goblin grunt",
# "health": lambda: randint(20,30),
# "resists": ["cold", "poison"],
# "attacks": ["fists"],
# "weaknesses": ["fire", "light"],
# "tags": = [("greenskin", "monster"), ("humanoid", "monster")]
# }
#
# GOBLIN_WIZARD = {
# "prototype_parent": "GOBLIN",
# "key": "goblin wizard",
# "spells": ["fire ball", "lighting bolt"]
# }
#
# GOBLIN_ARCHER = {
# "prototype_parent": "GOBLIN",
# "key": "goblin archer",
# "attacks": ["short bow"]
# }
#
# This is an example of a prototype without a prototype
# (nor key) of its own, so it should normally only be
# used as a mix-in, as in the example of the goblin
# archwizard below.
# ARCHWIZARD_MIXIN = {
# "attacks": ["archwizard staff"],
# "spells": ["greater fire ball", "greater lighting"]
# }
#
# GOBLIN_ARCHWIZARD = {
# "key": "goblin archwizard",
# "prototype_parent" : ("GOBLIN_WIZARD", "ARCHWIZARD_MIXIN")
# }


# ITEM-07A starting equipment.  These module prototypes are deliberately plain
# data: starting_packages validates the same fields a Builder can author.
def _starting_item(key, name, weight, value=0, item_type="other", **fields):
    """Build one source-controlled SRD starting-equipment prototype."""
    return {
        "prototype_key": key,
        "key": name,
        "typeclass": "typeclasses.objects.Item",
        "weight": weight,
        "value": value,
        "type": item_type,
        "no_drop": False,
        "account_bound": False,
        "srd_reference": "SRD 5.2.1 equipment",
        **fields,
    }


# Armor and weapons
chain_shirt = _starting_item(
    "chain_shirt",
    "chain shirt",
    20.0,
    50,
    "armor",
    base_ac=13,
    subtype="medium",
    mitigation_flat=0,
    mitigation_percent=0,
    wear_locations=["body"],
)
chain_mail = _starting_item(
    "chain_mail",
    "chain mail",
    55.0,
    75,
    "armor",
    base_ac=16,
    subtype="heavy",
    mitigation_flat=0,
    mitigation_percent=0,
    wear_locations=["body"],
)
studded_leather = _starting_item(
    "studded_leather",
    "studded leather armor",
    13.0,
    45,
    "armor",
    base_ac=12,
    subtype="light",
    mitigation_flat=0,
    mitigation_percent=0,
    wear_locations=["body"],
)
leather_armor = _starting_item(
    "leather_armor",
    "leather armor",
    10.0,
    10,
    "armor",
    base_ac=11,
    subtype="light",
    mitigation_flat=0,
    mitigation_percent=0,
    wear_locations=["body"],
)
shield = _starting_item(
    "shield",
    "shield",
    6.0,
    10,
    "armor",
    base_ac=2,
    subtype="shield",
    mitigation_flat=0,
    mitigation_percent=0,
    wear_locations=["shield"],
)


def _weapon(key, name, weight, value, damage, subtype, category, ability="strength"):
    """Build a combat-ready weapon prototype using its stable training kind."""
    return _starting_item(
        key,
        name,
        weight,
        value,
        "weapon",
        damage=damage,
        subtype=subtype,
        weapon_category=category,
        weapon_kind=key,
        attack_ability=ability,
        wear_locations=["wield"],
    )


mace = _weapon("mace", "mace", 4.0, 5, "1d6", "bludgeoning", "simple")
greatsword = _weapon("greatsword", "greatsword", 6.0, 50, "2d6", "slashing", "martial")
flail = _weapon("flail", "flail", 2.0, 10, "1d8", "bludgeoning", "martial")
javelin = _weapon("javelin", "javelin", 2.0, 0, "1d6", "piercing", "simple")
scimitar = _weapon(
    "scimitar", "scimitar", 3.0, 25, "1d6", "slashing", "martial", "dexterity"
)
shortsword = _weapon(
    "shortsword", "shortsword", 2.0, 10, "1d6", "piercing", "martial", "dexterity"
)
longbow = _weapon(
    "longbow", "longbow", 2.0, 50, "1d8", "piercing", "martial", "dexterity"
)
shortbow = _weapon(
    "shortbow", "shortbow", 2.0, 25, "1d6", "piercing", "simple", "dexterity"
)
dagger = _weapon("dagger", "dagger", 1.0, 2, "1d4", "piercing", "simple", "dexterity")
quarterstaff = _weapon(
    "quarterstaff", "quarterstaff", 4.0, 0, "1d6", "bludgeoning", "simple"
)
spear = _weapon("spear", "spear", 3.0, 1, "1d6", "piercing", "simple")

# Containers and common supplies
backpack = _starting_item(
    "backpack", "backpack", 5.0, 2, "container", capacity=30.0, transparent="off"
)
quiver = _starting_item(
    "quiver", "quiver", 1.0, 1, "container", capacity=5.0, transparent="off"
)
pouch = _starting_item(
    "pouch", "pouch", 1.0, 0, "container", capacity=6.0, transparent="off"
)
arrows = _starting_item("arrows", "arrows", 0.05, 0)
thieves_tools = _starting_item(
    "thieves_tools",
    "thieves' tools",
    1.0,
    25,
    "other",
    tool_kind="thieves_tools",
    equipment_capabilities=["tool:thieves_tools"],
)
rowboat = _starting_item(
    "rowboat", "rowboat", 80.0, 50, "boat", equipment_capabilities=["terrain:boat"]
)
fireward_cloak = _starting_item(
    "fireward_cloak",
    "fireward cloak",
    2.0,
    50,
    "worn",
    wear_locations=["back"],
    equipment_capabilities=["resistance:fire"],
)
weatherproof_cloak = _starting_item(
    "weatherproof_cloak",
    "weatherproof cloak",
    2.0,
    10,
    "worn",
    wear_locations=["back"],
    equipment_capabilities=["weather_protection"],
)
wand_of_magic_missiles = _starting_item(
    "wand_of_magic_missiles",
    "Wand of Magic Missiles",
    1.0,
    100,
    "wand",
    equipment_capabilities=["activation:charged"],
    magic_item={"version": 1, "definition": "wand.magic_missiles", "uses": 0},
    item_resource={
        "version": 1,
        "kind": "charges",
        "resource_key": "wand_magic_missiles",
        "current": 7,
        "maximum": 7,
        "recharge": "dawn",
        "recharge_amount": 7,
    },
)
calligraphers_supplies = _starting_item(
    "calligraphers_supplies", "calligrapher's supplies", 5.0, 10
)
healers_kit = _starting_item("healers_kit", "healer's kit", 3.0, 5)
gaming_dice = _starting_item("gaming_dice", "dice set", 0.0, 0)
gaming_dragonchess = _starting_item("gaming_dragonchess", "dragonchess set", 0.5, 1)
gaming_cards = _starting_item("gaming_cards", "playing card set", 0.0, 0)
gaming_three_dragon_ante = _starting_item(
    "gaming_three_dragon_ante", "Three-Dragon Ante set", 0.0, 1
)
holy_symbol = _starting_item("holy_symbol", "holy symbol", 1.0, 5)
spellbook = _starting_item("spellbook", "spellbook", 3.0, 50)
robe = _starting_item("robe", "robe", 4.0, 1, "worn", wear_locations=["body"])
travelers_clothes = _starting_item(
    "travelers_clothes", "traveler's clothes", 4.0, 2, "worn", wear_locations=["body"]
)
fine_clothes = _starting_item(
    "fine_clothes", "fine clothes", 6.0, 15, "worn", wear_locations=["body"]
)
costume = _starting_item("costume", "costume", 4.0, 5, "worn", wear_locations=["body"])
book = _starting_item("book", "book", 5.0, 25)
parchment = _starting_item("parchment", "sheet of parchment", 0.0, 0)
ink = _starting_item("ink", "bottle of ink", 0.0, 10)
ink_pen = _starting_item("ink_pen", "ink pen", 0.0, 0, "pen")
crowbar = _starting_item("crowbar", "crowbar", 5.0, 2)
blanket = _starting_item("blanket", "blanket", 3.0, 0)
holy_water = _starting_item("holy_water", "flask of holy water", 1.0, 25)
tinderbox = _starting_item("tinderbox", "tinderbox", 1.0, 0)
ball_bearings = _starting_item("ball_bearings", "ball bearings", 2.0, 1)
bell = _starting_item("bell", "bell", 0.0, 1)
oil_flask = _starting_item("oil_flask", "flask of oil", 1.0, 0)
rope = _starting_item("rope", "50-foot hempen rope", 10.0, 1)
caltrops = _starting_item("caltrops", "bag of caltrops", 2.0, 1)
torch = _starting_item(
    "torch", "torch", 1.0, 0, "light", equipment_capabilities=["light"]
)
lamp = _starting_item("lamp", "lamp", 1.0, 0, "light")
hooded_lantern = _starting_item("hooded_lantern", "hooded lantern", 2.0, 5, "light")
candle = _starting_item("candle", "candle", 0.0, 0, "light")
rations = _starting_item("rations", "day of rations", 2.0, 0, "food")
waterskin = _starting_item("waterskin", "waterskin", 5.0, 0, "drinkcon")
bedroll = _starting_item("bedroll", "bedroll", 7.0, 1)
iron_pot = _starting_item("iron_pot", "iron pot", 10.0, 2)
shovel = _starting_item("shovel", "shovel", 5.0, 2)
artisans_tools = _starting_item("artisans_tools", "artisan's tools", 5.0, 15)
forgery_kit = _starting_item("forgery_kit", "forgery kit", 5.0, 15)
mirror = _starting_item("mirror", "steel mirror", 0.5, 5)
perfume = _starting_item("perfume", "perfume", 0.0, 5)
musical_instrument = _starting_item("musical_instrument", "musical instrument", 3.0, 5)
cartographers_tools = _starting_item(
    "cartographers_tools", "cartographer's tools", 6.0, 15
)
herbalism_kit = _starting_item("herbalism_kit", "herbalism kit", 3.0, 5)
navigators_tools = _starting_item("navigators_tools", "navigator's tools", 2.0, 25)
manacles = _starting_item("manacles", "manacles", 6.0, 2)
signet_ring = _starting_item(
    "signet_ring", "signet ring", 0.0, 5, "worn", wear_locations=["right finger"]
)
