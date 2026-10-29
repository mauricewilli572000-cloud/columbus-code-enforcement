# Run Columbus Code Enforcement on GitHub (no PowerShell after step 1)

## 1. One-time: make the small owner file (needs PowerShell once)
In the project folder, with your Parcel.csv in it:

    python make_parcel_lookup.py .\Parcel.csv

This creates `data\parcel_lookup.csv.gz` (should be roughly 15-25 MB).

## 2. Create the repo (github.com)
New repository -> name it e.g. `columbus-code-enforcement` -> Private is fine -> Create.

## 3. Upload the files (Add file -> Upload files, drag in, Commit)
- Everything in the project folder EXCEPT the `data` folder and `__pycache__`
  (include `docs`, `tests`, `.github` and all the .py files).
  If `.github` doesn't upload: Add file -> Create new file, type the name
  `.github/workflows/code_enforcement.yml`, and paste in the contents of that file.
- Then upload these into the repo's `data/` folder (type `data/` in the file-name box when creating,
  or drag them in a second upload): `records.json`, `pending_records.json`, `state.json`,
  `parcel_lookup.csv.gz`. This keeps the leads you already have.

## 4. Turn on the dashboard
Settings -> Pages -> Source: "Deploy from a branch" -> Branch `main`, folder `/docs` -> Save.
Dashboard address: `https://<your-username>.github.io/<repo-name>/code_enforcement.html`

## 5. Allow the workflow to save results
Settings -> Actions -> General -> Workflow permissions -> "Read and write permissions" -> Save.

## 6. First run
Actions tab -> "Columbus Code Enforcement (nightly)" -> Run workflow. Leave days blank.
When it finishes (green check) open the dashboard. After that it runs by itself every night;
Sundays re-check the last 60 days so closed cases drop off.

## If something is wrong
- Red X on the run: open it, click the failed step, and send me the last 30 lines.
- Portal times out / refuses: the city site may block GitHub's servers; use the Windows scheduled task instead.
- "no owner names": `data/parcel_lookup.csv.gz` is missing from the repo.
- Refresh the owner file whenever you get a newer Parcel.csv (repeat step 1, upload to `data/`).
