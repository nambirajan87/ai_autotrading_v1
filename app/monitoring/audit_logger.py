import ipaddress
import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("app.audit")

# Audit log storage paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
LOGS_DIR = PROJECT_ROOT / "logs"
AUDIT_LOG_FILE = LOGS_DIR / "audit.log"
AUDIT_JSONL_FILE = LOGS_DIR / "audit.jsonl"

_log_lock = threading.Lock()


def _ensure_logs_dir() -> None:
    """Ensure the logs directory exists."""
    os.makedirs(LOGS_DIR, exist_ok=True)


def parse_user_agent(ua_str: Optional[str]) -> Dict[str, str]:
    """Parse browser, OS, and device type from User-Agent string."""
    if not ua_str:
        return {"browser": "Unknown", "os": "Unknown", "device": "Unknown"}

    ua = ua_str.lower()

    # Device
    if any(k in ua for k in ("ipad", "tablet")):
        device = "Tablet"
    elif any(k in ua for k in ("mobile", "iphone", "android")):
        device = "Mobile"
    elif any(k in ua for k in ("bot", "spider", "crawl", "curl", "python", "postman")):
        device = "Bot / Automation"
    else:
        device = "Desktop"

    # Operating System
    if any(k in ua for k in ("iphone", "ipad", "ipod", "ios")):
        os_name = "iOS"
    elif "android" in ua:
        os_name = "Android"
    elif "windows nt 10.0" in ua:
        os_name = "Windows 10/11"
    elif "windows nt 6.3" in ua:
        os_name = "Windows 8.1"
    elif "windows nt 6.1" in ua:
        os_name = "Windows 7"
    elif "windows" in ua:
        os_name = "Windows"
    elif "macintosh" in ua or "mac os x" in ua:
        os_name = "macOS"
    elif "linux" in ua:
        os_name = "Linux"
    elif "crkey" in ua or "chromecast" in ua:
        os_name = "Chromecast"
    else:
        os_name = "Unknown OS"

    # Browser
    if "edg/" in ua:
        browser = "Microsoft Edge"
    elif "chrome/" in ua and "chromium" not in ua:
        browser = "Chrome"
    elif "firefox/" in ua:
        browser = "Firefox"
    elif "safari/" in ua:
        browser = "Safari"
    elif "opera/" in ua or "opr/" in ua:
        browser = "Opera"
    elif any(k in ua for k in ("curl", "python", "postman", "ngrok", "requests")):
        browser = "Script / Tool"
    else:
        browser = "Unknown Browser"

    return {"browser": browser, "os": os_name, "device": device}


def classify_access_source(client_ip: str, host: str, headers: Dict[str, str]) -> str:
    """
    Classify whether the traffic is from Localhost, LAN, Ngrok, Cloudflare, or Public Web.
    """
    host_lower = (host or "").lower()
    headers_keys = {k.lower() for k in headers.keys()}

    # Ngrok tunnel detection
    if any(domain in host_lower for domain in ("ngrok-free.dev", "ngrok.io", "ngrok.app")) or "ngrok-trace-id" in headers_keys:
        return "Public Tunnel (Ngrok)"

    # Cloudflare tunnel detection
    if "trycloudflare.com" in host_lower or "cloudflare" in host_lower or "cf-ray" in headers_keys or "cf-connecting-ip" in headers_keys:
        return "Public Tunnel (Cloudflare)"

    # Localhost check
    if host_lower.startswith(("localhost", "127.0.0.1", "[::1]")) or client_ip in ("127.0.0.1", "::1", "localhost"):
        return "Localhost"

    # IP address parsing
    try:
        ip_obj = ipaddress.ip_address(client_ip.strip())
        if ip_obj.is_loopback:
            return "Localhost"
        if ip_obj.is_private:
            return "LAN / Local Network"
        return "Public / Remote Internet"
    except Exception:
        pass

    # Fallback host heuristic for LAN addresses
    if any(host_lower.startswith(prefix) for prefix in ("192.168.", "10.", "172.16.", "172.17.", "172.18.", "172.19.", "172.2", "172.30.", "172.31.")):
        return "LAN / Local Network"

    return "Public / Remote Internet" if host else "Localhost"


