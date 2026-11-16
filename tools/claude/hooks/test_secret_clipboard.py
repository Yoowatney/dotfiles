"""Controlled GCP secret transfer; all sources and sinks are synthetic."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('secret_clipboard_runner', Path(__file__).with_name('secret-clipboard.py'))
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class SecretClipboard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.folder = self.home / '.config/gcloud'
        (self.folder / 'configurations').mkdir(parents=True)
        (self.folder / 'active_config').write_text('fixture')
        self.config = self.folder / 'configurations/config_fixture'
        self.config.write_text('[core]\naccount = fixture@example.invalid\n')
        patch = mock.patch.object(M.Path, 'home', return_value=self.home)
        patch.start(); self.addCleanup(patch.stop)
        self.installation = self.home / 'installation-properties'
        patch = mock.patch.object(M, 'INSTALLATION_PROPERTIES', self.installation)
        patch.start(); self.addCleanup(patch.stop)
        patch = mock.patch.dict(os.environ, {}, clear=True)
        patch.start(); self.addCleanup(patch.stop)

    def test_logging_endpoint_and_identity_overrides_refuse_before_reading(self):
        args = ['gcloud', '--project', 'example', '--secret', 'example']
        for body in ('[core]\nlog_http=true\n', '[api_endpoint_overrides]\nsecretmanager=http://127.0.0.1:9/\n',
                     '[proxy]\naddress=127.0.0.1\n', '[auth]\ncredential_file_override=/tmp/fixture\n',
                     '[core]\ncustom_ca_certs_file=/tmp/fixture.pem\n'):
            self.config.write_text(body)
            with mock.patch.object(M.subprocess, 'run') as run:
                self.assertNotEqual(M.transfer(args), 0)
                run.assert_not_called()
        self.config.write_text('[core]\n')
        for env in ({'CLOUDSDK_CORE_LOG_HTTP':'true'}, {'CLOUDSDK_CONFIG':'/tmp/fixture'},
                    {'CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE':'/tmp/fixture'}):
            with mock.patch.dict(os.environ, env):
                with mock.patch.object(M.subprocess, 'run') as run:
                    self.assertNotEqual(M.transfer(args), 0)
                    run.assert_not_called()

    def test_installation_properties_are_checked_too(self):
        for body in ('[core]\nlog_http=true\n', '[api_endpoint_overrides]\nsecretmanager=http://127.0.0.1:9/\n',
                     '[proxy]\naddress=127.0.0.1\n', '[auth]\naccess_token_file=/tmp/draft\n'):
            self.installation.write_text(body)
            with mock.patch.object(M.subprocess, 'run') as run:
                self.assertNotEqual(M.transfer(['gcloud','--project','example','--secret','example']),0)
                run.assert_not_called()

    def test_session_configuration_selection_is_preserved_and_judged(self):
        (self.folder / 'configurations/config_agent-fixture').write_text('[core]\naccount=agent@example.invalid\n[auth]\nimpersonate_service_account=fixture@example.invalid\n')
        self.config.write_text('[auth]\nimpersonate_service_account=operator@example.invalid\n')
        with mock.patch.dict(os.environ, {'CLOUDSDK_ACTIVE_CONFIG_NAME':'agent-fixture'}):
            with mock.patch.object(M, 'account_allowed', return_value=True) as guard:
                with mock.patch.object(M.subprocess, 'run', side_effect=[
                    subprocess.CompletedProcess([],0,b'CANARY',b''),subprocess.CompletedProcess([],0,b'',b'')]) as run:
                    self.assertEqual(M.transfer(['gcloud','--project','example','--secret','example']),0)
        self.assertIn('--configuration=agent-fixture', guard.call_args.args[0])
        self.assertEqual(run.call_args_list[0].kwargs['env']['CLOUDSDK_ACTIVE_CONFIG_NAME'],'agent-fixture')

    def test_inherited_logging_and_command_resolution_are_not_used(self):
        with mock.patch.dict(os.environ, {'CLOUDSDK_CORE_LOG_HTTP': 'false',
                                          'PATH': '/tmp/hostile', 'PYTHONPATH': '/tmp/hostile'}):
            with mock.patch.object(M, 'account_allowed', return_value=True):
                with mock.patch.object(M.subprocess, 'run', side_effect=[
                    subprocess.CompletedProcess([], 0, b'CANARY', b'NOT_FOR_OUTPUT'),
                    subprocess.CompletedProcess([], 0, b'', b'')]) as run:
                    self.assertEqual(M.transfer(['gcloud', '--project', 'example', '--secret', 'example']), 0)
        source, sink = run.call_args_list
        self.assertEqual(source.args[0][0], '/opt/homebrew/share/google-cloud-sdk/bin/gcloud')
        self.assertIn('--no-log-http', source.args[0])
        self.assertEqual(source.kwargs['env']['CLOUDSDK_CORE_LOG_HTTP'], 'false')
        self.assertEqual(source.kwargs['env']['CLOUDSDK_CORE_DISABLE_FILE_LOGGING'], 'true')
        self.assertNotIn('PYTHONPATH', source.kwargs['env'])
        self.assertEqual(sink.args[0], ['/usr/bin/pbcopy'])
        self.assertEqual(sink.kwargs['input'], b'CANARY')
        self.assertEqual(sink.kwargs['env']['PATH'], '/usr/bin:/bin')
        self.assertFalse(source.kwargs.get('shell', False))
        self.assertFalse(sink.kwargs.get('shell', False))

    def test_keychain_and_unknown_options_are_never_automatic_sources(self):
        for args in (['security', '-w', '-s', 'review-draft'],
                     ['gcloud', '--project', 'example', '--secret', 'example', '--log-http'],
                     ['gcloud', '--project', 'example', '--secret', '../draft'],
                     ['gcloud', '--project', 'example', '--secret', 'example', '--secret', 'draft']):
            with mock.patch.object(M.subprocess, 'run') as run:
                self.assertNotEqual(M.transfer(args), 0)
                run.assert_not_called()

    def test_account_denial_source_failure_and_timeout_do_not_copy(self):
        args = ['gcloud', '--project', 'example', '--secret', 'example']
        with mock.patch.object(M, 'account_allowed', return_value=False):
            with mock.patch.object(M.subprocess, 'run') as run:
                self.assertNotEqual(M.transfer(args), 0)
                run.assert_not_called()
        for result in (subprocess.CompletedProcess([], 1, b'CANARY', b'CANARY'),
                       subprocess.TimeoutExpired('fixture', 1), OSError('fixture')):
            with mock.patch.object(M, 'account_allowed', return_value=True):
                with mock.patch.object(M.subprocess, 'run', side_effect=[result]) as run:
                    self.assertNotEqual(M.transfer(args), 0)
                    self.assertEqual(run.call_count, 1)


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0]] if sys.argv[1:] == ['--selftest'] else None)
