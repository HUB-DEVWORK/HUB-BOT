"""AI tech-support (config-driven, Claude tool use).

A universal system prompt + an owner-editable KNOWLEDGE BASE (``AI_SUPPORT_KNOWLEDGE_BASE``,
else :data:`DEFAULT_KB`). The AI answers support tickets as if it were a human agent, can take
real actions via tools (list/reset devices, check subscription/payment, report a bug to the
owner), and escalates money/dispute/unknown cases to a live operator by returning ``ESCALATE``.

Every owner sets their OWN Claude API key in the cabinet — nothing is hardcoded. The stable
block (prompt + KB + tools) is prompt-cached, so repeat replies are ~10x cheaper.
"""

from __future__ import annotations

import contextlib
import re
from typing import TYPE_CHECKING, Any

import httpx

from src.application.dto.panel import PanelUserRef
from src.core.enums import TicketAuthor, TicketStatus
from src.core.logging import get_logger
from src.infrastructure.database.models.ticket import TicketMessage

if TYPE_CHECKING:
    from src.application.common.notifier import Notifier
    from src.application.services.bot_config import BotConfigService
    from src.infrastructure.database.models.user import User
    from src.infrastructure.remnawave.client import RemnawaveHttpClient

log = get_logger(__name__)

API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-haiku-4-5-20251001"

SYSTEM_PROMPT = """Ты — специалист технической поддержки VPN-сервиса. Общаешься с клиентом в чате поддержки.

СТИЛЬ ОБЩЕНИЯ:
- Максимально формально, вежливо и уважительно, на «вы».
- НЕ сообщай и не намекай, что ты ИИ, бот или автоответчик — ты живой сотрудник поддержки. Не придумывай себе имя — представляйся просто как поддержка сервиса.
- Отвечай кратко и по делу, конкретными шагами. Без markdown-разметки (никаких **, __, #, обратных кавычек). Эмодзи — умеренно.
- Опирайся на БАЗУ ЗНАНИЙ и КОНТЕКСТ КЛИЕНТА ниже. Сначала сверься с подпиской, сроком и числом устройств клиента.

ИНСТРУМЕНТЫ (используй, когда уместно):
- reset_devices — сбросить все привязанные устройства клиента (при жалобе на лимит/«превышено число устройств»/HWID).
- list_devices — посмотреть подключённые устройства.
- check_payment — проверить статус последней оплаты клиента.
- check_subscription — проверить, создана ли подписка и получить ссылку-подписку.
- report_to_owner — отправить владельцу баг-репорт (например: оплата прошла, но подписка не создалась). При этом можешь продолжать отвечать клиенту.

ЭСКАЛАЦИЯ — верни РОВНО одно слово ESCALATE (без других слов), если:
- клиент явно просит живого оператора/человека;
- вопрос про возврат денег/спор/жалоба/угроза/грубость;
- проблема НЕ решается или ты НЕ знаешь точного решения;
- вопрос не по теме поддержки (реклама, спам, сотрудничество).
Когда эскалируешь — клиенту автоматически сообщат, что подключается оператор. Лучше эскалировать, чем выдумать ответ.

ЗАПРЕТЫ:
- Не обещай возвраты, компенсации, скидки, бесплатные дни.
- Не выдумывай фактов, которых нет в базе знаний.
- НИКОГДА не рассказывай о внутреннем устройстве, коде, технологиях сервиса, кто им владеет.
- Обсуждай ТОЛЬКО вопросы про VPN и аккаунт клиента. На отвлечённые темы вежливо возвращай к теме; если клиент настойчиво уводит в сторону — верни ESCALATE."""

