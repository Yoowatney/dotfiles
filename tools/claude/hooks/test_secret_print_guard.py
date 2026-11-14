#!/usr/bin/env python3
"""Regression checks for secret file output; run with --selftest.

Only synthetic command strings enter the hook. No secret file is read and no
SSH connection is made by these tests.
"""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest


GUARD = Path(__file__).with_name("secret-print-guard.py")
SPEC = importlib.util.spec_from_file_location("secret_print_guard", GUARD)
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


class GcloudSecretProjectionTests(unittest.TestCase):
    # Synthetic resources only: these strings are passed to the parser, never gcloud.
    READS = (
        ("monitoring channels list", "name,type,displayName,enabled", "labels", "sensitiveLabels", "labels.url"),
        ("monitoring channels describe fixture", "name,type,enabled", "labels", "sensitiveLabels", "sensitiveLabels.authToken"),
        ("functions describe fixture", "name,state,serviceConfig.uri", "environmentVariables", "serviceConfig", "buildConfig"),
        ("functions list", "name,state", "environmentVariables", "serviceConfig.environmentVariables", "buildConfig.environmentVariables"),
        ("run services describe fixture", "metadata.name,status.url", "spec", "spec.template", "spec.template.spec.containers"),
        ("run services list", "metadata.name,status.url", "spec", "metadata.annotations", "spec.template.spec.containers.env"),
        ("run revisions describe fixture", "metadata.name,status", "spec", "spec.containers", "spec.containers.env"),
        ("run revisions list", "metadata.name,status", "spec", "metadata", "spec.containers.env.value"),
        ("run jobs describe fixture", "metadata.name,status", "spec", "spec.template", "spec.template.spec.template.spec.containers.env"),
        ("run jobs list", "metadata.name,status", "spec", "metadata.annotations", "spec.template"),
        ("run jobs executions describe fixture", "metadata.name,status", "spec", "spec.template", "spec.template.spec.containers.env"),
        ("run jobs executions list", "metadata.name,status", "spec", "metadata", "spec.template"),
        ("compute instances describe fixture", "name,id,status", "metadata", "metadata.items", "metadata.items.value"),
        ("compute instances list", "name,id,status", "metadata", "metadata.items", "metadata.items.value"),
        ("compute instance-templates describe fixture", "name,id", "properties", "properties.metadata", "properties.metadata.items"),
        ("compute instance-templates list", "name,id", "properties", "properties.metadata", "properties.metadata.items.value"),
        ("compute project-info describe", "name,id", "commonInstanceMetadata", "commonInstanceMetadata.items", "commonInstanceMetadata.items.value"),
        ("builds describe fixture", "id,status,createTime", "substitutions", "secrets", "steps"),
        ("builds list", "id,status", "substitutions", "availableSecrets", "steps.env"),
        ("builds triggers describe fixture", "id,name,disabled", "substitutions", "webhookConfig", "build"),
        ("builds triggers list", "id,name,disabled", "substitutions", "webhookConfig.secret", "build.steps"),
        ("container clusters describe fixture", "name,status", "masterAuth", "masterAuth.clientKey", "nodeConfig.metadata"),
        ("container clusters list", "name,status", "masterAuth", "nodeConfig", "nodeConfig.metadata"),
        ("container node-pools describe fixture", "name,status,version", "config", "config.metadata", "config.metadata.value"),
        ("container node-pools list", "name,status,version", "config", "config.metadata", "config.labels"),
        ("scheduler jobs describe fixture", "name,schedule,state", "httpTarget", "httpTarget.headers", "httpTarget.body"),
        ("run worker-pools describe fixture", "metadata.name,status", "spec", "spec.template", "spec.template.spec.containers.env"),
        ("run worker-pools list", "metadata.name,status", "spec", "metadata", "metadata.annotations"),
        ("run worker-pools revisions describe fixture", "metadata.name,status", "spec", "spec.containers", "spec.containers.env"),
        ("run jobs executions tasks describe fixture", "metadata.name,status", "spec", "spec.containers", "spec.containers.env"),
        ("container aws clusters describe fixture", "name,state", "authorization", "awsRegion", "controlPlane"),
        ("container azure clusters describe fixture", "name,state", "authorization", "azureClient", "controlPlane"),
        ("container attached clusters describe fixture", "name,state", "authorization", "oidcConfig", "labels"),
        ("tasks describe fixture", "name,scheduleTime", "httpRequest", "httpRequest.headers", "httpRequest.body"),
        ("tasks list", "name,scheduleTime", "httpRequest", "appEngineHttpRequest", "httpRequest.url"),
        ("workflows describe fixture", "name,state", "sourceContents", "userEnvVars", "labels"),
        ("workflows list", "name,state", "sourceContents", "userEnvVars", "labels"),
        ("workflows executions describe fixture", "name,state", "argument", "result", "error"),
        ("workflows executions list", "name,state", "argument", "result", "error.payload"),
        ("batch jobs describe fixture", "name,uid", "taskGroups", "taskGroups.taskSpec.environment", "labels"),
        ("batch jobs list", "name,uid", "taskGroups", "taskGroups.taskSpec.runnables", "labels"),
        ("app versions describe fixture", "id,name,servingStatus", "envVariables", "buildEnvVariables", "handlers"),
        ("app versions list", "id,name,servingStatus", "envVariables", "buildEnvVariables", "deployment"),
        ("composer environments describe fixture", "name,state", "config", "config.softwareConfig.envVariables", "labels"),
        ("composer environments list", "name,state", "config", "config.softwareConfig", "labels"),
        ("pubsub subscriptions describe fixture", "name,topic", "pushConfig", "pushConfig.pushEndpoint", "labels"),
        ("pubsub subscriptions list", "name,topic", "pushConfig", "pushConfig.attributes", "labels"),
    )

    def test_unprojected_and_sensitive_gcloud_reads_are_denied(self):
        for read, safe, *unsafe in self.READS:
            for release in ("", "beta ", "alpha "):
                base = "gcloud " + release + read
                for suffix in ("", " --format=json", " --format yaml", " --format='json(*)'",
                               *(f" --format='json({field})'" for field in unsafe)):
                    with self.subTest(command=base + suffix):
                        self.assertIsNotNone(guard.bash_problem(base + suffix))

    def test_explicit_safe_projections_are_allowed(self):
        for read, safe, *_ in self.READS:
            for fmt in (f"json({safe})", f"yaml({safe})", f"table({safe})", f"value({safe})"):
                for flag in (f"--format='{fmt}'", f"--format '{fmt}'"):
                    command = f"gcloud beta --project=fixture {read} {flag}"
                    with self.subTest(command=command):
                        self.assertIsNone(guard.bash_problem(command))

    def test_safe_function_and_run_schema_fields_are_allowed(self):
        # Function v1/v2 runtime and revision names are scalars, not the env maps
        # alongside them. Cloud Run exposes metadata in v1 and top-level v2 fields.
        cases = [("functions", field) for field in (
            "name", "state", "updateTime", "runtime", "buildConfig.runtime",
            "serviceConfig.revision",
        )]
        cases += [(resource, field) for resource in (
            "run services", "run revisions", "run jobs", "run jobs executions",
            "run jobs executions tasks", "run worker-pools", "run worker-pools revisions",
        ) for field in ("name", "uid", "generation", "createTime", "updateTime")]
        for resource, field in cases:
            for verb in ("describe fixture", "list"):
                for fmt in ("value", "json", "yaml", "table"):
                    base = f"gcloud {resource} {verb}"
                    command = f"{base} --format='{fmt}({field})'"
                    with self.subTest(command=command):
                        self.assertIsNone(guard.bash_problem(command))
                    unsafe = ("serviceConfig.environmentVariables" if resource == "functions"
                              else "template.containers.env")
                    command = f"{base} --format='{fmt}({field},{unsafe})'"
                    with self.subTest(command=command):
                        self.assertIsNotNone(guard.bash_problem(command))

    def test_format_cannot_be_borrowed_or_expanded(self):
        for command in (
            "gcloud monitoring channels list | jq 'del(.labels,.sensitiveLabels)'",
            "gcloud monitoring channels list; gcloud monitoring channels list --format='value(name)'",
            "gcloud monitoring channels list --format='json(name,labels)'",
            "gcloud monitoring channels list --format='json(name)' --format=json",
            "gcloud monitoring channels list --format='json(name)' --flags-file=fixture.yaml",
            "gcloud monitoring channels list --format='json(name)' --log-http",
            "CLOUDSDK_CORE_LOG_HTTP=true gcloud monitoring channels list --format='json(name)'",
            "gcloud monitoring channels list --format='$FORMAT'",
            "gcloud monitoring channels list --format='json(name,)'",
            "gcloud monitoring channels list --format='json(name).foo(labels)'",
            "gcloud functions describe fixture --format='json(serviceConfig)'",
            "gcloud run services describe fixture --format='json(metadata)'",
            "gcloud monitoring channels list --format='table(name,labels.url:label=name)'",
            "gcloud functions describe help",
            "(/opt/sdk/bin/gcloud beta monitoring channels list)",
            "gcloud --project fixture --quiet monitoring channels list",
            "gcloud scheduler jobs describe fixture --format='value(httpTarget)'",
            "gcloud scheduler jobs list --format='value(httpTarget.body)'",
            "gcloud scheduler jobs list --format=yaml",
            "timeout 5 bash -lc 'gcloud beta monitoring channels list'",
            "ssh fixture-host 'gcloud monitoring channels describe fixture'",
            "R=$(gcloud monitoring channels list); echo $R",
            "R=$(gcloud functions describe fixture); printf '%s' \"${R:0:8}\"",
            'echo "$(gcloud monitoring channels list)"',
            'printf "%s" "`gcloud functions describe fixture`"',
            "R=$(gcloud monitoring channels list); echo ${#R}",
            "R=$(gcloud functions describe fixture)",
            'echo "R=$(gcloud monitoring channels list)"',
            "bash -c 'echo \"$(gcloud monitoring channels list)\"'",
            'cat <<EOF\n$(gcloud monitoring channels list)\nEOF',
            'export CLOUDSDK_CORE_LOG_HTTP=1; gcloud functions describe fixture --format="value(name)"',
        ):
            with self.subTest(command=command):
                self.assertIsNotNone(guard.bash_problem(command))

    def test_help_captures_and_ordinary_reads_remain_allowed(self):
        for command in (
            "gcloud monitoring channels list --help",
            "gcloud help functions describe",
            "gcloud config get-value project",
            "gcloud projects list", "gcloud auth list", "gcloud secrets list",
            "gcloud monitoring metrics list --format=json",
            "gcloud tasks queues list", "gcloud builds worker-pools list",
            "cat <<'EOF'\n$(gcloud monitoring channels list)\nEOF",
            "R=$(gcloud monitoring channels list --format='value(name)'); echo ${#R}",
            "R=$(gcloud functions describe fixture --format='json(name)')",
            "gcloud monitoring channels list --format='json(name, type)'",
            "gcloud --project fixture --format 'value(name)' beta monitoring channels list",
            "git commit -m 'guard gcloud monitoring channels list'",
            "rg 'gcloud monitoring channels list' tools",
        ):
            with self.subTest(command=command):
                self.assertIsNone(guard.bash_problem(command))

    def test_review_shell_and_logging_bypasses(self):
        for command in (
            r'bash -c "echo \"\$(gcloud monitoring channels list)\""',
            "bash --noprofile -c 'gcloud monitoring channels list'",
            "bash -euo pipefail -c 'gcloud monitoring channels list'",
            "bash -eo pipefail -c 'gcloud monitoring channels list'",
            "bash +x -c 'gcloud monitoring channels list'",
            "bash -c -e 'gcloud monitoring channels list'",
            "bash <<'EOF'\ngcloud monitoring channels list\nEOF",
            "bash 2>&1 <<'EOF'\ngcloud monitoring channels list\nEOF",
            "bash -s 2>&1 <<'EOF'\ngcloud monitoring channels list\nEOF",
            "timeout 30 bash 2>&1 <<'EOF'\ngcloud monitoring channels list\nEOF",
            "ssh fixture-host bash -s 2>&1 <<'EOF'\ngcloud monitoring channels list\nEOF",
            "ssh fixture-host 2>&1 <<'EOF'\ngcloud monitoring channels list\nEOF",
            "cat <<'EOF' 2>&1 | bash\ngcloud monitoring channels list\nEOF",
            "cat <<'EOF' |& bash\ngcloud monitoring channels list\nEOF",
            "timeout 30 bash <<'EOF'\ngcloud monitoring channels list\nEOF",
            "env FOO=1 bash <<'EOF'\ngcloud monitoring channels list\nEOF",
            "ssh fixture-host bash -s <<'EOF'\ngcloud monitoring channels list\nEOF",
            "ssh fixture-host <<'EOF'\ngcloud monitoring channels list\nEOF",
            'declare -x CLOUDSDK_CORE_LOG_HTTP=1; gcloud functions describe fixture --format="value(name)"',
            'typeset -x CLOUDSDK_CORE_LOG_HTTP=true; gcloud functions describe fixture --format="value(name)"',
            'gcloud config set core/log_http true',
            'gcloud config set core/log_http 1; gcloud functions describe fixture --format="value(name)"',
        ):
            with self.subTest(command=command):
                self.assertIsNotNone(guard.bash_problem(command))
        for command in (
            r'''pane-tell w "\"\$(x)\"' gcloud functions describe x"''',
            'gcloud config set core/log_http false',
            'gcloud config get-value core/log_http',
            r"bash -c 'echo \$(gcloud monitoring channels list)'",
            "bash scripts/fixture.sh -c 'gcloud monitoring channels list'",
            "ssh fixture-host cat <<'EOF'\ngcloud monitoring channels list\nEOF",
            "bash scripts/check.sh && git commit -F - <<'EOF'\nfix: guard gcloud monitoring channels list\nEOF",
            "sh -c true; cat <<'EOF'\nnote: gcloud functions describe fixture\nEOF",
            "bash -c true | cat > /tmp/fixture <<'EOF'\nnote: gcloud monitoring channels list\nEOF",
        ):
            with self.subTest(command=command):
                self.assertIsNone(guard.bash_problem(command))

    def test_hook_protocol_blocks_synthetic_gcloud_read(self):
        result = subprocess.run(
            [sys.executable, "-I", str(GUARD)],
            input=json.dumps({"tool_name": "Bash", "tool_input": {
                "command": "gcloud beta monitoring channels list --project=fixture"}}),
            text=True, capture_output=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout or "{}").get("hookSpecificOutput", {}).get("permissionDecision"), "deny")


