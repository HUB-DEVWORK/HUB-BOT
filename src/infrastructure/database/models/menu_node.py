"""MenuNode — one button/screen of the bot menu constructor (admin screen 05).

A tree: ``parent_id`` is NULL for root-level buttons; a SCREEN node owns child buttons
and its own message text. Deleting a node removes its whole subtree (cascade). Order
among siblings is ``order_index``.
"""

from __future__ import annotations

from sqlalchemy import Boolean, Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.enums import MenuNodeKind
from src.infrastructure.database.base import Base, IntPk, TimestampMixin


class MenuNode(IntPk, TimestampMixin, Base):
    __tablename__ = "menu_nodes"

    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("menu_nodes.id", ondelete="CASCADE"), index=True
    )
    order_index: Mapped[int] = mapped_column(default=0)  # position within its row
    row_index: Mapped[int] = mapped_column(
        default=0
    )  # buttons sharing a row_index sit side by side

    label: Mapped[str] = mapped_column(String(64))  # button caption
    kind: Mapped[MenuNodeKind] = mapped_column(
        Enum(MenuNodeKind, native_enum=False, length=16), default=MenuNodeKind.ACTION
    )
    # SCREEN: message text shown when the submenu opens. LINK: URL. ACTION: action code.
    payload: Mapped[str | None] = mapped_column(String(4096))
    custom_emoji_id: Mapped[str | None] = mapped_column(String(32))  # premium emoji
    color: Mapped[str | None] = mapped_column(String(9))  # #RRGGBB — Bot API button style
    image_path: Mapped[str | None] = mapped_column(String(512))  # screen photo (uploads/)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    children: Mapped[list[MenuNode]] = relationship(
        cascade="all, delete-orphan", order_by="MenuNode.order_index"
    )
