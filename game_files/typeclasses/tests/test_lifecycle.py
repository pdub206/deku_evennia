"""Tests for the WORLD-03 lifecycle and recovery contract."""

import time
from unittest.mock import MagicMock, patch

from commands.account import CmdOOC
from django.conf import settings
from evennia.commands.default.account import CmdOOC as BaseCmdOOC
from evennia.server.models import ServerConfig
from evennia.server.serversession import ServerSession as BaseServerSession
from evennia.utils.test_resources import EvenniaTest
from server.conf import at_server_startstop
from server.conf.serversession import ServerSession
from systems.effects import (EFFECT_REGISTRY, EFFECTS_ATTRIBUTE,
                             EffectDefinition, EffectMessage)
from systems.lifecycle import (CHARACTER_LIFECYCLE_ATTRIBUTE,
                               CHARACTER_NOTICE_ATTRIBUTE,
                               SERVER_LIFECYCLE_CONFIG_KEY,
                               CharacterAvailability, LifecycleConsumer,
                               ServerTransitionMode, ServerTransitionPhase,
                               UnavailabilityCause, deliver_character_notices,
                               mark_character_available,
                               mark_character_unavailable,
                               mark_session_unpuppet_cause,
                               prepare_server_transition,
                               queue_or_deliver_character_notice,
                               recover_server_transition,
                               register_lifecycle_consumer,
                               resolve_unavailability_cause,
                               unregister_lifecycle_consumer)
from systems.pulses import PulseEvent, PulseLane, process_effect_pulse

_OFFLINE_EFFECT = EffectDefinition(
    key="test.world03.offline",
    name="Offline Effect",
    duration=1,
    messages={"expire": EffectMessage(target="Your offline effect ends.")},
)
_OFFLINE_PERMANENT = EffectDefinition(
    key="test.world03.offline_permanent",
    name="Offline Permanent Effect",
)
for _definition in (_OFFLINE_EFFECT, _OFFLINE_PERMANENT):
    if EFFECT_REGISTRY.get(_definition.key) is None:
        EFFECT_REGISTRY.register(_definition)


