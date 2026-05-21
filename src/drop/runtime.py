"""Volatile runtime state per page (pids, ports, tunnel_url). Phase 2.

PageRuntime dataclass, stored in ~/.drop/runtime.json. Provides
is_app_alive / is_proxy_alive / is_tunnel_alive via PID-probe.
"""