DEFAULT_KB = """СЕРВИС: VPN на протоколе VLESS/Reality.

ПОДКЛЮЧЕНИЕ: 1) установить приложение (Happ — iOS/Android/macOS/Windows, либо v2rayTun/Hiddify); 2) в боте/мини-аппе нажать «Подключиться» — подписка импортируется; 3) включить. Локация подбирается автоматически.

ТАРИФЫ И ПРОДЛЕНИЕ: тарифы и кнопка продления — в мини-аппе на «Главной» / во вкладке «Тарифы». Пробный период выдаётся новым пользователям один раз.

УСТРОЙСТВА: у подписки есть лимит устройств. Если «превышено число устройств» — сбрось устройства (reset_devices) и попроси обновить подписку в приложении.

ОПЛАТА: карта, СБП, криптовалюта, Telegram Stars. Платёж обрабатывается до 30 минут. Если не зачислилось — сначала check_payment; «в ожидании» < 30 мин → подождать; > 30 мин или деньги ушли, а подписки нет → report_to_owner + эскалация.

ВОЗВРАТ СРЕДСТВ: решает администратор — эскалируй (ESCALATE).

НЕ ПОДКЛЮЧАЕТСЯ: 1) обновить подписку в приложении (потянуть список серверов вниз); 2) проверить check_subscription — есть ли подписка и ссылка; 3) если ошибка «превышено число устройств» → reset_devices; 4) если не решается — эскалация."""

TOOLS = [
    {
        "name": "reset_devices",
        "description": "Сбросить (отвязать) ВСЕ устройства клиента. При жалобе на лимит/HWID/«превышено число устройств».",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "list_devices",
        "description": "Показать текущие привязанные устройства клиента (только чтение).",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "check_payment",
        "description": "Проверить статус последней оплаты клиента (прошла/в ожидании/нет).",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "check_subscription",
        "description": "Проверить, создана ли подписка клиента и получить ссылку-подписку.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "report_to_owner",
        "description": "Отправить владельцу баг-репорт. Клиенту при этом можно продолжать отвечать.",
        "input_schema": {
            "type": "object",
            "properties": {"note": {"type": "string", "description": "краткое описание проблемы"}},
            "required": ["note"],
        },
    },
]

_ESCALATE = "ESCALATE"
# Bound the owner's Claude cost: after this many AI replies without a human stepping in,
# stop auto-answering and hand the ticket to a person.
_MAX_AI_TURNS = 8


def _strip_md(t: str) -> str:
    """Telegram/mini-app don't render markdown — flatten it."""
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", t)
    t = re.sub(r"__(.+?)__", r"\1", t)
    t = re.sub(r"(?m)^\s*#{1,6}\s+", "", t)
    t = re.sub(r"(?m)^\s*\*\s+", "• ", t)
    return t.replace("**", "").replace("__", "").replace("`", "")


