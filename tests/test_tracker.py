"""Offline tests: python -m unittest discover -s tests"""

import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import update_jobs as uj  # noqa: E402


def job(**kw):
    base = {"id": "reed:1", "source": "Reed", "title": "Paralegal", "company": "Acme LLP", "location": "London",
            "salary_min": 28000, "salary_max": 32000, "salary_estimated": False, "contract_raw": "", "hours_raw": "",
            "url": "https://example.com/1", "posted": "2026-10-01", "expires": None, "description": "", "category": ""}
    base.update(kw)
    return base


class ScoringTests(unittest.TestCase):
    def score(self, **kw):
        return uj.enrich(job(**kw))

    def test_neutral_job_scores_three(self):
        result = self.score(title="Corporate Paralegal", description="Join our busy team.")
        self.assertEqual(result["score"], 3)
        self.assertFalse(result["policy_focus"])
        self.assertIn("Base 3", result["score_reason"])

    def test_public_sector_and_career_signals(self):
        result = self.score(title="Public Law Paralegal",
                            description="Judicial review work for a local authority. Hybrid working. SQE support.")
        self.assertEqual(result["score"], 5)
        self.assertTrue(result["policy_focus"])
        self.assertEqual(result["practice_area"], "Public Law")

    def test_experience_penalty(self):
        result = self.score(title="Paralegal", description="You will have at least 2 years of paralegal experience.")
        self.assertEqual(result["score"], 2)
        self.assertIn("−1", result["score_reason"])

    def test_score_floor_and_ceiling(self):
        low = self.score(description="Minimum 3 years experience required.")
        self.assertGreaterEqual(low["score"], 1)
        high = self.score(title="Housing Paralegal - Legal Aid, Human Rights",
                          description="Public law, judicial review. Part-time, hybrid, future trainee.")
        self.assertEqual(high["score"], 5)

    def test_experience_parsing(self):
        self.assertIsNone(uj.requires_experience("1-2 years experience preferred"))
        self.assertIsNone(uj.requires_experience("a 2 year training contract"))
        self.assertTrue(uj.requires_experience("2+ years' paralegal experience"))
        self.assertTrue(uj.requires_experience("minimum of two years in a similar role"))
        self.assertTrue(uj.requires_experience("3-5 years PQE"))

    def test_hr_boilerplate_is_not_policy(self):
        result = self.score(description="We follow an equal opportunities policy and privacy policy.")
        self.assertFalse(result["policy_focus"])

    def test_contract_and_salary(self):
        result = self.score(title="Temporary Paralegal", salary_min=18, salary_max=20)
        self.assertEqual(result["contract_type"], "Temporary")
        self.assertEqual(result["salary"], "£18 – £20/hr")
        self.assertEqual(self.score(contract_raw="Permanent")["contract_type"], "Permanent")
        self.assertEqual(self.score(salary_min=None, salary_max=None)["salary"], "Not specified")


