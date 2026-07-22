"""Merging a web-cabinet account into a Telegram account («связка»).

A person may start on the website (e-mail / VK / Yandex) and later open the bot — or
the other way round. Linking joins the two into ONE account so the subscription,
balance and history are visible everywhere. The Telegram row always survives (the
bot resolves users by ``telegram_id``); everything the web row owns is re-pointed to
it inside a single transaction, then the empty web row is deleted.

Money and history are never dropped: balances are summed, subscriptions /
transactions / earnings / tickets move wholesale. The only rows that may be skipped
are exact duplicates a UNIQUE constraint forbids (same promocode activated on both
accounts, same referral binding) — the survivor's copy wins.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select, update

from src.core.enums import SubscriptionStatus
from src.core.logging import get_logger
from src.infrastructure.database.models.audit_log import AuditLog
from src.infrastructure.database.models.cabinet_token import CabinetRefreshToken
from src.infrastructure.database.models.linked_account import LinkedAccount
from src.infrastructure.database.models.promo_group import UserPromoGroup
from src.infrastructure.database.models.promocode import PromocodeActivation
from src.infrastructure.database.models.referral import Referral, ReferralEarning
from src.infrastructure.database.models.subscription import Subscription
from src.infrastructure.database.models.ticket import Ticket
from src.infrastructure.database.models.transaction import Transaction
from src.infrastructure.database.models.user import User
from src.infrastructure.database.models.withdrawal import WithdrawalRequest
from src.infrastructure.database.uow import UnitOfWork

log = get_logger(__name__)

# Redis: one-time deep-link code -> web user id (minted by the cabinet, spent by the bot).
TG_LINK_PREFIX = "link_tg:"
# Redis: «Войти через Telegram» on the website — pending until the person confirms in
# the bot, then carries the Telegram user's id for the site's poll to pick up.
TG_WEBLOGIN_PREFIX = "weblogin:"


class AccountLinkError(Exception):
    """Human-readable refusal (already linked, conflicting e-mails, …)."""


async def merge_web_into_telegram(uow: UnitOfWork, tg_user: User, web_user_id: int) -> User:
    """Absorb ``web_user_id`` into ``tg_user`` and delete the web row.

    Caller owns the transaction (no commit here). Raises :class:`AccountLinkError`
    with a message that is safe to show to the user.
    """
    if tg_user.telegram_id is None:
        raise AccountLinkError("аккаунт не привязан к Telegram")
    if web_user_id == tg_user.id:
        raise AccountLinkError("этот аккаунт уже привязан")

    # Lock both rows in id order so two concurrent link attempts serialize (no-op on sqlite).
    for uid in sorted((tg_user.id, web_user_id)):
        await uow.users.lock_for_update(uid)
    web = await uow.users.get(web_user_id)
    if web is None:
        raise AccountLinkError("ссылка устарела — начни привязку заново")
    if web.telegram_id is not None:
        raise AccountLinkError("к этому аккаунту уже привязан другой Telegram")
    if web.role.is_staff or tg_user.role.is_staff:
        raise AccountLinkError("служебные аккаунты связывать нельзя")
    if tg_user.email and web.email and tg_user.email != web.email:
        raise AccountLinkError("к твоему Telegram уже привязана другая почта")

    s = uow.session
    src, dst = web.id, tg_user.id

    # --- identity ----------------------------------------------------------
    # Free the e-mail with its own flush BEFORE the survivor takes it: one combined
    # flush orders UPDATEs by pk, and the survivor's row would hit uq_users_email
    # while the web row still holds the address.
    web_email, web_email_verified = web.email, web.email_verified
    web_password_hash = web.password_hash
    web.email = None
    web.email_verified = False
    await s.flush()
    if not tg_user.email and web_email:
        tg_user.email = web_email
        tg_user.email_verified = web_email_verified
    if not tg_user.password_hash and web_password_hash:
        tg_user.password_hash = web_password_hash
    await s.flush()

    # --- wallet + lifecycle flags -----------------------------------------
    if web.balance_minor:
        await uow.users.increment_balance(tg_user, web.balance_minor)
        web.balance_minor = 0
    tg_user.has_had_paid_subscription = (
        tg_user.has_had_paid_subscription or web.has_had_paid_subscription
    )
    tg_user.has_made_first_topup = tg_user.has_made_first_topup or web.has_made_first_topup
    # One trial per PERSON: after the merge the trial survives only if neither side spent it.
    tg_user.is_trial_available = tg_user.is_trial_available and web.is_trial_available
    tg_user.personal_discount_pct = max(tg_user.personal_discount_pct, web.personal_discount_pct)
    tg_user.purchase_discount_pct = max(tg_user.purchase_discount_pct, web.purchase_discount_pct)
    if tg_user.referral_commission_percent is None:
        tg_user.referral_commission_percent = web.referral_commission_percent
    if tg_user.campaign_id is None:
        tg_user.campaign_id = web.campaign_id
    if tg_user.saved_payment_method_id is None and web.saved_payment_method_id:
        tg_user.saved_payment_method_id = web.saved_payment_method_id
        tg_user.saved_payment_method_title = web.saved_payment_method_title

    # --- subscriptions (handled first: uq_active_sub can't take two live rows on one
    #     plan for the same user, so resolve those collisions before the move) --------
    await _merge_subscriptions(s, src, dst)

    # --- bulk-move owned rows ---------------------------------------------
    for model, column in (
        (Transaction, Transaction.user_id),
        (Ticket, Ticket.user_id),
        (WithdrawalRequest, WithdrawalRequest.user_id),
        (LinkedAccount, LinkedAccount.user_id),
    ):
        await s.execute(update(model).where(column == src).values(user_id=dst))
    await s.execute(update(AuditLog).where(AuditLog.actor_user_id == src).values(actor_user_id=dst))
    # Refresh tokens are NOT transferred: a pre-merge web session must not silently become a
    # session on the (now larger) survivor account — a stolen/pre-existing web refresh token
    # would otherwise keep working against the merged account. Drop them; re-login is cheap.
    await s.execute(delete(CabinetRefreshToken).where(CabinetRefreshToken.user_id == src))

    # Promocode activations: UNIQUE(promocode_id, user_id) — a code both accounts used
    # stays "used once" on the survivor, the duplicate source row is dropped.
    dst_codes = select(PromocodeActivation.promocode_id).where(PromocodeActivation.user_id == dst)
    await s.execute(
        delete(PromocodeActivation).where(
            PromocodeActivation.user_id == src,
            PromocodeActivation.promocode_id.in_(dst_codes),
        )
    )
    await s.execute(
        update(PromocodeActivation).where(PromocodeActivation.user_id == src).values(user_id=dst)
    )

    # Promo-group membership: composite PK (user_id, promo_group_id) — same dedup dance.
    dst_groups = select(UserPromoGroup.promo_group_id).where(UserPromoGroup.user_id == dst)
    await s.execute(
        delete(UserPromoGroup).where(
            UserPromoGroup.user_id == src,
            UserPromoGroup.promo_group_id.in_(dst_groups),
        )
    )
    await s.execute(update(UserPromoGroup).where(UserPromoGroup.user_id == src).values(user_id=dst))

    # --- referral graph ----------------------------------------------------
    # People the web account referred now belong to the survivor; a web→tg self-link
    # would make the survivor its own referrer — drop that binding instead.
    await s.execute(
        delete(Referral).where(Referral.referrer_id == src, Referral.referred_id == dst)
    )
    await s.execute(update(Referral).where(Referral.referrer_id == src).values(referrer_id=dst))
    # "Who referred ME": keep the survivor's binding if it has one (referred_id is UNIQUE).
    dst_binding = (await s.scalars(select(Referral).where(Referral.referred_id == dst))).first()
    src_binding = (await s.scalars(select(Referral).where(Referral.referred_id == src))).first()
    if src_binding is not None:
        if dst_binding is None and src_binding.referrer_id != dst:
            src_binding.referred_id = dst
            if tg_user.referred_by_id is None:
                tg_user.referred_by_id = src_binding.referrer_id
        else:
            await s.delete(src_binding)
    if tg_user.referred_by_id == web.id:
        tg_user.referred_by_id = None
    await s.execute(update(User).where(User.referred_by_id == src).values(referred_by_id=dst))
    # Earnings: dedup on the partial UNIQUE (user_id, transaction_id), then move.
    dst_txns = select(ReferralEarning.transaction_id).where(
        ReferralEarning.user_id == dst, ReferralEarning.transaction_id.is_not(None)
    )
    await s.execute(
        delete(ReferralEarning).where(
            ReferralEarning.user_id == src,
            ReferralEarning.transaction_id.in_(dst_txns),
        )
    )
    await s.execute(
        update(ReferralEarning).where(ReferralEarning.user_id == src).values(user_id=dst)
    )

    # Point the survivor at a usable subscription. The old rule ("keep dst's pointer unless
    # it's None") stranded a freshly bought paid sub behind an expired trial — the whole UI
    # reads current_subscription_id, so the buyer saw "no subscription" and could pay twice.
    await _pick_current_subscription(s, tg_user)

    await s.flush()
    await s.delete(web)
    await s.flush()
    log.info("accounts merged", survivor=dst, absorbed=src)
    return tg_user


_LIVE_STATUSES = (
    SubscriptionStatus.ACTIVE,
    SubscriptionStatus.TRIAL,
    SubscriptionStatus.LIMITED,
)


async def _merge_subscriptions(s: Any, src: int, dst: int) -> None:
    """Move the source account's subscriptions onto the survivor, resolving the one case
    the DB forbids: two live subscriptions for the same plan (``uq_active_sub``).

    A trial always yields to anything else. If BOTH sides hold a live subscription on the
    same plan and neither is a trial, we can't silently drop a paid subscription — refuse
    the whole link with a message pointing the owner at support.
    """
    src_subs = list(
        (await s.scalars(select(Subscription).where(Subscription.user_id == src))).all()
    )
    dst_live = {
        sub.plan_id: sub
        for sub in (
            await s.scalars(
                select(Subscription).where(
                    Subscription.user_id == dst, Subscription.status.in_(_LIVE_STATUSES)
                )
            )
        ).all()
        if sub.plan_id is not None
    }
    for sub in src_subs:
        clash = dst_live.get(sub.plan_id) if sub.plan_id is not None else None
        if clash is not None and sub.status in _LIVE_STATUSES:
            if sub.is_trial:
                # Source trial is disposable — expire it, keep the survivor's live sub.
                sub.status = SubscriptionStatus.EXPIRED
            elif clash.is_trial:
                # Survivor's is only a trial — retire it so the paid one can take the slot.
                clash.status = SubscriptionStatus.EXPIRED
                del dst_live[sub.plan_id]
            else:
                raise AccountLinkError(
                    "на обоих аккаунтах активная подписка на один тариф — "
                    "напиши в поддержку, объединим вручную"
                )
        await s.execute(update(Subscription).where(Subscription.id == sub.id).values(user_id=dst))


async def _pick_current_subscription(s: Any, user: User) -> None:
    """Ensure ``current_subscription_id`` points at a usable subscription when one exists."""
    subs = list(
        (await s.scalars(select(Subscription).where(Subscription.user_id == user.id))).all()
    )
    current = next((sub for sub in subs if sub.id == user.current_subscription_id), None)
    if current is not None and current.status in _LIVE_STATUSES:
        return  # already usable — leave it
    usable = [sub for sub in subs if sub.status in _LIVE_STATUSES]
    if usable:
        # Prefer a paid subscription, then the one expiring latest.
        usable.sort(key=lambda x: (x.is_trial, -(x.expire_at.timestamp() if x.expire_at else 0)))
        user.current_subscription_id = usable[0].id
