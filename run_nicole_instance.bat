@echo off
rem Start Nicole's geïsoleerde AgentOS-instance (poort 1251) als achtergrondproces.
rem Web-accessible maken zou via een TLS-reverse-proxy moeten (Caddy/Cloudflare
rem Tunnel) — NIET poort 1251 openzetten op het internet zonder TLS, want dan
rem gaat AGENTOS_PASSWORD in platte tekst over de lijn.
set "PYTHONIOENCODING=utf-8"
call "D:\apps\agentos\agentos_service_nicole.cmd"
exit /b 0
