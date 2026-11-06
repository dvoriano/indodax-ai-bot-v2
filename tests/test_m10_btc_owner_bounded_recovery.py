"""M10 BTC owner failed-batch recovery: pure offline source and unit tests."""
from hashlib import sha256
import unittest
from unittest.mock import patch

from indodax_ai_bot.btc_ops_gate import AdmissionDenied
from scripts import m10_btc_owner_bounded_recovery as module

from scripts.m10_btc_owner_bounded_recovery import (
    BtcRecoveryDenied, PILOT_SECONDS, ROOT, RT, UNIT, UNITDIR, DROP,
    assess_exact_failed_btc, assess_original_btc_systemd,
    recover_public_btc_once,
)

NOW = 1793940000
BYTES = b'{"status":"REJECT","original":true}\n'


def old():
    return {
        "scope": "BTC_PUBLIC_MONITOR_ONLY",
        "expires_at_epoch": NOW - 60,
        "max_gap_count": 87,
    }


def status():
    return {
        "status": "REJECT", "pair": "btc_idr",
        "requested_sessions": 10, "attempted_sessions": 10,
        "completed_safe_sessions": 9,
        "collector_failure_code": "PUBLIC_WS_CONNECTION_OR_FRAME_FAILED",
        "last_completed_batch_epoch": NOW - 72000,
        "unrecoverable_gap_rebaseline_count": 7,
        "g1_receipt_issuance_allowed": False,
        "g2_provider_call_allowed": False,
        "execution_allowed": False,
        "restart_only_after_clean_batch": False,
    }


def hold():
    return {
        "scope": "BTC_PUBLIC_MONITOR_ONLY",
        "reason": "PUBLIC_WS_SERVICE_FAILURE_HOLD",
    }


def unit():
    root = str(ROOT)
    gate = root + "/indodax_ai_bot/btc_ops_gate.py"
    python = root + "/.venv/bin/python"
    return {
        "fragment": str(UNITDIR / UNIT),
        "workdir": root,
        "load": "loaded",
        "service_active": "inactive",
        "restart": "no",
        "needs_reload": "no",
        "timer_active": "active",
        "timer_enabled": "enabled",
        "m05_active": "active",
        "dropins": str(DROP),
        "execstart": (
            python + " -B -m indodax_ai_bot "
            "--public-ws-supervised-once --pair btc_idr "
            "--collector-sessions 10"
        ),
        "condition": python + " -B " + gate + " check",
        "exit_hook": python + " -B " + gate + " exit-hook",
    }