class TestCharacterLifecycle(EvenniaTest):
    """Character transitions are final-session-aware and idempotent."""

    consumer_key = "test.world03.character"

    def setUp(self):
        super().setUp()
        self.events = []
        register_lifecycle_consumer(
            LifecycleConsumer(self.consumer_key, on_character=self.events.append)
        )
        self.addCleanup(unregister_lifecycle_consumer, self.consumer_key)
        self.char1.attributes.remove(CHARACTER_LIFECYCLE_ATTRIBUTE)
        self.char1.attributes.remove(CHARACTER_NOTICE_ATTRIBUTE)

    def test_individual_session_loss_does_not_make_character_unavailable(self):
        result = mark_character_unavailable(
            self.char1,
            UnavailabilityCause.DISCONNECT,
            has_controlling_sessions=True,
        )

        self.assertFalse(result.changed)
        self.assertEqual(self.events, [])
        self.assertIsNone(self.char1.attributes.get(CHARACTER_LIFECYCLE_ATTRIBUTE))

    def test_final_loss_and_reconnect_each_dispatch_once(self):
        unavailable = mark_character_unavailable(
            self.char1,
            UnavailabilityCause.DISCONNECT,
            has_controlling_sessions=False,
        )
        duplicate = mark_character_unavailable(
            self.char1,
            UnavailabilityCause.DISCONNECT,
            has_controlling_sessions=False,
        )
        available = mark_character_available(self.char1)
        duplicate_available = mark_character_available(self.char1)

        self.assertTrue(unavailable.changed)
        self.assertFalse(duplicate.changed)
        self.assertTrue(available.changed)
        self.assertFalse(duplicate_available.changed)
        self.assertEqual(
            [event.availability for event in self.events],
            [CharacterAvailability.UNAVAILABLE, CharacterAvailability.AVAILABLE],
        )
        self.assertEqual([event.sequence for event in self.events], [1, 2])

    def test_first_session_reconnect_delivers_notice_once(self):
        mark_character_unavailable(
            self.char1,
            UnavailabilityCause.DISCONNECT,
            has_controlling_sessions=False,
        )
        queue_or_deliver_character_notice(
            self.char1,
            "Welcome back notice.",
            notice_id="reconnect-test",
        )
        self.char1.msg = MagicMock()

        with patch.object(self.char1.sessions, "count", side_effect=[1, 2]):
            self.char1.at_post_puppet()
            self.char1.at_post_puppet()

        reconnect_messages = [
            call
            for call in self.char1.msg.call_args_list
            if call.args == ("Welcome back notice.",)
        ]
        self.assertEqual(len(reconnect_messages), 1)
        self.assertEqual(
            [event.availability for event in self.events],
            [CharacterAvailability.UNAVAILABLE, CharacterAvailability.AVAILABLE],
        )

    def test_unpuppet_hook_dispatches_before_default_character_is_stowed(self):
        locations = []
        unregister_lifecycle_consumer(self.consumer_key)
        register_lifecycle_consumer(
            LifecycleConsumer(
                self.consumer_key,
                on_character=lambda event: locations.append(event.character.location),
            )
        )
        self.char1.db.session_login_time = time.time() - 5

        with patch.object(self.char1.sessions, "count", return_value=0):
            self.char1.at_post_unpuppet(self.account, session=None)

        self.assertEqual(locations, [self.room1])
        self.assertIsNone(self.char1.location)
        self.assertIsNone(self.char1.db.session_login_time)
        self.assertGreaterEqual(self.char1.db.time_played, 5)

    def test_nonfinal_unpuppet_preserves_location_and_play_interval(self):
        login_time = time.time() - 5
        self.char1.db.session_login_time = login_time

        with patch.object(self.char1.sessions, "count", return_value=1):
            self.char1.at_post_unpuppet(self.account, session=None)

        self.assertEqual(self.events, [])
        self.assertEqual(self.char1.location, self.room1)
        self.assertEqual(self.char1.db.session_login_time, login_time)

    def test_session_markers_distinguish_disconnect_ooc_and_cold_shutdown(self):
        session = MagicMock()
        session.ndb = MagicMock()
        session.ndb._world_unpuppet_cause = None

        self.assertEqual(
            resolve_unavailability_cause(session), UnavailabilityCause.UNPUPPET
        )
        mark_session_unpuppet_cause(session, UnavailabilityCause.OOC)
        self.assertEqual(resolve_unavailability_cause(session), UnavailabilityCause.OOC)
        self.assertEqual(
            resolve_unavailability_cause(session, reason="connection lost"),
            UnavailabilityCause.DISCONNECT,
        )
        self.assertEqual(
            resolve_unavailability_cause(session, cold_shutdown=True),
            UnavailabilityCause.COLD_SHUTDOWN,
        )

    def test_consumer_failure_does_not_block_other_consumers(self):
        good_key = "test.world03.good"
        bad_key = "test.world03.bad"
        received = []
        register_lifecycle_consumer(
            LifecycleConsumer(good_key, on_character=received.append)
        )
        register_lifecycle_consumer(
            LifecycleConsumer(
                bad_key,
                on_character=MagicMock(side_effect=RuntimeError("broken")),
            )
        )
        self.addCleanup(unregister_lifecycle_consumer, good_key)
        self.addCleanup(unregister_lifecycle_consumer, bad_key)

        with patch("systems.lifecycle.logger.log_trace") as log_trace:
            result = mark_character_unavailable(
                self.char1,
                UnavailabilityCause.DISCONNECT,
                has_controlling_sessions=False,
            )

        self.assertEqual(result.failures, 1)
        self.assertEqual(result.dispatched, 2)
        self.assertEqual(len(received), 1)
        log_trace.assert_called_once()

    def test_malformed_character_state_is_repaired(self):
        self.char1.db.world_lifecycle = {
            "version": 1,
            "availability": "missing",
            "sequence": -1,
            "cause": "unknown",
        }

        with patch("systems.lifecycle.logger.log_err") as log_err:
            result = mark_character_unavailable(
                self.char1,
                UnavailabilityCause.DISCONNECT,
                has_controlling_sessions=False,
            )

        self.assertTrue(result.changed)
        self.assertEqual(result.event.sequence, 1)
        log_err.assert_called_once()


