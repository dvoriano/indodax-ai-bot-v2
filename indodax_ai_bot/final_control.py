"""M05 staged owner Final contract. All paths remain non-executable.

No Telegram network, no owner secrets, no Indodax account, and no orders.
Fixture-only preview staging is deliberately disabled on the actual bot path
until independent M04 G1, model, capital and preflight gates are qualified.
"""
from __future__ import annotations

from decimal import Decimal
import re
import time

from .contracts import FinalDecision, RiskDecision, TradeProposal, RiskVeto
from .journal import DecisionJournal

_DIGEST = re.compile(r"^[a-f0-9]{64}$")
_PAIR = re.compile(r"^[a-z0-9]+_idr$")


def _reject(reason: str, **extra) -> dict:
    return {
        "stage": "M05_FINAL_OWNER_CONTRACT_OFFLINE",
        "status": "REJECT",
        "reason": reason,
        "final_decision": FinalDecision.REJECT.value,
        "execution_allowed": False,
        "trade_authorized": False,
        "telegram_connected": False,
        **extra,
    }


class FinalOwnerControl:
    """Single durable control bound to the existing V2 DecisionJournal.

    A recorded owner PASS is *only an inert vote*. The one operational Final
    remains REJECT because M06/M07 exchange preflight is not implemented.
    """
    def __init__(self, journal: DecisionJournal, *, owner_id: str, clock=None):
        if not isinstance(journal, DecisionJournal):
            raise ValueError("INVALID_M05_DECISION_JOURNAL")
        if (not isinstance(owner_id, str)
                or not 1 <= len(owner_id) <= 128 or owner_id != owner_id.strip()):
            raise ValueError("INVALID_M05_OWNER")
        self.journal = journal
        self.owner_id = owner_id
        self.clock = clock if clock is not None else time.time

    def get_mode(self) -> str:
        return self.journal.final_mode()

    def change_mode(self, *, actor_id: str, mode: str) -> dict:
        if actor_id != self.owner_id:
            return _reject("UNAUTHORIZED_OWNER_CONTROL")
        if mode not in ("MANUAL", "AUTO"):
            return _reject("INVALID_OWNER_FINAL_MODE")
        try:
            self.journal.final_set_mode(mode, actor_id, now_epoch=int(self.clock()))
        except ValueError:
            return _reject("INVALID_OWNER_MODE_TIME")
        return _reject("M05_MODE_STAGED_NO_TRADE", configured_mode=mode)

    def offer_fixture_preview(
        self, proposal: TradeProposal, *, source_digest_sha256: str,
        risk: RiskVeto, roles_validated: bool, independently_verified_source: bool,
        spendable_and_preflight_verified: bool, fixture_only: bool = False,
    ) -> dict:
        """Offline contract test seam, never called by PUBLIC mainpath.

        Real onboarding requires a trusted issuer, authenticated Telegram
        identity and an actual account/preflight integration, NONE available.
        """
        if fixture_only is not True:
            return _reject("M05_REAL_PROPOSAL_ISSUANCE_DISABLED")
        if not isinstance(proposal, TradeProposal) or not isinstance(risk, RiskVeto):
            return _reject("INVALID_M05_PROPOSAL_CONTRACT")
        source = proposal.source
        if (not source.verified
                or independently_verified_source is not True
                or roles_validated is not True
                or spendable_and_preflight_verified is not True
                or risk.decision != RiskDecision.ALLOW):
            return _reject("M05_PREREQUISITE_NOT_VERIFIED")
        if (not isinstance(source_digest_sha256, str)
                or not _DIGEST.fullmatch(source_digest_sha256)
                or not isinstance(source.pair, str) or not _PAIR.fullmatch(source.pair)
                or not isinstance(proposal.proposal_id, str)
                or not 1 <= len(proposal.proposal_id) <= 128
                or proposal.proposal_id != proposal.proposal_id.strip()):
            return _reject("INVALID_M05_PROPOSAL_BINDING")
        now = int(self.clock())
        if (type(proposal.expires_epoch) is not int
                or not now < proposal.expires_epoch <= now + 90
                or type(source.closed_1m_epoch) is not int
                or source.closed_1m_epoch <= 0
                or source.observed_epoch > now
                or now - source.observed_epoch > 60):
            return _reject("M05_PROPOSAL_STALE_OR_INVALID_TTL")
        try:
            values = (
                proposal.entry_idr, proposal.quantity,
                proposal.estimated_net_profit_idr,
                proposal.estimated_net_roi_percent,
            )
            if (any(not isinstance(v, Decimal) or not v.is_finite() for v in values)
                    or proposal.entry_idr <= 0 or proposal.quantity <= 0
                    or proposal.estimated_net_profit_idr < Decimal("10000")
                    or proposal.estimated_net_roi_percent < Decimal("2")):
                return _reject("M05_PROFIT_OR_SIZING_NOT_QUALIFIED")
            self.journal.final_stage_preview(
                proposal.proposal_id, source.pair, source_digest_sha256,
                self.owner_id, now_epoch=now, expires_epoch=proposal.expires_epoch,
            )
        except ValueError:
            return _reject("M05_PROPOSAL_ID_CONFLICT_OR_INVALID")
        return _reject(
            "M05_OFFLINE_FIXTURE_PREVIEW_ONLY",
            proposal_id=proposal.proposal_id, mode=self.get_mode(),
            owner_vote_pending=True, expires_epoch=proposal.expires_epoch,
        )

    def vote(self, *, actor_id: str, proposal_id: str,
             source_digest_sha256: str, decision: str) -> dict:
        if actor_id != self.owner_id:
            return _reject("UNAUTHORIZED_OWNER_CONTROL")
        if decision not in (FinalDecision.PASS.value, FinalDecision.REJECT.value):
            return _reject("INVALID_M05_BINARY_DECISION")
        if (not isinstance(proposal_id, str) or not proposal_id
                or not isinstance(source_digest_sha256, str)
                or not _DIGEST.fullmatch(source_digest_sha256)):
            return _reject("INVALID_M05_CALLBACK_BINDING")
        outcome = self.journal.final_consume_vote(
            proposal_id, source_digest_sha256, actor_id, decision,
            now_epoch=int(self.clock()),
        )
        return _reject(
            outcome,
            owner_vote_observed=decision if outcome == "M05_OWNER_VOTE_RECORDED" else None,
            proposal_id=proposal_id,
        )

    @staticmethod
    def mainpath_admission(ai_shadow: dict | None, *, mode: str) -> dict:
        """The only live bulk-mainpath M05 connection; always REJECT.

        Existing M04 outputs cannot establish signed full market tape,
        private spendable capital, Telegram identity or final preflight.
        Even a forged synthetic M04 PASS cannot open an owner trade proposal.
        """
        return _reject(
            "M05_G1_ACCOUNT_TELEGRAM_PREFLIGHT_NOT_CONNECTED",
            decision_mode=mode if mode in ("MANUAL", "AUTO") else "MANUAL",
            m04_shadow_available=isinstance(ai_shadow, dict),
            owner_approval_available=False,
            telegram_proposal_issued=False,
            auto_execution_allowed=False,
        )