class SecretFileOutputTests(unittest.TestCase):
    def test_content_readers_are_denied_locally_and_over_ssh(self):
        paths = (
            ".envrc", ".env", ".env.production", ".env.*",
            ".env*", "*.env", "*/.env*", "**/.env*", "project-*.env", "*.env.*",
            ".envrc*", ".envrc.*", "*.envrc*",
            "/workspace/project/.envrc", "credentials.json",
            "service-account.json", "private.pem", "~/.ssh/id_ed25519",
        )
        for path in paths:
            for reader in ("cat", "head -2", "sed -n '1,8p'", "less"):
                local = f"{reader} {path}"
                for command in (local, "ssh fixture-host " + repr(local)):
                    with self.subTest(command=command):
                        self.assertIsNotNone(guard.bash_problem(command))

    def test_incident_command_shape_is_denied(self):
        command = (
            "cat .envrc\n"
            "sed -n '1,150p' .claude/API.md\n"
            "rg -n 'viewer|readOnly' Infrastructure -g '*.tf' | head -70\n"
            "rg --files /workspace | rg 'use-account.sh$'"
        )
        self.assertIsNotNone(guard.bash_problem(command))

    def test_remote_shell_wrappers_and_ssh_options_are_denied(self):
        for command in (
            'ssh -p 2222 -o BatchMode=yes fixture-host "cat .envrc"',
            'ssh -i /tmp/fixture-key -J fixture-jump fixture-host "head .env"',
            'ssh -- fixture-host "less credentials.json"',
            '/usr/bin/ssh fixture-host "bash -lc \'cat .envrc\'"',
            'ssh fixture-host cat .envrc',
            'ssh fixture-host "true; cat .envrc"',
            "ssh fixture-host 'V=$(cat .envrc); echo $V'",
            "bash -lc 'cat .envrc'",
            "cat .env.example.production",
            "cat .env.example?",
        ):
            with self.subTest(command=command):
                self.assertIsNotNone(guard.bash_problem(command))

    def test_examples_counts_and_existence_remain_allowed(self):
        for command in (
            "cat .env.example", "head .env.sample", "less .env.template",
            "cat app.env.example", "head app.env.sample", "less app.env.template",
            "grep -c TOKEN .envrc", "rg --count TOKEN .env.production",
            "grep -c TOKEN .env*", "rg --count TOKEN *.env", "test -f app.env",
            "grep -q TOKEN credentials.json", "test -f .envrc",
            "[ -e .env ] && echo exists", "ls -l .envrc", "stat .env",
            "source .envrc", "VALUE=$(cat .envrc)",
            'ssh fixture-host "cat .env.example"',
            'ssh -p 2222 fixture-host "grep -c TOKEN .envrc"',
            'ssh fixture-host "test -f .envrc"',
            "ssh fixture-host 'V=$(cat .envrc); echo ${#V}'",
            "V=$(ssh fixture-host 'cat .envrc')",
            "git commit -m 'block ssh fixture-host cat .envrc'",
        ):
            with self.subTest(command=command):
                self.assertIsNone(guard.bash_problem(command))

    def test_read_and_grep_tools_deny_content_but_allow_counts(self):
        for tool, inp, denied in (
            ("Read", {"file_path": "/workspace/.envrc"}, True),
            ("Read", {"file_path": "/workspace/.env.example"}, False),
            ("Grep", {"path": "/workspace/.envrc", "output_mode": "content"}, True),
            ("Grep", {"path": "/workspace/.envrc", "output_mode": "count"}, False),
        ):
            with self.subTest(tool=tool, input=inp):
                self.assertEqual(guard.tool_problem({"tool_name": tool, "tool_input": inp}) is not None, denied)

    def test_hook_protocol_returns_deny_before_any_command_runs(self):
        for command in ("cat /tmp/fixture/.envrc", "cat .env*", "cat *.env", "cat */.env*"):
            with self.subTest(command=command):
                payload = {"tool_name": "Bash", "tool_input": {"command": command}}
                result = subprocess.run(
                    [sys.executable, "-I", str(GUARD)], input=json.dumps(payload),
                    text=True, capture_output=True, check=True,
                )
                response = json.loads(result.stdout or "{}")
                self.assertEqual(response.get("hookSpecificOutput", {}).get("permissionDecision"), "deny")


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
