# Releasing env-repair

## 1. PyPI (GitHub Actions / Trusted Publishing)

1. Bump `[project].version` in `pyproject.toml` once, or run `python release.py`.
   Do not bump again if the intended release version is already set.
2. Synchronize local recipe versions before publication:
   `python tools\sync_versions.py`.
   No new source hash is available yet; do not submit the conda-forge recipe at this stage.
3. Finalize the versioned `CHANGELOG.md` entry and release date.
4. Commit and push the release changes, and wait for CI.
5. Create and push only the intended tag:
   `git tag vX.Y.Z`, then `git push origin vX.Y.Z`.
6. Wait for `.github/workflows/release-pypi.yml` to finish and confirm the version
   is available on PyPI. A published version cannot be overwritten.

One-time setup: configure PyPI Trusted Publishing for this repository and
`release-pypi.yml`.

## 2. conda-forge: update the existing feedstock

The package has already been accepted. Subsequent releases belong in
`conda-forge/env-repair-feedstock`, not in staged-recipes.

1. Check for an existing version-update PR in
   https://github.com/conda-forge/env-repair-feedstock/pulls.
   If the bot already opened one, review that PR rather than creating a duplicate.
2. For a manual update, use a fork/checkout of the current feedstock main branch.
   Update your fork from upstream before starting an update branch; keep any
   unrelated local work safe.
3. **After the version is published on PyPI**, run from the env-repair repository:

   ```cmd
   python tools\sync_versions.py --pypi-sdist --feedstock env-repair-feedstock
   ```

   This synchronizes the local recipe versions/hash and updates
   `env-repair-feedstock/recipe/recipe.yaml` in place. Only the context version,
   source SHA256, and (for a changed version) build number are changed.
   Feedstock-specific requirements, variants, maintainers and build/test settings
   are preserved. The build number resets to 0 for a new version; it is retained
   when synchronizing the same version.

   The checkout must already contain `recipe/recipe.yaml`. The command does not
   create a feedstock, select a branch, commit, push or open a PR.
   A PyPI download failure returns a nonzero exit code without updating recipes.
4. Review `git diff` inside the feedstock, commit the recipe update, push the
   branch to your fork and open a PR targeting `conda-forge/env-repair-feedstock:main`.
5. Wait for feedstock CI and merge the update PR. No new env-repair tag is needed.
6. After the post-merge build/upload, allow time for channel indexes and caches
   to update. The Anaconda package page can show a version before search sees it.
7. Commit the synchronized local recipe metadata in the env-repair repository too.

The SHA256 must match the published PyPI archive, not an independently rebuilt
local archive. `--sdist path\to\env_repair-X.Y.Z.tar.gz` is an offline alternative
only when that file is the exact published archive. `--build-sdist` is useful
for local testing, not for deriving a guaranteed PyPI checksum.

Do not use `release.py --sync` for this post-publication step: that command
bumps the version and retains the legacy staged-recipes flow. Use the explicit
feedstock command above instead.

## Legacy: staged-recipes (retained)

The original admission workflow is still supported; no checkout is deleted.
It is not the update path for env-repair now that a feedstock exists.

```cmd
python tools\sync_versions.py --pypi-sdist --staged-recipes staged-recipes
```

This copies the local template to
`staged-recipes/recipes/env-repair/recipe.yaml`. Unlike `--feedstock`, it replaces
the target recipe with the template. The two target options are mutually exclusive.

## Local builds

PyPI artifacts:

```cmd
python -m pip install -U build twine
python -m build
python -m twine check dist\*
```

Review stale build outputs before packaging; never upload old distributions
accidentally. If a local `build/` directory shadows the PyPI `build` module,
inspect and clean that directory before retrying.

Local conda-build recipe:

```cmd
conda install -n base -c conda-forge conda-build
conda run --no-capture-output -n base conda-build conda.recipe
```

The feedstock uses the separate v1 recipe and its own rattler-build CI.

## Own anaconda.org channel (optional, not conda-forge)

`.github/workflows/release-anaconda.yml` builds the local recipe and publishes to
your own channel. Set the `ANACONDA_TOKEN` GitHub Actions secret once.

- A `vX.Y.Z` tag triggers both this workflow and PyPI publishing independently.
- The default channel owner is the GitHub repository owner.
- A manual run accepts `anaconda_user` to override the channel owner.
- For a workflow-only fix after a release, push the fix and manually run the
  Anaconda workflow on the updated branch. Re-running an old tag run still uses
  its old workflow definition. Do not move the release tag or republish to PyPI.
