"""P04-A01 immutable source-census coverage."""

from dataclasses import replace

from evennia.utils.test_resources import EvenniaTest
from systems.srd_content_census import (SRD_CONTENT_CENSUS, ContentCensusError,
                                        build_content_census, census_report)


class TestSRDContentCensus(EvenniaTest):
    """Every authored progression row has one accountable P-04 record."""

    def test_census_is_immutable_and_covers_current_source_projection(self):
        report = census_report()

        self.assertEqual(SRD_CONTENT_CENSUS.version, 1)
        self.assertEqual(len(SRD_CONTENT_CENSUS.records), 370)
        self.assertEqual(len(report["released"]), 3)
        self.assertIn("feature:fighter.second_wind:level:1", report["released"])
        self.assertIn("feature:barbarian.rage:level:1", report["catalogued"])

    def test_duplicate_or_unowned_record_fails_closed(self):
        first = SRD_CONTENT_CENSUS.records[0]
        with self.assertRaises(ContentCensusError):
            build_content_census((first, first))
        invalid = replace(first, owner_task="P04-UNKNOWN")
        with self.assertRaises(ContentCensusError):
            build_content_census((invalid,))
