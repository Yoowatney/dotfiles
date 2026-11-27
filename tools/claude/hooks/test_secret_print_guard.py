#!/usr/bin/env python3
"""Regression checks for secret file output; run with --selftest.

Only synthetic command strings enter the hook. No secret file is read and no
SSH connection is made by these tests.
"""
import importlib.util
import json
from pathlib import Path
import shlex
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

    def test_function_config_safe_schema_fields_are_allowed(self):
        # Rejecting a config parent must not reject its non-secret children.
        # Literals are reviewed against the Functions v2/alpha/beta schema;
        # expectations do not come from the guard's field policy.
        safe = {
            "serviceConfig": (
                "allTrafficOnLatestRevision availableCpu availableMemory binaryAuthorizationPolicy "
                "directVpcEgress directVpcNetworkInterface directVpcNetworkInterface.network "
                "directVpcNetworkInterface.subnetwork directVpcNetworkInterface.tags ingressSettings "
                "maxInstanceCount maxInstanceRequestConcurrency minInstanceCount revision securityLevel "
                "service serviceAccountEmail timeoutSeconds uri vpcConnector vpcConnectorEgressSettings"
            ).split(),
            "buildConfig": (
                "automaticUpdatePolicy build dockerRegistry dockerRepository entryPoint "
                "onDeployUpdatePolicy onDeployUpdatePolicy.runtimeVersion runtime serviceAccount workerPool "
                "source.repoSource source.repoSource.branchName source.repoSource.commitSha "
                "source.repoSource.dir source.repoSource.projectId source.repoSource.repoName "
                "source.repoSource.tagName source.storageSource.bucket source.storageSource.generation "
                "source.storageSource.object sourceProvenance.resolvedRepoSource "
                "sourceProvenance.resolvedRepoSource.branchName sourceProvenance.resolvedRepoSource.commitSha "
                "sourceProvenance.resolvedRepoSource.dir sourceProvenance.resolvedRepoSource.projectId "
                "sourceProvenance.resolvedRepoSource.repoName sourceProvenance.resolvedRepoSource.tagName "
                "sourceProvenance.resolvedStorageSource.bucket "
                "sourceProvenance.resolvedStorageSource.generation sourceProvenance.resolvedStorageSource.object"
            ).split(),
        }
        for parent, children in safe.items():
            for field in children:
                for release in ("", "beta ", "alpha "):
                    for verb in ("describe fixture", "list"):
                        for fmt in ("value", "json", "yaml", "table"):
                            base = f"gcloud {release}functions {verb} --format='{fmt}"
                            command = f"{base}({parent}.{field})'"
                            with self.subTest(command=command):
                                self.assertIsNone(guard.bash_problem(command))
                            command = f"{base}({parent}.{field},{parent}.environmentVariables)'"
                            with self.subTest(command=command):
                                self.assertIsNotNone(guard.bash_problem(command))

    def test_function_config_secrets_parents_and_unknown_fields_are_denied(self):
        unsafe = (
            "serviceConfig buildConfig serviceConfig.environmentVariables "
            "serviceConfig.environmentVariables.FIXTURE serviceConfig.secretEnvironmentVariables "
            "serviceConfig.secretEnvironmentVariables.secret serviceConfig.secretVolumes "
            "serviceConfig.secretVolumes.versions buildConfig.environmentVariables "
            "buildConfig.environmentVariables.FIXTURE buildConfig.sourceToken buildConfig.source "
            "buildConfig.source.gitUri buildConfig.source.storageSource "
            "buildConfig.source.storageSource.sourceUploadUrl buildConfig.sourceProvenance "
            "buildConfig.sourceProvenance.gitUri buildConfig.sourceProvenance.resolvedStorageSource "
            "buildConfig.sourceProvenance.resolvedStorageSource.sourceUploadUrl "
            "serviceConfig.futureField buildConfig.futureField serviceConfig.timeoutSeconds.secret "
            "buildConfig.runtime.environmentVariables"
        ).split()
        for field in unsafe:
            for release in ("", "beta ", "alpha "):
                for verb in ("describe fixture", "list"):
                    for fmt in ("value", "json", "yaml", "table"):
                        for fields in (field, f"serviceConfig.timeoutSeconds,{field}"):
                            command = f"gcloud {release}functions {verb} --format='{fmt}({fields})'"
                            with self.subTest(command=command):
                                self.assertIsNotNone(guard.bash_problem(command))

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


