# Working together on Sturfrymacro

How we make changes without treading on each other or on the data bot. Every change
goes through a **pull request (PR)**: you do the work on a branch, the other person
looks it over, and only then does it go into `main`.

## Why bother with pull requests?

- `main` is what the bot refreshes every weekday morning and what anyone reading
  our charts sees. A broken config there means a red run and stale charts.
- A second pair of eyes catches the economics mistakes code can't: the wrong CDID,
  a non-seasonally-adjusted series charted as if it were adjusted, a rebase in the
  wrong quarter.
- Each PR becomes a record of *why* a chart or series changed.

## One-off setup

1. Accept the GitHub invitation (David) and clone the repo:
   ```bash
   git clone https://github.com/Emily-coding/Sturfrymacro.git
   cd Sturfrymacro
   python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
   ```
   Prefer buttons to commands? [GitHub Desktop](https://desktop.github.com) does
   everything below (clone, branch, commit, push, open a PR). VS Code's Source
   Control panel does too.
2. Check it works: `.venv/bin/python builders/gdp/build.py` should print `ok` for
   each chart.

## Making a change, step by step

**1. Start from the latest `main`.** The bot commits data most mornings, so always
pull first.
```bash
git switch main
git pull
```

**2. Make a branch for one change.** Name it after what you're doing.
```bash
git switch -c add-cpi-energy
```
One chart, one fix or one new source per branch. Small PRs get reviewed the same
day; big ones sit around.

**3. Make the change and check it locally.** Usually this is an edit to
`builders/<area>/config.yaml`. Then run that area's builder and look at the PNG
in `img/<area>/`:
```bash
.venv/bin/python builders/inflation/build.py
```

**4. Commit and push.** Write the commit message as a sentence saying what changed
and why.
```bash
git add builders/inflation/config.yaml
git commit -m "Inflation: add CPI energy, the main driver of the 2022 spike"
git push -u origin add-cpi-energy
```
Only add the files you meant to change (see "Data files" below).

**5. Open the PR.** GitHub shows a "Compare & pull request" button after you push
(or run `gh pr create`). Fill in the checklist that appears, paste the new chart
PNG into the description (drag and drop works), and request the other person as
reviewer.

**6. Review.** The reviewer reads the change and the chart and either approves,
comments, or asks for changes. Fix anything on the same branch, push again, and
the PR updates itself.

**7. Merge.** Once approved, the *author* clicks **Squash and merge** (this turns
the branch's commits into one tidy commit on `main`), then **Delete branch**.
Locally:
```bash
git switch main
git pull
git branch -d add-cpi-energy
```

## Reviewing a PR: what to check

You're reviewing the economics first and the code second.

- **Right series?** Open the ONS (or other) page for each code and check the title
  matches what the chart claims.
- **Seasonal adjustment** stated in the chart `note`, and consistent: don't mix
  adjusted and unadjusted series on one chart without saying so.
- **Units and transformations**: level vs growth, % change on a quarter vs a year,
  the rebase date, real vs nominal.
- **Does the chart look right?** Compare the latest value with the official release.
- **The note** says anything a reader needs (breaks in the series, "official
  statistics in development" status, revisions).
- **Code changes** (anything outside a `config.yaml`): comments explain what each
  step does in plain English. If you can't follow it, say so; that's a fault in
  the code, not in you.

Leave comments on the exact line in the "Files changed" tab. Approve with
**Review changes → Approve**.

## Data files

`data/` and `img/` are written by the weekday bot. In a PR:

- Don't commit regenerated data for areas you didn't touch. Running a builder
  locally rewrites them; undo that with `git restore data img` before committing.
- For a new or changed chart, it's fine to commit that chart's own JSON and PNG so
  the reviewer can see it. The bot will overwrite it on the next refresh anyway.
- If GitHub says the branch has **conflicts** in `data/` or `img/`, it's almost
  always the bot. Pull `main` into your branch and keep `main`'s version:
  ```bash
  git pull origin main
  git checkout --theirs data img && git add data img && git commit
  ```

## When something goes red

A red run on the Actions tab means a source failed. The chart still publishes from
the last good value and is marked `stale`. Run `.venv/bin/python
scripts/check_sources.py` to see which source is down. If it's ours to fix, open an
issue and fix it in a PR like any other change.

## House conventions

- Every chart has a `source` and a `note`; the note says whether the data is
  seasonally adjusted.
- When you add a series code, note the date you checked it against the live source
  at the top of the config (e.g. `verified against live ONS 2026-11-10`).
- Charts follow the house style in `assets/colour-scheme.md`; `ukmacro/charts.py`
  applies it automatically.
- Anything we *estimate* (nowcasts, decompositions) gets a short method note next to
  the code before the code is written, so the method can be reviewed on its own.
- To-dos and ideas go in GitHub Issues; bigger plans in `NEXT.md`.
