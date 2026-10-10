from aiogram import Router

from . import commands, expenses, private, service_flow

_root: Router | None = None


def build_router() -> Router:
    """Корневой роутер бота.

    Роутеры хендлеров — модульные синглтоны (обычный для aiogram приём), поэтому
    привязать их можно ровно один раз. Результат кэшируем, чтобы повторный вызов
    не падал с «Router is already attached».
    """
    global _root
    if _root is None:
        # Порядок важен: личка забирает своё до общих команд (там свой /start),
        # а детект трат идёт последним — он самый жадный
        _root = Router(name="root")
        # Ответ пробегом на «Отметить в ТО?» должен перехватываться раньше всех
        _root.include_router(service_flow.router)
        _root.include_router(private.router)
        _root.include_router(commands.router)
        _root.include_router(expenses.router)
    return _root


__all__ = ["build_router", "commands", "expenses", "private", "service_flow"]