class BrowserAndTranscriptOutputTests(unittest.TestCase):
    # These are command strings only. Never run a browser or read a transcript.
    TRANSCRIPTS = (
        "/Users/fixture/.codex/sessions/2026/01/01/rollout-fixture.jsonl",
        "/Users/fixture/.codex/archived_sessions/rollout-fixture.jsonl",
        "/Users/fixture/.claude/projects/-workspace/fixture.jsonl",
        "/Users/fixture/.claude/projects/-workspace/fixture/subagents/agent-fixture.jsonl",
    )
    RAW_BROWSER = (
        "ego-browser nodejs <<'JS'\n"
        "const task=await taskSpace(1), page=task.page('p1');\n"
        "const r=await page.fetch('/api/products/fixture'); "
        "const d=JSON.parse(r.body); console.log({status:r.status,data:d});\nJS"
    )

    def test_browser_raw_fetch_output_is_denied(self):
        for script in (
            "const r=await page.fetch('/api/fixture'); console.log(r);",
            "console.log(await page.fetch('/api/fixture'));",
            "const r=await fetch('/api/fixture'); console.log(await r.json());",
            "console.log(await page.evaluate(async()=>{const r=await fetch('/api/fixture'); return r.json()}));",
            "const r=await page.fetch('/api/fixture'); console.dir(JSON.parse(r.body));",
            "const r=await page.fetch('/api/fixture'); process.stdout.write(r.body);",
            "const r=await page.fetch('/api/fixture'); console.trace(r);",
            "const r=await page.fetch('/api/fixture'); console.group(r);",
            "const r=await page.fetch('/api/fixture'); console.assert(false, r);",
            "const r=await page.fetch('/api/fixture'); console['log'](r);",
        ):
            command = "ego-browser nodejs <<'JS'\n" + script + "\nJS"
            with self.subTest(script=script):
                self.assertIsNotNone(guard.bash_problem(command))
        self.assertIsNotNone(guard.tool_problem({
            "tool_name": "Bash", "tool_input": {"command": self.RAW_BROWSER}}))

    def test_inline_and_shell_wrapped_browser_output_is_denied(self):
        script = "const r=await page.fetch('/api/fixture'); console.log(r.body)"
        for command in (
            "ego-browser nodejs -e " + repr(script),
            "bash -lc " + shlex.quote("ego-browser nodejs -e " + shlex.quote(script)),
            "/usr/local/bin/ego-browser nodejs <<'JS'\n" + script + "\nJS",
            "ego-browser nodejs <<'JS'\nconsole.log(require('fs').readFileSync('/Users/fixture/.claude.json','utf8'))\nJS",
        ):
            with self.subTest(command=command):
                self.assertIsNotNone(guard.bash_problem(command))

    def test_python_transcript_replay_is_denied(self):
        for path in self.TRANSCRIPTS:
            command = (
                "uv run python - <<'PY'\nimport json\nfrom pathlib import Path\n"
                f"p=Path('{path}');ls=p.read_text().splitlines()\n"
                "for n in [4,8]:\n d=json.loads(ls[n-1]);o=d['payload']['output'];print(n,o)\nPY"
            )
            with self.subTest(path=path):
                self.assertIsNotNone(guard.tool_problem({
                    "tool_name": "Bash", "tool_input": {"command": command}}))

    def test_other_transcript_content_readers_are_denied(self):
        for path in self.TRANSCRIPTS:
            for reader in ("cat", "head -2", "rg -n output", "jq .", "nodejs dump.js"):
                with self.subTest(path=path, reader=reader):
                    self.assertIsNotNone(guard.bash_problem(f"{reader} {path}"))
            self.assertIsNotNone(guard.tool_problem({
                "tool_name": "Read", "tool_input": {"file_path": path}}))
            self.assertIsNotNone(guard.tool_problem({
                "tool_name": "Grep", "tool_input": {"path": path, "output_mode": "content"}}))

    def test_transcript_directory_search_is_denied(self):
        for path in ("~/.codex/sessions", "~/.codex/sessions/2026/01", "~/.codex/archived_sessions",
                     "~/.claude/projects", "~/.claude/projects/-workspace",
                     "~/.claude/projects/-workspace/fixture/subagents"):
            with self.subTest(path=path):
                self.assertIsNotNone(guard.bash_problem("rg -n output " + path))
                self.assertIsNone(guard.bash_problem("rg --files " + path))
                self.assertIsNotNone(guard.tool_problem({"tool_name": "Grep", "tool_input": {
                    "path": path, "output_mode": "content"}}))
                self.assertIsNone(guard.tool_problem({"tool_name": "Grep", "tool_input": {
                    "path": path, "output_mode": "count"}}))

    def test_captured_browser_response_cannot_be_echoed(self):
        command = "R=$(ego-browser nodejs -e \"const r=await page.fetch('/api/fixture'); console.log(r)\"); echo $R"
        self.assertIsNotNone(guard.bash_problem(command))

    def test_normal_browser_and_safe_fetch_summaries_remain_allowed(self):
        for script in (
            "const task=await taskSpace(1); console.log(await task.page('p1').domSnapshot());",
            "console.log('Use fetch() to read an API response');",
            "const r=await page.fetch('/api/fixture'); console.log({status:r.status,ok:r.ok});",
            "const r=await page.fetch('/api/fixture'); const d=JSON.parse(r.body); console.log({count:d.items.length,present:!!d.value,type:typeof d.value});",
            "const r=await page.fetch('/api/fixture'); await page.fill('#value', JSON.parse(r.body).value); console.log('filled');",
        ):
            command = "ego-browser nodejs <<'JS'\n" + script + "\nJS"
            with self.subTest(script=script):
                self.assertIsNone(guard.bash_problem(command))
        self.assertIsNone(guard.bash_problem("R=$(" + self.RAW_BROWSER.replace("nodejs ", "nodejs 2>&1 ", 1) + "\n)"))

    def test_browser_capture_without_stderr_is_denied(self):
        # ego-browser nodejs prints console output on stderr: $() alone captures nothing, so the value meant for the
        # variable reaches the chat (2026-11-20, a Slack bot token read from its settings page).
        script = "console.log('X=' + await page.evaluate(() => document.querySelector('input').value))"
        for command in (
            "T=$(ego-browser nodejs <<'JS' | sed -n 's/^X=//p'\n" + script + "\nJS\n)",
            "T=$(ego-browser nodejs -e " + shlex.quote(script) + ")",
            "T=`ego-browser nodejs -e " + shlex.quote(script) + "`",
            "R=$(" + self.RAW_BROWSER + "\n)",
        ):
            with self.subTest(command=command):
                self.assertIsNotNone(guard.bash_problem(command))
        for command in (
            "T=$(ego-browser nodejs 2>&1 <<'JS' | sed -n 's/^X=//p'\n" + script + "\nJS\n)",
            "T=$(ego-browser nodejs <<'JS' 2>&1 | sed -n 's/^X=//p'\n" + script + "\nJS\n)",
            "T=$(ego-browser nodejs -e " + shlex.quote(script) + " 2>&1)",
        ):
            with self.subTest(command=command):
                self.assertIsNone(guard.bash_problem(command))

    def test_transcript_counts_and_unrelated_files_remain_allowed(self):
        for path in self.TRANSCRIPTS:
            for command in (f"rg --count output {path}", f"grep -q output {path}",
                            f"ls -l {path}", f"test -f {path}", f"wc -l {path}"):
                with self.subTest(command=command):
                    self.assertIsNone(guard.bash_problem(command))
        for command in (
            "cat /workspace/fixtures/events.jsonl",
            "cat /Users/fixture/.claude/projects/-workspace/memory/MEMORY.md",
            "git commit -m 'protect ~/.codex/sessions/fixture.jsonl'",
            "cat <<'EOF'\nego-browser nodejs; console.log(await fetch('/api/fixture'))\nEOF",
        ):
            with self.subTest(command=command):
                self.assertIsNone(guard.bash_problem(command))

    def test_hook_protocol_blocks_both_shapes_without_execution(self):
        for command in (self.RAW_BROWSER, f"cat {self.TRANSCRIPTS[0]}"):
            result = subprocess.run(
                [sys.executable, "-I", str(GUARD)],
                input=json.dumps({"tool_name": "Bash", "tool_input": {"command": command}}),
                text=True, capture_output=True, check=True,
            )
            self.assertEqual(json.loads(result.stdout or "{}").get(
                "hookSpecificOutput", {}).get("permissionDecision"), "deny")

    def test_only_installed_isolated_user_reader_is_allowed(self):
        helper = str(Path.home() / '.claude/hooks/session-user-messages.py')
        path = self.TRANSCRIPTS[0]
        for command in (f'python3 -I {helper} {path}',
                        f'uv run --no-project python -I {helper} {path}'):
            with self.subTest(command=command):
                self.assertIsNone(guard.bash_problem(command))
        for command in (f'python3 {helper} {path}',
                        f'python3 -I /tmp/session-user-messages.py {path}',
                        f'python3 -I {helper} --raw {path}',
                        f'python3 -I {helper} {path}; cat {path}',
                        f'python3 -I {helper} {path} /workspace/other.jsonl',
                        f'node -e "console.log(require(\"fs\").readFileSync(\"{path}\"))"',
                        f'jq . {path}'):
            with self.subTest(command=command):
                self.assertIsNotNone(guard.bash_problem(command))


