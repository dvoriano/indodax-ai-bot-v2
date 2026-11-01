"""M06 original CLI fixed-code-only diagnostic; no private network activity."""
import unittest

from indodax_ai_bot.__main__ import _m06_redacted_error_code
from indodax_ai_bot.m06_scoped_https import M06ScopeDenied, _m06_safe_http_status


class M06SafeErrorReasonTests(unittest.TestCase):
    def test_known_fixed_error_code_is_visible(self):
        self.assertEqual(
            _m06_redacted_error_code(
                M06ScopeDenied("M06_KEY_NOT_PROVEN_VIEW_ONLY")
            ),
            "M06_KEY_NOT_PROVEN_VIEW_ONLY",
        )

    def test_untrusted_exception_text_never_logged(self):
        self.assertEqual(
            _m06_redacted_error_code(
                ValueError("PRIVATE_KEY=do-not-print")
            ),
            "M06_COVERAGE_NOT_ACCEPTED",
        )

    def test_malicious_scope_error_text_does_not_leak(self):
        self.assertEqual(
            _m06_redacted_error_code(
                M06ScopeDenied("M06_INVALID\\nSecret:never-share")
            ),
            "M06_COVERAGE_NOT_ACCEPTED",
        )

    def test_no_private_scope_error_escapes(self):
        self.assertEqual(
            _m06_redacted_error_code(M06ScopeDenied("HTTP 403: sensitive response")),
            "M06_COVERAGE_NOT_ACCEPTED",
        )


    def test_safe_http_status_401_credentials_class_only(self):
        self.assertEqual(_m06_safe_http_status(401), "M06_PRIVATE_HTTP_401")

    def test_safe_http_status_403_permission_class_only(self):
        self.assertEqual(_m06_safe_http_status(403), "M06_PRIVATE_HTTP_403")

    def test_safe_http_status_400_clock_or_signature_not_overdiagnosed(self):
        self.assertEqual(_m06_safe_http_status(400), "M06_PRIVATE_HTTP_400")

    def test_unknown_http_status_is_redacted(self):
        self.assertEqual(_m06_safe_http_status("401 SECRET=bad"), "M06_PRIVATE_HTTP_NON_200")
        self.assertEqual(_m06_safe_http_status(777), "M06_PRIVATE_HTTP_NON_200")


if __name__ == "__main__":
    unittest.main()
