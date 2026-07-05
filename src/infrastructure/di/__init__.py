"""Dependency wiring. The base uses a composition root (``AppContainer``) that builds the
singletons and yields a per-operation :class:`UnitOfWork`. It is Dishka-ready: when the bot
introduces request-scoped handlers, swap ``AppContainer`` for Dishka providers with the same
object graph (Scope.APP for adapters/factories, Scope.REQUEST for the UoW)."""

from __future__ import annotations

from src.infrastructure.di.container import AppContainer

__all__ = ["AppContainer"]
