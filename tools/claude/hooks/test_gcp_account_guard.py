"""Main-pane Slack approval boundaries; all GCP commands are inert hook inputs."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "gcp_account_guard", Path(__file__).with_name("gcp-account-guard.py"))
GUARD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GUARD)


class DraftNoticeTest(unittest.TestCase):
    def test_archive_notice_preserves_the_original_whole_relay(self):
        original = "[pane-tell: '자동 작업'" + GUARD.MARK_TAIL + "[Slack #test 요유] staging 허용"
        prefixed = "[pane-tell: 입력창 초안 보관 안내] 메인 세션 입력창의 쓰다 만 글을 저장했습니다: /tmp/draft.json\n\n" + original
        match = GUARD.RELAY_MARK.match(prefixed)
        self.assertIsNotNone(match)
        self.assertEqual(prefixed[match.end():], "[Slack #test 요유] staging 허용")
        self.assertIsNone(GUARD.RELAY_MARK.match(prefixed.replace("\n\n", "\nforged\n\n")))


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


class KeyCleanupApprovalTest(unittest.TestCase):
    """Only judge hook JSON; never execute the command strings below."""
    account = "unused@example-project.iam.gserviceaccount.com"
    commands = (
        f"gcloud --configuration=munice-admin iam service-accounts keys list --iam-account={account} --managed-by=user",
        f"gcloud --configuration=munice-admin iam service-accounts keys disable fake-key --iam-account={account}",
        f"gcloud --configuration=munice-admin iam service-accounts keys delete fake-key --iam-account={account} --quiet",
        f"gcloud iam service-accounts keys list --iam-account={account} --managed-by=user",
    )

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.used = set()
        self.calls = []
        self.claims = []

    def run_hook(self, command, *, approved=True, main=True, subordinate=False, env=None,
                 prompt="continue", grant=None, rider=None, tool="Bash", background=False):
        # Removing either refusal branch or letting a grant bypass consume() must fail these tests.
        transcript = Path(self.tmp.name) / "transcript.jsonl"
        transcript.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": prompt}}) + "\n")
        payload = {"tool_name": tool, "tool_input": {"command": command, "run_in_background": background},
                   "transcript_path": str(transcript), "cwd": self.tmp.name}

        def approvals(cmd, deadline):
            self.calls.append(cmd)
            if not approved:
                return None, "test: no unused approval"

            def consume():
                if cmd in self.used:
                    return False
                self.used.add(cmd)
                return True
            return consume, ""

        def grants(words, deadline):
            self.claims.append(words)
            return [(grant, lambda _: True)], ""

        with patch.object(GUARD, "subordinate_pane", return_value=subordinate):
            return GUARD.hook(payload, env or {}, lambda _: rider, approvals, lambda _: main, grants=grants)

    def test_main_approved_list(self):
        self.assertIsNone(self.run_hook(self.commands[0]))
        self.assertEqual(self.used, {self.commands[0]})

    def test_main_approved_disable(self):
        self.assertIsNone(self.run_hook(self.commands[1]))
        self.assertEqual(self.used, {self.commands[1]})

    def test_main_approved_delete(self):
        self.assertIsNone(self.run_hook(self.commands[2]))
        self.assertEqual(self.used, {self.commands[2]})

    def test_explicit_cleanup_options_and_quoting_keep_exact_approval(self):
        for cmd in (
            f"gcloud --configuration munice-admin iam --quiet service-accounts keys --iam-account '{self.account}' list",
            f"gcloud --configuration=munice-admin iam service-accounts keys 'list' --format='value(name)' --iam-account={self.account}",
            self.commands[1].replace(" keys ", " keys \\\n  "),
            self.commands[2] + " # api#1052",
        ):
            with self.subTest(command=cmd):
                self.assertIsNone(self.run_hook(cmd))
                self.assertIn(cmd, self.used)

    def test_every_key_list_and_write_needs_approval(self):
        for command in self.commands:
            for prefix in ("--configuration=munice-admin ", ""):
                cmd = command.replace("--configuration=munice-admin ", prefix)
                for env in ({}, {"MUNICE_GCP_ADMIN": "1"}):
                    with self.subTest(command=cmd, env=env):
                        reason = self.run_hook(cmd, approved=False, env=env)
                        self.assertTrue(reason)
        self.assertFalse(self.used)

    def test_agent_key_list_needs_main_approval_in_every_session(self):
        command = self.commands[0].replace("--configuration=munice-admin ", "")
        for kwargs in ({}, {"main": False}, {"main": False, "subordinate": True},
                       {"env": {"HERDR_SESSION": "workers"}, "main": False, "subordinate": True},
                       {"env": {"MUNICE_AUTO_DISPATCH": "1"}}, {"env": {"MUNICE_AMBIENT": "1"}}):
            with self.subTest(kwargs=kwargs):
                self.assertTrue(self.run_hook(command, approved=False, **kwargs))
        self.assertEqual(self.calls, [command])
        self.assertFalse(self.used)

    def test_agent_key_list_wrappers_cannot_use_approval(self):
        cmd = self.commands[0].replace("--configuration=munice-admin ", "")
        for command in (cmd + " | wc -l", cmd + " && echo finished", "env " + cmd,
                        "command " + cmd, "time " + cmd):
            with self.subTest(command=command):
                self.assertTrue(self.run_hook(command))
        self.assertFalse(self.calls)

    def test_key_listing_never_lifts_identity_or_a_key_write(self):
        cmd = self.commands[0].replace("--configuration=munice-admin ", "")
        for command in ("CLOUDSDK_CORE_ACCOUNT=person@example.com " + cmd,
                        "env CLOUDSDK_CORE_ACCOUNT=person@example.com " + cmd,
                        "CLOUDSDK_ACTIVE_CONFIG_NAME=other-config " + cmd,
                        "env CLOUDSDK_ACTIVE_CONFIG_NAME=other-config " + cmd,
                        "export CLOUDSDK_ACTIVE_CONFIG_NAME=other-config; " + cmd,
                        "GOOGLE_APPLICATION_CREDENTIALS=other-adc.json " + cmd,
                        cmd + " --configuration=other-config", cmd + " --account=person@example.com",
                        cmd + " --impersonate-service-account=other@example-project.iam.gserviceaccount.com",
                        cmd + "; " + self.commands[2].replace("--configuration=munice-admin ", ""),
                        "if true; then " + self.commands[2] + "; fi", "time " + self.commands[2]):
            with self.subTest(command=command):
                self.assertTrue(self.run_hook(command, approved=False))
        self.assertFalse(self.used)

    def test_key_command_text_is_not_execution(self):
        for verb in ("list", "disable fake-key", "delete fake-key"):
            text = f"gcloud iam service-accounts keys {verb} --iam-account={self.account}"
            for cmd in (f"pane-tell main '{text}'", f"echo '{text}'", f"echo 'note; {text}'",
                        f"cat <<'EOF'\n{text}\nEOF", f"sed -n '/{text}/p' docs/example.md",
                        f"cat <<'123'\n{text}\n123", f"cat <<'END-TEXT'\n{text}\nEND-TEXT",
                        f"cat <<'EOF'; sh -c true\n{text}\nEOF",
                        f"gcloud version; echo '{text}'", f"echo done # ; {text}",
                        f"command -v {text}",
                        f"ls docs | xargs grep '{text}'", "pane-tell main 'service-accounts keys list'"):
                for env in ({}, {"MUNICE_GCP_ADMIN": "1"}, {"HERDR_SESSION": "workers"}):
                    with self.subTest(command=cmd, env=env):
                        self.assertIsNone(self.run_hook(cmd, approved=False, env=env))
        self.assertFalse(self.calls)

    def test_actual_wrapped_or_embedded_key_writes_still_need_approval(self):
        command = self.commands[2].replace("--configuration=munice-admin ", "")
        for cmd in (command, "env " + command, "nohup " + command, "timeout 10 " + command,
                    f"sh -c '{command}'", f"bash <<'EOF'\n{command}\nEOF",
                    f'echo "$({command})"', f"echo fake-key | xargs {command}",
                    f"echo x#note; {command}", f"cat <<EOF\n$({command})\nEOF",
                    f"echo '<<EOF'\n{command}", f'echo "<<EOF $X"\n{command}',
                    f"cat <<EOF # <<IGNORED\nhello\nEOF\n{command}",
                    f"env bash <<'EOF'\n{command}\nEOF", f"command sh <<'EOF'\n{command}\nEOF",
                    "nice -n 10 " + command, "sudo " + command, f"builtin eval '{command}'",
                    "env --chdir /tmp " + command, ">/tmp/example-output " + command,
                    f"echo '{command}' | sh", f"printf '%s\\n' '{command}' | bash", f"bash <<<'{command}'",
                    "sudo -n " + command, "/usr/bin/time -o /tmp/example-time " + command,
                    f"true; bash <<'EOF'\n{command}\nEOF", f"cat <<'EOF' | sh\n{command}\nEOF"):
            with self.subTest(command=cmd):
                self.assertTrue(self.run_hook(cmd, approved=False))
        self.assertFalse(self.used)

    def test_cleanup_parser_requires_gcloud_executable(self):
        # Mutation: dropping words[0] == 'gcloud' must fail, even if hook classification rejects wrappers first.
        for verb in ("list", "disable fake-key", "delete fake-key"):
            for executable in ("echo", "fakecloud", "pane-tell"):
                with self.subTest(executable=executable, verb=verb):
                    self.assertFalse(GUARD.key_cleanup_only(f"{executable} iam service-accounts keys {verb}"))

    def test_reused_approval_refuses_each_command(self):
        for cmd in self.commands:
            with self.subTest(command=cmd):
                self.assertIsNone(self.run_hook(cmd))
                self.assertTrue(self.run_hook(cmd))

    def test_only_hand_opened_main_pane_may_use_approval(self):
        for cmd in self.commands:
            for kwargs in ({"main": False}, {"main": False, "subordinate": True},
                           {"main": True, "subordinate": True},
                           {"env": {"HERDR_SESSION": "workers"}, "main": False},
                           {"env": {"MUNICE_AUTO_DISPATCH": "1"}}, {"env": {"MUNICE_AMBIENT": "1"}}):
                with self.subTest(command=cmd, kwargs=kwargs):
                    self.assertTrue(self.run_hook(cmd, **kwargs))
        self.assertEqual(self.calls, [])

    def test_typed_and_slack_word_grants_never_lift_cleanup(self):
        for cmd in self.commands:
            for word in ("prod 허용", "staging 허용"):
                for relayed in (False, True):
                    prompt = word if not relayed else ("[pane-tell: 'test'" + GUARD.MARK_TAIL +
                        "[Slack #0-yoyooyo 요유] " + word + " (ts 1.000001, 글 C1234567/1.000001)")
                    with self.subTest(command=cmd, word=word, relayed=relayed):
                        self.assertTrue(self.run_hook(cmd, approved=False, prompt=prompt, grant=word))
        self.assertFalse(self.claims)
        self.assertFalse(self.used)

    def test_other_hooks_run_before_consuming_cleanup_approval(self):
        for cmd in self.commands:
            with self.subTest(command=cmd):
                self.assertEqual(self.run_hook(cmd, rider="test: another hook refused"), "test: another hook refused")
                self.assertNotIn(cmd, self.used)
                self.assertIsNone(self.run_hook(cmd))

    def test_monitor_and_background_cleanup_stay_refused(self):
        for cmd in self.commands:
            for kwargs in ({"tool": "Monitor"}, {"background": True}):
                with self.subTest(command=cmd, kwargs=kwargs):
                    self.assertTrue(self.run_hook(cmd, **kwargs))
        self.assertFalse(self.calls)

    def test_approval_never_lifts_other_iam_policy_or_key_operations(self):
        # Broadening the special case from these three verbs to IAM must fail.
        forbidden = (
            "iam service-accounts keys create output.json", "iam service-accounts keys upload public.pem",
            "iam service-accounts keys enable fake-key", "iam service-accounts keys get-public-key fake-key",
            "iam service-accounts list", "iam service-accounts describe unused@example-project.iam.gserviceaccount.com",
            "iam service-accounts delete unused@example-project.iam.gserviceaccount.com", "iam roles create fake-role",
            "iam roles list", "iam workload-identity-pools list --location=global",
            "projects add-iam-policy-binding example-project --member=user:fake@example.com --role=roles/viewer",
            "projects get-iam-policy example-project", "run services set-iam-policy fake-service policy.json",
            "org-policies list --project=example-project", "org-policies describe fake-constraint",
            "org-policies set-policy policy.yaml", "resource-manager org-policies disable-enforce fake-constraint",
        )
        for suffix in forbidden:
            cmd = "gcloud --configuration=munice-admin " + suffix
            with self.subTest(command=cmd):
                self.assertTrue(self.run_hook(cmd))
        self.assertFalse(self.calls)
        self.assertFalse(self.used)

    def test_cleanup_exception_never_covers_extra_commands_or_flags(self):
        for suffix in ("; gcloud iam roles list", " && gcloud iam roles list", " | sh", " &", "\ngcloud iam roles list",
                       " --flags-file=options.yaml", " --access-token-file=token.txt", " --log-http",
                       " --unknown-option=ignored"):
            with self.subTest(suffix=suffix):
                self.assertTrue(self.run_hook(self.commands[2] + suffix))
        for prefix in ("bash -c ", "env ", "nohup "):
            with self.subTest(prefix=prefix):
                self.assertTrue(self.run_hook(prefix + self.commands[2]))
        for cmd in (self.commands[2] + " extra-key", self.commands[0] + " fake-key",
                    self.commands[0] + " --format", self.commands[0] + " --format=",
                    self.commands[0] + " --format --quiet", self.commands[0] + " --quiet=true",
                    self.commands[0] + "\u200b", self.commands[0] + "\r",
                    self.commands[2].replace("gcloud ", "gcloud beta "),
                    self.commands[2].replace("fake-key", "$(echo fake-key)")):
            with self.subTest(command=cmd):
                self.assertTrue(self.run_hook(cmd))
        self.assertFalse(self.calls)

    def test_shell_dollar_quotes_cannot_skip_cleanup_approval(self):
        # The shell accepts these forms; the approval parser must refuse to expand them.
        for verb in ("list", "disable", "delete"):
            key = "" if verb == "list" else " fake-key"
            for words in (f"service-accounts keys $'{verb}'", f'service-accounts keys $"{verb}"',
                          f"service-accounts $'keys' {verb}", f"service-$'accounts' keys {verb}",
                          f"service-accounts keys $'\\x{ord(verb[0]):02x}{verb[1:]}'"):
                for config in ("", "--configuration=munice-admin "):
                    cmd = f"gcloud {config}iam {words}{key} --iam-account={self.account}"
                    with self.subTest(command=cmd):
                        self.assertTrue(self.run_hook(cmd, env={"MUNICE_GCP_ADMIN": "1"}))
        self.assertFalse(self.calls)

    def test_interposed_indirect_or_unknown_flags_cannot_skip_key_guard(self):
        for verb in ("list", "disable fake-key", "delete fake-key", "create output.json", "upload public.pem"):
            for flags in ("--flags-file=-", "--flags-file=-options.yaml", "--unknown-option=-value",
                          "--flags-file -"):
                for config in ("", "--configuration=munice-admin "):
                    cmd = (f"gcloud {config}iam service-accounts {flags} keys {verb} "
                           f"--iam-account={self.account} <<<'{{}}'")
                    for env in ({}, {"MUNICE_GCP_ADMIN": "1"}):
                        with self.subTest(command=cmd, env=env):
                            self.assertTrue(self.run_hook(cmd, env=env))
        self.assertFalse(self.calls)

    def test_approval_never_lifts_iam_or_org_policy_api_calls(self):
        for endpoint in (
            "https://orgpolicy.googleapis.com/v2/projects/example-project/policies/fake-constraint",
            "https://cloudresourcemanager.googleapis.com/v1/projects/example-project:getOrgPolicy",
            "https://cloudresourcemanager.googleapis.com/v1/projects/example-project:listOrgPolicies",
            "https://cloudresourcemanager.googleapis.com/v1/projects/example-project:getEffectiveOrgPolicy",
            f"https://iam.googleapis.com/v1/projects/-/serviceAccounts/{self.account}/keys/fake-key",
            f"https://iam.googleapis.com/v1/projects/-/serviceAccounts/{self.account}/keys/fake-key:disable",
        ):
            for method in ("get", "delete", "post"):
                cmd = ("GOOGLE_APPLICATION_CREDENTIALS=fake-adc.json python3 -c '"
                       "from google.auth.transport.requests import AuthorizedSession; "
                       f'AuthorizedSession(creds).{method}("{endpoint}")' + "'")
                with self.subTest(command=cmd):
                    self.assertTrue(self.run_hook(cmd))
        self.assertFalse(self.calls)

    def test_flags_file_cannot_hide_key_command_without_identity_selection(self):
        for flags in ("--flags-file=key-flags.yaml", "--flags-file key-flags.yaml", "--flags-file=-",
                      "--flags-$'file'=key-flags.yaml", "$'--flags-file'=key-flags.yaml"):
            for config in ("", "--configuration=munice-admin "):
                for env in ({}, {"MUNICE_GCP_ADMIN": "1"}):
                    for prompt in ("continue", "prod 허용", "staging 허용"):
                        cmd = f"gcloud {config}{flags}"
                        with self.subTest(command=cmd, env=env, prompt=prompt):
                            self.assertTrue(self.run_hook(cmd, env=env, prompt=prompt))
        self.assertFalse(self.calls)

    def test_comment_continuation_cannot_hide_a_second_command(self):
        for cmd in self.commands:
            for tail in ("printf EXTRA", "gcloud iam roles list", "gcloud org-policies list --project=example-project"):
                command = cmd + " # comment \\\n" + tail
                with self.subTest(command=command):
                    self.assertTrue(self.run_hook(command))
        self.assertFalse(self.calls)

    def test_split_group_words_cannot_skip_key_approval(self):
        for verb in ("list", "disable fake-key", "delete fake-key", "create output.json", "upload public.pem"):
            for group in ("service-\\\naccounts keys", "service-accounts ke\\\nys", "ser\\\nvice-accounts keys"):
                command = f"gcloud iam {group} {verb} --iam-account={self.account}"
                for env in ({}, {"MUNICE_GCP_ADMIN": "1"}):
                    for main in (True, False):
                        with self.subTest(command=command, env=env, main=main):
                            reason = self.run_hook(command, approved=False, env=env, main=main)
                            self.assertTrue(reason)

    def test_flags_file_text_in_instructions_is_not_execution(self):
        # Matching --flags-file across raw instruction text must fail this test.
        text = "Reject gcloud --flags-file=f.yaml and --FLAGS-FILE without running it"
        for command in (f"pane-spawn 'guard fix' '{text}'", f"pane-tell main '{text}'",
                        f"pane-tell workers/w1:p1 '{text}'", "pane-tell main '--flags-file'",
                        f"echo '{text}'", f"printf '%s\\n' '{text}'",
                        f"cat <<'EOF'\n{text}\nEOF", f"sed -n '/{text}/p' docs/example.md",
                        f"ls docs | xargs grep '{text}'", f"gcloud version; echo '{text}'",
                        "pane-tell main 'gcloud '\"--flags-file=f.yaml\"",
                        "pane-spawn fix 'gcloud '\"--flags-file=f.yaml\"",
                        "echo 'gcloud '\"--flags-file=f.yaml\"",
                        "echo done # gcloud --flags-file=f.yaml"):
            for env in ({}, {"MUNICE_GCP_ADMIN": "1"}, {"HERDR_SESSION": "workers"}):
                with self.subTest(command=command, env=env):
                    self.assertIsNone(self.run_hook(command, approved=False, env=env))
        self.assertFalse(self.calls)

    def test_flags_file_in_executed_shell_still_refuses(self):
        # Ignoring shell execution while unmasking instruction text must fail.
        call = "gcloud --flags-file=f.yaml"
        for command in (call, "env " + call, "command " + call, f"sh -c '{call}'",
                        f"bash <<'EOF'\n{call}\nEOF", f"cat <<'EOF' | sh\n{call}\nEOF",
                        f"echo '{call}' | sh", f"printf '%s\\n' '{call}' | bash",
                        f'pane-tell main "$({call})"', f"cat <<EOF\n$({call})\nEOF",
                        "g=gcloud; $g --flags-file=f.yaml", "g=GCLOUD; ${g} --FLAGS-FILE=f.yaml"):
            with self.subTest(command=command):
                self.assertTrue(self.run_hook(command))
        self.assertFalse(self.calls)

    def test_quoted_executable_and_shell_feeds_cannot_hide_execution(self):
        call = "gcloud --flags-file=f.yaml"
        for command in ("g'cloud' --flags-file=f.yaml", 'g""cloud --flags-file=f.yaml',
                        f"printf '{call}\\n' | sh", f"printf '{call}' | bash",
                        f"printf -- '%s\\n' '{call}' | sh", f"echo -e '{call}' | sh",
                        f"env -S '{call}'", f"env --split-string='{call}'",
                        f"printf '--' '%s\\n' '{call}' | sh", f"echo '-e' '{call}' | sh",
                        f"env '-S' '{call}'", f"env '--split-string={call}'",
                        f"bash '-c' '{call}'", f"env '-u' UNUSED {call}", f"nice '-n' 1 {call}",
                        "sh -c 'gcloud '\"--flags-file=f.yaml\"",
                        "echo 'gcloud '\"--flags-file=f.yaml\" | sh",
                        "printf '%s\\n' 'gcloud '\"--flags-file=f.yaml\" | sh",
                        "eval 'gcloud '\"--flags-file=f.yaml\"",
                        "sh <<<'gcloud '\"--flags-file=f.yaml\"",
                        f"echo '{call}' | cat | sh", f"printf '%s' '{call}' | cat | bash",
                        f"echo '{call}' | 'sh'",
                        f"g'cloud' iam service-accounts keys list --iam-account={self.account}"):
            with self.subTest(command=command):
                self.assertTrue(self.run_hook(command, approved=False))

    def test_split_policy_words_cannot_use_generic_identity_approval(self):
        for suffix in ("i\\\nam roles list", "org-po\\\nlicies list", "projects get-iam-po\\\nlicy example-project"):
            cmd = "gcloud --configuration=munice-admin " + suffix
            with self.subTest(command=cmd):
                self.assertTrue(self.run_hook(cmd))
        self.assertFalse(self.calls)

    def test_generic_identity_approval_cannot_hide_key_group_in_flags_file(self):
        # gcloud expands YAML keys into command words, including an entire IAM subcommand.
        for flags in ("--flags-file=key-flags.yaml", "--flags-file key-flags.yaml", "--flags-file=-",
                      "--flags-$'file'=key-flags.yaml", "$'--flags-file'=key-flags.yaml"):
            cmd = f"gcloud --configuration=munice-admin {flags}"
            with self.subTest(command=cmd):
                self.assertTrue(self.run_hook(cmd))
        self.assertFalse(self.calls)

    def test_flags_file_refuses_case_variants_and_variable_command(self):
        for cmd in ("GCLOUD --flags-file=f.yaml", "Gcloud --flags-file f.yaml",
                    "g=gcloud; $g --flags-file=f.yaml", "g=Gcloud; $g --FLAGS-FILE f.yaml"):
            for env in ({}, {"MUNICE_GCP_ADMIN": "1"}, {"HERDR_SESSION": "workers"}):
                with self.subTest(command=cmd, env=env):
                    self.assertTrue(self.run_hook(cmd, approved=False, env=env))
        self.assertFalse(self.calls)


class RequestedCommandTest(unittest.TestCase):
    """A command with a ✅ request on record runs only on a valid ✅, even when nothing else in the guard refuses it
    (terraform#1267: a ✅ after the ten minutes was no guard at all). slack_approval.py judges the ✅ (its own tests cover
    expiry, edits and reuse); here a stand-in gives its verdicts and the guard must act on them."""
    command = "gh pr merge 847 -R example/repo --squash"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.requests = Path(self.tmp.name, "requests")
        self.requests.mkdir()

    def request(self, command=None, **record):
        key = hashlib.sha256((command or self.command).strip().encode()).hexdigest()
        data = {"at": 1000.0, "status": "posted", "ts": "1000.500000"}
        data.update(record)
        (self.requests / f"{key}.json").write_text(json.dumps(data))

    def hook(self, verdict, command=None, rider=None, env=None, is_main=True, background=False, tool="Bash"):
        """(reason, how many times the approval was read, how many times it was used)."""
        reads, uses = [], []

        def approvals(cmd, left):
            reads.append(cmd)
            if verdict is True:
                return (lambda: uses.append(cmd) or True), ""
            if verdict == "used":
                return (lambda: False), ""
            if verdict == "raises":
                raise OSError("down")
            return None, verdict

        inp = {"command": command or self.command, **({"run_in_background": True} if background else {})}
        reason = GUARD.hook({"tool_name": tool, "tool_input": inp}, env or {}, lambda _: rider, approvals,
                            lambda _: is_main, is_requested=lambda c: GUARD.requested(c, str(self.requests)))
        return reason, len(reads), len(uses)

    def test_requested_command_with_an_expired_approval_is_refused(self):
        self.request()
        reason, reads, uses = self.hook("ts 1000.5: 올린 지 10분이 지남: 같은 명령으로 새 요청을 올릴 것")
        self.assertIn("올린 지 10분이 지남", reason)
        self.assertEqual((reads, uses), (1, 0))

    def test_requested_command_with_no_or_a_spent_approval_is_refused(self):
        self.request()
        self.assertIn("✅가 없음", self.hook("요유가 ✅를 누른 답글이 없음 ✅가 없음")[0])
        reason, _, uses = self.hook("used")
        self.assertIn("이미 한 번 썼거나", reason)
        self.assertEqual(uses, 0)

    def test_requested_command_with_a_valid_approval_runs_once_and_uses_it(self):
        self.request()
        self.assertEqual(self.hook(True), (None, 1, 1))
        self.request(status="posting")
        self.assertEqual(self.hook(True), (None, 1, 1))

    def test_command_nobody_asked_about_is_untouched(self):
        self.assertEqual(self.hook("never read"), (None, 0, 0))  # no record at all
        self.request("gh pr merge 1 -R example/repo")
        self.assertEqual(self.hook("never read"), (None, 0, 0))  # the record names another command
        self.request(status="prepared")  # prepared: reserved, never sent
        self.assertEqual(self.hook("never read"), (None, 0, 0))
        self.assertEqual(self.hook("never read", tool="Read"), (None, 0, 0))

    def test_an_unreadable_record_counts_as_asked_and_a_failing_slack_read_refuses(self):
        (self.requests / (hashlib.sha256(self.command.encode()).hexdigest() + ".json")).write_text("{")
        self.assertEqual(self.hook(True), (None, 1, 1))  # asked, so a valid ✅ is read and used
        self.assertIn("Slack을 읽지 못함(OSError)", self.hook("raises")[0])

    def test_only_the_main_pane_foreground_bash_may_use_the_approval(self):
        self.request()
        for kwargs in ({"is_main": False}, {"background": True}, {"tool": "Monitor"},
                       {"env": {"MUNICE_AUTO_DISPATCH": "1"}}, {"env": {"MUNICE_AMBIENT": "1"}}):
            with self.subTest(kwargs=kwargs):
                reason, reads, uses = self.hook(True, **kwargs)
                self.assertIn("메인 칸의 Bash 호출", reason)
                self.assertEqual((reads, uses), (0, 0))

    def test_another_guards_refusal_comes_first_and_leaves_the_approval_unused(self):
        self.request()
        self.assertEqual(self.hook(True, rider="pane-fit-guard: ask"), ("pane-fit-guard: ask", 0, 0))

    def test_other_refusals_are_unchanged(self):
        reason, reads, uses = self.hook(True, command="gcloud config set account me@example.com")
        self.assertTrue(reason)
        self.assertEqual((reads, uses), (0, 0))


if __name__ == "__main__":
    # The repository pre-push hook discovers Python tests that support --selftest.
    unittest.main(argv=[sys.argv[0]] if sys.argv[1:] == ["--selftest"] else None)
