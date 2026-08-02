"""Title bucketing, and in particular the titles that must NOT be caught."""

import unittest

from bands import roles


class TestFamilies(unittest.TestCase):
    def test_software_engineer_variants_all_land_in_one_bucket(self):
        for title in ("Software Engineer", "SOFTWARE ENGINEER", "Sr. Software Engineer",
                      "Senior Software Engineer", "SOFTWARE ENGINEER II", "SDE II",
                      "Software Developer", "SOFTWARE DEVELOPMENT ENGINEER",
                      "Software Engineering", "SWE III", "Sw Engineer",
                      "Staff Software Engineer", "Principal Software Engineer",
                      "Software Architect", "SOFTWARE PROGRAMMER"):
            with self.subTest(title=title):
                self.assertEqual(roles.classify(title).family, "Software engineer")

    def test_NEGATIVE_CONTROL_lookalike_titles_stay_out(self):
        # Each of these contains a word the software clause looks for. Each is a different
        # job. If the rule ever loosens, this is what notices.
        cases = {
            "Software Sales Engineer": roles.UNMATCHED,
            "Sales Engineer, Software": roles.UNMATCHED,
            "Software Engineering Manager": "Engineering management",
            "Director of Software Engineering": "Engineering management",
            "Senior Manager, Software Development": "Engineering management",
            "Software QA Engineer": "QA / test engineer",
            "SDET II": "QA / test engineer",
            "Software Test Engineer": "QA / test engineer",
            "Software Quality Assurance Analyst": "QA / test engineer",
            "Hardware Engineer": roles.UNMATCHED,
            "Civil Engineer": roles.UNMATCHED,
            "Assistant Professor": roles.UNMATCHED,
            "Technical Program Manager": "Engineering management",
        }
        for title, want in cases.items():
            with self.subTest(title=title):
                self.assertEqual(roles.classify(title).family, want)

    def test_machine_learning_titles(self):
        for title in ("Machine Learning Engineer", "ML Engineer", "MLE",
                      "Senior Machine Learning Engineer", "Machine Learning Engineer II"):
            with self.subTest(title=title):
                self.assertEqual(roles.classify(title).family, "Machine learning engineer")
        self.assertEqual(roles.classify("Machine Learning Scientist").family,
                         "Applied / research scientist (ML, AI)")
        self.assertEqual(roles.classify("AI Research Scientist").family,
                         "Applied / research scientist (ML, AI)")

    def test_data_clauses_run_before_the_software_clause(self):
        # A documented merge, not an accident: DATA + ENGINEER wins over SOFTWARE.
        self.assertEqual(roles.classify("Big Data Software Engineer").family, "Data engineer")
        self.assertEqual(roles.classify("Data Scientist").family, "Data scientist")
        self.assertEqual(roles.classify("Data Analyst").family, "Data analyst")

    def test_infrastructure_families(self):
        for title, want in {
            "Site Reliability Engineer": "DevOps / SRE / platform engineer",
            "SRE": "DevOps / SRE / platform engineer",
            "DevOps Engineer": "DevOps / SRE / platform engineer",
            "Platform Engineer": "DevOps / SRE / platform engineer",
            "Cloud Engineer": "DevOps / SRE / platform engineer",
            "Security Engineer": "Security engineer",
            "Product Manager": "Product manager",
        }.items():
            with self.subTest(title=title):
                self.assertEqual(roles.classify(title).family, want)

    def test_unmatched_is_a_real_bucket_not_a_dumping_ground_for_everything(self):
        # Negative control for the classifier as a whole: if every clause were broken, this
        # would still pass by returning unmatched, so assert the opposite direction too.
        self.assertEqual(roles.classify("Chef de Partie").family, roles.UNMATCHED)
        self.assertNotEqual(roles.classify("Software Engineer").family, roles.UNMATCHED)


class TestLevels(unittest.TestCase):
    def test_levels(self):
        cases = {
            "Software Engineer": "Unstated",
            "Sr. Software Engineer": "Senior",
            "Senior Software Engineer": "Senior",
            "Staff Software Engineer": "Staff",
            "Senior Staff Software Engineer": "Staff",
            "Principal Software Engineer": "Principal",
            "Lead Software Engineer": "Lead",
            "Software Engineer II": "Level II",
            "SDE 3": "Level III",
            "Junior Software Engineer": "Junior/entry",
        }
        for title, want in cases.items():
            with self.subTest(title=title):
                self.assertEqual(roles.classify(title).level, want)

    def test_NEGATIVE_CONTROL_level_does_not_change_the_family(self):
        base = roles.classify("Software Engineer").family
        for prefix in ("Senior ", "Staff ", "Principal ", "Lead ", "Junior "):
            self.assertEqual(roles.classify(prefix + "Software Engineer").family, base)


if __name__ == "__main__":
    unittest.main()
