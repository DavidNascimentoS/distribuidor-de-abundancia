"""Event loop factory for local development on Windows.

psycopg's async mode refuses Windows' default ProactorEventLoop. Uvicorn only switches to a
selector loop when it runs with --reload (subprocess mode), so for a plain run pass:

    uvicorn app.main:app --loop app.loop:selector_loop

Linux (production) never needs this.
"""

import asyncio
import selectors


def selector_loop() -> asyncio.AbstractEventLoop:
    return asyncio.SelectorEventLoop(selectors.SelectSelector())
