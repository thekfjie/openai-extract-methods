from __future__ import annotations

import unittest

from tools.analyze_pp_history import analyze, checkout_family


class PPHistoryAnalysisTests(unittest.TestCase):
    def test_checkout_families_do_not_alias_oailive(self) -> None:
        self.assertEqual(checkout_family("oaics_example"), "OAICS")
        self.assertEqual(checkout_family("cs_live_example"), "CS_LIVE")
        self.assertEqual(checkout_family("OAILIVE"), "NONE")

    def test_prior_oaics_cohort_and_verification_are_counted_separately(self) -> None:
        jobs = {"jobs": [
            {
                "method": "direct_card", "createdAt": "2026-08-01T00:00:00Z", "options": {"country": "GB"},
                "items": [{"email": "a@example.test", "tokenHash": "same", "status": "succeeded", "checkoutId": "oaics_old", "startedAt": "2026-08-01T00:00:00Z"}],
            },
            {
                "method": "paypal_ba", "createdAt": "2026-08-02T00:00:00Z", "options": {"country": "GB"},
                "items": [{"email": "a@example.test", "tokenHash": "same", "status": "succeeded", "extractionStatus": "ba_ready", "checkoutId": "cs_live_new", "startedAt": "2026-08-02T00:00:00Z"}],
            },
        ]}
        audit = [
            {"event": "protocol-pay", "task_id": "one", "status": "succeeded", "payment_status": "prepared"},
            {"event": "protocol-pay", "task_id": "one", "status": "succeeded", "payment_status": "verification_required"},
        ]
        report = analyze(jobs, audit, [], "oailive OAILIVE")
        cohort = report["priorHistoryHypotheses"]["directCardOAICSToGBPayPal"]
        self.assertEqual((cohort["succeeded"], cohort["total"]), (1, 1))
        self.assertEqual(report["protocolPaymentAudit"]["latestTaskOutcomes"], {"verification_required": 1})
        self.assertEqual(report["linkVocabulary"]["exactOAILIVEOccurrences"], 2)


if __name__ == "__main__":
    unittest.main()