class ExperienceTests(unittest.TestCase):
    def level(self, description="", title="Paralegal"):
        return uj.classify_experience(job(title=title, description=description))[0]

    def assertGraduate(self, description="", title="Paralegal"):
        self.assertEqual(self.level(description, title), uj.EXPERIENCE_GRADUATE, f"{title!r} / {description!r}")

    def assertRequired(self, description="", title="Paralegal"):
        self.assertEqual(self.level(description, title), uj.EXPERIENCE_REQUIRED, f"{title!r} / {description!r}")

    def assertNotStated(self, description="", title="Paralegal"):
        self.assertEqual(self.level(description, title), uj.EXPERIENCE_NOT_STATED, f"{title!r} / {description!r}")

    def test_graduate_signals(self):
        self.assertGraduate(title="Graduate Paralegal")
        self.assertGraduate(title="Junior Paralegal")
        self.assertGraduate(title="Trainee Paralegal")
        self.assertGraduate(title="Entry-Level Legal Assistant")
        self.assertGraduate(title="Disputes Paralegal - Future Trainee")
        self.assertGraduate("No experience necessary, full training given.")
        self.assertGraduate("No prior experience needed.")
        self.assertGraduate("Ideal for a law graduate or recent graduate.")
        self.assertGraduate("LLB students welcome. SQE students welcome.")
        self.assertGraduate("Under 1 year of experience is fine.")
        self.assertGraduate("You will have 6+ months experience as a conveyancing paralegal.")
        self.assertGraduate("An excellent opportunity for a law graduate or experienced paralegal.")

    def test_experience_required_signals(self):
        self.assertRequired("You will have 1 year of paralegal experience.")
        self.assertRequired("Minimum 12 months' experience in conveyancing.")
        self.assertRequired("Minimum of 3 years' PQE.")
        self.assertRequired("Proven experience as a paralegal in a busy firm.")
        self.assertRequired("We are seeking an experienced paralegal.")
        self.assertRequired("A law firm is looking to add an experienced Family Paralegal to its team.")
        self.assertRequired("Requirements: previous experience as a Litigation Paralegal.")
        self.assertRequired(title="Senior Paralegal")

    def test_requirement_beats_graduate_wording(self):
        self.assertRequired("Law graduate preferred, 2+ years' experience.")
        self.assertRequired("Graduate Paralegal role. Minimum 1 year's experience required.")

    def test_colleagues_are_not_graduate_signals(self):
        self.assertNotStated("You will supervise trainees and junior staff.")
        self.assertNotStated("Working closely with trainee solicitors and partners.")
        self.assertNotStated("You will manage graduates on the team.")

    def test_soft_or_unrelated_wording_is_not_a_requirement(self):
        self.assertNotStated("Working alongside experienced paralegals and partners.")
        self.assertNotStated("A great chance to gain hands-on experience within a busy team.")
        self.assertNotStated("Previous experience in property would be advantageous.")
        self.assertNotStated("This is a 12 month fixed term contract.")
        self.assertNotStated("The firm has grown for over 170 years.")
        self.assertNotStated("Salary dependent on experience.")

    def test_enrich_reclassifies_existing_jobs(self):
        stored = job(description="Minimum 2 years' paralegal experience.")
        stored.pop("experience_level", None)
        self.assertEqual(uj.enrich(stored)["experience_level"], uj.EXPERIENCE_REQUIRED)
        stored["experience_level"] = "stale value"
        self.assertEqual(uj.enrich(stored)["experience_level"], uj.EXPERIENCE_REQUIRED)


class MergeTests(unittest.TestCase):
    def test_dedup_across_sources_and_runs(self):
        reed = job(id="reed:1", title="Litigation Paralegal", company="Smith & Co Solicitors LLP")
        adz = job(id="adzuna:9", source="Adzuna", title="Litigation Paralegal", company="Smith Co Ltd",
                  url="https://adzuna.example/9", description="Longer description text from Adzuna")
        jobs, new = uj.merge([], [reed, adz], "2026-10-01T09:00:00Z")
        self.assertEqual((len(jobs), new), (1, 1))
        self.assertEqual(jobs[0]["sources"], ["Reed", "Adzuna"])
        self.assertIn("Adzuna", jobs[0]["links"])

        again = job(id="adzuna:9", source="Adzuna", title="Litigation Paralegal", company="Smith Co Ltd")
        jobs, new = uj.merge(jobs, [again], "2026-10-01T12:00:00Z")
        self.assertEqual((len(jobs), new), (1, 0))
        self.assertEqual(jobs[0]["first_seen"], "2026-10-01T09:00:00Z")
        self.assertEqual(jobs[0]["last_seen"], "2026-10-01T12:00:00Z")

    def test_prune_old_and_expired(self):
        jobs = [
            dict(job(id="a", posted="2026-08-20"), first_seen="2026-09-25T00:00:00Z"),
            dict(job(id="b", posted=None), first_seen="2026-08-01T00:00:00Z"),
            dict(job(id="c", posted="2026-09-30", expires="2026-10-01"), first_seen="2026-09-30T00:00:00Z"),
            dict(job(id="d", posted="2026-09-30"), first_seen="2026-09-30T00:00:00Z"),
        ]
        kept = uj.prune(jobs, date(2026, 10, 3))
        self.assertEqual([j["id"] for j in kept], ["d"])


