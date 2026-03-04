"""
aiohttp session tagging for Azure SDK traceability.

Context-aware logging of ClientSession open/close to diagnose unclosed sessions.
Import tag_aiohttp_session and wrap SDK instantiations to assign meaningful tags.
"""
import contextvars
import logging
from contextlib import contextmanager

# Context variable to hold the meaningful tag for the current async task/thread
aiohttp_session_tag = contextvars.ContextVar("aiohttp_session_tag", default="Untagged/Background")


@contextmanager
def tag_aiohttp_session(name: str):
    """Wrap SDK instantiations in this to meaningfully tag their underlying aiohttp sessions."""
    token = aiohttp_session_tag.set(name)
    try:
        yield
    finally:
        aiohttp_session_tag.reset(token)


def _patch_aiohttp_session_tracing():
    try:
        import aiohttp
    except ImportError:
        return
    if getattr(aiohttp.ClientSession, "_daibai_patched", False):
        return

    _original_init = aiohttp.ClientSession.__init__
    _original_close = aiohttp.ClientSession.close
    _session_ids = {"next": 0}

    def _logged_init(self, *args, **kwargs):
        _session_ids["next"] += 1
        sid = _session_ids["next"]
        self._daibai_sid = sid

        # Pull the meaningful tag from the current context
        self._daibai_tag = aiohttp_session_tag.get()

        logging.getLogger("daibai.aiohttp").info("[aiohttp] Session OPEN id=%s tag=[%s]", sid, self._daibai_tag)
        _original_init(self, *args, **kwargs)

    async def _logged_close(self):
        sid = getattr(self, "_daibai_sid", "?")
        tag = getattr(self, "_daibai_tag", "?")
        logging.getLogger("daibai.aiohttp").info("[aiohttp] Session CLOSE id=%s tag=[%s]", sid, tag)
        return await _original_close(self)

    aiohttp.ClientSession.__init__ = _logged_init
    aiohttp.ClientSession.close = _logged_close
    aiohttp.ClientSession._daibai_patched = True