class TestReconnectNotices(EvenniaTest):
    """Offline terminal effect messages are delivered at most once."""

    def setUp(self):
        super().setUp()
        self.char1.attributes.remove(EFFECTS_ATTRIBUTE)
        self.char1.attributes.remove(CHARACTER_LIFECYCLE_ATTRIBUTE)
        self.char1.attributes.remove(CHARACTER_NOTICE_ATTRIBUTE)

    def test_offline_effect_expires_and_queues_one_reconnect_notice(self):
        self.char1.effects.add(_OFFLINE_EFFECT.key, quiet=True)
        mark_character_unavailable(
            self.char1,
            UnavailabilityCause.DISCONNECT,
            has_controlling_sessions=False,
        )
        self.char1.move_to(None, to_none=True)
        self.char1.msg = MagicMock()

        first = process_effect_pulse(PulseEvent(6, PulseLane.EFFECTS, 1))
        second = process_effect_pulse(PulseEvent(12, PulseLane.EFFECTS, 2))

        self.assertEqual(first.removals, 1)
        self.assertEqual(second.removals, 0)
        self.assertFalse(self.char1.effects.has(_OFFLINE_EFFECT.key))
        self.char1.msg.assert_not_called()
        notices = self.char1.attributes.get(CHARACTER_NOTICE_ATTRIBUTE)
        self.assertEqual(len(notices["entries"]), 1)

        mark_character_available(self.char1)
        delivered = deliver_character_notices(self.char1)
        delivered_again = deliver_character_notices(self.char1)

        self.assertEqual((delivered.delivered, delivered.failures), (1, 0))
        self.assertEqual((delivered_again.delivered, delivered_again.failures), (0, 0))
        self.char1.msg.assert_called_once_with("Your offline effect ends.")

    def test_duplicate_notice_identity_is_stored_once(self):
        mark_character_unavailable(
            self.char1,
            UnavailabilityCause.OOC,
            has_controlling_sessions=False,
        )

        queue_or_deliver_character_notice(self.char1, "One.", notice_id="same")
        queue_or_deliver_character_notice(self.char1, "One.", notice_id="same")

        state = self.char1.attributes.get(CHARACTER_NOTICE_ATTRIBUTE)
        self.assertEqual(state["entries"], [{"id": "same", "message": "One."}])

    def test_offline_permanent_effect_survives_live_pulses(self):
        permanent = self.char1.effects.add(_OFFLINE_PERMANENT.key, quiet=True)
        mark_character_unavailable(
            self.char1,
            UnavailabilityCause.DISCONNECT,
            has_controlling_sessions=False,
        )
        self.char1.move_to(None, to_none=True)

        for sequence in range(1, 4):
            process_effect_pulse(PulseEvent(sequence * 6, PulseLane.EFFECTS, sequence))

        self.assertIsNotNone(self.char1.effects.get(permanent.effect.instance_id))

    def test_malformed_notice_state_is_repaired_without_delivery(self):
        self.char1.db.pending_world_notices = {"version": 999, "entries": "bad"}
        self.char1.msg = MagicMock()

        with patch("systems.lifecycle.logger.log_err") as log_err:
            result = deliver_character_notices(self.char1)

        self.assertEqual((result.delivered, result.failures), (0, 0))
        self.assertEqual(
            self.char1.db.pending_world_notices,
            {"version": 1, "entries": []},
        )
        self.char1.msg.assert_not_called()
        log_err.assert_called_once()