def extract_client_info(headers_dict: Optional[Dict[str, str]] = None, raw_ip: Optional[str] = None) -> Dict[str, Any]:
    """
    Extracts and standardizes client request details from headers and context.
    Works seamlessly with Streamlit headers (st.context.headers) or custom dicts.
    """
    headers: Dict[str, str] = {}
    ip_from_context: Optional[str] = raw_ip

    # If inside Streamlit and headers not supplied, attempt to read st.context
    if headers_dict is None:
        try:
            import streamlit as st
            ctx = getattr(st, "context", None)
            if ctx is not None:
                ctx_headers = getattr(ctx, "headers", None)
                if ctx_headers is not None:
                    # StreamlitHeaders supports mapping protocol
                    headers = {str(k).lower(): str(v) for k, v in ctx_headers.items()}
                if not ip_from_context:
                    ip_from_context = getattr(ctx, "ip_address", None)
        except Exception:
            pass
    else:
        headers = {str(k).lower(): str(v) for k, v in headers_dict.items()}

    # Resolve Client IP (priority: Cloudflare -> X-Forwarded-For -> X-Real-IP -> st.context.ip_address -> localhost)
    client_ip = "127.0.0.1"
    if headers.get("cf-connecting-ip"):
        client_ip = headers["cf-connecting-ip"].strip()
    elif headers.get("x-forwarded-for"):
        # The first IP in the comma-separated list is the original client IP
        client_ip = headers["x-forwarded-for"].split(",")[0].strip()
    elif headers.get("x-real-ip"):
        client_ip = headers["x-real-ip"].strip()
    elif ip_from_context:
        client_ip = str(ip_from_context).strip()

    # Resolve Host & Protocol
    host = headers.get("x-forwarded-host") or headers.get("host") or "localhost:8501"
    protocol = headers.get("x-forwarded-proto") or ("https" if "443" in host or "ngrok" in host or "cloudflare" in host else "http")

    # Classification
    access_source = classify_access_source(client_ip, host, headers)

    # Parse User-Agent
    user_agent_str = headers.get("user-agent", "")
    ua_meta = parse_user_agent(user_agent_str)

    # Country / Geo if forwarded
    country = headers.get("cf-ipcountry") or headers.get("x-country-code") or "N/A"

    return {
        "client_ip": client_ip,
        "host": host,
        "protocol": protocol,
        "access_source": access_source,
        "user_agent": user_agent_str,
        "browser": ua_meta["browser"],
        "os": ua_meta["os"],
        "device": ua_meta["device"],
        "country": country,
    }