class EndToEndTests(unittest.TestCase):
    def test_one_source_failing_still_updates(self):
        with tempfile.TemporaryDirectory() as tmp:
            data, page = Path(tmp) / "jobs.json", Path(tmp) / "index.html"
            fetched = [job(id="adzuna:1", source="Adzuna", posted=date.today().isoformat(),
                           title="Paralegal </script><b>x</b>", url="javascript:alert(1)")]
            with mock.patch.object(uj, "DATA_FILE", data), mock.patch.object(uj, "PAGE_FILE", page), \
                    mock.patch.object(uj, "ROOT", Path(tmp)), \
                    mock.patch.object(uj, "fetch_reed", side_effect=RuntimeError("HTTP 401 Unauthorized")), \
                    mock.patch.object(uj, "fetch_adzuna", return_value=fetched):
                self.assertEqual(uj.main(), 0)
            store = json.loads(data.read_text())
            self.assertEqual(len(store["jobs"]), 1)
            self.assertTrue(store["meta"]["sources"]["Reed"].startswith("error"))
            html = page.read_text()
            self.assertNotIn("</script><b>", html)
            self.assertIn("safeUrl", html)

    def test_run_classifies_jobs_already_in_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            data, page = Path(tmp) / "jobs.json", Path(tmp) / "index.html"
            old = dict(job(id="reed:7", title="Junior Paralegal", posted=date.today().isoformat()),
                       ids=["reed:7"], sources=["Reed"], links={}, first_seen=date.today().isoformat() + "T00:00:00Z")
            data.write_text(json.dumps({"meta": {}, "jobs": [old]}))
            with mock.patch.object(uj, "DATA_FILE", data), mock.patch.object(uj, "PAGE_FILE", page), \
                    mock.patch.object(uj, "ROOT", Path(tmp)), \
                    mock.patch.object(uj, "fetch_reed", return_value=[]), \
                    mock.patch.object(uj, "fetch_adzuna", return_value=[]):
                self.assertEqual(uj.main(), 0)
            stored = json.loads(data.read_text())["jobs"][0]
            self.assertEqual(stored["experience_level"], uj.EXPERIENCE_GRADUATE)
            self.assertIn('"experience_level":"Graduate / no experience"', page.read_text())

    def test_conflicted_store_stops_the_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "jobs.json"
            data.write_text('{\n<<<<<<< HEAD\n "jobs": []\n=======\n "jobs": [1]\n>>>>>>> abc\n}\n')
            with mock.patch.object(uj, "DATA_FILE", data), \
                    mock.patch.object(uj, "fetch_reed", return_value=[]), \
                    mock.patch.object(uj, "fetch_adzuna", return_value=[]):
                with self.assertRaises(json.JSONDecodeError):
                    uj.main()
            self.assertIn("<<<<<<<", data.read_text())  # left for a human, not overwritten

    def test_all_sources_failing_leaves_files_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "jobs.json"
            with mock.patch.object(uj, "DATA_FILE", data), mock.patch.object(uj, "PAGE_FILE", Path(tmp) / "i.html"), \
                    mock.patch.object(uj, "fetch_reed", side_effect=RuntimeError("down")), \
                    mock.patch.object(uj, "fetch_adzuna", side_effect=RuntimeError("down")):
                self.assertEqual(uj.main(), 1)
            self.assertFalse(data.exists())


if __name__ == "__main__":
    unittest.main()
