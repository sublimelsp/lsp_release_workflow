"""
Work out whether a package needs a release, and its version and release notes.

Runs in the package checkout on the GitHub runner. Reads the conventional commits since the latest x.y.z tag that is
reachable from the given commit and, if they need a release, writes `version`, `tag`, `previous-tag` and `notes-file`
to $GITHUB_OUTPUT. Writes nothing to $GITHUB_OUTPUT when there is nothing to release.

Usage: next_version.py <commit> <bump: auto, patch, minor or major> <messages.json path> <notes file>
"""

from __future__ import annotations

from dataclasses import dataclass
from operator import itemgetter
from pathlib import Path
from pathlib import PurePosixPath
import json
import os
import re
import subprocess
import sys

BUMPS = ('patch', 'minor', 'major')
TAG_RE = re.compile(r'^(?P<prefix>v?)(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)$')
# type(scope)!: description
SUBJECT_RE = re.compile(r'^(?P<type>[a-z]+)(?:\([^)]*\))?(?P<breaking>!)?: \S', re.IGNORECASE)
BREAKING_FOOTER_RE = re.compile(r'^BREAKING[ -]CHANGE: ', re.MULTILINE)
SKIP_RELEASE = '[skip release]'
# Commit types that change what users get, and the release they need. Other types (chore, ci, docs, refactor, style,
# test, build) don't need a release on their own.
TYPE_BUMPS = {
    'feat': 'minor',
    'fix': 'patch',
    'perf': 'patch',
    'revert': 'patch',
}
NOTES_SECTIONS = (('feat', 'Features'), ('fix', 'Fixes'), ('perf', 'Performance'), ('revert', 'Reverts'))

Version = tuple[int, int, int]


@dataclass
class Commit:
    sha: str
    message: str

    @property
    def subject(self) -> str:
        return self.message.split('\n', 1)[0].strip()

    @property
    def type(self) -> str | None:
        match = SUBJECT_RE.match(self.subject)
        return match.group('type').lower() if match else None

    @property
    def breaking(self) -> bool:
        match = SUBJECT_RE.match(self.subject)
        return bool(match and match.group('breaking')) or bool(BREAKING_FOOTER_RE.search(self.message))

    @property
    def bump(self) -> str | None:
        # Breaking changes only bump the minor version: a major version of a package is a decision for a maintainer,
        # who can release one with the `major` bump input.
        if self.breaking:
            return 'minor'
        return TYPE_BUMPS.get(self.type or '')


@dataclass
class Decision:
    reason: str
    version: Version | None = None
    tag: str | None = None


def parse_tag(tag: str) -> tuple[str, Version] | None:
    match = TAG_RE.match(tag)
    if not match:
        return None
    return match.group('prefix'), (int(match.group('major')), int(match.group('minor')), int(match.group('patch')))


def format_version(version: Version) -> str:
    return '.'.join(map(str, version))


def latest_tag(tags: list[str]) -> tuple[str, str, Version] | None:
    """Return the tag with the highest x.y.z version, with its prefix and version. Other tags are ignored."""
    parsed = [(tag, *result) for tag in tags if (result := parse_tag(tag))]
    return max(parsed, key=itemgetter(2)) if parsed else None


def bump_version(version: Version, bump: str) -> Version:
    major, minor, patch = version
    if bump == 'major':
        return (major + 1, 0, 0)
    if bump == 'minor':
        return (major, minor + 1, 0)
    return (major, minor, patch + 1)


def highest_bump(commits: list[Commit]) -> str | None:
    bumps = [commit.bump for commit in commits if commit.bump]
    return max(bumps, key=BUMPS.index) if bumps else None


def messages_version(messages: dict[str, str], latest: Version) -> Version | None:
    """Return the highest x.y.z key of `messages` that is newer than the latest release."""
    versions = [parsed[1] for key in messages if (parsed := parse_tag(key)) and not parsed[0]]
    newer = [version for version in versions if version > latest]
    return max(newer) if newer else None


def decide(
    tags: list[str],
    head_tags: list[str],
    commits: list[Commit],
    bump: str,
    new_messages: dict[str, str],
) -> Decision:
    """
    Decide whether to release.

    `tags` are the tags reachable from the commit, `head_tags` the tags on the commit itself and `commits` the commits
    since the latest release, newest first. Merge commits are left out, and so can't skip a release. `new_messages` are
    the entries of messages.json added since the latest release: a message for a version that was already in
    messages.json then may be stale, for a release that was planned but never made.
    """
    if released := latest_tag(head_tags):
        return Decision(f'The commit is already released as {released[0]}.')
    latest = latest_tag(tags)
    if not latest:
        return Decision('There is no x.y.z tag to continue from. Create the first release manually.')
    previous_tag, prefix, previous = latest
    if bump == 'auto' and commits and SKIP_RELEASE in commits[0].message:
        return Decision(f'The commit message contains {SKIP_RELEASE}.')
    if version := messages_version(new_messages, previous):
        reason = f'messages.json has a message for {format_version(version)}.'
    elif bump != 'auto':
        version = bump_version(previous, bump)
        reason = f'A {bump} release was requested.'
    elif level := highest_bump(commits):
        version = bump_version(previous, level)
        types = sorted({commit.type or '' for commit in commits if commit.bump})
        reason = f'{len(commits)} commits since {previous_tag} include {", ".join(types)} changes.'
    else:
        return Decision(f'None of the {len(commits)} commits since {previous_tag} need a release.')
    return Decision(reason, version, prefix + format_version(version))


