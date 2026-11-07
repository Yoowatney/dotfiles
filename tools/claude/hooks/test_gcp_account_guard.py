"""Credential refusals explain the per-command ADC route only in the main pane."""
import importlib.util
from pathlib import Path
import sys
import unittest


SPEC = importlib.util.spec_from_file_location(
    "gcp_account_guard", Path(__file__).with_name("gcp-account-guard.py"))
GUARD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GUARD)


class CredentialRefusalTest(unittest.TestCase):
    # These strings are hook inputs only; no credential file or GCP command is opened or run.
    path = "$HOME/.config/gcloud/application_default_credentials.json"
    assignment = f"GOOGLE_APPLICATION_CREDENTIALS={path} node fix.js"

    def run_hook(self, command, main):
        payload = {"tool_name": "Bash", "tool_input": {"command": command}}
        calls = []

        def approvals(cmd, deadline):
            calls.append(cmd)
            return None, "test: no approval"

        reason = GUARD.hook(payload, {}, lambda _: None, approvals, lambda _: main)
        return reason, calls

    def test_main_credential_refusal_explains_assignment_without_lifting_denial(self):
        # Removing the hint, or allowing a path outside its assignment, must fail.
        for command in (f"ls -l {self.path}", self.assignment + f"\n# {self.path}",
                        self.assignment + f'\nconst credentialPath = "{self.path}";'):
            with self.subTest(command=command):
                reason, calls = self.run_hook(command, main=True)
                self.assertTrue(reason.startswith("gcp-account-guard: 자격 증명 파일:"))
                self.assertIn("✅ 요청 명령에서", reason)
                self.assertIn("GOOGLE_APPLICATION_CREDENTIALS에 한 번 할당하는 것은 ✅로 풀립니다", reason)
                self.assertIn("경로를 다른 곳에 적지 마세요", reason)
                self.assertEqual(calls, [])

    def test_other_pane_credential_refusal_never_offers_main_approval_route(self):
        # Offering an unavailable approval route outside the main pane must fail.
        reason, calls = self.run_hook(f"ls -l {self.path}", main=False)
        self.assertTrue(reason.startswith("gcp-account-guard: 자격 증명 파일:"))
        self.assertNotIn("✅ 요청 명령에서", reason)
        self.assertNotIn("GOOGLE_APPLICATION_CREDENTIALS", reason)
        self.assertEqual(calls, [])

    def test_assignment_alone_still_reaches_main_slack_approval(self):
        reason, calls = self.run_hook(self.assignment, main=True)
        self.assertEqual(calls, [self.assignment])
        self.assertIn("test: no approval", reason)
        self.assertNotIn("자격 증명 파일:", reason)

    def test_other_persist_refusals_do_not_offer_adc_assignment(self):
        reason, calls = self.run_hook("gcloud auth login", main=True)
        self.assertIn("gcloud 로그인:", reason)
        self.assertNotIn("GOOGLE_APPLICATION_CREDENTIALS", reason)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    # The repository pre-push hook discovers Python tests that support --selftest.
    unittest.main(argv=[sys.argv[0]] if sys.argv[1:] == ["--selftest"] else None)