class BrowserPageStateOutputTests(unittest.TestCase):
    # Parser inputs only: no browser, page, account or real credential is used.
    NEXT_READ = (
        "const data=await page.evaluate(() => JSON.parse("
        "document.getElementById('__NEXT_DATA__').textContent)); "
    )
    DENIED = (
        "window.__NEXT_DATA__",
        "__NEXT_DATA__",
        "window.__NEXT_DATA__\n.props.pageProps",
        "await\npage.evaluate(() => window.__NEXT_DATA__)",
        "window.__NEXT_DATA__.props.pageProps;",
        "JSON.stringify(window.__NEXT_DATA__.props.pageProps)",
        "await page.evaluate(() => window.__NEXT_DATA__)",
        "await page.evaluate(() => window.__NEXT_DATA__.props.pageProps)",
        NEXT_READ + "data",
        NEXT_READ + "data.props.pageProps; // implicit output",
        NEXT_READ + "(\nJSON.stringify(data.props.pageProps)\n)",
        "const data=window.__NEXT_DATA__\ndata.props.pageProps",
        NEXT_READ + "console.log('loaded');\ndata.props.pageProps",
        NEXT_READ + "const {props:{pageProps:pp}}=data; console.log(pp);",
        NEXT_READ + "const {props:{pageProps:pp}}=data; pp",
        NEXT_READ + "const {props:{pageProps:{settings:cfg}}}=data; console.log(cfg);",
        "const raw=await page.locator('#__NEXT_DATA__').textContent(); console.log(raw);",
        "console.log(await page.textContent('#__NEXT_DATA__'));",
        "console.log(await page.innerText('#__NEXT_DATA__'));",
        "console.log(await page.locator('script#__NEXT_DATA__').innerText());",
        "console.log(await page.$eval('#__NEXT_DATA__', el => el.textContent));",
        "console.log(await page.evaluate(() => document.querySelector('#__NEXT_DATA__').innerText));",
        "const el=page.locator('#__NEXT_DATA__'); console.log(await el.textContent());",
        NEXT_READ + "console.trace(data);",
        NEXT_READ + "console.group(data);",
        NEXT_READ + "console.assert(false, data);",
        NEXT_READ + "console['log'](data);",
        NEXT_READ + 'console["log"](data);',
        NEXT_READ + "console.log(data);",
        NEXT_READ + "console.log(data.props.pageProps);",
        NEXT_READ + "console.log(JSON.stringify(data.props.pageProps.settings));",
        NEXT_READ + "const settings=data.props.pageProps.settings; console.dir(settings);",
        NEXT_READ + "const settings=data.props.pageProps.settings; console.log({settings});",
        NEXT_READ + "const {settings}=data.props.pageProps; console.table(settings);",
        "console.log(await page.evaluate(() => window.__NEXT_DATA__));",
        "const d=await page.evaluate(() => window.__NUXT__); console.info(d.config);",
        "const d=await page.evaluate(() => window['__NUXT__']); console.warn(d);",
        "const d=await page.evaluate(() => window.__INITIAL_STATE__); console.error(d.user);",
        "const d=await page.evaluate(() => globalThis.__PRELOADED_STATE__); process.stdout.write(JSON.stringify(d));",
        "const d=await page.evaluate(() => window.__APOLLO_STATE__); process.stderr.write(JSON.stringify(d));",
        "console.log(await page.evaluate(() => JSON.parse(document.querySelector('#__NEXT_DATA__').textContent).props.pageProps.settings));",
        NEXT_READ + "console.log({data:data});",
        NEXT_READ + "console.log({...data.props.pageProps});",
        "const d=\nawait page.evaluate(() => window.__NUXT__); console.log(d);",
        "const d=await page.evaluate('window.__INITIAL_STATE__'); console.log(d);",
        "console.log(await page.evaluate(() => {if (ready) return window.__NUXT__; return {title:window.__NUXT__.title};}));",
        "console.log(await page.evaluate(() => ({title:window.__NUXT__.title})) + window.__NUXT__);",
        "console.log(await page.evaluate(() => JSON.parse(document.querySelector('script#__NEXT_DATA__').textContent)));",
        NEXT_READ + "console.log('state', data.props.pageProps);",
        "console.log(await page.evaluate(function () {return window.__NUXT__;}));",
        # api#1662: the event list and cookie reads carry IAP/session tokens in headers and values.
        "console.log(page.events());",
        "console.log(JSON.stringify(await page.events()));",
        "const ev=await page.events(); console.log(ev);",
        "const ev=page.events(); console.log(ev.slice(-5));",
        "const ev=page.events(); const last=ev[ev.length-1]; console.log(last);",
        "await page.events()",
        "console.log(await page.evaluate(() => document.cookie));",
        "const c=await page.evaluate('document.cookie'); console.log(c);",
        "const r=await page.cdp('Network.getCookies'); console.log(r);",
        "const r=await page.cdp(\"Network.getAllCookies\", {}); console.log(JSON.stringify(r.cookies));",
        "const r=await task.cdp('Storage.getCookies'); r",
        "const cs=await page.context().cookies(); console.log(cs);",
    )
    ALLOWED = (
        "await page.content()",
        "await page.screenshot({path:'/tmp/fixture.png'})",
        "await page.locator('h1').textContent()",
        "await page.innerText('h1')",
        "await page.$eval('h1', el => el.textContent)",
        "await page.evaluate(() => document.title)",
        "const data=window.__NEXT_DATA__",
        "const data=\nawait page.evaluate(() => window.__NEXT_DATA__)",
        "const data=window.__NEXT_DATA__;\nconst summary=\n({title:data.props.pageProps.title}); summary",
        NEXT_READ + "({title:data.props.pageProps.title})",
        NEXT_READ + "JSON.stringify({count:data.props.pageProps.items.length})",
        "await page.evaluate(() => ({title:window.__NEXT_DATA__.props.pageProps.title}))",
        NEXT_READ + "console['log']({count:data.props.pageProps.items.length});",
        NEXT_READ + "console.trace({present:!!data.props.pageProps});",
        NEXT_READ + "console.log(await page.locator('h1').textContent());",
        NEXT_READ + "console.log(await page.evaluate(() => document.title));",
        NEXT_READ + "console.log({title:data.props.pageProps.title,enabled:data.props.pageProps.enabled});",
        NEXT_READ + "console.log({count:data.props.pageProps.items.length,present:!!data.props.pageProps.settings,type:typeof data.props.pageProps.settings});",
        NEXT_READ + "const summary={title:data.props.pageProps.title}; console.log(summary);",
        "const summary=await page.evaluate(() => ({title:window.__NEXT_DATA__.props.pageProps.title})); console.log(summary);",
        "console.log(await page.evaluate(() => ({title:window.__INITIAL_STATE__.title})));",
        "console.log(await page.content());",
        "await page.screenshot({path:'/tmp/fixture.png'});",
        NEXT_READ + "console.log(await page.content()); await page.screenshot({path:'/tmp/fixture.png'});",
        "console.log('__NEXT_DATA__ is a script element');",
        "// window.__NUXT__ is mentioned in a comment\nconsole.log(await page.content());",
        "const d=await page.evaluate(() => document.querySelector('#fixture').textContent); console.log(d);",
        NEXT_READ + "console.log('loaded');",
        "const summary=await page.evaluate(() => {const d=window.__NUXT__; return {title:d.title};}); console.log(summary);",
        "const summary=await page.evaluate('({title:window.__INITIAL_STATE__.title})'); console.log(summary);",
        NEXT_READ + "console.log({title:data?.props?.pageProps?.title});",
        "console.log(await page.evaluate(function () {return {title:window.__NUXT__.title};}));",
        "console.log(page.events().length);",
        "const ev=page.events(); console.log({count:ev.length});",
        "console.log(await page.evaluate(() => document.cookie.length));",
        "console.log(await page.evaluate(() => typeof document.cookie));",
        "const r=await page.cdp('Network.getCookies'); console.log({count:r.cookies.length});",
        "console.log('page.events() and document.cookie are secret sources');",
        "const r=await page.cdp('Page.getLayoutMetrics'); console.log(r);",
        "await page.cdp('Network.clearBrowserCookies');",
    )

    def test_whole_page_state_and_child_objects_are_denied(self):
        for script in self.DENIED:
            with self.subTest(script=script):
                self.assertIsNotNone(guard.bash_problem("ego-browser nodejs <<'JS'\n" + script + "\nJS"))

    def test_explicit_projections_and_common_qa_remain_allowed(self):
        for script in self.ALLOWED:
            with self.subTest(script=script):
                self.assertIsNone(guard.bash_problem("ego-browser nodejs <<'JS'\n" + script + "\nJS"))

    def test_inline_shell_wrapped_and_captured_state_output(self):
        inline = "ego-browser nodejs -e " + shlex.quote(self.DENIED[2])
        for command in (inline, "bash -lc " + shlex.quote(inline)):
            with self.subTest(command=command):
                self.assertIsNotNone(guard.bash_problem(command))
        self.assertIsNone(guard.bash_problem("R=$(" + inline + " 2>&1)"))

    def test_session_final_expression_output(self):
        for script in self.DENIED:
            with self.subTest(script=script):
                command = "ego-browser nodejs --session fixture -e " + shlex.quote(script)
                self.assertIsNotNone(guard.bash_problem(command))
        for script in self.ALLOWED:
            with self.subTest(script=script):
                command = "ego-browser nodejs --session fixture -e " + shlex.quote(script)
                self.assertIsNone(guard.bash_problem(command))

    def test_claude_and_codex_hook_protocol_before_execution(self):
        # Codex's shared hook adapter normalizes shell calls to Bash. Also cover
        # native exec_command input so the guard remains safe without that adapter.
        for tool, key in (("Bash", "command"), ("exec_command", "cmd")):
            cases = [(script, True) for script in self.DENIED]
            cases += [(script, False) for script in self.ALLOWED]
            for script, denied in cases:
                with self.subTest(tool=tool, denied=denied, script=script):
                    result = subprocess.run(
                        [sys.executable, "-I", str(GUARD)],
                        input=json.dumps({"tool_name": tool, "tool_input": {
                            key: "ego-browser nodejs <<'JS'\n" + script + "\nJS"}}),
                        text=True, capture_output=True, check=True,
                    )
                    decision = json.loads(result.stdout or "{}").get(
                        "hookSpecificOutput", {}).get("permissionDecision")
                    self.assertEqual(decision, "deny" if denied else None)


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