def log_audit_event(
    event_type: str,
    details: Optional[Dict[str, Any]] = None,
    session_id: Optional[str] = None,
    client_info: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Log an audit event to logs/audit.log, logs/audit.jsonl, and Python logger.
    Thread-safe and guaranteed not to throw exceptions into calling code.
    """
    _ensure_logs_dir()

    now_utc = datetime.now(timezone.utc)
    now_local = datetime.now()
    timestamp_iso = now_utc.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    timestamp_local_str = now_local.strftime("%Y-%m-%d %H:%M:%S")

    info = client_info or extract_client_info()

    # Determine session ID
    resolved_session_id = session_id
    if not resolved_session_id:
        try:
            import streamlit as st
            if "_audit_session_id" in st.session_state:
                resolved_session_id = st.session_state["_audit_session_id"]
        except Exception:
            pass
    if not resolved_session_id:
        resolved_session_id = str(uuid.uuid4())[:8]

    event_payload: Dict[str, Any] = {
        "timestamp_utc": timestamp_iso,
        "timestamp_local": timestamp_local_str,
        "access_source": info.get("access_source", "Unknown"),
        "client_ip": info.get("client_ip", "127.0.0.1"),
        "host": info.get("host", "unknown"),
        "protocol": info.get("protocol", "http"),
        "event_type": event_type,
        "session_id": resolved_session_id,
        "browser": info.get("browser", "Unknown"),
        "os": info.get("os", "Unknown"),
        "device": info.get("device", "Desktop"),
        "country": info.get("country", "N/A"),
        "details": details or {},
    }

    # Formatted human-readable line for audit.log
    details_str = json.dumps(details, default=str) if details else "{}"
    formatted_line = (
        f"[{timestamp_local_str}] [AUDIT] [{event_payload['access_source']}] "
        f"[IP: {event_payload['client_ip']}] [HOST: {event_payload['host']}] "
        f"[EVENT: {event_type}] [DEVICE: {event_payload['browser']} on {event_payload['os']} ({event_payload['device']})] "
        f"[SESSION: {resolved_session_id}] Details: {details_str}\n"
    )

    jsonl_line = json.dumps(event_payload, default=str) + "\n"

    try:
        with _log_lock:
            with open(AUDIT_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(formatted_line)
            with open(AUDIT_JSONL_FILE, "a", encoding="utf-8") as f:
                f.write(jsonl_line)
    except Exception as exc:
        logger.error("Failed to write audit log: %s", exc)

    logger.info(
        "AUDIT [%s] [%s] IP: %s | Host: %s | Event: %s",
        event_payload["access_source"],
        resolved_session_id,
        event_payload["client_ip"],
        event_payload["host"],
        event_type,
    )

    return event_payload


def record_session_visit() -> Dict[str, Any]:
    """
    Hook to place at the top of a Streamlit app.
    Logs SESSION_START once per browser session.
    Returns the client info dictionary.
    """
    try:
        import streamlit as st

        # Assign session ID if not present
        if "_audit_session_id" not in st.session_state:
            st.session_state["_audit_session_id"] = str(uuid.uuid4())[:8]

        session_id = st.session_state["_audit_session_id"]

        # Only log SESSION_START once per browser session
        if "_audit_session_logged" not in st.session_state:
            info = extract_client_info()
            st.session_state["_audit_client_info"] = info
            st.session_state["_audit_session_logged"] = True
            log_audit_event(
                event_type="SESSION_START",
                details={"status": "connected"},
                session_id=session_id,
                client_info=info,
            )
            return info
        else:
            return st.session_state.get("_audit_client_info", extract_client_info())
    except Exception as exc:
        logger.warning("record_session_visit encountered error: %s", exc)
        return extract_client_info()


def get_recent_audit_logs(limit: int = 50) -> List[Dict[str, Any]]:
    """
    Read the most recent audit records from logs/audit.jsonl.
    Returns newest entries first.
    """
    if not AUDIT_JSONL_FILE.exists():
        return []

    records: List[Dict[str, Any]] = []
    try:
        with open(AUDIT_JSONL_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except Exception as exc:
        logger.error("Failed to read audit logs: %s", exc)
        return []

    records.reverse()
    return records[:limit]


def get_audit_summary() -> Dict[str, Any]:
    """Calculate summary statistics from the audit log."""
    records = get_recent_audit_logs(limit=1000)
    source_counts: Dict[str, int] = {}
    ip_counts: Dict[str, int] = {}
    event_counts: Dict[str, int] = {}
    sessions = set()

    for r in records:
        src = r.get("access_source", "Unknown")
        source_counts[src] = source_counts.get(src, 0) + 1

        ip = r.get("client_ip", "Unknown")
        ip_counts[ip] = ip_counts.get(ip, 0) + 1

        evt = r.get("event_type", "Unknown")
        event_counts[evt] = event_counts.get(evt, 0) + 1

        sess = r.get("session_id")
        if sess:
            sessions.add(sess)

    return {
        "total_records": len(records),
        "unique_sessions": len(sessions),
        "source_counts": source_counts,
        "top_ips": sorted(ip_counts.items(), key=lambda x: x[1], reverse=True)[:5],
        "event_counts": event_counts,
    }


if __name__ == "__main__":
    import argparse
    import sys

    # Support UTF-8 terminal output safely on Windows
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="AI Auto-Trading App Access & Audit Logger")
    parser.add_argument("--tail", action="store_true", help="Print recent audit log entries")
    parser.add_argument("--summary", action="store_true", help="Display summary statistics")
    parser.add_argument("-n", type=int, default=15, help="Number of records to display (default: 15)")
    args = parser.parse_args()

    if args.summary:
        summary = get_audit_summary()
        print("\n" + "=" * 50)
        print("[AUDIT] ACCESS & AUDIT LOG SUMMARY")
        print("=" * 50)
        print(f"Total Logged Events : {summary['total_records']}")
        print(f"Unique Sessions     : {summary['unique_sessions']}")
        print("\nTraffic Breakdown:")
        for src, cnt in summary["source_counts"].items():
            print(f"  * {src:<28}: {cnt}")
        print("\nTop Events:")
        for evt, cnt in summary["event_counts"].items():
            print(f"  * {evt:<28}: {cnt}")
        print("\nTop Client IPs:")
        for ip, cnt in summary["top_ips"]:
            print(f"  * {ip:<28}: {cnt} hits")
        print("=" * 50 + "\n")
    else:
        recent = get_recent_audit_logs(limit=args.n)
        print(f"\nShowing last {len(recent)} audit records (file: {AUDIT_LOG_FILE}):\n")
        if not recent:
            print("No audit records logged yet.")
        for r in recent:
            src_tag = f"[{r.get('access_source', 'Unknown')}]"
            print(f"{r.get('timestamp_local')} {src_tag:<24} IP: {r.get('client_ip'):<15} Host: {r.get('host'):<32} Event: {r.get('event_type')}")
        print("")

