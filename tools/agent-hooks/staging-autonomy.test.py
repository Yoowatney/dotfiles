"""Ordinary staging approval requests are rejected; protected scopes remain decisions."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SHARE = load("share_staging", ROOT / "agent-hooks/yoyoo-share-guard.py")
GCP = load("gcp_staging", ROOT / "claude/hooks/gcp-account-guard.py")


class StagingAutonomy(unittest.TestCase):
    def test_stop_rejects_ordinary_staging_approval_questions(self):
        for text in ("May I merge this staging-only Terraform IAM PR?",
                     "staging 배포를 승인해 주세요.", "staging 설정을 바꿔도 될까요?",
                     "staging IAM 승인 부탁드립니다.", "고를 것: staging IAM 머지 여부"):
            with self.subTest(text=text):
                reason = SHARE.decide({"last_assistant_message": text}, {})
                self.assertIsNotNone(reason)
                self.assertIn("staging", reason.lower())

    def test_protected_questions_are_not_called_ordinary_staging(self):
        for text in ("May I delete staging data?", "May I change production IAM?",
                     "May I change staging billing?", "May I change external service settings for staging?",
                     "staging 데이터를 복구 불가능하게 바꿔도 될까요?"):
            with self.subTest(text=text):
                self.assertIsNone(SHARE.staging_approval_reason(text))

    def test_staging_status_is_not_an_approval_request(self):
        for text in ("staging IAM PR checks passed. Merging next.",
                     "staging 승인 요청 차단 시험이 통과했습니다."):
            self.assertIsNone(SHARE.staging_approval_reason(text))

    def test_key_approval_is_not_an_ordinary_staging_request(self):
        for text in ("May I list service account keys in staging?",
                     "staging 서비스계정키 조회를 승인해 주세요."):
            self.assertIsNone(SHARE.staging_approval_reason(text))

    def test_separate_production_status_cannot_hide_a_staging_question(self):
        self.assertIsNotNone(SHARE.staging_approval_reason(
            "Production deployment remains held.\n\nMay I merge this staging IAM PR?"))

    def test_identity_guard_never_suggests_an_unlock_for_ordinary_staging(self):
        project = sorted(GCP.STAGING_IDS)[-1]
        command = f"gcloud --account=operator@example.invalid run services update trial --project={project} --set-env-vars=MODE=trial"
        reason = GCP.tool_problem({"tool_name": "Bash", "tool_input": {"command": command}}, {}, prompt="")
        self.assertTrue(reason)
        self.assertNotIn(GCP.STAGING_UNLOCK, reason)

    def test_destructive_staging_keeps_its_approval_boundary(self):
        project = sorted(GCP.STAGING_IDS)[-1]
        command = f"gcloud --account=operator@example.invalid firestore databases delete trial --project={project}"
        reason = GCP.tool_problem({"tool_name": "Bash", "tool_input": {"command": command}}, {}, prompt="")
        self.assertIn(GCP.STAGING_UNLOCK, reason)

    def test_infrastructure_removal_does_not_use_a_data_destruction_grant(self):
        project = sorted(GCP.STAGING_IDS)[-1]
        self.assertFalse(GCP.destructive_staging(
            f"gcloud --account=operator@example.invalid run services delete trial --project={project}"))

    def test_flag_values_and_resource_names_do_not_turn_reads_into_deletion(self):
        project = sorted(GCP.STAGING_IDS)[-1]
        for suffix in ("firestore databases list --filter='name:delete'",
                       "sql databases describe delete", "storage ls gs://delete"):
            self.assertFalse(GCP.destructive_staging(
                f"gcloud --account=operator@example.invalid {suffix} --project={project}"))


if __name__ == "__main__":
    unittest.main()
