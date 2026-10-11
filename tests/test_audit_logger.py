import json
from pathlib import Path
import pytest

from app.monitoring.audit_logger import (
    classify_access_source,
    extract_client_info,
    log_audit_event,
    parse_user_agent,
    get_recent_audit_logs,
)


def test_parse_user_agent_desktop_chrome():
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    res = parse_user_agent(ua)
    assert res["browser"] == "Chrome"
    assert res["os"] == "Windows 10/11"
    assert res["device"] == "Desktop"


def test_parse_user_agent_iphone_safari():
    ua = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Mobile/15E148 Safari/604.1"
    res = parse_user_agent(ua)
    assert res["browser"] == "Safari"
    assert res["os"] == "iOS"
    assert res["device"] == "Mobile"


def test_parse_user_agent_edge():
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0"
    res = parse_user_agent(ua)
    assert res["browser"] == "Microsoft Edge"
    assert res["os"] == "Windows 10/11"
    assert res["device"] == "Desktop"


def test_classify_access_source_localhost():
    headers = {"host": "localhost:8501"}
    source = classify_access_source("127.0.0.1", "localhost:8501", headers)
    assert source == "Localhost"


def test_classify_access_source_ngrok():
    headers = {
        "host": "stubbly-denatured-unsmooth.ngrok-free.dev",
        "x-forwarded-proto": "https",
        "ngrok-trace-id": "abcd1234",
    }
    source = classify_access_source("49.37.102.14", "stubbly-denatured-unsmooth.ngrok-free.dev", headers)
    assert source == "Public Tunnel (Ngrok)"


def test_classify_access_source_cloudflare():
    headers = {
        "host": "cool-tunnel.trycloudflare.com",
        "cf-connecting-ip": "103.21.244.5",
        "cf-ray": "ray12345",
    }
    source = classify_access_source("103.21.244.5", "cool-tunnel.trycloudflare.com", headers)
    assert source == "Public Tunnel (Cloudflare)"


def test_classify_access_source_lan():
    headers = {"host": "192.168.127.157:8501"}
    source = classify_access_source("192.168.127.157", "192.168.127.157:8501", headers)
    assert source == "LAN / Local Network"


def test_classify_access_source_public_remote():
    headers = {"host": "trading.mycompany.com"}
    source = classify_access_source("8.8.8.8", "trading.mycompany.com", headers)
    assert source == "Public / Remote Internet"


def test_extract_client_info_with_ngrok_forwarding():
    headers = {
        "Host": "stubbly-denatured-unsmooth.ngrok-free.dev",
        "X-Forwarded-For": "49.37.102.14, 127.0.0.1",
        "X-Forwarded-Proto": "https",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
    }
    info = extract_client_info(headers_dict=headers)
    assert info["client_ip"] == "49.37.102.14"
    assert info["host"] == "stubbly-denatured-unsmooth.ngrok-free.dev"
    assert info["protocol"] == "https"
    assert info["access_source"] == "Public Tunnel (Ngrok)"
    assert info["browser"] == "Chrome"
    assert info["os"] == "Windows 10/11"


def test_log_audit_event_writes_to_file(tmp_path, monkeypatch):
    test_log = tmp_path / "audit.log"
    test_jsonl = tmp_path / "audit.jsonl"
    monkeypatch.setattr("app.monitoring.audit_logger.AUDIT_LOG_FILE", test_log)
    monkeypatch.setattr("app.monitoring.audit_logger.AUDIT_JSONL_FILE", test_jsonl)
    monkeypatch.setattr("app.monitoring.audit_logger.LOGS_DIR", tmp_path)

    custom_info = {
        "client_ip": "49.37.102.14",
        "host": "stubbly-denatured-unsmooth.ngrok-free.dev",
        "protocol": "https",
        "access_source": "Public Tunnel (Ngrok)",
        "user_agent": "Mozilla/5.0",
        "browser": "Chrome",
        "os": "Windows 10/11",
        "device": "Desktop",
        "country": "IN",
    }

    event = log_audit_event(
        event_type="BACKTEST_RUN",
        details={"symbol": "RELIANCE", "strategy": "RSI_MACD"},
        session_id="test_sess_1",
        client_info=custom_info,
    )

    assert event["event_type"] == "BACKTEST_RUN"
    assert event["client_ip"] == "49.37.102.14"
    assert test_log.exists()
    assert test_jsonl.exists()

    # Verify JSONL content
    with open(test_jsonl, "r", encoding="utf-8") as f:
        data = json.loads(f.readline())
    assert data["session_id"] == "test_sess_1"
    assert data["details"]["symbol"] == "RELIANCE"
    assert data["access_source"] == "Public Tunnel (Ngrok)"

    # Verify get_recent_audit_logs
    recent = get_recent_audit_logs(limit=10)
    assert len(recent) == 1
    assert recent[0]["session_id"] == "test_sess_1"
