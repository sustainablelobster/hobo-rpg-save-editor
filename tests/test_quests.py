from __future__ import annotations

import json
import unittest

from hobo_rpg_save_editor import npc
from hobo_rpg_save_editor import quests


def flag_table(values: dict[str, int]) -> npc.NpcFlagTable:
    return npc.NpcFlagTable(
        count_offset=0,
        records_start=4,
        records_end=4 + len(values) * 8,
        records=tuple(
            npc.NpcFlagRecord(index, index + 1, index + 2, key, value)
            for index, (key, value) in enumerate(values.items())
        ),
    )


class QuestCatalogParsingTests(unittest.TestCase):
    def test_builds_catalog_from_all_quest_assets(self) -> None:
        assets = {
            "assets/hobothor/quests/json/brunoquest.json": json.dumps(
                {
                    "questTitle": "Who's Bruno?",
                    "questID": 42,
                    "qnodes": [{"aC": {"arch": 28}}],
                    "opts": [
                        {
                            "id": "opt1",
                            "c": "bool_BrunoMet_0",
                            "t": "Ask Anton about Bruno.",
                        }
                    ],
                    "reacts": [
                        {
                            "id": "react1",
                            "rews": ["bool_BrunoMet_1"],
                            "t": "You found out who Bruno is.",
                        }
                    ],
                }
            ),
            "assets/hobothor/quests/json/brigadasklad.json": json.dumps(
                {
                    "isPermanent": True,
                    "qnodes": [],
                }
            ),
        }

        catalog = quests.build_quest_catalog(assets)

        self.assertEqual(len(catalog.quests), 2)
        bruno = next(
            quest for quest in catalog.quests if quest.title == "Who's Bruno?"
        )
        self.assertEqual(bruno.quest_id, "42")
        self.assertEqual(bruno.type_label, quests.QUEST_TYPE_QUEST)
        self.assertEqual(bruno.associations, ("Bruno",))
        self.assertEqual(
            sorted({reference.key for reference in bruno.references}),
            ["BrunoMet"],
        )
        self.assertIn("Ask Anton about Bruno.", bruno.evidence)

        repeatable = next(
            quest for quest in catalog.quests if quest.asset_name == "brigadasklad"
        )
        self.assertEqual(repeatable.title, "Brigadasklad")
        self.assertEqual(
            repeatable.type_label,
            quests.QUEST_TYPE_REPEATABLE,
        )

    def test_reports_malformed_assets_without_dropping_valid_quests(
        self,
    ) -> None:
        catalog = quests.build_quest_catalog(
            {
                "assets/hobothor/quests/json/good.json": json.dumps(
                    {"questTitle": "Good Quest"}
                ),
                "assets/hobothor/quests/json/bad.json": "{not json",
            }
        )

        self.assertEqual([quest.title for quest in catalog.quests], ["Good Quest"])
        self.assertEqual(len(catalog.warnings), 1)
        self.assertIn("bad.json is not valid JSON", catalog.warnings[0])


class QuestCorrelationTests(unittest.TestCase):
    def quest(self, key_names: tuple[str, ...]) -> quests.QuestDefinition:
        return quests.QuestDefinition(
            asset_path="assets/hobothor/quests/json/test.json",
            asset_name="test",
            title="Test Quest",
            quest_id=None,
            type_label=quests.QUEST_TYPE_QUEST,
            associations=(),
            references=tuple(
                quests.QuestFlagReference(
                    key=key,
                    value=1,
                    role="sets",
                    text=f"Evidence for {key}.",
                )
                for key in key_names
            ),
            evidence=(),
        )

    def test_status_for_no_known_flags_none_partial_and_all_enabled(
        self,
    ) -> None:
        unknown = quests.correlate_quest(
            self.quest(("MissingFlag",)),
            flag_table({}),
        )
        self.assertEqual(
            unknown.progress.status,
            quests.QUEST_STATUS_NO_FLAGS,
        )

        not_started = quests.correlate_quest(
            self.quest(("FlagA", "FlagB")),
            flag_table({"FlagA": 0, "FlagB": 0}),
        )
        self.assertEqual(
            not_started.progress.status,
            quests.QUEST_STATUS_NOT_STARTED,
        )

        partial = quests.correlate_quest(
            self.quest(("FlagA", "FlagB")),
            flag_table({"FlagA": 1, "FlagB": 0}),
        )
        self.assertEqual(
            partial.progress.status,
            quests.QUEST_STATUS_IN_PROGRESS,
        )
        self.assertEqual(partial.progress.flag_summary, "1/2")

        completed = quests.correlate_quest(
            self.quest(("FlagA", "FlagB")),
            flag_table({"FlagA": 1, "FlagB": 1}),
        )
        self.assertEqual(
            completed.progress.status,
            quests.QUEST_STATUS_COMPLETED,
        )

    def test_status_marks_enabled_negative_and_positive_variants_unknown(
        self,
    ) -> None:
        state = quests.correlate_quest(
            self.quest(("DostalLetaky", "NedostalLetaky")),
            flag_table({"DostalLetaky": 1, "NedostalLetaky": 1}),
        )

        self.assertEqual(
            state.progress.status,
            quests.QUEST_STATUS_CONFLICTING,
        )


if __name__ == "__main__":
    unittest.main()
