"""Tests for the phishing-incident-triage agent.

Each test calls the agent as a subprocess — the way the orchestrator does —
so stdout hygiene and exit codes are covered rather than assumed.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parent.parent
ENTRYPOINT = AGENT_DIR / "agent_main.py"
PROTOCOL = "agentcall/v1"


def call(capability: str, payload: dict | None = None) -> dict:
    """Invoke the agent as a subprocess and return its envelope."""
    request = json.dumps({"protocol": PROTOCOL, "capability": capability, "input": payload or {}})
    proc = subprocess.run(
        [sys.executable, str(ENTRYPOINT)],
        input=request,
        capture_output=True,
        text=True,
        cwd=AGENT_DIR,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, f"agent exited {proc.returncode}\n{proc.stderr}"
    return json.loads(proc.stdout)


# ---------------------------------------------------------------------------
# Contract tests (hold for every agent)
# ---------------------------------------------------------------------------


class TestContract(unittest.TestCase):
    def test_describe_reports_name_and_capabilities(self) -> None:
        envelope = call("describe")
        self.assertTrue(envelope["ok"], envelope["error"])
        self.assertEqual(envelope["output"]["name"], "phishing-incident-triage")
        caps = [c["name"] for c in envelope["output"]["capabilities"]]
        self.assertIn("triage_email", caps)
        self.assertIn("describe", caps)

    def test_unknown_capability_is_a_typed_error(self) -> None:
        envelope = call("no-such-capability")
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "invalid_request")

    def test_every_response_reports_usage(self) -> None:
        envelope = call("describe")
        self.assertIn("usage", envelope)
        self.assertIn("input_tokens", envelope["usage"])

    def test_empty_stdin_fails_the_protocol_check(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(ENTRYPOINT)],
            input="",
            capture_output=True,
            text=True,
            cwd=AGENT_DIR,
            timeout=30,
            check=False,
        )
        assert proc.returncode == 0
        envelope = json.loads(proc.stdout)
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "invalid_request")

    def test_rejects_invalid_json(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(ENTRYPOINT)],
            input="not json",
            capture_output=True,
            text=True,
            cwd=AGENT_DIR,
            timeout=30,
            check=False,
        )
        assert proc.returncode == 0
        envelope = json.loads(proc.stdout)
        self.assertFalse(envelope["ok"])
        self.assertIn("valid JSON", envelope["error"]["message"])

    def test_rejects_wrong_protocol(self) -> None:
        # Send wrong protocol
        proc = subprocess.run(
            [sys.executable, str(ENTRYPOINT)],
            input=json.dumps({"protocol": "wrong/v1", "capability": "describe", "input": {}}),
            capture_output=True,
            text=True,
            cwd=AGENT_DIR,
            timeout=30,
            check=False,
        )
        assert proc.returncode == 0
        env = json.loads(proc.stdout)
        self.assertFalse(env["ok"])
        self.assertEqual(env["error"]["type"], "invalid_request")

    def test_rejects_missing_capability(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(ENTRYPOINT)],
            input=json.dumps({"protocol": PROTOCOL, "input": {}}),
            capture_output=True,
            text=True,
            cwd=AGENT_DIR,
            timeout=30,
            check=False,
        )
        assert proc.returncode == 0
        envelope = json.loads(proc.stdout)
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "invalid_request")


# ---------------------------------------------------------------------------
# triage_email tests
# ---------------------------------------------------------------------------


class TestTriageEmail(unittest.TestCase):
    """Test the triage_email capability."""

    def test_legitimate_email_scores_low(self) -> None:
        envelope = call(
            "triage_email",
            {
                "subject": "Team meeting tomorrow",
                "sender": "manager@company.com",
                "body": "Hi team, let's meet at 10am tomorrow to discuss Q3 plans.",
                "urls": ["https://company.com/calendar"],
                "attachments": ["agenda.pdf"],
                "spf": "pass",
                "dkim": "pass",
                "dmarc": "pass",
            },
        )
        self.assertTrue(envelope["ok"], envelope["error"])
        output = envelope["output"]
        self.assertLessEqual(output["risk_score"], 30)
        self.assertEqual(output["severity"], "low")
        self.assertEqual(output["classification"], "likely_legitimate")

    def test_benign_office_mail_scores_low(self) -> None:
        """Ordinary business mail must not be flagged as phishing.

        One benign case with an Office attachment and a URL whose query
        carries an '@'.  Every one of these fixtures used to false-
        positive: substring technique matches ("first" -> irs,
        "courtesy" -> court), the @-in-query and path-substring URL rules,
        and missing auth results scoring 9 points.  Today they score 15
        (two macro-capable documents) and stay in the low band.
        """
        envelope = call(
            "triage_email",
            {
                "subject": "First quarter numbers",
                "sender": "billing@acme.com",
                "body": (
                    "As a courtesy, here is the signed SOW and the Q3 invoice. "
                    "The portal link is below."
                ),
                "urls": ["https://portal.vendor.co.uk/account/invoices?email=alice@acme.com"],
                "attachments": ["invoice_4471.doc", "SOW_signed.rtf"],
            },
        )
        self.assertTrue(envelope["ok"], envelope["error"])
        output = envelope["output"]
        self.assertLessEqual(output["risk_score"], 25)
        self.assertEqual(output["severity"], "low")
        self.assertEqual(output["classification"], "likely_legitimate")
        self.assertNotIn("authority_impersonation", output["social_engineering_techniques"])
        self.assertEqual(output["suspicious_urls"], [])

    def test_obvious_phishing_scores_high(self) -> None:
        envelope = call(
            "triage_email",
            {
                "subject": "URGENT: Your account has been compromised",
                "sender": "security@paypa1-verify.tk",
                "body": (
                    "Dear customer, your account will be suspended within 24 hours. "
                    "Click here to verify your identity immediately. "
                    "Failure to comply will result in permanent account closure."
                ),
                "urls": [
                    "http://192.168.1.1/verify?user=victim@corp.com",
                    # Real credential-injection: userinfo hides the actual host.
                    "https://www.paypa1-verify.tk@192.168.1.1/secure/login",
                ],
                "attachments": ["invoice.pdf.exe"],
                "spf": "fail",
                "dkim": "fail",
                "dmarc": "fail",
            },
        )
        self.assertTrue(envelope["ok"], envelope["error"])
        output = envelope["output"]
        self.assertGreaterEqual(output["risk_score"], 60)
        self.assertIn(output["severity"], ("high", "critical"))
        self.assertNotEqual(output["classification"], "likely_legitimate")

    def test_credential_harvesting_detection(self) -> None:
        envelope = call(
            "triage_email",
            {
                "subject": "URGENT: Verify your account password immediately",
                "sender": "noreply@amaz0n-security.xyz",
                "reply_to": "admin@phishing-site.tk",
                "body": (
                    "Dear customer, we detected unusual activity on your account. "
                    "Your account will be suspended within 24 hours. "
                    "Please confirm your identity by providing your password: "
                    "and your social security number immediately. "
                    "Failure to comply will result in permanent account closure."
                ),
                "urls": [
                    "http://verify-amazon.tk/login",
                    "http://192.168.1.1/update",
                ],
                "attachments": [],
                "spf": "fail",
                "dkim": "fail",
                "dmarc": "fail",
            },
        )
        self.assertTrue(envelope["ok"], envelope["error"])
        output = envelope["output"]
        self.assertGreaterEqual(output["risk_score"], 50)
        # Should detect credential harvesting indicators
        indicators_text = " ".join(output["detected_indicators"]).lower()
        self.assertIn("credential", indicators_text)

    def test_url_shortener_detected(self) -> None:
        envelope = call(
            "triage_email",
            {
                "subject": "Check this out",
                "sender": "friend@gmail.com",
                "body": "Hey, look at this link: https://bit.ly/3xYz",
                "urls": ["https://bit.ly/3xYz"],
            },
        )
        self.assertTrue(envelope["ok"], envelope["error"])
        output = envelope["output"]
        self.assertTrue(len(output["suspicious_urls"]) > 0)

    def test_reply_to_mismatch_detected(self) -> None:
        envelope = call(
            "triage_email",
            {
                "subject": "Important update",
                "sender": "hr@company.com",
                "reply_to": "attacker@evil.com",
                "body": "Please review the attached document.",
                "urls": [],
            },
        )
        self.assertTrue(envelope["ok"], envelope["error"])
        output = envelope["output"]
        self.assertTrue(len(output["sender_inconsistencies"]) > 0)

    def test_authentication_findings_present(self) -> None:
        envelope = call(
            "triage_email",
            {
                "subject": "Test",
                "sender": "test@example.com",
                "body": "Hello",
                "spf": "pass",
                "dkim": "pass",
                "dmarc": "pass",
            },
        )
        self.assertTrue(envelope["ok"], envelope["error"])
        output = envelope["output"]
        self.assertEqual(len(output["authentication_findings"]), 3)
        checks = {f["check"] for f in output["authentication_findings"]}
        self.assertEqual(checks, {"SPF", "DKIM", "DMARC"})

    def test_social_engineering_techniques_detected(self) -> None:
        envelope = call(
            "triage_email",
            {
                "subject": "CEO Request",
                "sender": "ceo@company-inc.com",
                "body": (
                    "I am the CEO and I need you to wire transfer $50,000 "
                    "immediately. Do not tell anyone about this."
                ),
                "urls": [],
            },
        )
        self.assertTrue(envelope["ok"], envelope["error"])
        output = envelope["output"]
        self.assertTrue(len(output["social_engineering_techniques"]) > 0)

    def test_dangerous_attachment_detected(self) -> None:
        envelope = call(
            "triage_email",
            {
                "subject": "Invoice",
                "sender": "billing@vendor.com",
                "body": "Please find the attached invoice.",
                "urls": [],
                "attachments": ["invoice.vbs", "document.scr"],
            },
        )
        self.assertTrue(envelope["ok"], envelope["error"])
        output = envelope["output"]
        self.assertGreaterEqual(output["risk_score"], 15)

    def test_output_has_all_required_fields(self) -> None:
        envelope = call(
            "triage_email",
            {
                "subject": "Test",
                "sender": "test@example.com",
                "body": "Hello world",
            },
        )
        self.assertTrue(envelope["ok"], envelope["error"])
        output = envelope["output"]
        required = [
            "risk_score",
            "severity",
            "classification",
            "detected_indicators",
            "suspicious_urls",
            "sender_inconsistencies",
            "social_engineering_techniques",
            "authentication_findings",
            "evidence",
            "recommended_actions",
            "analyst_summary",
            "confidence",
            "warnings",
        ]
        for field in required:
            self.assertIn(field, output, f"missing field: {field}")

    def test_risk_score_in_range(self) -> None:
        envelope = call(
            "triage_email",
            {
                "subject": "Test",
                "sender": "test@example.com",
                "body": "Hello",
            },
        )
        self.assertTrue(envelope["ok"], envelope["error"])
        score = envelope["output"]["risk_score"]
        self.assertGreaterEqual(score, 0)
        self.assertLessEqual(score, 100)

    def test_confidence_in_range(self) -> None:
        envelope = call(
            "triage_email",
            {
                "subject": "Test",
                "sender": "test@example.com",
                "body": "Hello",
            },
        )
        self.assertTrue(envelope["ok"], envelope["error"])
        conf = envelope["output"]["confidence"]
        self.assertGreaterEqual(conf, 0.0)
        self.assertLessEqual(conf, 1.0)


class TestInputValidation(unittest.TestCase):
    """Test that invalid inputs are rejected with invalid_request."""

    def test_missing_subject(self) -> None:
        envelope = call(
            "triage_email",
            {
                "sender": "test@example.com",
                "body": "Hello",
            },
        )
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "invalid_request")

    def test_missing_sender(self) -> None:
        envelope = call(
            "triage_email",
            {
                "subject": "Test",
                "body": "Hello",
            },
        )
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "invalid_request")

    def test_missing_body_treated_as_empty(self) -> None:
        envelope = call(
            "triage_email",
            {
                "subject": "Test",
                "sender": "test@example.com",
            },
        )
        # body is optional (defaults to empty string)
        self.assertTrue(envelope["ok"], envelope["error"])

    def test_non_string_urls_rejected(self) -> None:
        envelope = call(
            "triage_email",
            {
                "subject": "Test",
                "sender": "test@example.com",
                "body": "Hello",
                "urls": [123],
            },
        )
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "invalid_request")

    def test_non_object_input_rejected(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(ENTRYPOINT)],
            input=json.dumps(
                {
                    "protocol": PROTOCOL,
                    "capability": "triage_email",
                    "input": "not an object",
                }
            ),
            capture_output=True,
            text=True,
            cwd=AGENT_DIR,
            timeout=30,
            check=False,
        )
        assert proc.returncode == 0
        envelope = json.loads(proc.stdout)
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["type"], "invalid_request")


if __name__ == "__main__":
    unittest.main()
