# LSP release workflow

A reusable GitHub Actions workflow that releases an LSP package for Sublime Text. On every push to the default branch it
reads the [conventional commits](https://www.conventionalcommits.org/) since the latest release and, if any of them
change what users get, creates the next `x.y.z` tag and a GitHub release with notes. Package Control picks up the new
tag on its own.

## Usage

Add this workflow to the package repository:

```yaml
# .github/workflows/release.yml
name: Release

on:
  push:
    branches:
      - main
  workflow_dispatch:
    inputs:
      bump:
        description: auto releases only if the commits need it. patch, minor and major always release.
        type: choice
        options: [auto, patch, minor, major]
        default: auto
      dry-run:
        description: Only report the release that would be made.
        type: boolean
        default: false

permissions:
  contents: write

jobs:
  release:
    uses: sublimelsp/lsp_release_workflow/.github/workflows/release.yml@v1
    with:
      bump: ${{ inputs.bump || 'auto' }}
      dry-run: ${{ inputs.dry-run == true }}
```

Use the repository's default branch under `branches`. The workflow needs `contents: write` to push the tag and create
the release.

The repository needs at least one `x.y.z` (or `vx.y.z`) tag to continue from. New tags keep the `v` prefix of the latest
one.

## Version

The commits since the latest tag that is reachable from the pushed commit decide the release:

| Commits | Release |
|---|---|
| `feat` | minor |
| `fix`, `perf`, `revert` | patch |
| breaking change (`feat!:`, or a `BREAKING CHANGE:` footer) | minor |
| only other types (`chore`, `ci`, `docs`, `refactor`, `style`, `test`, `build`) | none |
| not conventional, such as `Update dependency x to v2` | none |

Renovate uses `fix(deps)` for dependencies and `chore(deps)` for GitHub Actions and development dependencies, so
server updates are released and action updates aren't. Updates tracked by a custom manager use `chore(deps)` unless the
repository or the [`automerge-server`](https://github.com/sublimelsp/renovate-preset) preset changes it.

Breaking changes only bump the minor version, because a new major version of a package is better decided by a
maintainer. Release it by running the workflow manually with `bump: major`.

Merge commits are ignored.

### Choosing the version

- **Upgrade message:** add the message to `messages.json` for the version you want, for example
  `"1.6.0": "messages/1.6.0.md"`. If the push adds an entry for a version newer than the latest release, that version
  is released, even if no commit needs a release, and the message is also used at the top of the release notes. Only
  entries added since the latest release count, so a message for a release that was planned but never made doesn't
  set the version.
- **Manually:** run the workflow from the Actions tab with `bump` set to `patch`, `minor` or `major`. It releases even
  if no commit needs a release.

### Not releasing

Add `[skip release]` to the message of the pushed commit, for example to merge several pull requests before releasing
them together. The next push releases everything since the latest release.

Use `dry-run` to see what would be released. The run summary shows the version, the reason and the release notes.

## Release notes

The notes list the `feat`, `fix`, `perf` and `revert` commits by type, breaking changes first, with a link to the full
changelog. Commits pushed directly are included as well as pull requests. If the release has an upgrade message in
`messages.json`, it comes first.

## Inputs

| Input | Default | Description |
|---|---|---|
| `bump` | `auto` | `auto`, `patch`, `minor` or `major`. See [Version](#version). |
| `dry-run` | `false` | Only report the release that would be made. |
| `messages-file` | `messages.json` | Package Control's `messages.json`, relative to the repository root. |

The `tag` output is the tag of the new release, or empty.

## Notes

- Releases are only made from the default branch. A dry run works on any branch.
- Pushes in quick succession are released one at a time. If a third push arrives while one is waiting, the waiting run
  is cancelled, and the third covers its commits.
- Releases are created with the workflow's `GITHUB_TOKEN`, so they don't trigger other workflows.
- The workflow doesn't wait for other checks. Require them in a ruleset so that pull requests can't be merged while
  they fail.

## Development

```sh
uv sync
uv run ruff check
uv run ruff format --check
uv run pyright
uv run python -B -m unittest discover -s tests
```

## Releases

Callers use the `v1` tag. After a change, move it to the new commit:

```sh
git tag -f v1 && git push -f origin v1
```

The reusable workflow checks out the script from its own commit, so the workflow and the script always match.