class M10BtcOneOriginalRecovery(unittest.TestCase):
    def inspect(self, **kwargs):
        data = {
            "old_go": old(), "status": status(),
            "status_bytes": BYTES, "hold": hold(),
            "gaps": 7, "epoch": NOW,
        }
        data.update(kwargs)
        return assess_exact_failed_btc(**data)

    def test_valid_reject_only_exact_sidecar_acknowledged(self):
        result = self.inspect()
        self.assertEqual(result["acknowledged_failure_sha256"],
                         sha256(BYTES).hexdigest())
        self.assertEqual(result["expires_at_epoch"], NOW + PILOT_SECONDS)
        self.assertEqual(result["max_gap_count"], 87)
        self.assertEqual(result["scope"], "BTC_PUBLIC_MONITOR_ONLY")

    def test_expired_go_not_reused_and_live_old_go_rejected(self):
        x = old()
        x["expires_at_epoch"] = NOW + 5
        with self.assertRaises(BtcRecoveryDenied):
            self.inspect(old_go=x)

    def test_owner_hold_other_than_machine_failure_not_overridden(self):
        with self.assertRaises(BtcRecoveryDenied):
            self.inspect(hold={"reason": "OPERATOR_INTENTIONAL_HOLD"})

    def test_clean_old_batch_not_marked_failed_recovery(self):
        s = status()
        s["status"] = "PUBLIC_COLLECTION_BATCH_OK_UNATTESTED"
        s["completed_safe_sessions"] = 10
        with self.assertRaises(BtcRecoveryDenied):
            self.inspect(status=s)

    def test_forged_source_authority_denied(self):
        for key in ("g1_receipt_issuance_allowed",
                    "g2_provider_call_allowed", "execution_allowed"):
            s = status()
            s[key] = True
            with self.subTest(key=key), self.assertRaises(BtcRecoveryDenied):
                self.inspect(status=s)

    def test_8_9_of_10_can_only_be_recovered_not_promoted(self):
        result = self.inspect()
        self.assertIn("acknowledged_failure_sha256", result)
        self.assertNotIn("PUBLIC_COLLECTION_BATCH_OK_UNATTESTED", result.values())

    def test_unknown_failure_code_denied(self):
        s = status()
        s["collector_failure_code"] = "API_KEY=secret"
        with self.assertRaises(BtcRecoveryDenied):
            self.inspect(status=s)

    def test_gap_budget_cannot_expand(self):
        with self.assertRaises(BtcRecoveryDenied):
            self.inspect(gaps=88)
        with self.assertRaises(BtcRecoveryDenied):
            self.inspect(gaps=6)

    def test_status_older_than_72h_denied(self):
        s = status()
        s["last_completed_batch_epoch"] = NOW - 80 * 3600
        with self.assertRaises(BtcRecoveryDenied):
            self.inspect(status=s)

    def test_exact_original_v2_unit_admitted(self):
        assess_original_btc_systemd(values=unit())

    def test_unreviewed_execcondition_other_repo_denied(self):
        d = unit()
        d["condition"] = "/home/ubuntu/crypto-bot/v1.py check"
        with self.assertRaises(BtcRecoveryDenied):
            assess_original_btc_systemd(values=d)

    def test_unreviewed_extra_dropin_denied(self):
        d = unit()
        d["dropins"] += " /tmp/unknown.conf"
        with self.assertRaises(BtcRecoveryDenied):
            assess_original_btc_systemd(values=d)

    def test_active_service_or_unauthorized_restart_denied(self):
        for key, val in (("service_active", "active"),
                         ("restart", "on-success"),
                         ("timer_active", "inactive"),
                         ("m05_active", "inactive")):
            d = unit()
            d[key] = val
            with self.subTest(key=key), self.assertRaises(BtcRecoveryDenied):
                assess_original_btc_systemd(values=d)

    def test_owner_recovery_without_explicit_action_denied(self):
        with self.assertRaises(BtcRecoveryDenied):
            recover_public_btc_once(approve=False, dry_run=False, epoch=NOW)


    def test_private_readers_classify_exact_missing_source_without_payloads(self):
        sample = ({}, b"{}")
        for path, expected in (
            (module.GO, "M10_BTC_PRIVATE_GO_UNAVAILABLE"),
            (module.STATUS, "M10_BTC_PRIVATE_STATUS_UNAVAILABLE"),
            (module.HOLD, "M10_BTC_PRIVATE_HOLD_UNAVAILABLE"),
        ):
            def reader(candidate, _size, *, fail=path):
                if candidate == fail:
                    raise AdmissionDenied("PRIVATE_FILE_UNREADABLE")
                return sample
            with (self.subTest(path=path.name),
                  patch.object(module, "_private_json", side_effect=reader),
                  patch.object(module, "_journal_gap_count", return_value=7)):
                with self.assertRaises(BtcRecoveryDenied) as error:
                    module.read_original_private_sources()
                self.assertEqual(str(error.exception), expected)

    def test_private_readers_distinguish_sqlite_journal_error(self):
        with (patch.object(module, "_private_json", return_value=({}, b"{}")),
              patch.object(module, "_journal_gap_count",
                           side_effect=AdmissionDenied("PUBLIC_WS_JOURNAL_INVALID"))):
            with self.assertRaises(BtcRecoveryDenied) as error:
                module.read_original_private_sources()
        self.assertEqual(
            str(error.exception),
            "M10_BTC_PRIVATE_WS_JOURNAL_UNAVAILABLE",
        )

    def test_private_readers_success_is_readonly_and_no_authority(self):
        with (patch.object(module, "_private_json", return_value=({}, b"{}")) as files,
              patch.object(module, "_journal_gap_count", return_value=7) as gaps):
            values = module.read_original_private_sources()
        self.assertEqual(values, ({}, b"{}", {}, b"{}", {}, b"{}", 7))
        self.assertEqual(files.call_count, 3)
        gaps.assert_called_once_with(module.WSDB)


if __name__ == "__main__":
    unittest.main()
