# London Paralegal Jobs

An automated tracker for **every paralegal role within 10 miles of London**, in every practice area. A GitHub
Actions workflow runs every 3 hours. It pulls jobs from the [Reed](https://www.reed.co.uk/developers) and
[Adzuna](https://developer.adzuna.com/) APIs, deduplicates them, scores them and publishes a mobile-friendly
dashboard to GitHub Pages.

## What it does

- **Fetches everything.** It searches both APIs for `paralegal` within 10 miles (16 km for Adzuna) of London. No job
  is dropped because of keywords or score. If one API fails, the error is logged and the run continues with the
  other one. If both fail, the run exits with an error and leaves the existing data untouched.
- **Deduplicates** across sources, using a normalised title and employer, and against earlier runs, using
  `data/jobs.json`. Each job records the time it was `first_seen`. Jobs that are more than 30 days old (by posting
  date or first-seen date) and Reed jobs past their expiry date are dropped.
- **Scores each job from 1 to 5** as a preference signal, never as a filter. Each job also gets a one-line reason:
  - Every job starts at **3**.
  - **+1** for a political or public-sector angle (public law, public inquiry, regulatory, government, parliament,
    local authority, public sector, policy, judicial review, housing, legal aid, human rights). It's **+2** if two
    or more of these appear. These jobs also get a **Policy focus** badge.
  - **+1** for career-friendly signals: future trainee, SQE, graduate, part-time, temporary or hybrid.
  - **−1** if it asks for 2+ years of experience.
- **Classifies experience level** from each job's title and description, as one of three values:
  - **Graduate / no experience:** graduate, entry level, junior, trainee, future trainee, "no experience necessary",
    "no prior experience", law or recent graduate, "LLB/LLM/SQE students welcome", or a requirement of under 1 year.
  - **Experience required:** any stated requirement of 1+ years (or 12+ months, or PQE), "proven experience",
    "an experienced … paralegal", "previous experience as a …", or a "Senior Paralegal" title.
  - **Not stated:** neither of the above.

  A stated requirement always wins, so "law graduate preferred, 2+ years' experience" counts as *Experience
  required*. Colleagues don't count: "supervise trainees" and "working alongside experienced paralegals" are ignored.
  Every job is re-classified on every run.
- **Publishes `docs/index.html`.** It shows all jobs by default, with a "New since last run" section at the top.
  Each job shows its salary, location, contract type, practice area, a direct link and when the page was last
  updated. You can sort by **Newest** (the default) or **Best fit**. Optional filters cover *Policy focus only*,
  *Graduate / no experience only*, contract type and minimum score; all of them are off by default.

## Setup

### 1. Get API keys (both free)

- **Reed:** register at <https://www.reed.co.uk/developers/jobseeker> to get an API key.
- **Adzuna:** sign up at <https://developer.adzuna.com/> to get an *App ID* and an *App Key*.

### 2. Add them as repository secrets

Go to **Settings → Secrets and variables → Actions → New repository secret** and add:

| Secret name      | Value              |
| ---------------- | ------------------ |
| `REED_API_KEY`   | your Reed API key  |
| `ADZUNA_APP_ID`  | your Adzuna App ID |
| `ADZUNA_APP_KEY` | your Adzuna App Key |

The keys are only ever read from environment variables, so never commit them to the repo. You can run the tracker
with just one source configured; the other will show as an error in the dashboard footer.

### 3. Enable GitHub Pages

1. Make sure this code is on the **`main`** branch. Scheduled workflows only run on the default branch.
2. Go to **Settings → Pages**.
3. Under **Build and deployment**, set **Source** to **Deploy from a branch**.
4. Choose branch **`main`** and folder **`/docs`**, then click **Save**.
5. After a minute or so, the site is live at `https://<your-username>.github.io/<repo-name>/`.

### 4. Trigger the first run

1. Open the **Actions** tab. If prompted, click **I understand my workflows, go ahead and enable them**.
2. Select **Update paralegal jobs** in the left sidebar.
3. Click **Run workflow**, choose `main`, then click **Run workflow** again.
4. When it finishes, the workflow commits `data/jobs.json` and `docs/index.html`, and Pages republishes the
   dashboard.

After that, the workflow runs automatically every 3 hours (at minute 17, UTC).

Each run checks out the latest `main`. If `main` moves during a run (for example, you merge a PR), the push would
clash with the regenerated files. In that case the commit step throws its commit away, resets to the new `main` and
re-runs the update on top of it, retrying up to 3 times. It never commits merge-conflict markers. If
`data/jobs.json` is ever corrupt, the run stops rather than starting from an empty list.

If the commit step fails with a permissions error, go to **Settings → Actions → General → Workflow permissions**
and select **Read and write permissions**.

## API usage (free-tier friendly)

Each run makes at most:

| Source | Calls per run | Per day (8 runs) | Notes |
| ------ | ------------- | ---------------- | ----- |
| Adzuna | ≤ 5 searches (50 results each) | ≤ 40 | Free tier is 250/day, 1,000/week and 2,500/month. Calls are 3 s apart. |
| Reed   | ≤ 10 searches (100 results each) + ≤ 40 job-detail lookups | typically ≈ 100 | Detail lookups only happen for jobs not seen before. They supply the contract type and the full description. |

You can tune these limits with the constants at the top of `scripts/update_jobs.py`.

## Running locally

```bash
export REED_API_KEY=...  ADZUNA_APP_ID=...  ADZUNA_APP_KEY=...
python3 scripts/update_jobs.py              # updates data/jobs.json and docs/index.html
python3 -m unittest discover -s tests       # offline tests
```

The tracker needs Python 3.10+ and has no third-party dependencies.

## Files

- `scripts/update_jobs.py`: fetching, deduplication, pruning, classification and scoring
- `scripts/render_dashboard.py`: the static dashboard template
- `data/jobs.json`: the job store (committed by the workflow)
- `docs/index.html`: the published dashboard (committed by the workflow)
- `scripts/commit_and_push.sh`: the commit step, which rebuilds on the latest `main` if a push clashes
- `.github/workflows/update-jobs.yml`: the schedule
