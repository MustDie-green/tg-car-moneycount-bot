from aiogram import Router

from . import commands, expenses

_root: Router | None = None


def build_router() -> Router:
    """Корневой роутер бота.

    Роутеры хендлеров — модульные синглтоны (обычный для aiogram приём), поэтому
    привязать их можно ровно один раз. Результат кэшируем, чтобы повторный вызов
    не падал с «Router is already attached».
    """
    global _root
    if _root is None:
        # Порядок важен: команды разбираются раньше, чем детект трат
        _root = Router(name="root")
        _root.include_router(commands.router)
        _root.include_router(expenses.router)
    return _root


__all__ = ["build_router", "commands", "expenses"]