def release_notes(
    commits: list[Commit],
    previous_tag: str,
    tag: str,
    repository: str,
    message: str | None,
) -> str:
    """Release notes: the message shown in Package Control, if any, and the user-facing commits by type."""
    parts = []
    if message:
        parts.append(message.strip())
    for commit_type, title in NOTES_SECTIONS:
        lines = [f'- {commit.subject} ({commit.sha[:7]})' for commit in commits if commit.type == commit_type]
        if lines:
            parts.append(f'## {title}\n\n' + '\n'.join(lines))
    breaking = [f'- {commit.subject} ({commit.sha[:7]})' for commit in commits if commit.breaking]
    if breaking:
        parts.insert(1 if message else 0, '## Breaking changes\n\n' + '\n'.join(breaking))
    if not parts:
        parts.append(f'No user-facing changes since {previous_tag}.')
    if repository:
        parts.append(f'**Full changelog**: https://github.com/{repository}/compare/{previous_tag}...{tag}')
    return '\n\n'.join(parts) + '\n'


def git(*args: str) -> str:
    return subprocess.run(['git', *args], check=True, capture_output=True, text=True, encoding='utf-8').stdout


def read_commits(revision_range: str) -> list[Commit]:
    output = git('log', '--no-merges', '--format=%H%x1f%B%x1e', revision_range)
    commits = []
    for record in output.split('\x1e'):
        if record.strip():
            sha, message = record.strip().split('\x1f', 1)
            commits.append(Commit(sha, message.strip()))
    return commits


def read_file(revision: str, path: str) -> str | None:
    try:
        return git('show', f'{revision}:{path}')
    except subprocess.CalledProcessError:
        return None


def read_messages(revision: str, path: str) -> dict[str, str]:
    if (text := read_file(revision, path)) is None:
        return {}
    messages = json.loads(text)
    if not isinstance(messages, dict):
        print(f'::warning::{path} is not a JSON object, ignoring it')
        return {}
    return messages


def added_messages(messages: dict[str, str], released: dict[str, str]) -> dict[str, str]:
    return {version: name for version, name in messages.items() if version not in released}


def write_output(name: str, value: str) -> None:
    if output := os.environ.get('GITHUB_OUTPUT'):
        with Path(output).open('a', encoding='utf-8') as file:
            file.write(f'{name}={value}\n')


def write_summary(text: str) -> None:
    if summary := os.environ.get('GITHUB_STEP_SUMMARY'):
        with Path(summary).open('a', encoding='utf-8') as file:
            file.write(text + '\n')


def main() -> None:
    commit, bump, messages_file, notes_file = sys.argv[1:]
    if bump not in {'auto', *BUMPS}:
        print(f'::error::Unknown bump {bump!r}, expected auto, patch, minor or major')
        sys.exit(1)
    tags = git('tag', '--merged', commit).split()
    head_tags = git('tag', '--points-at', commit).split()
    latest = latest_tag(tags)
    commits = read_commits(f'{latest[0]}..{commit}' if latest else commit)
    messages = read_messages(commit, messages_file)
    new_messages = added_messages(messages, read_messages(latest[0], messages_file) if latest else {})

    decision = decide(tags, head_tags, commits, bump, new_messages)
    print(decision.reason)
    if not (decision.version and decision.tag and latest):
        write_summary(f'No release: {decision.reason}')
        return
    version = format_version(decision.version)
    name = messages.get(version)
    message = read_file(commit, str(PurePosixPath(messages_file).parent / name)) if name else None
    notes = release_notes(commits, latest[0], decision.tag, os.environ.get('GITHUB_REPOSITORY', ''), message)
    Path(notes_file).write_text(notes, encoding='utf-8')
    print(f'Releasing {decision.tag} (previous: {latest[0]})')
    write_output('version', version)
    write_output('tag', decision.tag)
    write_output('previous-tag', latest[0])
    write_output('notes-file', notes_file)
    write_summary(f'Release {decision.tag}: {decision.reason}\n\n{notes}')


if __name__ == '__main__':
    main()