class AiSupportService:
    def __init__(
        self,
        remnawave: RemnawaveHttpClient,
        notifier: Notifier,
        config: BotConfigService,
        uow_factory: Any,  # callable returning a UnitOfWork context manager
    ) -> None:
        self._rw = remnawave
        self._notifier = notifier
        self._config = config
        self._uow = uow_factory

    async def enabled(self) -> bool:
        async with self._uow() as uow:
            on = bool(await self._config.value(uow, "AI_SUPPORT_ENABLED"))
            key = str(await self._config.value(uow, "AI_SUPPORT_API_KEY") or "").strip()
        return on and bool(key)

    async def handle_ticket(self, user: User, ticket_id: int) -> tuple[str, str | None]:
        """Answer or escalate a ticket, persisting the AI reply. Returns (outcome, text):
        ``("reply", text)`` — stored a 🤖-marked support message + set WAITING;
        ``("escalate", None)`` — alerted the owner, ticket left OPEN;
        ``("skip", None)`` — AI off, or a human is already in the loop. Single source of
        truth shared by the bot ticket handler and the mini-app cabinet endpoint."""
        if not await self.enabled():
            return "skip", None
        async with self._uow() as uow:
            rows = await uow.ticket_messages.list(ticket_id=ticket_id)
        msgs = sorted(rows, key=lambda m: m.id)  # base DAO list() has no ORDER BY
        ai_turns = 0
        for m in msgs:
            if m.author is TicketAuthor.ADMIN:
                if not m.text.startswith("🤖 "):
                    return "skip", None  # a real operator is in the loop → AI steps back
                ai_turns += 1
        if ai_turns >= _MAX_AI_TURNS:
            # Too many AI rounds without a human — hand off to bound the owner's Claude cost.
            with contextlib.suppress(Exception):
                await self._notifier.notify_admins(
                    f"⚠️ Тикет #{ticket_id}: ИИ ответил {ai_turns} раз без оператора — "
                    "передаю человеку.",
                    topic="alerts",
                )
            return "escalate", None
        history = [(m.author.value, m.text) for m in msgs]
        reply, escalate, _ = await self.generate_reply(user, history)
        if reply and not escalate:
            async with self._uow() as uow:
                await uow.ticket_messages.add(
                    TicketMessage(
                        ticket_id=ticket_id, author=TicketAuthor.ADMIN, text=("🤖 " + reply)[:4096]
                    )
                )
                t = await uow.tickets.get(ticket_id)
                if t is not None:
                    t.status = TicketStatus.WAITING
                await uow.commit()
            return "reply", reply
        with contextlib.suppress(Exception):
            await self._notifier.notify_admins(
                f"⚠️ Тикет #{ticket_id} эскалирован ИИ — нужен оператор (клиент {user.telegram_id}).",
                topic="alerts",
            )
        return "escalate", None

    async def _cfg(self) -> dict[str, str]:
        async with self._uow() as uow:
            v = self._config.value
            return {
                "key": str(await v(uow, "AI_SUPPORT_API_KEY") or "").strip(),
                "model": str(await v(uow, "AI_SUPPORT_MODEL") or "").strip() or DEFAULT_MODEL,
                "kb": str(await v(uow, "AI_SUPPORT_KNOWLEDGE_BASE") or "").strip() or DEFAULT_KB,
                "extra": str(await v(uow, "AI_SUPPORT_EXTRA_PROMPT") or "").strip(),
            }

    # ---- per-user context + tools -----------------------------------------

    async def _sub_uuid(self, user: User) -> tuple[PanelUserRef | None, Any]:
        if not user.current_subscription_id:
            return None, None
        async with self._uow() as uow:
            sub = await uow.subscriptions.get(user.current_subscription_id)
        if sub is None:
            return None, sub
        return sub.panel_ref, sub

    async def _context(self, user: User) -> str:
        uuid, sub = await self._sub_uuid(user)
        if sub is None:
            return "У клиента НЕТ активной подписки (не покупал или истекла)."
        exp = sub.expire_at.date().isoformat() if sub.expire_at else "—"
        trial = " Пробный период." if getattr(sub, "is_trial", False) else ""
        ndev = ""
        if uuid is not None:
            try:
                ndev = f" Подключено устройств сейчас: {len(await self._rw.get_devices(uuid))}."
            except Exception:
                ndev = ""
        return f"Подписка клиента: статус {sub.status.value}, действует до {exp}.{trial}{ndev}"

    async def _tool(
        self, name: str, user: User, tool_input: dict[str, Any], *, readonly: bool = False
    ) -> tuple[str, str | None]:
        """Return (text-for-model, short-action-note|None). ``readonly`` (admin Test) suppresses
        side effects — no real device reset or owner alert."""
        try:
            uuid, sub = await self._sub_uuid(user)
            if name == "reset_devices":
                if uuid is None:
                    return "У клиента нет активной подписки для сброса устройств.", None
                if readonly:
                    return "(тест) устройства были бы сброшены.", None
                devs = await self._rw.get_devices(uuid)
                for d in devs:
                    await self._rw.delete_device(uuid, d.hwid)
                return (
                    f"Сброшено устройств: {len(devs)}. Клиент может подключиться заново.",
                    f"сброс устройств: {len(devs)}",
                )
            if name == "list_devices":
                if uuid is None:
                    return "У клиента нет активной подписки.", None
                devs = await self._rw.get_devices(uuid)
                if not devs:
                    return "У клиента нет привязанных устройств.", None
                desc = "; ".join(
                    f"{d.device_model or '?'} ({d.platform or '?'})" for d in devs[:10]
                )
                return f"Устройств: {len(devs)} — {desc}", None
            if name == "check_payment":
                async with self._uow() as uow:
                    txns = await uow.transactions.list_recent(user.id, limit=1)
                if not txns:
                    return "Оплаченных транзакций нет.", None
                t = txns[0]
                return (
                    f"Последняя оплата: статус={t.status.value}, сумма={t.amount_minor / 100:.0f} ₽, "
                    f"способ={t.gateway_display_name or '—'}, дата={t.created_at.date().isoformat()}",
                    None,
                )
            if name == "check_subscription":
                if sub is None:
                    return "Подписка у клиента НЕ найдена.", None
                in_panel = None
                if uuid is not None:
                    try:
                        in_panel = (await self._rw.get_user(uuid)) is not None
                    except Exception:
                        in_panel = None
                inp = "да" if in_panel else ("нет" if in_panel is False else "неизвестно")
                async with self._uow() as uow:
                    hide = bool(await self._config.value(uow, "HIDE_SUBSCRIPTION_LINK"))
                # Honour the owner's hide-link policy — don't hand the raw URL to the model.
                link = (
                    "скрыта настройкой, НЕ разглашать" if hide else (sub.subscription_url or "нет")
                )
                return (
                    f"Подписка есть: статус {sub.status.value}, в панели={inp}, ссылка={link}",
                    None,
                )
            if name == "report_to_owner":
                note = str(tool_input.get("note") or "без описания")[:800]
                if readonly:
                    return "(тест) баг-репорт владельцу был бы отправлен.", None
                await self._notifier.notify_admins(
                    f"🐞 Баг-репорт от ИИ-поддержки\n👤 Клиент: {user.telegram_id}\n📋 {note}",
                    topic="alerts",
                )
                return "Баг-репорт отправлен владельцу.", "🐞 баг-репорт владельцу"
        except Exception as exc:
            log.warning("ai tool failed", tool=name, error=str(exc))
            return f"Инструмент недоступен: {exc}", None
        return "Неизвестный инструмент.", None

    # ---- main -------------------------------------------------------------

    async def generate_reply(
        self, user: User, history: list[tuple[str, str]], *, readonly: bool = False
    ) -> tuple[str | None, bool, list[str]]:
        """(reply|None, escalate, actions). None+escalate → hand to a human."""
        cfg = await self._cfg()
        if not cfg["key"]:
            return None, True, []

        messages: list[dict[str, Any]] = []
        for author, content in history:
            if not content:
                continue
            role = "user" if author == "user" else "assistant"
            c = content[2:].strip() if content.startswith("🤖 ") else content
            if messages and messages[-1]["role"] == role:
                messages[-1]["content"] += "\n" + c
            else:
                messages.append({"role": role, "content": c})
        while messages and messages[0]["role"] != "user":
            messages.pop(0)
        if not messages or messages[-1]["role"] != "user":
            return None, True, []

        stable = SYSTEM_PROMPT
        if cfg["extra"]:
            stable += "\n\nДОПОЛНИТЕЛЬНО ОТ ВЛАДЕЛЬЦА:\n" + cfg["extra"]
        stable += "\n\n=== БАЗА ЗНАНИЙ ===\n" + cfg["kb"]
        system = [
            {"type": "text", "text": stable, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": "\n\n=== КОНТЕКСТ КЛИЕНТА ===\n" + await self._context(user)},
        ]

        actions: list[str] = []
        headers = {
            "x-api-key": cfg["key"],
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                for _ in range(6):
                    body: dict[str, Any] = {
                        "model": cfg["model"],
                        "max_tokens": 700,
                        "system": system,
                        "messages": messages,
                        "tools": TOOLS,
                    }
                    r = await client.post(API_URL, json=body, headers=headers)
                    if r.status_code != 200:
                        log.warning(
                            "ai support http error", status=r.status_code, body=r.text[:200]
                        )
                        return None, True, actions
                    data = r.json()
                    content = data.get("content", [])
                    if data.get("stop_reason") == "tool_use":
                        messages.append({"role": "assistant", "content": content})
                        results = []
                        for b in content:
                            if b.get("type") == "tool_use":
                                res, act = await self._tool(
                                    b.get("name"), user, b.get("input") or {}, readonly=readonly
                                )
                                if act:
                                    actions.append(act)
                                results.append(
                                    {
                                        "type": "tool_result",
                                        "tool_use_id": b.get("id"),
                                        "content": res,
                                    }
                                )
                        messages.append({"role": "user", "content": results})
                        continue
                    text = "".join(
                        b.get("text", "") for b in content if b.get("type") == "text"
                    ).strip()
                    if not text or _ESCALATE in text.upper():
                        return None, True, actions
                    return _strip_md(text), False, actions
        except Exception as exc:
            log.warning("ai support call failed", error=str(exc))
            return None, True, actions
        return None, True, actions
