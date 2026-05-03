import structlog
from app.config import settings

log = structlog.get_logger()


class NotificationService:
    """Stub notification service — wire real SMTP / webhook in production."""

    async def send_approval(self, claim_id: str, claimant_name: str, approved_amount: float) -> None:
        log.info(
            "notification_sent",
            type="approval",
            claim_id=claim_id,
            recipient=claimant_name,
            amount=approved_amount,
        )

    async def send_denial(self, claim_id: str, claimant_name: str, denial_reason: str, appeals_pathway: str) -> None:
        log.info(
            "notification_sent",
            type="denial",
            claim_id=claim_id,
            recipient=claimant_name,
            denial_reason=denial_reason[:100],
        )

    async def send_pending(self, claim_id: str, claimant_name: str, reason: str) -> None:
        log.info(
            "notification_sent",
            type="pending",
            claim_id=claim_id,
            recipient=claimant_name,
            reason=reason[:100],
        )

    async def send_to_provider(self, claim_id: str, provider_name: str, verdict: str) -> None:
        log.info(
            "notification_sent",
            type="provider",
            claim_id=claim_id,
            provider=provider_name,
            verdict=verdict,
        )

    async def post_webhook(self, payload: dict) -> None:
        if not settings.notification_webhook_url if hasattr(settings, "notification_webhook_url") else True:
            log.debug("webhook_skipped", reason="no_url_configured")
            return
        log.info("webhook_posted", payload_keys=list(payload.keys()))


notification_service = NotificationService()
