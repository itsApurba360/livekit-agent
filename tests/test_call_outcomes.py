# -*- coding: utf-8 -*-
import unittest

from call_outcomes import failure_status_for_reason, sip_failure_reason


class CallOutcomeTestCase(unittest.TestCase):
    def test_sip_failure_reason_maps_common_outcomes(self):
        self.assertEqual(sip_failure_reason("486", "Busy Here"), "busy")
        self.assertEqual(sip_failure_reason("603", "Decline"), "rejected")
        self.assertEqual(sip_failure_reason("408", "Request Timeout"), "no_answer")
        self.assertEqual(sip_failure_reason("480", "Temporarily Unavailable"), "unreachable")
        self.assertEqual(sip_failure_reason("503", "Service Unavailable"), "trunk")

    def test_failure_status_for_reason_maps_api_statuses(self):
        self.assertEqual(failure_status_for_reason("busy"), "failed_busy")
        self.assertEqual(failure_status_for_reason("rejected"), "failed_rejected")
        self.assertEqual(failure_status_for_reason("no_answer"), "failed_no_answer")
        self.assertEqual(failure_status_for_reason("unreachable"), "failed_unreachable")
        self.assertEqual(failure_status_for_reason("trunk"), "failed_trunk")
        self.assertEqual(failure_status_for_reason("other"), "failed")


if __name__ == "__main__":
    unittest.main()
