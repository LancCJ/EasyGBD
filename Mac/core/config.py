# config.py

import platform
import random

# SIP Server Configuration
SIP_SERVER_IP = "192.168.1.100"  # Replace with actual SIP server IP
SIP_SERVER_PORT = 5060           # SIP server port
SIP_SERVER_ID = "34020000002000000001" # GB28181 standard server ID
SIP_SERVER_DOMAIN = SIP_SERVER_ID[0:10]

# Local Device Configuration
LOCAL_IP = "192.168.1.10"        # Replace with actual Mac IP
LOCAL_PORT = 5060                # Local SIP port (UDP)
DEVICE_ID = "34020000001110000001" # Device ID (must match 111 for NVR/Device)
CHANNEL_ID = "34020000001310000001" # Camera Channel ID (131)
PASSWORD = "12345"               # SIP Password
MANUFACTURER = "AntigravityMac"

# Media configuration
SSRC_BASE = "0111000000"
VIDEO_CODEC = "H264"          # H264（默认）或 H265
AUDIO_CODEC = "G711A"         # G711A（默认）、G711U、G723 或 AAC
GB28181_VERSION = "2016"      # GB/T 28181-2016（默认）或 2022
TALK_ENABLED = False           # 是否接受平台发起的 s=Talk / s=Broadcast 语音会话
TALK_CODEC = "G711A"          # 基础语音对讲仅支持 G711A 或 G711U

# Generate a random Call-ID
def generate_call_id():
    return f"{random.randint(10000000, 99999999)}@{LOCAL_IP}"
