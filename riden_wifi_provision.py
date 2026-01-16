#!/usr/bin/env python3
import argparse
import getpass
import socket
import threading
import time
from typing import List, Optional, Tuple

TARGET_PORT = 7001
LISTEN_PORT = 18266


def crc8_update(value: int, crc: int) -> int:
    value &= 0xFF
    crc &= 0xFF
    for _ in range(8):
        odd = ((value ^ crc) & 0x01) == 1
        crc >>= 1
        value >>= 1
        if odd:
            crc ^= 0x8C
    return crc & 0xFF


def encode_byte(data_byte: int, seq: int) -> Tuple[int, int, int]:
    if seq < 0 or seq > 127:
        raise ValueError("sequence header must be 0..127")

    crc = 0
    crc = crc8_update(data_byte, crc)
    crc = crc8_update(seq, crc)

    crc_high = (crc >> 4) & 0x0F
    crc_low = crc & 0x0F
    data_high = (data_byte >> 4) & 0x0F
    data_low = data_byte & 0x0F

    first = ((crc_high << 4) | data_high) + 40
    second = 296 + seq  # 256 + 40 + seq
    third = ((crc_low << 4) | data_low) + 40
    return first, second, third


def guide_code() -> Tuple[int, int, int, int]:
    return (515, 514, 513, 512)


def datum_code(ssid: bytes, password: bytes, bssid: bytes, data: bytes) -> Tuple[int, int, int, int, int]:
    total_len = 5 + len(data)
    pass_len = len(password)

    ssid_crc = 0
    for b in ssid:
        ssid_crc = crc8_update(b, ssid_crc)

    bssid_crc = 0
    for b in bssid:
        bssid_crc = crc8_update(b, bssid_crc)

    total_xor = total_len ^ pass_len ^ ssid_crc ^ bssid_crc
    for b in data:
        total_xor ^= b

    return total_len, pass_len, ssid_crc, bssid_crc, total_xor


def prepare_codes(ssid: bytes, password: bytes, bssid: bytes, data: bytes) -> List[int]:
    codes: List[int] = []
    header = datum_code(ssid, password, bssid, data)

    seq = 0
    for d in header:
        codes.extend(encode_byte(d, seq))
        seq += 1

    bssid_seq = len(header) + len(data)
    bssid_idx = 0
    data_idx = 0

    for d in data:
        if (data_idx % 4) == 0 and bssid_idx < len(bssid):
            codes.extend(encode_byte(bssid[bssid_idx], bssid_seq))
            bssid_seq += 1
            bssid_idx += 1

        codes.extend(encode_byte(d, seq))
        seq += 1
        data_idx += 1

    while bssid_idx < len(bssid):
        codes.extend(encode_byte(bssid[bssid_idx], bssid_seq))
        bssid_seq += 1
        bssid_idx += 1

    return codes


def parse_bssid(bssid: Optional[str]) -> bytes:
    if not bssid:
        return b""
    cleaned = bssid.replace(":", "").replace("-", "").strip()
    if len(cleaned) % 2 != 0:
        raise ValueError("BSSID hex string must have even length")
    return bytes.fromhex(cleaned)


def send_loop(ssid: bytes, password: bytes, bssid: bytes, server_ip_bytes: bytes,
              repeat: int, use_broadcast: bool) -> None:
    data = server_ip_bytes + password + ssid
    codes = prepare_codes(ssid, password, bssid, data)

    def next_target(counter: int) -> Tuple[str, int]:
        if use_broadcast:
            return "255.255.255.255", TARGET_PORT
        n = (counter % 100) + 1
        return f"234.{n}.{n}.{n}", TARGET_PORT

    def send_packet(sock: socket.socket, size: int, addr: Tuple[str, int]) -> None:
        sock.sendto(bytearray(size), addr)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    if use_broadcast:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

    try:
        for _ in range(repeat):
            idx = 0
            start = time.monotonic()
            next_t = start
            while time.monotonic() - start < 2.0 or idx != 0:
                now = time.monotonic()
                if now >= next_t:
                    addr = next_target(idx)
                    send_packet(sock, guide_code()[idx], addr)
                    idx = (idx + 1) % 4
                    next_t = now + 0.008

            idx = 0
            start = time.monotonic()
            next_t = start
            while time.monotonic() - start < 4.0 or idx != 0:
                now = time.monotonic()
                if now >= next_t:
                    addr = next_target(idx)
                    send_packet(sock, codes[idx], addr)
                    idx = (idx + 1) % len(codes)
                    next_t = now + 0.008
    finally:
        sock.close()


