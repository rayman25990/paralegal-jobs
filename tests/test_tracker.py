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
