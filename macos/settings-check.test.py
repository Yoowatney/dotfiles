#!/usr/bin/env python3
"""Parsers of settings-check.py. The live checks run inside verify.sh on each Mac."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('settings_check', Path(__file__).with_name('settings-check.py'))
assert spec and spec.loader
sc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sc)


class ParseTests(unittest.TestCase):
    def test_defaults_lines_keep_typed_writes_only(self):
        text = '''defaults write com.apple.dock autohide -bool true
  defaults -currentHost write NSGlobalDomain com.apple.mouse.tapBehavior -int 1
defaults write com.apple.dock orientation -string "bottom"
defaults write org.example.im CapsLock -bool true 2>/dev/null || true
defaults write com.apple.symbolichotkeys AppleSymbolicHotKeys -dict-add 60 '
defaults write com.apple.screencapture location -string "$HOME/Desktop"
# defaults write com.example.off key -bool true
sudo defaults write /Library/Preferences/x key -bool true
'''
        self.assertEqual(sc.parse_defaults(text), [
            (False, 'com.apple.dock', 'autohide', 'bool', 'true'),
            (True, 'NSGlobalDomain', 'com.apple.mouse.tapBehavior', 'int', '1'),
            (False, 'com.apple.dock', 'orientation', 'string', 'bottom'),
            (False, 'org.example.im', 'CapsLock', 'bool', 'true'),
        ])

    def test_same_value_reads_defaults_output(self):
        self.assertTrue(sc.same_value('bool', 'true', '1'))
        self.assertTrue(sc.same_value('bool', 'false', '0'))
        self.assertTrue(sc.same_value('float', '0.8', '0.80'))
        self.assertTrue(sc.same_value('string', 'YES', 'YES'))
        self.assertFalse(sc.same_value('int', '2', '3'))
        self.assertFalse(sc.same_value('bool', 'true', None))
        self.assertFalse(sc.same_value('float', '3', 'abc'))

    def test_brewfile_casks_and_mas(self):
        text = 'brew "git"\ncask "aldente"  # 충전 한도\n# cask "old"\ncask "user/tap/tool"\nmas "Snap", id: 418073146\n'
        self.assertEqual(sc.parse_brewfile(text), (['aldente', 'tool'], [('Snap', '418073146')]))

    def test_dock_ids_in_order_without_blank_tiles(self):
        prefs = {'persistent-apps': [{'tile-data': {'bundle-identifier': 'com.a'}}, {'tile-data': {}},
                                     {'tile-data': {'bundle-identifier': 'com.b'}}]}
        self.assertEqual(sc.dock_ids(prefs), ['com.a', 'com.b'])
        self.assertEqual(sc.dock_ids({}), [])

    def test_dock_ids_skip_apps_in_home_applications(self):
        tile = lambda bid, url: {'tile-data': {'bundle-identifier': bid, 'file-data': {'_CFURLString': url}}}
        prefs = {'persistent-apps': [tile('com.a', 'file:///Applications/A.app/'),
                                     tile('com.local', 'file:///Users/someone/Applications/L.app/')]}
        self.assertEqual(sc.dock_ids(prefs), ['com.a'])


if __name__ == '__main__':
    unittest.main()
