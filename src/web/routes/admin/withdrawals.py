"""Admin: referral-withdrawal requests (manual payout queue).

Money already left the wallet at request time; «paid» just records the payout,
«rejected» refunds the wallet and both notify the user.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from src.core.enums import WithdrawalStatus
from src.core.logging import get_logger
from src.infrastructure.database.base import utcnow
from src.infrastructure.database.models.user import User
from src.infrastructure.database.models.withdrawal import WithdrawalRequest
from src.infrastructure.di import AppContainer
from src.web.deps import get_container
from src.web.routes.admin._common import OkOut, audit, iso
from src.web.routes.admin.deps import AdminIdentity, require_admin

log = get_logger(__name__)

router = APIRouter(prefix="/withdrawals")


@router.get("")
async def list_withdrawals(
    status: str = "all", container: AppContainer = Depends(get_container)
) -> dict[str, Any]:
    async with container.uow() as uow:
        stmt = (
            select(WithdrawalRequest, User)
            .join(User, User.id == WithdrawalRequest.user_id)
            .order_by(WithdrawalRequest.id.desc())
            .limit(200)
        )
        if status in ("pending", "paid", "rejected"):
            stmt = stmt.where(WithdrawalRequest.status == WithdrawalStatus(status))
        rows = (await uow.session.execute(stmt)).all()
        pending = await uow.withdrawals.count(status=WithdrawalStatus.PENDING)
    return {
        "pending_count": pending,
        "items": [
            {
                "id": w.id,
                "user_id": w.user_id,
                "username": u.username,
                "telegram_id": u.telegram_id,
                "amount_minor": w.amount_minor,
                "method": w.method,
                "details": w.details,
                "status": w.status.value,
                "admin_comment": w.admin_comment,
                "created_at": iso(w.created_at),
                "processed_at": iso(w.processed_at),
            }
            for w, u in rows
        ],
    }


class WithdrawalPatch(BaseModel):
    status: str = Field(..., pattern="^(paid|rejected)$")
    comment: str | None = Field(None, max_length=256)


@router.patch("/{withdrawal_id}")
async def process_withdrawal(
    withdrawal_id: int,
    body: WithdrawalPatch,
    identity: AdminIdentity = Depends(require_admin),
    container: AppContainer = Depends(get_container),
) -> OkOut:
    async with container.uow() as uow:
        req = await uow.withdrawals.get(withdrawal_id)
        if req is None:
            raise HTTPException(404, "withdrawal not found")
        if req.status is not WithdrawalStatus.PENDING:
            raise HTTPException(409, "already processed")
        user = await uow.users.get(req.user_id)
        req.status = WithdrawalStatus(body.status)
        req.admin_comment = body.comment
        req.processed_at = utcnow()
        if req.status is WithdrawalStatus.REJECTED and user is not None:
            await uow.users.increment_balance(user, req.amount_minor)  # refund the hold
        await audit(
            uow,
            identity,
            "withdrawal.process",
            f"withdrawal:{withdrawal_id}",
            status=body.status,
            amount_minor=req.amount_minor,
        )
        await uow.commit()
        telegram_id = user.telegram_id if user else None
        amount = req.amount_minor

    if telegram_id is not None:
        if body.status == "paid":
            text = f"✅ Выплата по заявке #{withdrawal_id} на {amount / 100:.2f} ₽ отправлена."
        else:
            text = (
                f"↩️ Заявка на вывод #{withdrawal_id} отклонена, {amount / 100:.2f} ₽ "
                f"возвращены на баланс."
                + (f"\nКомментарий: {body.comment}" if body.comment else "")  # noqa: RUF001
            )
        await container.notifier.notify_user(telegram_id, text)
    return OkOut()
