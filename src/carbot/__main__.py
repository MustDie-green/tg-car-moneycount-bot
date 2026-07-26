from __future__ import annotations

import asyncio
import logging

from .bot import run


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    try:
        asyncio.run(run())
    except (KeyboardInterrupt, SystemExit) as error:
        if str(error):
            print(error)


if __name__ == "__main__":
    main()