class TestServerLifecycle(EvenniaTest):
    """Server preparation and recovery use persistent exact-once tokens."""

    consumer_key = "test.world03.server"

    def setUp(self):
        super().setUp()
        ServerConfig.objects.conf(SERVER_LIFECYCLE_CONFIG_KEY, delete=True)
        self.events = []
        register_lifecycle_consumer(
            LifecycleConsumer(self.consumer_key, on_server=self.events.append)
        )
        self.addCleanup(unregister_lifecycle_consumer, self.consumer_key)
        self.addCleanup(
            ServerConfig.objects.conf,
            SERVER_LIFECYCLE_CONFIG_KEY,
            delete=True,
        )

    def test_reload_prepare_and_recover_share_one_token_without_duplicates(self):
        prepared = prepare_server_transition(
            ServerTransitionMode.HOT_RELOAD, boot_id="old-boot"
        )
        duplicate_prepare = prepare_server_transition(
            ServerTransitionMode.HOT_RELOAD, boot_id="old-boot"
        )
        recovered = recover_server_transition(
            ServerTransitionMode.HOT_RELOAD, boot_id="new-boot"
        )
        duplicate_recovery = recover_server_transition(
            ServerTransitionMode.HOT_RELOAD, boot_id="new-boot"
        )

        self.assertTrue(prepared.changed)
        self.assertFalse(duplicate_prepare.changed)
        self.assertTrue(recovered.changed)
        self.assertFalse(duplicate_recovery.changed)
        self.assertEqual(
            [(event.phase, event.sequence) for event in self.events],
            [
                (ServerTransitionPhase.PREPARE, 1),
                (ServerTransitionPhase.RECOVER, 1),
            ],
        )

    def test_cold_recovery_without_clean_prepare_gets_a_new_token(self):
        first = recover_server_transition(
            ServerTransitionMode.COLD_RESTART, boot_id="cold-boot"
        )
        repeated = recover_server_transition(
            ServerTransitionMode.COLD_RESTART, boot_id="cold-boot"
        )

        self.assertTrue(first.changed)
        self.assertFalse(repeated.changed)
        self.assertEqual(first.event.sequence, 1)
        self.assertEqual(first.event.phase, ServerTransitionPhase.RECOVER)

    def test_malformed_server_state_is_repaired(self):
        ServerConfig.objects.conf(
            SERVER_LIFECYCLE_CONFIG_KEY,
            value={"version": 999},
        )

        with patch("systems.lifecycle.logger.log_err") as log_err:
            result = recover_server_transition(
                ServerTransitionMode.COLD_RESTART, boot_id="repair-boot"
            )

        self.assertTrue(result.changed)
        self.assertEqual(result.event.sequence, 1)
        log_err.assert_called_once()

    def test_dangling_consumer_failure_does_not_block_recovery(self):
        good_key = "test.world03.server_good"
        bad_key = "test.world03.server_dangling"
        received = []
        register_lifecycle_consumer(
            LifecycleConsumer(good_key, on_server=received.append)
        )
        register_lifecycle_consumer(
            LifecycleConsumer(
                bad_key,
                on_server=MagicMock(
                    side_effect=LookupError("referenced object was deleted")
                ),
            )
        )
        self.addCleanup(unregister_lifecycle_consumer, good_key)
        self.addCleanup(unregister_lifecycle_consumer, bad_key)

        with patch("systems.lifecycle.logger.log_trace") as log_trace:
            result = recover_server_transition(
                ServerTransitionMode.HOT_RELOAD,
                boot_id="dangling-boot",
            )

        self.assertEqual(result.dispatched, 2)
        self.assertEqual(result.failures, 1)
        self.assertEqual(len(received), 1)
        log_trace.assert_called_once()


class TestLifecycleWiring(EvenniaTest):
    """Evennia entry points classify transitions without changing behavior."""

    def test_custom_server_session_is_enabled(self):
        self.assertEqual(
            settings.SERVER_SESSION_CLASS,
            "server.conf.serversession.ServerSession",
        )

    def test_ooc_command_marks_only_its_unpuppet_operation(self):
        session = ServerSession()
        session.sessid = 1
        command = CmdOOC()
        command.session = session
        observed = []

        def observe_ooc(_command):
            observed.append(resolve_unavailability_cause(session))

        with patch.object(BaseCmdOOC, "func", new=observe_ooc):
            command.func()

        self.assertEqual(observed, [UnavailabilityCause.OOC])
        self.assertEqual(
            resolve_unavailability_cause(session),
            UnavailabilityCause.UNPUPPET,
        )

    def test_network_disconnect_marks_only_base_cleanup(self):
        session = ServerSession()
        session.sessid = 1
        observed = []

        def observe_disconnect(_session, reason=None):
            observed.append(resolve_unavailability_cause(session, reason=reason))

        with patch.object(
            BaseServerSession,
            "at_disconnect",
            new=observe_disconnect,
        ):
            session.at_disconnect()

        self.assertEqual(observed, [UnavailabilityCause.DISCONNECT])
        self.assertEqual(
            resolve_unavailability_cause(session),
            UnavailabilityCause.UNPUPPET,
        )

    def test_server_hooks_pass_explicit_hot_and_cold_modes(self):
        with (
            patch.object(at_server_startstop, "prepare_server_transition") as prepare,
            patch.object(at_server_startstop, "recover_server_transition") as recover,
        ):
            at_server_startstop.at_server_reload_stop()
            at_server_startstop.at_server_reload_start()
            at_server_startstop.at_server_cold_stop()
            at_server_startstop.at_server_cold_start()

        self.assertEqual(
            [call.args[0] for call in prepare.call_args_list],
            [ServerTransitionMode.HOT_RELOAD, ServerTransitionMode.COLD_RESTART],
        )
        self.assertEqual(
            [call.args[0] for call in recover.call_args_list],
            [ServerTransitionMode.HOT_RELOAD, ServerTransitionMode.COLD_RESTART],
        )
