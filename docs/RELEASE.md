# Release checklist

1. **Update the version** (single source of truth: `src/hstool/__init__.py`)

   ```powershell
   .\build.ps1 -Version 1.0.1
   ```

   The script rewrites `__version__` and stamps the exe's Windows version resource.

2. **Update `CHANGELOG.md`** - move entries from `[Unreleased]` into a new
   `## [x.y.z] - YYYY-MM-DD` section.

3. **Build and test**

   ```powershell
   .venv\Scripts\python tools\run_tests.py
   .\build.ps1
   ```

   `build.ps1` already runs the unit tests and then executes
   `dist\HandwrittenSignatureTool.exe --selftest`, which must print
   `all styles rendered`.

4. **Smoke test on a clean machine (or a VM)**
   - copy `dist\release\HandwrittenSignatureTool-<ver>-portable.zip` to the target box,
   - extract and run, confirm the window can be maximized/minimized,
   - render a Chinese and an English signature, save a PNG, insert it in Word,
   - check `logs\hstool.log` exists and no `crash.log` was written.

5. **Commit and tag**

   ```powershell
   git add -A
   git commit -m "release: v1.0.1"
   git tag -a v1.0.1 -m "v1.0.1"
   git push origin main --tags
   ```

6. **Publish the GitHub release**

   ```powershell
   gh release create v1.0.1 dist\release\HandwrittenSignatureTool-1.0.1-portable.zip `
       --title "v1.0.1" --notes-file RELEASE_NOTES.md
   ```

7. **Verify** the badge in `README.md`, the download link and the CHANGELOG links.

## Version rules (semver)

| change | bump |
| --- | --- |
| new font / new option / new feature | minor (`1.0.0` -> `1.1.0`) |
| bug fix with no UI change | patch (`1.0.0` -> `1.0.1`) |
| incompatible change (e.g. settings format) | major (`1.0.0` -> `2.0.0`) |

## What must never be committed

* `assets/bundle/*.bin` - subsets of proprietary Windows fonts, packed locally only.
* `assets/fonts/custom/`, `fonts/`, any `*.ttf/*.otf/*.ttc` - user supplied fonts
  (Mao Zedong style fonts etc. are licensed, never redistribute).
* `settings.json`, `logs/` - user data.
* `dist/`, `build/`, `.venv/`.
