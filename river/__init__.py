#!/usr/bin/env python3
"""river/__init__.py — river.ai 注册 + API Key 获取包"""
from .client import (AUTH, CONSOLE, LOGIN, REDIRECT_URI, AuthError,
                     PhoneVerificationRequired, authenticate, extract_rsc,
                     fetch_config, new_pkce, new_session, new_state)
from .keys import KeyManager, KeysError, get_me
from .register import (SKIP_PHONE_VERIFICATION, gen_password, register,
                       register_loop, register_via_browser_flow)
from .browser import Browser, register_via_browser

__all__ = [
    "CONSOLE", "AUTH", "LOGIN", "REDIRECT_URI",
    "AuthError", "PhoneVerificationRequired", "KeysError",
    "fetch_config", "new_session", "new_pkce", "new_state", "authenticate",
    "extract_rsc", "KeyManager", "get_me",
    "Browser", "register_via_browser",
    "register", "register_loop", "register_via_browser_flow",
    "gen_password", "SKIP_PHONE_VERIFICATION",
]