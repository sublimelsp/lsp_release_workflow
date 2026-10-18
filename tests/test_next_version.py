from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))

from next_version import Commit
from next_version import decide
from next_version import Decision
from next_version import latest_tag
from next_version import messages_version
from next_version import release_notes

SCRIPT = Path(__file__).parent.parent / 'scripts' / 'next_version.py'


def commits(*messages: str) -> list[Commit]:
    return [Commit(f'{index:040x}', message) for index, message in enumerate(messages, 1)]


def decide_auto(
    *messages: str, tags: tuple[str, ...] = ('1.2.3',), messages_json: dict[str, str] | None = None
) -> Decision:
    return decide(list(tags), [], commits(*messages), 'auto', messages_json or {})


class TestCommit(unittest.TestCase):
    def test_types(self) -> None:
        self.assertEqual(Commit('a', 'fix(deps): update dependency pyright to v1.1.414 (#456)').bump, 'patch')
        self.assertEqual(Commit('a', 'feat: support 3.14 env').bump, 'minor')
        self.assertEqual(Commit('a', 'perf: cache settings').bump, 'patch')
        self.assertEqual(Commit('a', 'Fix: capitalized type').bump, 'patch')
        self.assertIsNone(Commit('a', 'chore(deps): update astral-sh/setup-uv action to v10.3.0 (#459)').bump)
        self.assertIsNone(Commit('a', 'ci: add smoke test (#460)').bump)
        self.assertIsNone(Commit('a', 'Update dependency zuban to v0.10.1 (#3)').bump)
        self.assertIsNone(Commit('a', 'fix:no space after colon').bump)

    def test_breaking_bumps_minor(self) -> None:
        self.assertEqual(Commit('a', 'feat!: drop ST3').bump, 'minor')
        self.assertEqual(Commit('a', 'refactor(api)!: rename settings').bump, 'minor')
        self.assertEqual(Commit('a', 'refactor: rename settings\n\nBREAKING CHANGE: old names are gone').bump, 'minor')
        self.assertFalse(Commit('a', 'fix: mention BREAKING CHANGE: in passing').breaking)


class TestVersions(unittest.TestCase):
    def test_latest_tag_ignores_other_tags(self) -> None:
        self.assertEqual(
            latest_tag(['st3-1.2.42', '1.5.6', '1.10.0', '1.9.9', '2.0.0-beta', 'v3']), ('1.10.0', '', (1, 10, 0))
        )
        self.assertEqual(latest_tag(['v1', 'v1.8.1', 'v1.9.0']), ('v1.9.0', 'v', (1, 9, 0)))
        self.assertIsNone(latest_tag(['st3-1.0.0', 'v1']))

    def test_messages_version(self) -> None:
        messages = {'install': 'messages/install.txt', '1.4.0': 'a', '1.6.0': 'b', '1.5.0': 'c'}
        self.assertEqual(messages_version(messages, (1, 5, 6)), (1, 6, 0))
        self.assertIsNone(messages_version(messages, (1, 6, 0)))


class TestDecide(unittest.TestCase):
    def test_fix_releases_patch(self) -> None:
        decision = decide_auto('fix(deps): update dependency pyright to v1.1.415', 'chore: update deps')
        self.assertEqual((decision.version, decision.tag), ((1, 2, 4), '1.2.4'))

    def test_feat_releases_minor(self) -> None:
        self.assertEqual(decide_auto('fix: a', 'feat: b', 'fix: c').tag, '1.3.0')

    def test_keeps_tag_prefix(self) -> None:
        self.assertEqual(decide_auto('fix: a', tags=('v1', 'v1.8.1')).tag, 'v1.8.2')

    def test_only_chores_dont_release(self) -> None:
        decision = decide_auto('chore(deps): update actions/checkout action to v7', 'ci: add smoke test')
        self.assertIsNone(decision.version)
        self.assertIn('None of the 2 commits', decision.reason)

    def test_no_tag_doesnt_release(self) -> None:
        self.assertIsNone(decide_auto('fix: a', tags=('st3-1.0.0',)).version)

    def test_already_released(self) -> None:
        decision = decide(['1.2.3'], ['1.2.3'], [], 'patch', {})
        self.assertIsNone(decision.version)
        self.assertIn('already released', decision.reason)

    def test_skip_release(self) -> None:
        self.assertIsNone(decide_auto('fix: a [skip release]', 'feat: b').version)
        # Only the latest commit counts, so the next push releases the skipped changes.
        self.assertEqual(decide_auto('fix: c', 'fix: a [skip release]').tag, '1.2.4')

    def test_requested_bump(self) -> None:
        self.assertEqual(decide(['1.2.3'], [], commits('ci: a'), 'major', {}).tag, '2.0.0')
        # A requested release ignores [skip release].
        self.assertEqual(decide(['1.2.3'], [], commits('fix: a [skip release]'), 'minor', {}).tag, '1.3.0')

    def test_messages_json_sets_version(self) -> None:
        decision = decide_auto('chore: add upgrade message', messages_json={'1.2.3': 'a', '2.0.0': 'b'})
        self.assertEqual(decision.tag, '2.0.0')
        self.assertIn('messages.json', decision.reason)


