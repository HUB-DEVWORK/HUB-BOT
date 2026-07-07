"""/start: deep-link attribution (referral / campaign) + main menu."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import Message

from src.bot.menu_render import send_main_menu
from src.core.logging import get_logger
from src.infrastructure.database.models.user import User
from src.infrastructure.di import AppContainer

log = get_logger(__name__)

router = Router(name="start")


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    command: CommandObject,
    container: AppContainer,
    db_user: User,
    db_user_created: bool,
) -> None:
    param = (command.args or "").strip()
    gift_note: str | None = None
    if param.startswith("gift_"):
        gift_note = await _claim_gift(container, db_user, param.removeprefix("gift_"))
    elif param:
        await _attribute(container, db_user, param, created=db_user_created)
    if gift_note:
        await message.answer(gift_note, parse_mode="HTML")
    await send_main_menu(message, container, db_user)


async def _claim_gift(container: AppContainer, db_user: User, code: str) -> str:
    """t.me/<bot>?start=gift_<CODE> — apply the promocode right from the deep-link.

    Reuses the promocode engine wholesale: per-user unique activation, limits,
    expiry, and instant rewards (balance / days / subscription / discount).
    """
    from src.application.services.promo import PromoError

    async with container.uow() as uow:
        user = await uow.users.get(db_user.id)
        if user is None:
            return "Ошибка, попробуй ещё раз."
        try:
            reward = await container.promo.apply(uow, user, code.strip().upper())
        except PromoError as exc:
            return f"🎁 Не получилось активировать подарок: {exc}"
        await uow.commit()
    log.info("gift claimed", user=db_user.id, code=code[:16], reward=reward.value)
    return "🎁 <b>Подарок активирован!</b> Загляни в «Личный кабинет»."


async def _attribute(container: AppContainer, db_user: User, param: str, *, created: bool) -> None:
    """ref_<code> -> referred_by; anything else -> campaign start_param (first touch only)."""
    async with container.uow() as uow:
        user = await uow.users.get(db_user.id)
        if user is None:
            return
        if param.startswith("ref_"):
            if user.referred_by_id is None and created:
                # bind() creates the Referral row the commission engine reads
                # (reward_on_topup) — setting referred_by_id alone pays nobody.
                referral = await container.referrals.bind(uow, user, param.removeprefix("ref_"))
                if referral is not None:
                    log.info("referral attributed", user=user.id, referrer=referral.referrer_id)
        elif user.campaign_id is None:
            campaign = await uow.campaigns.find_one(start_param=param, is_active=True)
            if campaign is not None:
                user.campaign_id = campaign.id
                if campaign.promo_group_id is not None:
                    from src.infrastructure.database.models.promo_group import UserPromoGroup

                    existing = await uow.session.get(
                        UserPromoGroup,
                        {"user_id": user.id, "promo_group_id": campaign.promo_group_id},
                    )
                    if existing is None:
                        uow.session.add(
                            UserPromoGroup(user_id=user.id, promo_group_id=campaign.promo_group_id)
                        )
                log.info("campaign attributed", user=user.id, campaign=campaign.id)
        await uow.commit()
