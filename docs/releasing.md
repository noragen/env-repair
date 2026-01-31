# Releasing env-repair

## PyPI (via GitHub Actions / Trusted Publishing)

1) Bump version in `pyproject.toml`.
2) Update `CHANGELOG.md`.
3) Create and push a tag:
   - `git tag vX.Y.Z`
   - `git push --tags`
4) The workflow `.github/workflows/release-pypi.yml` builds and publishes to PyPI.

PyPI setup (one-time):
- Create the project on PyPI (first manual upload or via the UI).
- Configure **Trusted Publishing** for this GitHub repo + workflow.

## conda-forge (recommended)

Publishing to conda-forge is done via a **feedstock** repo.

Typical flow:
1) Ensure the version is published on PyPI.
2) Create a PR to `conda-forge/staged-recipes` with a recipe using a PyPI source URL + sha256.
   In this repo, `conda.recipe/meta-forge.yaml` is the ready-to-copy starting point.
3) After merge, conda-forge will create `env-repair-feedstock` automatically.
4) Future releases are handled via version bump PRs to the feedstock (usually via `conda-forge-bot`).

## Local builds

PyPI artifacts:
- Clean build dirs first (important: a `build/` directory will shadow the PyPI `build` module on the next run):
  - `rmdir /s /q build 2>nul`
  - `rmdir /s /q dist 2>nul`
- Build:
  - `python -m pip install -U build`
  - `python -m build`
  - `python -m twine check dist\*`

conda recipe:
- `conda install -y conda-build`
- `conda build conda.recipe`

## anaconda.org (optional)

If you want to publish to your own Anaconda channel (not conda-forge), use the workflow:
`.github/workflows/release-anaconda.yml` (manual trigger).

One-time setup:
- Create an Anaconda token with upload permissions.
- Add it as a GitHub Actions secret: `ANACONDA_TOKEN`.

Tag-based release:
- Pushing a tag `vX.Y.Z` will also trigger `.github/workflows/release-anaconda.yml` and upload the built conda package to your channel.
- By default it uploads to the channel matching the GitHub repo owner (e.g. `noragen`). You can override via `workflow_dispatch` input `anaconda_user`.
