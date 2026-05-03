import uuid
import structlog
from datetime import datetime

log = structlog.get_logger()


class PaymentService:
    """Stub payment service — integrate with real payment processor in production."""

    async def trigger_payment(
        self,
        claim_id: str,
        provider_id: str,
        amount: float,
        policy_number: str,
    ) -> dict:
        payment_id = str(uuid.uuid4())
        log.info(
            "payment_triggered",
            payment_id=payment_id,
            claim_id=claim_id,
            provider_id=provider_id,
            amount=amount,
        )
        return {
            "payment_id": payment_id,
            "claim_id": claim_id,
            "provider_id": provider_id,
            "amount": amount,
            "policy_number": policy_number,
            "status": "QUEUED",
            "initiated_at": datetime.utcnow().isoformat(),
        }

    async def void_payment(self, payment_id: str, reason: str) -> dict:
        log.info("payment_voided", payment_id=payment_id, reason=reason)
        return {"payment_id": payment_id, "status": "VOIDED"}


payment_service = PaymentService()
