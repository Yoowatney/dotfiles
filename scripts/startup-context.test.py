"""Contracts for process-local startup reduction; no installed files are changed."""
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class StartupContextTest(unittest.TestCase):
    def test_claude_all_roles_share_description_budget_and_six_connection_exclusions(self):
        profile = json.loads((ROOT / 'tools/claude/startup-profile.json').read_text())
        for role in ('main', 'worker'):
            merged = dict(profile['common'], **profile['roles'][role])
            self.assertLessEqual(merged['skillListingBudgetFraction'], .0015)
            self.assertLessEqual(merged['skillListingMaxDescChars'], 160)
            names = {s['serverName'] for s in merged['deniedMcpServers']}
            self.assertTrue({'rea', 'Amplitude', 'notion-token', 'obsidian',
                             'plugin:figma:figma', 'plugin:discord:discord'} <= names)
            self.assertNotIn('hooks', merged)
            self.assertNotEqual(merged.get('enabledPlugins', {}).get('ponytail@ponytail'), False)


if __name__ == '__main__':
    unittest.main()
