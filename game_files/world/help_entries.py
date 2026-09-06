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
        "key": "checks",
        "aliases": ["ability checks", "skill checks", "advantage", "dc"],
        "category": "Character",
        "text": """
            When an uncertain action matters, the game may call for an ability
            check. Roll a d20 and add the relevant ability modifier. A skill or
            tool you are proficient with adds your proficiency bonus once;
            expertise doubles that proficiency contribution.

            A Difficulty Class (DC) is the number the total must meet or beat.
            Some actions oppose another character's check instead. Ties usually
            preserve the current situation. Passive checks use 10 plus the same
            bonuses, which lets a character notice things without rolling.

            Advantage rolls two d20s and uses the higher result; disadvantage
            uses the lower. If both apply, they cancel. A natural 1 or 20 on an
            ability check is not an automatic failure or success.
        """,
    },
    {
        "key": "check diagnostics",
        "aliases": ["@check", "builder checks", "adv-04"],
        "category": "Building",
        "locks": "read:perm(Builder)",
        "text": """
            Builders can test a visible character's calculation with
            |w@check <target> = <ability>[/<skill>] <dc>|n. DCs are bounded
            from 5 to 30. This command rolls only a diagnostic check: it does
            not carry out an action, reveal hidden targets, bypass a lock, or
            apply any consequence.

            Builder-authored action DC fields must use the same 5–30 range.
            Do not store arbitrary player-entered DC text in prototypes.
        """,
    },
    {
        "key": "class progression",
        "aliases": ["classes", "class features", "level features"],
        "category": "Character",
        "text": """
            Your class determines your hit die, training, saving throws, and
            the features, resources, spell access, and choices available as
            you gain levels. Class progress is fixed when you create your
            character: multiclassing is not available.

            Class features and resources unlock automatically when their level
            is earned. Some gains are choices; they remain pending until you
            make the required selection through training. A later rules update
            never silently changes benefits already recorded on your character.
        """,
    },
    {
        "key": "class progression registry",
        "aliases": ["adv-02", "class registry", "progression validation"],
        "category": "Building",
        "locks": "read:perm(Builder)",
        "text": """
            The class progression registry is the sole source for selectable
            class keys, level grants, training, resources, and spell access.
            A class is selectable only when all twenty levels and every
            referenced feature, choice, resource, spell access entry, owner,
            and help key validate together.

            Registry definitions contain only stable primitive keys. Do not
            store callbacks, imported classes, commands, or display prose in
            prototypes or character Attributes. A registry fingerprint is
            recorded at level one so a changed definition can be identified;
            correcting an incompatible existing character is an ADV-06 task,
            never a reload side effect.
        """,
    },
    {
        "key": "practice",
        "aliases": ["training choices", "train", "class training"],
        "category": "Character",
        "text": """
            Use |wpractice|n to review your known skill proficiencies,
            automatic class features, class resources, spell access, and any
            choices still awaiting training. It is read-only and works
            anywhere. |wtrain|n with no arguments gives a short list of your
            pending choices.

            XP raises your level, hit points, and automatic class benefits
            immediately. A trainer never holds an earned level hostage.
            Training is only for a listed class choice. To make one, use
            |wtrain <choice> = <option>|n, or name an NPC explicitly with
            |wtrain <choice> = <option> at <trainer>|n. The trainer must be
            nearby, offer your class and that choice, and be willing to train
            you. There is currently no generic practice-point pool or fee.

            Some spellcasting classes will later distinguish spells known,
            prepared spells, and spellbooks. Until their spell rules are
            released, |wpractice|n only reports the class access you have
            earned; it cannot teach unregistered spells.
        """,
    },
    {
        "key": "trainer profiles",
        "aliases": ["adv-03", "npc trainers", "training service"],
        "category": "Building",
        "locks": "read:perm(Builder)",
        "text": """
            A trainer is an NPC with a validated versioned trainer profile.
            The profile names only registered class keys and ADV-02 choice
            keys, plus inclusive minimum and maximum level bands and an
            ordinary service-access lock. It contains no Python, callbacks,
            player-selected import paths, implicit practice points, or
            arbitrary costs.

            A profile is opt-in: an NPC without one is not a trainer. Keep
            training locks ordinary and player-facing; never expose private
            lock expressions in room text or command feedback. The service
            validates the profile, NPC location, class, level, pending
            entitlement, and lock both before and inside its transaction.
            Current builder UI support is intentionally deferred with MOB-06;
            configure profiles through reviewed content tooling only.
        """,
    },
    {
        "key": "pets",
        "aliases": ["pet", "followers", "order", "charm"],
        "category": "Character",
        "text": """
            A pet has a durable owner, while charm is temporary control. Either
            may let you issue an |worder|n while the creature is in your room:
            |worder <pet> <action>|n. The available actions are |wfollow|n,
            |wstay|n, and |wflee|n; for example, |worder hound follow|n.
            Orders never run arbitrary commands, speech, building tools, or
            movement. Flee is queued for the pet's next combat action.

            Use |wpet <pet> follow|n or |wpet <pet> stay|n for the same direct
            control, and |wpet <pet> release|n to end durable ownership.
            Pets are acquired or charmed only by an ability or encounter that
            explicitly permits it. Following uses normal exits and movement rules. A pet will not
            teleport, reveal hidden routes, bypass a locked door, or cross an
            area boundary it is not allowed to cross. If you disconnect, it
            stops following but remains yours; reconnecting never moves it to
            you automatically. Releasing a pet ends ownership and control but
            does not destroy it or its possessions. Damage dealt by a pet is
            credited to its responsible owner or controller at the time of the
            damage.
        """,
    },
    {
        "key": "NPC special behaviors",
        "aliases": ["npc specials", "mobile specials", "mob specials"],
        "category": "Building",
        "locks": "read:perm(Builder)",
        "text": """
            NPC templates can have registered special behaviors through the
            |wspecials|n builder field. The value is JSON with version 1 and a
            |wbehaviors|n list. Every list entry has only a registered |wkey|n
            and a primitive |wconfig|n mapping; it can never contain Python,
            commands, callbacks, live objects, or an arbitrary lock string.

            Specials have code-owned priorities and run in that deterministic
            order. A special may decline an event; only one may take an action
            for a given mobile decision or event, and declared conflicts are
            reported as blocked rather than silently replacing another special.
            Unknown, unavailable, malformed, or duplicate stored entries fail
            closed without preventing the other valid entries from running.

            Built-in |wguard|n and |wscavenger|n behaviors delegate to the
            existing legal combat and pickup rules. |wspeaker|n needs a trigger
            and response and replies through ordinary language-aware speech.
            |wunique_trigger|n is a small message trigger with an optional
            once-only primitive state. Shopkeeper, trainer, caster, and healer
            assignments remain deferred until their owning services are added.

            Template changes affect future spawned copies only. Edit a live NPC
            for a deliberate one-off. Builder permission is required to assign
            or edit any special configuration.
        """,
    },
    {
        "key": "NPC pet control",
        "aliases": ["npc ownership", "npc charm", "pet locks"],
        "category": "Building",
        "locks": "read:perm(Builder)",
        "text": """
            Pet ownership and charm are opt-in NPC capabilities. An NPC must
            grant the appropriate |wpet|n, |wcharm|n, |wtransfer|n, and
            |worder|n locks; control is denied when a lock is absent. Content
            such as a taming encounter or a charm effect uses the shared
            relationship service to acquire or bind control.

            A controlled NPC may receive only registered orders: |wfollow|n,
            |wstay|n, and |wflee|n. Player text is never forwarded to an NPC's
            normal command handler. Durable ownership persists through normal
            disconnects; transient following and queued combat work do not.
            The |w@mobile|n diagnostic shows ownership, charm source, follow
            state, and retained safe failure information for a live NPC.
        """,
    },
    {
        "key": "NPC combat profiles",
        "aliases": ["npc combat", "mobile combat", "mob combat"],
        "category": "Building",
        "locks": "read:perm(Builder)",
        "text": """
            NPCs retaliate only after they take hostile damage; they do not begin
            fights on their own. Their |wcombat_profile|n builder field is JSON
            containing a target policy, an allowlisted weighted list of registered
            tactical actions with primitive arguments and cooldowns, and an NPC
            |wwimpy|n percentage from 0 through 90.

            New NPC templates use the safe basic-attack profile: current target,
            no tactics, and wimpy 0. Invalid profiles are rejected. A wimpy NPC
            makes one normal flee attempt when it crosses its threshold, then must
            heal above it before another automatic attempt. Pursuit and navigation
            are not part of this profile.
        """,
    },
    {
        "key": "account",
        "aliases": ["account controls", "ic", "ooc", "puppet"],
        "category": "General",
        "text": """
            Your account owns exactly one player character. Use |wic <name>|n
            from the Out-of-Character screen to enter the world and |wooc|n to
            return to the account screen without disconnecting.

            You may connect several sessions to the same account, but only one
            session can control your character at a time. A second |wic|n attempt
            is rejected and does not disconnect or replace the active controller.
            Other connected sessions remain Out-of-Character.
        """,
    },
    {
        "key": "charcreate",
        "aliases": ["character creation", "chargen"],
        "category": "Character",
        "text": """
            The |wcharcreate|n command opens the character-creation wizard.

            Each account may own exactly one player character. Once creation is
            complete, you cannot create another character on that account.

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

            Only one connected session may control your character at a time.
            Other sessions can remain connected Out-of-Character, but an |wic|n
            attempt never takes control away from the active session.

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
              |wDexterity (DEX)|n    — Agility; ranged attacks, AC, Stealth, Reaction.
              |wConstitution (CON)|n — Endurance; Hit Points, concentration.
              |wIntelligence (INT)|n — Memory and reasoning; Arcana, History, Investigation.
              |wWisdom (WIS)|n       — Perception and intuition; Insight, Perception, Medicine.
              |wCharisma (CHA)|n     — Force of personality; Deception, Persuasion, Performance.

            Each score has a modifier: (score − 10) / 2, rounded down.

            ## Combat Stats

              |wHit Points (HP)|n     — Determined at level 1 by class hit die + CON modifier.
              |wArmor Class (AC)|n    — 10 + DEX modifier (base, no armor).
              |wReaction|n            — DEX modifier; slightly adjusts attack cadence.
              |wProficiency Bonus|n   — +2 at level 1; increases as you gain levels.
              |wPassive Perception|n  — 10 + WIS (Perception) modifier.
              |wSpeed|n               — Scales travel time relative to the normal 30 feet.

            Effective statistics update when your abilities, level, equipment,
            or active conditions change.

            ## Encumbrance

            Your sheet and |winventory|n show carried item count and weight. Your
            carrying capacity is based on effective Strength and size. A filled
            container counts as itself plus everything inside it. You may carry
            exactly your listed limits, but cannot pick up or receive anything
            that would exceed either one. If a later change leaves you
            overloaded, drop or give away items until your load is legal; you
            cannot use normal exits while overloaded.

            ## Natural Recovery

            While you are in the world, HP recovers automatically every minute
            when you are alive and not fighting. Recovery starts at your level
            plus Constitution modifier (at least 1), then improves with posture:
            sitting restores 1.25×, resting 1.5×, and sleeping 2× the standing
            amount. Fractions round down. You do not recover while incapacitated,
            dying, dead, at 0 HP, or while your character is offline or stowed.

            ## Identity

              |wClass|n, |wBackground|n, |wSpecies|n, |wAlignment|n, |wLanguages|n

              Level 1 characters start with 0 XP and advance by earning Experience Points.

        """,
    },
    {
        "key": "attack",
        "aliases": ["kill", "hit", "combat"],
        "category": "Combat",
        "text": """
            Start a fight with |wattack <target>|n. You can also use |wkill|n or
            |whit|n. Starting or switching a target does not strike immediately:
            attacks happen automatically on combat rounds.

            You attack with your wielded weapon, or an unarmed strike when no
            weapon is wielded. Armor Class determines whether a blow lands.
            A natural 1 misses, while a natural 20 is a critical and rolls the
            weapon's damage dice twice. Hits land on a body location; armor worn
            at that location can reduce matching damage.

            You may only attack an eligible character in the same room. Protected
            characters, unquelled staff, and no-combat locations cannot be
            attacked. Player-versus-player combat is enabled everywhere else;
            there are no consent, level-range, or opt-out rules.

            While fighting, normal exits are blocked. Use |wflee [exit]|n to queue
            an escape on your next combat action. With no exit, an eligible route
            is chosen for you. Fleeing never moves immediately, does not guarantee
            a route will remain available, and does not cause pursuit or an
            opportunity attack.
        """,
    },
    {
        "key": "tactical combat",
        "aliases": ["aim", "backstab", "bash", "kick", "tactical actions"],
        "category": "Combat",
        "text": """
            Tactical actions are prepared now and resolve on your next ready
            combat action. Preparing a new tactical action replaces the one you
            already prepared. If its target, equipment, position, or other
            requirement changes before then, the prepared action is spent and
            does not turn into an ordinary attack.

            |waim <location>|n or |waim <target> <location>|n chooses one supported
            location: head, neck, body, shoulders, arms, wrists, hands, legs, or
            feet. Aimed attacks have disadvantage, even when they choose the
            location successfully.

            |wbackstab [target]|n is a Rogue Sneak Attack. You need a wielded
            finesse weapon. It adds 1d6 at Rogue level 1 and another 1d6 every
            two Rogue levels, once per combat round, when your target is unaware
            of you in a solo fight or is focused on someone else in a larger
            fight. A miss does not use that round's successful Sneak Attack.

            |wbash [target]|n requires a shield. Your Strength (Athletics) is
            contested by the target's better Athletics or Acrobatics. A creature
            more than one size larger cannot be knocked down. A successful bash
            deals no damage, makes the target prone, and costs its next combat
            action as it regains its footing. Prone does not stack or refresh.

            |wkick [target]|n is available to everyone and needs no free hand.
            It attacks with Strength for 1d4 + Strength bludgeoning damage at the
            body or a leg. Hit or miss, your following combat action is delayed
            to 150% of your normal current combat delay.
        """,
    },
    {
        "key": "consider",
        "aliases": ["assess", "combat estimate"],
        "category": "Combat",
        "text": """
            Use |wconsider <target>|n to make a quick, non-hostile assessment of
            a visible character in your room. It neither rolls dice nor starts a
            fight. The result is a broad estimate — |wtrivial|n, |weasy|n,
            |weven|n, |wdangerous|n, |wdeadly|n, or |woverwhelming|n — based on
            both combatants' current health, effective level, equipment,
            defenses, damage, and combat pace. Conditions and equipment can
            change the answer. You never see another character's exact numbers.

            A conscious player character you consider is privately notified that
            you looked them over; nobody else in the room is notified.
        """,
    },
    {
        "key": "wimpy",
        "aliases": ["automatic flee", "auto flee"],
        "category": "Combat",
        "text": """
            Use |wwimpy <0-90>|n to choose the HP percentage at which your
            character automatically queues an ordinary random |wflee|n attempt.
            |wwimpy 0|n (the default) turns it off. The attempt happens only on
            your next ready combat action; it never moves you immediately and
            can still fail if no route remains available.

            Wimpy triggers once when your HP crosses down to the selected value.
            Healing above that value rearms it. You may always use |wflee|n
            manually, including after an automatic attempt fails.
        """,
    },
    {
        "key": "combat prompt",
        "aliases": ["combatprompt", "health"],
        "category": "Combat",
        "text": """
            While fighting, the combat prompt shows your current and maximum HP,
            your target's qualitative health, your queued action, and whether
            wimpy is armed or triggered. Target HP and other private combat
            numbers are never shown. Use |wcombatprompt on|n or
            |wcombatprompt off|n; it is on by default and only changes the
            prompt, never combat events.

            Health descriptions are |wunhurt|n (100%), |whealthy|n (76--99%),
            |wwounded|n (51--75%), |wbadly wounded|n (26--50%), and |wnear death|n
            (1--25%). At 0 HP, the display says |wdying|n, |wstable|n, or |wdead|n.
        """,
    },
    {
        "key": "combat verbosity",
        "aliases": ["combatverbose", "combat messages"],
        "category": "Combat",
        "text": """
            Use |wcombatverbose compact|n, |wnormal|n, or |wdetailed|n to set
            your saved combat-message preference. Normal is the default.
            Compact keeps routine output brief; important damage, tactical,
            injury, stabilization, and death events still appear. Detailed adds
            your own attack and damage mechanics, but never another character's
            Armor Class, modifiers, or private statistics.
        """,
    },
    {
        "key": "injuries",
        "aliases": ["unconscious", "death saves", "stabilize", "stabilise", "death"],
        "category": "Combat",
        "text": """
            At 0 HP, player characters fall unconscious and begin making death
            saves on each recovery pulse. Roll 10 or higher for a success; three
            successes leave you stable and unconscious. A 2--9 is a failure, a
            natural 1 counts as two failures, and a natural 20 restores 1 HP.
            Three failures means death. Stable characters do not recover on
            their own, but healing above 0 HP wakes them resting.

            Use |wstabilize <target>|n (or |wstabilise|n) on a dying character in
            your room. It makes a DC 10 Wisdom (Medicine) check. During combat,
            this is queued for your next action rather than taking effect at once.
            Damage to an unconscious character is especially dangerous: a hit is
            critical. Damage large enough to exceed a character's maximum HP
            after reaching 0 HP causes immediate death.

            Ordinary NPCs die at 0 HP unless a builder enables their death-save
            policy. Leaving with |wooc|n at 0 HP is fatal; a disconnect leaves
            death saves running. Corpses remain in the room after a death.

            When your character dies, you return to the account screen. Choose
            that character again to respawn immediately at the world sanctuary.
            Respawning restores maximum HP, clears temporary effects, and leaves
            you resting. It costs no XP, level, banked money, or additional items,
            but it does not reveal where your corpse is or how long it remains.

            A disconnected character stays in the world for 30 minutes. It can
            be seen, attacked, and continue fighting normally. Reconnecting in
            time restores control. Once safely stowed, a stable unconscious
            character returns to its original room at 1 HP; no corpse is made.
        """,
    },
    {
        "key": "corpses",
        "aliases": ["corpse", "loot", "looting"],
        "category": "Combat",
        "text": """
            When a character dies, their possessions remain in a corpse in the
            room. Use |wlook in <corpse>|n to inspect its visible contents and
            |wget <item> from <corpse>|n or |wget all from <corpse>|n to take
            items. If you cannot carry every item, |wget all|n takes what it
            can and tells you what remains.

            NPC corpses can be looted by anyone. A player character's corpse
            can only be emptied by that exact character, though everyone may
            inspect it. Corpses cannot be picked up or moved. NPC corpses decay
            after about ten minutes and player corpses after about thirty;
            remaining contents then spill into the room for anyone to take.

            Builders may set an NPC's |wcorpse_decay_minutes|n prototype field
            to a positive duration in minutes. Player corpse duration is a
            global game policy.

            NPC templates also have an |wxp_reward|n field. Set it to the
            non-negative base XP awarded when that NPC is defeated; zero means
            no XP. This is separate from the NPC's own |wxp|n statistic. NPC
            carried equipment and inventory are the only loot: changing a
            template does not replenish an existing NPC's lost items.
        """,
    },
    {
        "key": "advancement",
        "aliases": ["experience", "xp", "leveling", "level up"],
        "category": "Character",
        "text": """
            Experience Points (XP) are cumulative. Reaching a level's XP
            threshold raises your level automatically; no trainer is needed
            for your level, hit points, or other automatic benefits. Some
            future class benefits may be choices. Those remain pending until
            you train them instead of being selected for you.

            The current level cap is 20 at 355,000 XP. You may still earn and
            retain XP after that point, but it grants no additional levels or
            level-based benefits. Characters have one class; multiclassing is
            not available.
        """,
    },
    {
        "key": "experience from combat",
        "aliases": ["combat experience", "npc xp", "xp rewards"],
        "category": "Combat",
        "text": """
            Defeating an NPC can award Experience Points. The NPC's authored
            base reward is adjusted by ten percent for every level it is above
            or below you, from zero at ten levels below to double at ten levels
            above. You must be alive, conscious, and in the NPC's room when it
            dies. The most recent eligible contributor receives the whole award.

            Player-versus-player deaths never award XP. NPC loot is simply what
            the NPC was still carrying or wearing when it died; equipment or
            items it lost while alive do not reappear.
        """,
    },
    {
        "key": "positions",
        "aliases": ["position", "posture", "resting", "sleeping"],
        "category": "Character",
        "text": """
            Your position determines which actions you can take. Use |wsit|n,
            |wrest|n, |wsleep|n, |wstand|n, and |wwake|n to change posture.

            # subtopics

            ## Postures

            |wStanding|n characters can act, handle items, move, and fight.
            |wSitting|n and |wresting|n characters can look, communicate, handle
            items, and change posture, but must stand before moving or fighting.
            |wSleeping|n characters cannot perceive or act; use |wwake|n to wake
            into a sitting posture.

            ## Restricted States

            Combat, injuries, and conditions can temporarily impose a more
            restrictive state without changing your chosen posture. While
            |wfighting|n, you may look, communicate, and use combat actions, but
            cannot handle items, change posture, or use a normal exit. While
            |wstunned|n, |wincapacitated|n, |wdying|n, or |wdead|n, you cannot
            perform in-world actions.

            Help, account controls, character information, and appropriate staff
            recovery commands remain available regardless of position.

            A fight ends when you are no longer part of its encounter, such as
            when combat separates you from the other participants. Your chosen
            posture is restored automatically when fighting ends.

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
        "key": "conditions",
        "aliases": ["effects", "status effects", "buffs", "debuffs"],
        "category": "Character",
        "text": """
            Conditions are lasting effects that can change what your character
            can do or alter statistics shown by commands such as |wscore|n.
            They may be permanent or expire after a number of world pulses.

            The game tells you when a visible condition begins, changes,
            expires, or is removed. Reapplying a condition may be rejected,
            refresh its duration, replace it, or add a limited number of stacks,
            depending on that condition's rules.

            Some hostile conditions allow a saving throw when first applied or
            on later pulses. Others can be removed by an appropriate cure or
            dispelling effect. Those details depend on the individual condition.
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
        "key": "junk",
        "aliases": ["discard", "destroy item"],
        "category": "Items",
        "text": """
            Permanently discard an item you are carrying.

            Usage:
              junk <item>

            Junking destroys only that particular item. It cannot be recovered,
            but its underlying template remains in the game and builders can
            spawn new copies from it.
        """,
    },
    {
        "key": "wear",
        "aliases": ["equipment", "equip"],
        "category": "Items",
        "text": """
            Wear or ready an item you are carrying.

            Usage:
              wear <item>
              wear <item> <location>

            Most equipment chooses its only valid location automatically. Items
            that can use either side, such as rings, wristbands, shoulder items,
            and ankle items, require you to choose |wleft|n or |wright|n when both
            sides are open. If only one allowed location is open, it is chosen
            automatically.
            If an item supports unrelated locations, name the location instead.

            One item can occupy each equipment location. If a location is already
            occupied, you must free it before wearing another item there.

            # subtopics

            ## Armor

            Primary armor worn on your body determines Armor Class: light armor
            adds your full Dexterity modifier, medium armor adds at most +2 from
            Dexterity, and heavy armor does not add Dexterity. A shield adds its
            defense separately. Armor worn anywhere else never increases Armor
            Class.

            Locational armor instead reduces damage when its worn location is
            struck. Head, neck, and body protect those locations directly. Arms,
            hands, legs, and feet protect both sides; shoulder and wrist items
            protect only their named side. Percentage reduction is applied before
            flat reduction and can reduce damage to zero. Ordinary armor protects
            against bludgeoning, piercing, and slashing unless built otherwise.

            ## Training

            You may equip any armor, shield, or weapon, even without class
            training, and the equipment command will not stop or warn you.
            Untrained weapons do not add your proficiency bonus to attacks.
            Untrained armor still supplies its AC and damage mitigation, but may
            penalize relevant rolls and spellcasting.
        """,
    },
    {
        "key": "remove",
        "aliases": ["remove equipment", "unequip"],
        "category": "Items",
        "text": """
            Remove an item you are wearing and return it to your carried inventory.

            Usage:
              remove <item>

            You may abbreviate the item name as long as it identifies one equipped
            item. Removing equipment frees its location for another item.
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
              build                   show what you can build / what you're editing
              edit here               edit the room you're standing in
              edit new room <name>    create a fresh unlinked room and go to it
              edit new item <name>    create a new item template and edit it
              edit new npc <name>     create a template and spawn an NPC here
              edit item <name>        edit an existing item template
              edit npc <name>         edit an existing NPC template
              edit <object>           edit a live room, item, or NPC by name/#dbref

            You must have |wBuilder|n permission.  The classic builder commands
            (dig, create, set, desc, spawn, ...) still work for expert use.

            # subtopics

            ## Editing

            |wedit here|n (or |wedit <object>|n) puts you in a sticky editing
            context bound to that object.  A header like |w[build: Town Square
            (#5, Room)]|n reminds you what you're editing (and its kind), and the prompt
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
            the live world — use |wedit new room <name>|n.  That creates a standalone
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

            ## Items

            Items are authored as |ytemplates|n (prototypes) and then stamped into
            the world as copies — the DIKU object-vnum model.  You edit the
            template once; every copy is consistent with it, and you can tweak an
            individual copy for a one-off without touching the template.

              |wedit new item <name>|n   create a new item template and edit it
              |wedit item <name>|n       edit an existing template
              |w@spawn <key>|n           place a copy of a template in this room
              |witems|n / |witems <type>|n   list templates and untemplated live items

            A template uses the same verbs as a room — |wset|n, |wdesc|n, |wshow|n,
            |wfields|n, |wdel|n, |wdone|n — over these shared fields:

              |wname|n      the item's name
              |wdesc|n      its description
              |wweight|n    weight in pounds (counts against carry capacity)
              |wvalue|n     worth in coins
              |wwear_locations|n  comma-separated equipment slots, such as
                              |whead|n or |wleft wrist, right wrist|n

            Changes to a template persist immediately and apply to copies spawned
            afterwards.  To customise one existing copy in the world, |wedit|n it
            directly (by name or |w#dbref|n) — that's a one-off and leaves the
            template and the other copies alone.  Items created directly, including
            older items that predate templates, appear in |witems|n under
            "Untemplated live items" and can be edited by their |w#dbref|n.

            ### Types

            Give a template a |wtype|n with |wset type <kind>|n and extra fields for
            kinds that support them appear in |wfields|n and |wshow|n. Supported
            kinds are:

              |wlight, scroll, wand, staff, weapon, furniture, treasure, armor,
              potion, worn, other, trash, container, note, drinkcon, key, food,
              money, pen, boat, fountain|n

            Most kinds are classifications ready for future mechanics. These kinds
            currently add their own editable fields:

              |wset type weapon|n     adds |wdamage|n, damage |wsubtype|n,
                                  |wweapon_category|n, |wweapon_kind|n, and
                                  |wattack_ability|n
              |wset type armor|n      adds |wbase_ac|n, armor |wsubtype|n,
                                  |wmitigation_flat|n, |wmitigation_percent|n,
                                  and |wmitigation_types|n
              |wset type container|n  adds |wcapacity|n (max weight it can hold)
              |wset type none|n       back to a plain item (drops those fields)

            Armor mitigation protects the hit location implied by
            |wwear_locations|n; there is no separate coverage field. Mitigation
            types default to bludgeoning, piercing, and slashing when left unset.
            Percentage mitigation is limited to 80.

            The header shows what you're editing, e.g.
            |w[build: iron_sword (Weapon prototype)]|n.  Type |wfields|n any time
            to see exactly what you can set.

            ## NPCs

            NPCs are normal Character objects without an Account puppeting them.
            Like items, they are authored as reusable templates. Creating one
            also immediately spawns a live copy in the room where you are working:

              |wedit new npc <name>|n    create a template, spawn one here, and edit it
              |wedit npc <key>|n         edit an existing NPC template
              |wnpcs|n                   list templates and their live-copy counts
              |w@spawn <key>|n           place another copy from a template

            NPC templates expose every canonical field assigned to a finished
            player character: name, description, gender, species, class, age,
            alignment, background, size, languages, active language, skills,
            all six ability scores, level and XP, proficiency bonus, hit points,
            hit die, Reaction, Armor Class, passive Perception, speed, and an
            optional |wcorpse_decay_minutes|n override for that NPC's corpse.
            Use |wset behavior idle|n to keep an NPC still, or |wset behavior
            wander|n to let it take one legal exit per mobile decision. Wandering
            never opens or searches exits and respects ordinary exit locks and
            room limits. A sentinel does not wander. A stay-in-area wanderer (or
            fleeing NPC) remains within rooms carrying the same authored area
            tag; an untagged boundary leaves it with no route. NPCs that lose a
            combat target may pursue through legal routes, but stop when the
            target is lost, hidden, protected, unreachable, outside the allowed
            area, or the short pursuit limit is reached.
            MOB flags are set independently on the template: |wsentinel|n stops
            ordinary wandering; |wscavenger|n picks up at most one accessible,
            loose room item per mobile decision; |waggressive|n starts a normal
            fight with one detectable legal target; and |wstay_in_area|n limits
            later autonomous navigation to the NPC's authored area. Set each
            boolean flag with |wset <field> on|n or |woff|n. |wwimpy|n is the
            NPC-only 0–90% flee threshold, while |wdetection|n lists extra
            senses (hearing, sight, smell).

            |wprotected|n makes an NPC an illegal combat target. |wnoncombatant|n
            includes that protection and also prevents the NPC from initiating,
            joining, assisting, or retaliating in combat. Use protected for a
            target that may still take part in scripted combat; use noncombatant
            for someone who must never enter an encounter.
            Use |wfields|n for accepted values and |wshow|n for the current sheet.

            Changes persist immediately and affect copies spawned afterwards.
            A live NPC can be edited directly with |wedit <name>|n or
            |wedit #<dbref>|n for a one-off change that leaves its template alone.
            Each template-created copy records its source template permanently;
            older NPCs made directly remain explicitly untemplated. Killing or
            editing a live NPC never replaces, heals, moves, or re-equips it.
            Reset-managed populations are authored as named placements in an
            area's |wMOBILES|n data. A placement has a desired population and
            room/area ceilings; it is considered full even when one of its NPCs
            has wandered away. Resets, when enabled for an area, create only the
            missing fresh copies and never alter survivors. Manual |w@spawn|n
            copies have a source template but do not create a reset placement or
            change any population ceiling.

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
        "key": "@mobile",
        "aliases": ["@mob", "mobile diagnostics"],
        "category": "Building",
        "locks": "read:perm(Builder)",
        "text": """
            Inspect a mobile's current runtime state and safe MOB subsystem
            diagnostics. This command is for Builders and higher.

            Usage:
              @mobile <npc or #dbref>
              @mobile/template <prototype key>
              @mobile/area <area key>
              @mobile/placement <area key>:<placement key>
              @mobile/clear <npc or #dbref>

            A live NPC report distinguishes its durable source identity from
            mutable runtime state. |ymanaged|n copies belong to a named reset
            placement; |yunmanaged|n copies came from a template but have no
            reset placement; |yuntemplated|n legacy NPCs have neither identity.
            Area and placement reports use a fresh count of live NPC identity,
            never names, sessions, or a cached list.

            Sections marked unavailable have malformed or temporarily missing
            source data; inspection leaves them unchanged. The retained last
            failure contains stable subsystem/reason keys, repeats, and a
            recovery marker. A later success marks it recovered but keeps it
            visible. Use |w@mobile/clear|n only after following the repair
            workflow owned by the named MOB subsystem; clearing is audited.
        """,
    },
    {
        "key": "@effects",
        "aliases": ["@conditions", "effect inspection"],
        "category": "Staff",
        "locks": "read:perm(Builder)",
        "text": """
            Inspect persistent effects on a character, or explicitly repair
            malformed persistent effect data.

            Usage:
              @effects
              @effects <character or #dbref>
              @effects/repair <character or #dbref>

            Each entry shows its stable key and instance ID, stacks, remaining
            pulses or permanence, source, condition flags, numeric modifiers,
            saving throw, and ordinary removal categories. A missing definition
            is marked explicitly so staff can diagnose stale persistent data.

            Use |w@effects/repair|n only when data is invalid or an effect's
            definition is missing. The command quarantines the affected records
            and logs the staff member who requested the repair before removing
            them from active effect storage.

            You must have |wBuilder|n permission or higher.
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