def recv_results(expected: int, timeout: int) -> List[Tuple[str, str]]:
    results: List[Tuple[str, str]] = []
    seen = set()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", LISTEN_PORT))
    sock.settimeout(timeout)

    try:
        while True:
            try:
                data, _addr = sock.recvfrom(4096)
            except socket.timeout:
                break

            if not data:
                continue
            key = bytes(data)
            if key in seen:
                continue
            seen.add(key)

            if len(data) >= 11:
                mac_bytes = data[1:7]
                ip_bytes = data[-4:]
                mac = "".join(f"{b:02x}" for b in mac_bytes)
                ip = ".".join(str(b) for b in ip_bytes)
                results.append((mac, ip))

            if expected > 0 and len(results) >= expected:
                break
    finally:
        sock.close()

    return results


def esptouch_provision(ssid: str, password: str, bssid: Optional[str],
                       server_ip: str, expected: int, timeout: int,
                       repeat: int, use_broadcast: bool) -> List[Tuple[str, str]]:
    ssid_b = ssid.encode()
    password_b = (password or "").encode()
    bssid_b = parse_bssid(bssid)

    parts = [int(x) for x in server_ip.split(".") if x]
    if len(parts) != 4:
        raise ValueError("server_ip must be an IPv4 address")
    server_ip_bytes = bytes(parts)

    sender = threading.Thread(
        target=send_loop,
        args=(ssid_b, password_b, bssid_b, server_ip_bytes, repeat, use_broadcast),
        daemon=True,
    )
    sender.start()

    return recv_results(expected, timeout)


def prompt_nonempty(label: str) -> str:
    while True:
        value = input(label).strip()
        if value:
            return value
        print("Please enter a value.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Riden WiFi Provisioning (ESPTouch/SmartConfig)")
    parser.add_argument("--server-ip", help="IP the PSU should connect to after provisioning (e.g. Home Assistant)")
    parser.add_argument("--ssid", help="WiFi SSID (2.4GHz)")
    parser.add_argument("--password", help="WiFi password")
    parser.add_argument("--bssid", help="AP BSSID (MAC), optional")
    parser.add_argument("--timeout", type=int, default=60, help="Receive timeout (seconds)")
    parser.add_argument("--count", type=int, default=1, help="Expected number of devices")
    parser.add_argument("--repeat", type=int, default=8, help="Transmit repeat loops")
    parser.add_argument("--multicast", action="store_true", help="Use multicast instead of broadcast")
    args = parser.parse_args()

    print("Step 1: Enter the server IP (Home Assistant) that the PSU should connect to.")
    server_ip = args.server_ip or prompt_nonempty("Server IP: ")
    print("Step 2: Enter WiFi credentials for the 2.4GHz network.")
    ssid = args.ssid or prompt_nonempty("SSID: ")
    password = args.password if args.password is not None else getpass.getpass("Password: ")
    bssid = args.bssid or input("BSSID (optional, press Enter to skip): ").strip() or None

    print("\nProvisioning... (make sure the Riden WiFi is in config/SmartConfig mode)")
    results = esptouch_provision(
        ssid=ssid,
        password=password,
        bssid=bssid,
        server_ip=server_ip,
        expected=args.count,
        timeout=args.timeout,
        repeat=args.repeat,
        use_broadcast=not args.multicast,
    )

    if not results:
        print("Please check the device display or your Home Assistant logs to confirm configuration.")
        return 2

    print("Devices reported:")
    for mac, ip in results:
        print(f"- mac={mac} ip={ip}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