class TestReleaseNotes(unittest.TestCase):
    def test_sections(self) -> None:
        notes = release_notes(
            commits('fix: b', 'chore: c', 'feat!: a'), '1.2.3', '1.3.0', 'sublimelsp/LSP-example', 'Upgrade message.\n'
        )
        self.assertEqual(
            notes,
            'Upgrade message.\n\n'
            '## Breaking changes\n\n- feat!: a (0000000)\n\n'
            '## Features\n\n- feat!: a (0000000)\n\n'
            '## Fixes\n\n- fix: b (0000000)\n\n'
            '**Full changelog**: https://github.com/sublimelsp/LSP-example/compare/1.2.3...1.3.0\n',
        )

    def test_no_user_facing_changes(self) -> None:
        self.assertEqual(
            release_notes(commits('ci: a'), '1.2.3', '1.2.4', '', None), 'No user-facing changes since 1.2.3.\n'
        )


class TestScript(unittest.TestCase):
    """Runs the script in a temporary repository."""

    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.env = {
            **os.environ,
            'GIT_AUTHOR_NAME': 'Test',
            'GIT_AUTHOR_EMAIL': 'test@example.com',
            'GIT_COMMITTER_NAME': 'Test',
            'GIT_COMMITTER_EMAIL': 'test@example.com',
            'GITHUB_OUTPUT': str(self.repo / 'output.txt'),
            'GITHUB_REPOSITORY': 'sublimelsp/LSP-example',
        }
        self.env.pop('GITHUB_STEP_SUMMARY', None)
        self.git('init', '-q', '-b', 'main')

    def git(self, *args: str) -> str:
        return subprocess.run(
            ['git', *args], cwd=self.repo, env=self.env, check=True, capture_output=True, text=True
        ).stdout.strip()

    def commit(self, message: str) -> None:
        self.git('commit', '-q', '--allow-empty', '-m', message)

    def run_script(self, bump: str = 'auto') -> dict[str, str]:
        output = self.repo / 'output.txt'
        output.unlink(missing_ok=True)
        subprocess.run(
            [sys.executable, '-B', str(SCRIPT), 'HEAD', bump, 'messages.json', str(self.repo / 'notes.md')],
            cwd=self.repo,
            env=self.env,
            check=True,
            capture_output=True,
        )
        if not output.exists():
            return {}
        return dict(line.split('=', 1) for line in output.read_text(encoding='utf-8').splitlines())

    def test_release(self) -> None:
        self.commit('feat: first')
        self.git('tag', 'st3-0.9.0')
        self.git('tag', '1.0.0')
        self.commit('chore(deps): update actions')
        self.assertEqual(self.run_script(), {})
        self.commit('fix(deps): update dependency server to v2 (#12)')
        output = self.run_script()
        self.assertEqual(output['version'], '1.0.1')
        self.assertEqual(output['tag'], '1.0.1')
        self.assertEqual(output['previous-tag'], '1.0.0')
        notes = Path(output['notes-file']).read_text(encoding='utf-8')
        self.assertIn('- fix(deps): update dependency server to v2 (#12)', notes)
        self.assertNotIn('chore', notes)

    def test_messages_json(self) -> None:
        self.commit('feat: first')
        self.git('tag', '1.0.0')
        (self.repo / 'messages').mkdir()
        (self.repo / 'messages' / '1.1.0.md').write_text('New settings.\n', encoding='utf-8')
        (self.repo / 'messages.json').write_text(json.dumps({'1.1.0': 'messages/1.1.0.md'}), encoding='utf-8')
        self.git('add', 'messages', 'messages.json')
        self.commit('chore: add upgrade message')
        output = self.run_script()
        self.assertEqual(output['tag'], '1.1.0')
        self.assertTrue(Path(output['notes-file']).read_text(encoding='utf-8').startswith('New settings.\n'))

    def test_stale_message(self) -> None:
        """A message that was already there at the latest release doesn't set the version."""
        (self.repo / 'messages.json').write_text(json.dumps({'2.0.0': 'messages/2.0.0.md'}), encoding='utf-8')
        self.git('add', 'messages.json')
        self.commit('feat: first')
        self.git('tag', '1.0.0')
        self.commit('chore: unrelated')
        self.assertEqual(self.run_script(), {})
        self.commit('fix: something')
        self.assertEqual(self.run_script()['tag'], '1.0.1')

    def test_tagged_commit(self) -> None:
        self.commit('fix: first')
        self.git('tag', '1.0.0')
        self.assertEqual(self.run_script('patch'), {})


if __name__ == '__main__':
    unittest.main()
