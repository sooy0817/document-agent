"""Tests never inherit a real API key and cannot connect to external networks.

Loopback remains available for Windows asyncio self-pipes and local ASGI tests.
Actual GPT-4.1 execution lives in the explicit scripts/* E2E commands, not pytest.
"""
import ipaddress
import socket

import pytest


TEST_KEY='test-key-not-a-real-openai-credential'


def _loopback(host):
    if isinstance(host,bytes):
        host=host.decode('ascii')
    if host in ('localhost',None):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@pytest.fixture(autouse=True)
def no_external_network_or_real_api_key(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY',TEST_KEY)
    monkeypatch.setenv('OPENAI_MODEL','test-model')
    monkeypatch.setenv('LANGSMITH_TRACING','false')
    monkeypatch.setenv('LANGCHAIN_TRACING_V2','false')
    original_dns=socket.getaddrinfo
    original_connect=socket.socket.connect
    original_connect_ex=socket.socket.connect_ex

    def dns(host,*args,**kwargs):
        if not _loopback(host):
            raise RuntimeError('TEST_EXTERNAL_NETWORK_BLOCKED')
        return original_dns(host,*args,**kwargs)

    def check(address):
        if isinstance(address,tuple) and not _loopback(address[0]):
            raise RuntimeError('TEST_EXTERNAL_NETWORK_BLOCKED')

    def connect(sock,address):
        check(address)
        return original_connect(sock,address)

    def connect_ex(sock,address):
        check(address)
        return original_connect_ex(sock,address)

    monkeypatch.setattr(socket,'getaddrinfo',dns)
    monkeypatch.setattr(socket.socket,'connect',connect)
    monkeypatch.setattr(socket.socket,'connect_ex',connect_ex)
