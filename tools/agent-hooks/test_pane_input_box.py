import subprocess
from pathlib import Path
import unittest


SCRIPT = Path(__file__).with_name('pane-input-box.py')
RULE = '\x1b[38;5;8m' + '─' * 40 + '\x1b[0m\n'


def screen(text='', footer='? for shortcuts   auto · Gemini 4 Hydrogen · xhigh'):
    return RULE + '\x1b[38;5;12m>\x1b[0m' + text + '\n' + RULE + footer + '\n'


class AgyInputTest(unittest.TestCase):
    def empty(self, text):
        return subprocess.run(['python3', '-I', str(SCRIPT), 'agy'], input=text,
                              text=True, capture_output=True).returncode == 0

    def test_empty_prompt_for_all_models(self):
        for model in ('Hydrogen', 'Neon', 'Carbon'):
            self.assertTrue(self.empty(screen(footer=f'? for shortcuts   auto · Gemini 4 {model} · high')))

    def test_typed_single_and_multiline_drafts_stay_untouched(self):
        for draft in (' hello', '\ncontinuation', ' \x1b[2mtyped\x1b[0m'):
            self.assertFalse(self.empty(screen(draft)))

    def test_history_does_not_hide_current_draft(self):
        self.assertFalse(self.empty(screen() + screen(' draft')))

    def test_dialog_or_unknown_layout_fails_closed(self):
        for text in (screen(footer='Trust this folder?'), screen() + 'Allow command?\n',
                     RULE + '>\n? for shortcuts · Gemini 4 Neon\n',
                     screen().replace('>\x1b[0m', '❯\x1b[0m')):
            self.assertFalse(self.empty(text))


if __name__ == '__main__':
    unittest.main()
