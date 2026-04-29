# Release

Notes to self for cutting a new release.

## Bump

SemVer. PATCH for fixes, MINOR for features, MAJOR for breaking changes (still on `0.x` so MINOR can break too).

Edit `pyproject.toml`:

```toml
version = "0.1.1"
```

`stmtparser --version` reads from package metadata, so no second place to update.

## Changelog

Move `[Unreleased]` items into a new `[0.1.1] — YYYY-MM-DD` section in `CHANGELOG.md`. Use `Added` / `Changed` / `Deprecated` / `Removed` / `Fixed` / `Security` (Keep a Changelog headings).

## Check

```bash
uv run ruff check
uv run ty check src/ tests/
uv run pytest
```

Don't tag if anything's red.

## Commit, tag, push

```bash
git add pyproject.toml CHANGELOG.md
git commit -m "release: v0.1.1"

git tag -a v0.1.1 -m "v0.1.1: <one-line summary>"

git push origin main
git push origin v0.1.1
```

## Release on GitHub

```bash
gh release create v0.1.1 --title "v0.1.1" --notes-from-tag
```

`--notes-from-tag` uses the annotated tag's message as the release body, so make the tag message worth reading.

## Smoke test

```bash
uv tool install --reinstall git+https://github.com/hongjiaherng/stmtparser.my@v0.1.1
stmtparser --version
```

If broken, don't move the tag. Cut the next patch.
