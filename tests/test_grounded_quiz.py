from __future__ import annotations

import copy
import json
from pathlib import Path
import random
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "host"))
import dataset
from quiz_bank import make_question, resolve_review, validate_question
from textbook_parser.grounded_exercises import answer_variants, grammar_target


def row(sentence, lesson="013", meaning="我想吃寿司。"):
    return dict(sentence=sentence, lesson=lesson, meaning=meaning, label="动作愿望", pattern="たいです")


class GroundedQuizTests(unittest.TestCase):
    def test_tai_accepts_both_particles_without_touching_topic(self):
        source = "わたしはすしを食べたいです。"
        q = make_question(row(source), "fill_blank", random.Random(1))
        self.assertEqual(q["prompt"], "わたしはすし（　）食べたいです。")
        self.assertEqual(set(q["accepted_answers"]), {"を", "が"})
        self.assertEqual(q["options"][q["correct"] - 1], "を／が")
        self.assertEqual(answer_variants(source), {source, "わたしはすしが食べたいです。"})
        self.assertEqual(answer_variants("わたしが沖縄へ行きたいです。"), {"わたしが沖縄へ行きたいです。"})
        self.assertEqual(answer_variants("わたしが食べたいです。"), {"わたしが食べたいです。"})

    def test_destination_and_purpose_are_different_targets(self):
        q = make_question(row("沖縄へ行きたいです。"), "fill_blank", random.Random(1))
        self.assertEqual(q["accepted_answers"], ["へ", "に"])
        q = make_question(row("神戸へすしを食べに行きます。"), "fill_blank", random.Random(1))
        self.assertEqual(q["prompt"], "神戸へすしを食べ（　）行きます。")
        self.assertEqual(q["accepted_answers"], ["に"])

    def test_legacy_review_uses_recorded_source_not_a_replacement(self):
        wrong = "わたしにすしを食べたいです。"
        source = "わたしはすしを食べたいです。"
        item = dict(sentence=wrong, diagnosis="应为は")
        logs = [dict(sentence=wrong, diagnosis="应为は", source_sentence=source)]
        self.assertEqual(resolve_review(item, [row(source)], logs)["sentence"], source)
        self.assertIsNone(resolve_review(item, [row(source)], []))
        item["correct_sentence"] = "わたしにすしへ食べたいです。"
        self.assertIsNone(resolve_review(item, [row(source)], []))

    def test_every_lesson_has_repeatable_source_backed_complete_options(self):
        for n in range(1, 26):
            source = {r["sentence"] for r in dataset.load_lesson_sentences(str(n))}
            for mode in ("fill_blank", "correction", "translate_zh_to_ja", "review"):
                questions = dataset.generate_quiz_pack(str(n), mode)
                self.assertEqual(questions, dataset.generate_quiz_pack(str(n), mode))
                if mode in {"fill_blank", "translate_zh_to_ja"}:
                    self.assertTrue(questions, (n, mode))
                for q in questions:
                    validate_question(q)
                    self.assertIn(q["source_sentence"], source)
                    self.assertTrue(q["source_ref"])
                    if "（　）" not in q["prompt"]:
                        self.assertEqual(q["options"][q["correct"] - 1], q["answer_sentence"])
                        valid = answer_variants(q["answer_sentence"])
                        self.assertEqual(sum(o in valid for o in q["options"]), 1)

    def test_no_ambiguous_rewrites_of_invitation_or_permission(self):
        self.assertIsNone(make_question(row("いっしょに行きませんか。"), "correction", random.Random(1)))
        q = make_question(row("写真を撮ってもいいです。"), "correction", random.Random(1))
        self.assertNotIn("写真を撮ってはいけません。", q["options"])

    def test_stale_event_does_not_fall_back_to_question_index(self):
        q = make_question(row("わたしはすしを食べたいです。"), "fill_blank", random.Random(1))
        snapshot = {"quizzes_by_mode": {"fill_blank": [q]}}
        event = dict(lesson=13, quiz_mode="fill_blank", quiz_index=1)
        self.assertIsNone(dataset.resolve_quiz_event(event, snapshot))
        event["question_id"] = q["question_id"]
        self.assertEqual(dataset.resolve_quiz_event(event, snapshot), q)
        event["lesson"] = 6
        self.assertIsNone(dataset.resolve_quiz_event(event, snapshot))

    def test_record_and_review_cycle_in_temporary_profile(self):
        import app.student_profile as profile
        import app.review_manager as review
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(profile, "DATA_DIR", Path(tmp)), \
             patch.object(profile, "STUDENTS_DIR", Path(tmp) / "students"), \
             patch.object(dataset, "STUDENTS_DIR", Path(tmp) / "students"):
            q = make_question(row("わたしはすしを食べたいです。"), "fill_blank", random.Random(1))
            wrong = next(i for i in range(1, 4) if i != q["correct"])
            result = dataset.record_quiz_submission("test", "013", q, wrong)
            self.assertTrue(result["ok"], result)
            queue_path = Path(tmp) / "students/test/review_queue.json"
            queue = json.loads(queue_path.read_text())
            self.assertEqual(queue[0]["correct_sentence"], q["source_sentence"])
            self.assertEqual(queue[0]["question_id"], q["question_id"])
            qs = dataset.generate_quiz_pack("13", "review", student_id="test")
            rq = qs[0]
            self.assertEqual(rq["review_error_id"], queue[0]["error_id"])
            self.assertTrue(dataset.record_quiz_submission("test", "013", rq, rq["correct"])["ok"])
            self.assertEqual(len(json.loads(queue_path.read_text())), 1)
            self.assertEqual(json.loads(queue_path.read_text())[0]["correct_review_count"], 1)
            repeated = dataset.record_quiz_submission("test", "013", rq, rq["correct"])
            self.assertFalse(repeated["recorded"])
            self.assertEqual(json.loads(queue_path.read_text())[0]["correct_review_count"], 1)
            mastery = json.loads((queue_path.parent / "mastery.json").read_text())
            self.assertEqual(mastery[rq["target_label"]]["total_count"], 2)
            self.assertEqual(mastery[rq["target_label"]]["correct_count"], 1)
            self.assertEqual(mastery[rq["target_label"]]["error_count"], 1)
            self.assertFalse(dataset.record_quiz_submission("test", "006", q, wrong)["ok"])
            tampered = copy.deepcopy(q)
            tampered["correct"] = wrong
            self.assertFalse(dataset.record_quiz_submission("test", "013", tampered, wrong)["ok"])


if __name__ == "__main__":
    unittest.main()
