"""Basic-auth reverse proxy for drop apps. Phase 5 (port from v1, no
substantial changes — already clean).

Stdlib http.server + urllib.request. Rejects WebSocket/Upgrade with 501.
Validates path is absolute (SSRF guard).
"""
