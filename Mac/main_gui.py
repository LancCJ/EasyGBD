import sys
import random
import re
import socket
import subprocess
import string
import time
import html
import json
import os
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QFormLayout, QLineEdit, QPushButton,
                             QTextEdit, QLabel, QFrame, QComboBox, QScrollArea,
                             QGroupBox, QSizePolicy, QCheckBox, QSplitter, QListView,
                             QGraphicsView, QGraphicsScene, QGridLayout, QDialog,
                             QDialogButtonBox, QFileDialog, QSlider, QMessageBox,
                             QInputDialog, QTabWidget, QTableWidget, QTableWidgetItem,
                             QHeaderView, QSpinBox, QAbstractItemView)
from PySide6.QtCore import (
    QCameraPermission, Qt, QThread, QTimer, Signal, QObject, QUrl, QSizeF
)
from PySide6.QtGui import QTransform
from PySide6.QtMultimedia import (
    QAudioOutput, QCamera, QMediaCaptureSession, QMediaDevices, QMediaPlayer
)
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem

from core.gb28181_device import GB28181Device
from core.media_control import MediaController

# ──────────────────────── IP 工具 ────────────────────────

def get_all_local_ips():
    primary_ips = ["127.0.0.1"]
    real_ips = []
    virtual_ips = []
    try:
        output = subprocess.check_output(["ifconfig"], text=True)
        for match in re.finditer(r'inet\s+(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})', output):
            ip = match.group(1)
            if ip == "127.0.0.1":
                continue
            elif ip.startswith("198.18.") or ip.startswith("198.19."):
                virtual_ips.append(ip)
            else:
                real_ips.append(ip)
    except Exception:
        pass
    # 优先返回物理网卡真实局域网 IP，接着是虚拟网卡 IP，最后是 127.0.0.1 环回 IP
    return (real_ips if real_ips else []) + (virtual_ips if virtual_ips else []) + primary_ips


def best_local_ip_for_server(server_ip, all_ips):
    if not server_ip or not all_ips:
        return all_ips[0] if all_ips else "127.0.0.1"
    prefix3 = ".".join(server_ip.split(".")[:3])
    prefix2 = ".".join(server_ip.split(".")[:2])
    for ip in all_ips:
        if ip.startswith(prefix3 + "."):
            return ip
    for ip in all_ips:
        if ip.startswith(prefix2 + "."):
            return ip
    return all_ips[0]


def get_backend_default_config():
    """
    尝试从后端项目配置文件中获取默认的国标和媒体服务器配置
    """
    config = {
        'media_ip': '10.211.55.4',
        'rtp_port': '30000',
        'sip_ip': '10.185.4.157',
        'sip_port': '5060',
        'password': 'change-me',
        'zlm_base_url': 'http://10.211.55.4:80',
        'zlm_secret': 'q2OUqP1sS5DlG20tBOuYB8o49WjawyS7'
    }
    
    import os
    import re
    
    # 获取 main_gui.py 的所在目录
    base_dir = os.path.dirname(os.path.abspath(__file__))
    
    # 1. 尝试读取 application.yml 寻找 active profile 和 rtp 端口范围
    active_profile = 'company'  # 默认兜底
    app_yml_path = os.path.join(base_dir, "../works/monitoring-assistant/backend/src/main/resources/application.yml")
    if not os.path.exists(app_yml_path):
        app_yml_path = "/Users/chenjian/Documents/codes/works/monitoring-assistant/backend/src/main/resources/application.yml"
        
    if os.path.exists(app_yml_path):
        try:
            with open(app_yml_path, 'r', encoding='utf-8') as f:
                content = f.read()
                profile_match = re.search(r'active:\s*(\w+)', content)
                if profile_match:
                    active_profile = profile_match.group(1)
                
                rtp_start_match = re.search(r'rtp-port-range-start:\s*(?:\$\{ZLM_RTP_PORT_START:)?(\d+)\}?', content)
                if rtp_start_match:
                    config['rtp_port'] = rtp_start_match.group(1)
        except Exception as e:
            print(f"Error reading application.yml: {e}")
            
    # 2. 读取对应 profile 的 yml 文件
    profile_yml = f"application-{active_profile}.yml"
    profile_paths = [
        os.path.join(base_dir, f"../works/monitoring-assistant/backend/src/main/resources/{profile_yml}"),
        f"/Users/chenjian/Documents/codes/works/monitoring-assistant/backend/src/main/resources/{profile_yml}",
        os.path.join(base_dir, "../works/monitoring-assistant/backend/src/main/resources/application-company.yml"),
        "/Users/chenjian/Documents/codes/works/monitoring-assistant/backend/src/main/resources/application-company.yml",
        os.path.join(base_dir, "../works/monitoring-assistant/backend/src/main/resources/application-dev.yml"),
        "/Users/chenjian/Documents/codes/works/monitoring-assistant/backend/src/main/resources/application-dev.yml"
    ]
    
    for path in profile_paths:
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    
                    media_ip_match = re.search(r'media-ip:\s*(?:\$\{GB28181_MEDIA_IP:)?([\d\.]+)\}?', content)
                    if media_ip_match:
                        config['media_ip'] = media_ip_match.group(1)
                        
                    sip_ip_match = re.search(r'sip-ip:\s*(?:\$\{GB28181_SIP_IP:)?([\d\.]+)\}?', content)
                    if sip_ip_match:
                        config['sip_ip'] = sip_ip_match.group(1)
                        
                    sip_port_match = re.search(r'sip-port:\s*(?:\$\{GB28181_SIP_PORT:)?(\d+)\}?', content)
                    if sip_port_match:
                        config['sip_port'] = sip_port_match.group(1)
                        
                    pwd_match = re.search(r'digest-password:\s*(?:\$\{GB28181_PASSWORD:)?([^}\n]+)\}?', content)
                    if pwd_match:
                        config['password'] = pwd_match.group(1).strip()
                        
                    zlm_url_match = re.search(r'(?<!public-)base-url:\s*(?:\$\{ZLM_BASE_URL:)?([^\}\n]+)\}?', content)
                    if zlm_url_match:
                        config['zlm_base_url'] = zlm_url_match.group(1).strip()
                        
                    zlm_secret_match = re.search(r'secret:\s*(?:\$\{ZLM_SECRET:)?([^\}\n]+)\}?', content)
                    if zlm_secret_match:
                        config['zlm_secret'] = zlm_secret_match.group(1).strip()
                break
            except Exception as e:
                print(f"Error reading profile config {path}: {e}")
                
    return config


# ──────────────────────── 配置对象 ────────────────────────

def get_preset_file_path():
    base = os.path.expanduser("~/Library/Application Support/OpenGBD")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "presets.json")


def normalize_channel_config(channel, fallback=None, index=0):
    fallback = fallback or {}
    base_id = fallback.get("channel_id", "34020000001320000002")
    try:
        channel_id = str(int(base_id) + index).zfill(len(base_id))
    except Exception:
        channel_id = base_id
    data = dict(fallback)
    data.update(channel or {})
    data.setdefault("channel_id", channel_id)
    data.setdefault("name", f"通道{index + 1}")
    data.setdefault("camera_source_text", fallback.get("camera_source_text", "【虚拟源】测试彩条信号 (推荐)"))
    data.setdefault("custom_url", fallback.get("custom_url", "rtsp://127.0.0.1:8554/live"))
    return data


class ConfigMock:
    def __init__(self, data):
        self.SIP_SERVER_IP = data['server_ip']
        self.SIP_SERVER_PORT = int(data['server_port'])
        self.SIP_SERVER_ID = data['server_id']
        self.SIP_SERVER_DOMAIN = self.SIP_SERVER_ID[0:10]

        self.LOCAL_IP = data['local_ip']
        self.LOCAL_PORT = int(data['local_port'])
        self.DEVICE_ID = data['device_id']
        self.CHANNEL_ID = data['channel_id']
        self.PASSWORD = data['password']
        self.HEARTBEAT_INTERVAL = int(data['heartbeat_interval'])
        self.EXPIRE_TIME = int(data['expire_time'])
        self.CAMERA_INDEX = data.get('camera_index', 0)
        self.VIDEO_RESOLUTION = data.get('video_resolution', '1280x720')
        self.VIDEO_FPS = int(data.get('video_fps', 30))
        self.VIDEO_BITRATE = int(data.get('video_bitrate', 1000))
        self.camera_source_text = data.get('camera_source_text', '')
        self.camera_devices_list = data.get('camera_devices_list', [])
        self.custom_url = data.get('custom_url', '')
        self.video_mirror = data.get('video_mirror', False)
        self.audio_enabled = data.get('audio_enabled', False)
        self.audio_source_idx = int(data.get('audio_source_idx', 0))
        self.audio_devices_list = data.get('audio_devices_list', [])
        self.LOCAL_PREVIEW_PORT = int(data.get('local_preview_port', 23000))
        self.ptz_pan = float(data.get('ptz_pan', 0.0))
        self.ptz_tilt = float(data.get('ptz_tilt', 0.0))
        self.ptz_zoom = float(data.get('ptz_zoom', 1.0))

        self.MANUFACTURER = data.get('manufacturer', 'lanccj')
        self.MODEL = data.get('model', 'MacSimulator')
        self.DEVICE_NAME = data.get('device_name', 'lanccj')
        self.CIVIL_CODE = data.get('civil_code', '3402000000')
        self.ADDRESS = data.get('address', '实验室')
        self.OWNER = data.get('owner', 'Owner')
        default_channel = {
            "channel_id": self.CHANNEL_ID,
            "name": "通道1",
            "camera_source_text": self.camera_source_text,
            "custom_url": self.custom_url,
        }
        channels = data.get('channels') or [default_channel]
        self.channels = [
            normalize_channel_config(ch, default_channel, idx)
            for idx, ch in enumerate(channels)
        ]
        self.apply_channel_config(self.channels[0])

    def generate_call_id(self):
        return f"{random.randint(10000000, 99999999)}@{self.LOCAL_IP}"

    def get_channel(self, channel_id=None):
        if channel_id:
            for channel in self.channels:
                if str(channel.get("channel_id", "")).strip() == str(channel_id).strip():
                    return channel
        return self.channels[0] if self.channels else {
            "channel_id": self.CHANNEL_ID,
            "name": "通道1",
            "camera_source_text": self.camera_source_text,
            "custom_url": self.custom_url,
        }

    def apply_channel_config(self, channel):
        self.CHANNEL_ID = channel.get("channel_id", self.CHANNEL_ID)
        self.channel_name = channel.get("name", self.CHANNEL_ID)
        self.camera_source_text = channel.get("camera_source_text", self.camera_source_text)
        self.custom_url = channel.get("custom_url", self.custom_url)


# ──────────────────────── 信号 / 线程 ────────────────────────

class LogSignaler(QObject):
    log_signal = Signal(str)

log_signaler = LogSignaler()


class DeviceThread(QThread):
    registration_state_changed = Signal(str, str)
    ptz_state_changed = Signal(float, float, float, str)
    media_state_changed = Signal(str, str)
    media_preview_changed = Signal(str)

    def __init__(self, config_data, log_prefix="", log_callback=None):
        super().__init__()
        self.config_data = config_data
        self.log_prefix = log_prefix
        self.custom_log_callback = log_callback
        self.device = None
        self.last_registration_state = "IDLE"
        self.last_registration_message = ""

    def _log(self, msg):
        prefix = f"[{self.log_prefix}] " if self.log_prefix else ""
        formatted = f"{prefix}{msg}"
        if self.custom_log_callback:
            self.custom_log_callback(formatted)
        else:
            log_signaler.log_signal.emit(formatted)

    def _on_registration_state(self, state, message):
        self.last_registration_state = state
        self.last_registration_message = message
        self.registration_state_changed.emit(state, message)

    def run(self):
        try:
            config_obj = ConfigMock(self.config_data)
            self.device = GB28181Device(
                config_obj,
                log_callback=self._log,
                state_callback=self._on_registration_state,
                ptz_callback=self.ptz_state_changed.emit,
                media_state_callback=self.media_state_changed.emit,
                media_preview_callback=self.media_preview_changed.emit,
            )
            self.device.start()
        except Exception as e:
            self._log(f"[错误] 设备线程崩溃: {e}")

    def stop(self):
        if self.device:
            self.device.stop()
        self.wait()

    def apply_ptz_action(self, action, speed=1.0):
        if self.device:
            self.device.apply_ptz_action(action, speed=speed, source="界面")


# ──────────────────────── 自适应视频视图 (支持镜像) ────────────────────────

class ResizableVideoView(QGraphicsView):
    def __init__(self, scene, video_item, parent=None):
        super().__init__(scene, parent)
        self.video_item = video_item
        self._mirrored = False
        self._ptz_pan = 0.0
        self._ptz_tilt = 0.0
        self._ptz_zoom = 1.0

    def setMirrored(self, mirrored):
        self._mirrored = bool(mirrored)
        self._update_video_transform()

    def setPtzState(self, pan, tilt, zoom):
        self._ptz_pan = max(-1.0, min(1.0, float(pan)))
        self._ptz_tilt = max(-1.0, min(1.0, float(tilt)))
        self._ptz_zoom = max(1.0, min(4.0, float(zoom)))
        self._update_video_transform()

    def _update_video_transform(self):
        viewport_size = self.viewport().size()
        width = max(1, viewport_size.width())
        height = max(1, viewport_size.height())
        zoom = max(1.0, self._ptz_zoom)
        item_width = width * zoom
        item_height = height * zoom

        self.video_item.setSize(QSizeF(item_width, item_height))

        max_x = max(0.0, item_width - width)
        max_y = max(0.0, item_height - height)
        x = -max_x * (self._ptz_pan + 1.0) / 2.0
        y = -max_y * (self._ptz_tilt + 1.0) / 2.0
        self.video_item.setPos(x, y)

        transform = QTransform()
        if self._mirrored:
            # 先平移一个视频宽度再水平翻转，确保画面仍在可视场景内
            transform.translate(self.video_item.size().width(), 0)
            transform.scale(-1, 1)
        self.video_item.setTransform(transform)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_video_transform()
        self.scene().setSceneRect(0, 0, self.viewport().width(), self.viewport().height())


# ──────────────────────── 可折叠配置卡片 ────────────────────────

class CollapsibleSection(QWidget):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.toggle_btn = QPushButton(f"▶ {title}")
        self.toggle_btn.setCheckable(True)
        self.toggle_btn.setChecked(False)
        self.toggle_btn.setCursor(Qt.PointingHandCursor)
        self.toggle_btn.setStyleSheet(
            "QPushButton { text-align: left; padding: 6px 12px; font-weight: bold; "
            "border: 1px solid #3F3F46; border-radius: 6px; background-color: #27272A; color: #E4E4E7; }"
            "QPushButton:hover { background-color: #3F3F46; }"
            "QPushButton:checked { background-color: #312E81; border-color: #4F46E5; }"
        )
        self.toggle_btn.clicked.connect(self._toggle)

        self.content_area = QWidget()
        self.content_area.setVisible(False)
        self.content_layout = QFormLayout(self.content_area)
        self.content_layout.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.content_area.setStyleSheet(
            "QWidget { border: 1px solid #3F3F46; border-top: none; "
            "border-radius: 0 0 6px 6px; padding: 8px; background-color: #18181B; }"
        )

        main = QVBoxLayout(self)
        main.setSpacing(0)
        main.setContentsMargins(0, 2, 0, 2)
        main.addWidget(self.toggle_btn)
        main.addWidget(self.content_area)

    def add_row(self, label: str, widget):
        widget.setStyleSheet(
            "QLineEdit, QComboBox { background-color: #27272A; color: #F4F4F5; "
            "border: 1px solid #3F3F46; border-radius: 4px; padding: 4px; }"
        )
        lbl = QLabel(label)
        lbl.setStyleSheet("color: #D4D4D8;")
        self.content_layout.addRow(lbl, widget)

    def _toggle(self, checked):
        self.content_area.setVisible(checked)
        self.toggle_btn.setText(f"{'▼' if checked else '▶'} {self.toggle_btn.text()[2:]}")

def setup_combobox_popup(combobox):
    view = QListView(combobox)
    view.setStyleSheet("""
        QListView {
            background-color: #18181B;
            color: #E4E4E7;
            border: 1px solid #3F3F46;
            border-radius: 6px;
            selection-background-color: #4F46E5;
            selection-color: #FFFFFF;
            outline: 0px;
            padding: 4px;
        }
        QListView::item {
            height: 28px;
            padding-left: 8px;
            border-radius: 4px;
            color: #E4E4E7;
            background-color: transparent;
        }
        QListView::item:hover {
            background-color: #312E81;
            color: #FAFAFA;
        }
        QListView::item:selected {
            background-color: #4F46E5;
            color: #FFFFFF;
        }
    """)
    combobox.setView(view)


# ──────────────────────── 手动推流配置对话框 ────────────────────────

# ──────────────────────── ZLM API 控制接口 ────────────────────────

def call_zlm_open_rtp_server(zlm_base_url, secret, port, stream_id):
    import urllib.request
    import json
    import traceback
    # 去除换行符/回车，避免 Windows 换行符破坏 HTTP 请求行格式
    zlm_base_url = zlm_base_url.replace('\r', '').replace('\n', '').strip()
    secret = secret.replace('\r', '').replace('\n', '').strip()
    stream_id = stream_id.replace('\r', '').replace('\n', '').strip()
    
    if not zlm_base_url.startswith("http://") and not zlm_base_url.startswith("https://"):
        zlm_base_url = f"http://{zlm_base_url}"
    url = f"{zlm_base_url}/index/api/openRtpServer?secret={secret}&port={port}&enable_tcp=0&stream_id={stream_id}"
    try:
        req = urllib.request.Request(
            url, 
            headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'},
            method="GET"
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=3) as response:
            res_data = json.loads(response.read().decode('utf-8'))
            return res_data
    except Exception as e:
        return {"code": -1, "msg": f"{str(e)} | URL: {url} | Traceback: {traceback.format_exc()}"}

def call_zlm_close_rtp_server(zlm_base_url, secret, stream_id):
    import urllib.request
    import json
    import traceback
    zlm_base_url = zlm_base_url.replace('\r', '').replace('\n', '').strip()
    secret = secret.replace('\r', '').replace('\n', '').strip()
    stream_id = stream_id.replace('\r', '').replace('\n', '').strip()
    
    if not zlm_base_url.startswith("http://") and not zlm_base_url.startswith("https://"):
        zlm_base_url = f"http://{zlm_base_url}"
    url = f"{zlm_base_url}/index/api/closeRtpServer?secret={secret}&stream_id={stream_id}"
    try:
        req = urllib.request.Request(
            url, 
            headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'},
            method="GET"
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=3) as response:
            res_data = json.loads(response.read().decode('utf-8'))
            return res_data
    except Exception as e:
        return {"code": -1, "msg": f"{str(e)} | URL: {url} | Traceback: {traceback.format_exc()}"}


# ──────────────────────── 手动推流配置对话框 ────────────────────────

class TestPushDialog(QDialog):
    def __init__(self, default_config, parent=None):
        super().__init__(parent)
        self.setWindowTitle("手动测试推流配置")
        self.setMinimumWidth(360)
        self.setStyleSheet("""
            QDialog {
                background-color: #18181B;
            }
            QLabel {
                color: #E4E4E7;
                font-size: 13px;
            }
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(12)
        
        form_layout = QFormLayout()
        form_layout.setSpacing(10)
        
        self.input_ip = QLineEdit(default_config.get('media_ip', '10.211.55.4'))
        self.input_port = QLineEdit(default_config.get('rtp_port', '30000'))
        self.input_proto = QComboBox()
        self.input_proto.addItems(["UDP", "TCP"])
        setup_combobox_popup(self.input_proto)
        
        self.input_ssrc = QLineEdit("0123456789")
        self.input_stream_id = QLineEdit(default_config.get('channel_id', 'test_manual_push'))
        
        style = """
            QLineEdit, QComboBox {
                background-color: #27272A;
                color: #FAFAFA;
                border: 1px solid #3F3F46;
                border-radius: 6px;
                padding: 5px;
                font-size: 13px;
            }
            QLineEdit:focus, QComboBox:focus {
                border: 1px solid #6366F1;
            }
        """
        self.input_ip.setStyleSheet(style)
        self.input_port.setStyleSheet(style)
        self.input_proto.setStyleSheet(style)
        self.input_ssrc.setStyleSheet(style)
        self.input_stream_id.setStyleSheet(style)
        
        form_layout.addRow("目标 IP:", self.input_ip)
        form_layout.addRow("目标端口:", self.input_port)
        form_layout.addRow("传输协议:", self.input_proto)
        form_layout.addRow("SSRC:", self.input_ssrc)
        form_layout.addRow("流 ID (Stream ID):", self.input_stream_id)
        
        layout.addLayout(form_layout)
        
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        
        # Style buttons
        buttons.setStyleSheet("""
            QPushButton {
                background-color: #3F3F46;
                color: #E4E4E7;
                font-weight: bold;
                border-radius: 6px;
                padding: 6px 16px;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #52525B;
            }
        """)
        
        ok_button = buttons.button(QDialogButtonBox.Ok)
        if ok_button:
            ok_button.setStyleSheet("""
                QPushButton {
                    background-color: #4F46E5;
                    color: white;
                    font-weight: bold;
                    border-radius: 6px;
                    padding: 6px 16px;
                    font-size: 13px;
                }
                QPushButton:hover {
                    background-color: #4338CA;
                }
            """)
            
        for btn in buttons.buttons():
            btn.setCursor(Qt.PointingHandCursor)
            
        layout.addWidget(buttons)
        
    def get_values(self):
        return (
            self.input_ip.text().strip(),
            self.input_port.text().strip(),
            self.input_proto.currentText(),
            self.input_ssrc.text().strip(),
            self.input_stream_id.text().strip()
        )


# ──────────────────────── 主界面 (美化与自适应重构) ────────────────────────

class EasyGBDMacGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.thread = None
        self.camera = None
        self.capture_session = None
        self.media_player = None
        self.preview_audio_output = None
        self.camera_frame_received = False
        self.preview_duration_ms = 0
        self.is_previewing = False
        self.preview_requested = False
        self.preview_mode = None
        self.stream_preview_url = ""
        self.stream_preview_retry_count = 0
        self.camera_devices = []
        self.logs_archive = []
        self.is_manual_pushing = False
        self.manual_media_ctrl = None
        self.ptz_pan = 0.0
        self.ptz_tilt = 0.0
        self.ptz_zoom = 1.0
        self.ptz_step = 0.18
        self.ptz_zoom_step = 0.25
        self.stream_state = "IDLE"
        self.last_health_check_ts = time.monotonic()
        self.preset_data = {}
        self.channel_configs = []
        self.current_channel_index = 0
        self._loading_channel_ui = False
        self.multi_devices = []
        self.multi_device_counter = 0
        self.init_ui()

    def init_ui(self):
        self.setWindowTitle("OpenGBD - Mac原生国标设备模拟器")
        self.setMinimumSize(1000, 800)
        
        # 苹果黑/深色科技风格设计
        self.setStyleSheet("""
            QMainWindow {
                background-color: #09090B;
            }
            QLabel {
                color: #E4E4E7;
                font-size: 13px;
            }
            QLineEdit {
                background-color: #18181B;
                color: #FAFAFA;
                border: 1px solid #27272A;
                border-radius: 6px;
                padding: 5px;
                font-size: 13px;
            }
            QLineEdit:focus {
                border: 1px solid #6366F1;
            }
            QComboBox {
                background-color: #18181B;
                color: #FAFAFA;
                border: 1px solid #27272A;
                border-radius: 6px;
                padding: 6px 12px;
                font-size: 13px;
            }
            QComboBox:hover {
                border: 1px solid #3F3F46;
                background-color: #27272A;
            }
            QComboBox:focus {
                border: 1px solid #6366F1;
            }
            QComboBox QLineEdit {
                background-color: transparent;
                color: #FAFAFA;
                border: none;
                padding: 0px;
            }
            QComboBox::drop-down {
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 24px;
                border-left-width: 0px;
            }
            QComboBox::down-arrow {
                image: none;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 5px solid #A1A1AA;
                margin-right: 8px;
                margin-top: 2px;
            }
            QComboBox::down-arrow:hover {
                border-top-color: #FAFAFA;
            }
            QComboBox QAbstractItemView, QComboBox QListView {
                background-color: #18181B;
                color: #E4E4E7;
                border: 1px solid #3F3F46;
                border-radius: 6px;
                selection-background-color: #4F46E5;
                selection-color: #FFFFFF;
                padding: 4px;
                outline: 0px;
            }
            QComboBox QAbstractItemView::item, QComboBox QListView::item {
                height: 28px;
                padding-left: 8px;
                border-radius: 4px;
                color: #E4E4E7;
                background-color: transparent;
            }
            QComboBox QAbstractItemView::item:hover, QComboBox QListView::item:hover {
                background-color: #312E81;
                color: #FAFAFA;
            }
            QComboBox QAbstractItemView::item:selected, QComboBox QListView::item:selected {
                background-color: #4F46E5;
                color: #FFFFFF;
            }
            QFrame#basic_frame {
                background-color: #18181B;
                border: 1px solid #27272A;
                border-radius: 8px;
            }
            QTabWidget::pane {
                border: 1px solid #27272A;
                background-color: #09090B;
                border-radius: 8px;
                padding: 6px;
            }
            QTabBar::tab {
                background-color: #18181B;
                color: #A1A1AA;
                border: 1px solid #27272A;
                border-bottom: none;
                padding: 8px 18px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                font-size: 13px;
                font-weight: 600;
                margin-right: 4px;
            }
            QTabBar::tab:hover {
                background-color: #27272A;
                color: #FAFAFA;
            }
            QTabBar::tab:selected {
                background-color: #4F46E5;
                color: #FFFFFF;
                border-color: #6366F1;
            }
            QTableWidget {
                background-color: #18181B;
                alternate-background-color: #111113;
                color: #FAFAFA;
                border: 1px solid #27272A;
                border-radius: 6px;
                gridline-color: #27272A;
                selection-background-color: #312E81;
                selection-color: #FFFFFF;
            }
            QHeaderView::section {
                background-color: #27272A;
                color: #D4D4D8;
                padding: 6px;
                border: 1px solid #3F3F46;
                font-weight: bold;
                font-size: 12px;
            }
            QSpinBox {
                background-color: #18181B;
                color: #FAFAFA;
                border: 1px solid #27272A;
                border-radius: 6px;
                padding: 5px;
                font-size: 13px;
            }
            QSpinBox:focus {
                border: 1px solid #6366F1;
            }
        """)

        # 主 Widget 与自适应主布局
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QVBoxLayout(main_widget)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(15, 12, 15, 12)

        # 1. 头部标题栏
        header_layout = QHBoxLayout()
        title_label = QLabel("OpenGBD Mac 模拟器")
        title_label.setStyleSheet("font-size: 20px; font-weight: bold; color: #818CF8;")
        
        self.status_label = QLabel("● 未连接")
        self.status_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #EF4444;")
        self.stream_status_label = QLabel("● 未推流")
        self.stream_status_label.setToolTip("显示平台 INVITE 或手动测试触发的媒体推流状态")
        self.stream_status_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #71717A;")
        
        header_layout.addWidget(title_label)
        header_layout.addStretch()
        header_layout.addWidget(QLabel("注册:"))
        header_layout.addWidget(self.status_label)
        header_layout.addSpacing(14)
        header_layout.addWidget(QLabel("推流:"))
        header_layout.addWidget(self.stream_status_label)
        main_layout.addLayout(header_layout)

        # 2. 主自适应区域 (使用垂直 QSplitter 分割视频/配置与日志)
        main_splitter = QSplitter(Qt.Vertical)
        main_splitter.setStyleSheet("""
            QSplitter::handle {
                background-color: #27272A;
                height: 2px;
            }
        """)

        # 2a. 上半部分：左右分割 (配置 vs 视频)
        top_splitter = QSplitter(Qt.Horizontal)
        top_splitter.setStyleSheet("""
            QSplitter::handle {
                background-color: #27272A;
                width: 2px;
            }
        """)

        # ─── 左面板：滚动配置容器 ───
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        left_scroll.setStyleSheet("QScrollArea { border: none; background-color: transparent; }")

        left_content = QWidget()
        left_content.setStyleSheet("background-color: transparent;")
        left_layout = QVBoxLayout(left_content)
        left_layout.setSpacing(10)
        left_layout.setContentsMargins(0, 0, 10, 0)
        left_scroll.setWidget(left_content)

        # 基本配置卡片
        basic_frame = QFrame()
        basic_frame.setObjectName("basic_frame")
        basic_layout = QFormLayout(basic_frame)
        basic_layout.setContentsMargins(12, 12, 12, 12)
        basic_layout.setSpacing(8)
        basic_layout.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        local_ips = get_all_local_ips()
        self.local_ips = local_ips

        self.input_local_ip = QComboBox()
        setup_combobox_popup(self.input_local_ip)
        self.input_local_ip.setEditable(True)
        self.input_local_ip.addItems(local_ips)

        backend_config = get_backend_default_config()

        self.input_server_ip = QComboBox()
        setup_combobox_popup(self.input_server_ip)
        self.input_server_ip.setEditable(True)
        self.input_server_ip.addItems(local_ips)
        self.input_server_ip.currentTextChanged.connect(self.auto_select_local_ip)
        default_sip_ip = backend_config.get('sip_ip', '127.0.0.1')
        idx_sip = self.input_server_ip.findText(default_sip_ip)
        if idx_sip >= 0:
            self.input_server_ip.setCurrentIndex(idx_sip)
        else:
            self.input_server_ip.setEditText(default_sip_ip)

        self.input_server_port = QLineEdit(backend_config.get('sip_port', '5060'))
        self.input_server_id = QLineEdit("34020000002000000001")

        self.input_local_port = QLineEdit("50600")
        self.input_device_id = QLineEdit("34020000001110000002")
        self.input_channel_id = QLineEdit("34020000001320000002")
        self.input_channel_id.textChanged.connect(self.update_current_channel_from_ui)

        # 密码
        self.pwd_container = QWidget()
        pwd_layout = QHBoxLayout(self.pwd_container)
        pwd_layout.setContentsMargins(0, 0, 0, 0)
        pwd_layout.setSpacing(4)
        self.input_password = QLineEdit(backend_config.get('password', 'change-me'))
        self.input_password.setEchoMode(QLineEdit.Password)
        self.btn_toggle_pwd = QPushButton("👁️")
        self.btn_toggle_pwd.setFixedWidth(35)
        self.btn_toggle_pwd.setCursor(Qt.PointingHandCursor)
        self.btn_toggle_pwd.setStyleSheet("""
            QPushButton { background-color: #27272A; border: 1px solid #3F3F46; border-radius: 6px; color: white; padding: 4px; }
            QPushButton:hover { background-color: #3F3F46; }
        """)
        self.btn_toggle_pwd.clicked.connect(self.toggle_password_visibility)
        pwd_layout.addWidget(self.input_password)
        pwd_layout.addWidget(self.btn_toggle_pwd)

        self.camera_devices = QMediaDevices.videoInputs()
        camera_names = ["【虚拟源】测试彩条信号 (推荐)"]
        for cam in self.camera_devices:
            camera_names.append(f"【摄像头】{cam.description()}")
        camera_names.extend([
            "【桌面捕获】Capture screen 0",
            "【桌面捕获】Capture screen 1",
            "【自定义源】RTSP/RTMP网络流或本地文件"
        ])
        
        self.input_camera = QComboBox()
        setup_combobox_popup(self.input_camera)
        self.input_camera.addItems(camera_names)
        self.input_camera.currentIndexChanged.connect(self.on_camera_source_changed)

        self.custom_url_container = QWidget()
        custom_url_layout = QHBoxLayout(self.custom_url_container)
        custom_url_layout.setContentsMargins(0, 0, 0, 0)
        custom_url_layout.setSpacing(4)
        self.lbl_custom_url = QLabel("自定义源:")
        self.lbl_custom_url.setStyleSheet("color: #D4D4D8;")
        self.input_custom_url = QLineEdit("rtsp://127.0.0.1:8554/live")
        self.input_custom_url.setPlaceholderText("rtsp:// 或 rtmp:// 或 本地.mp4 绝对路径")
        self.input_custom_url.textChanged.connect(lambda _=None: self._sync_first_channel_source())
        self.btn_select_local_file = QPushButton("选择文件…")
        self.btn_select_local_file.setMinimumWidth(112)
        self.btn_select_local_file.setStyleSheet("""
            QPushButton {
                background-color: #4F46E5;
                color: #FFFFFF;
                border: 1px solid #6366F1;
                border-radius: 6px;
                padding: 7px 12px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #6366F1; }
            QPushButton:pressed { background-color: #4338CA; }
            QPushButton:disabled {
                background-color: #27272A;
                color: #71717A;
                border-color: #3F3F46;
            }
        """)
        self.btn_select_local_file.setCursor(Qt.PointingHandCursor)
        self.btn_select_local_file.setToolTip("选择本地视频文件作为推流源")
        self.btn_select_local_file.clicked.connect(self.select_local_video_file)
        custom_url_layout.addWidget(self.lbl_custom_url)
        custom_url_layout.addWidget(self.input_custom_url)
        custom_url_layout.addWidget(self.btn_select_local_file)
        self.custom_url_container.hide()

        self.combo_preset = QComboBox()
        setup_combobox_popup(self.combo_preset)
        self.combo_preset.addItems([
            "【推荐】高清模式 (720P / 30fps / 2000kbps)",
            "超清模式 (1080P / 30fps / 4000kbps)",
            "流畅模式 (360P / 15fps / 800kbps)",
            "自定义设置"
        ])
        self.combo_preset.currentIndexChanged.connect(self.on_preset_changed)

        self.input_video_resolution = QComboBox()
        setup_combobox_popup(self.input_video_resolution)
        self.input_video_resolution.addItems(["1920x1080", "1280x720", "640x360"])
        self.input_video_resolution.setCurrentText("1280x720")
        self.input_video_resolution.setEnabled(False)

        self.input_video_fps = QComboBox()
        setup_combobox_popup(self.input_video_fps)
        self.input_video_fps.addItems(["15", "25", "30"])
        self.input_video_fps.setCurrentText("30")
        self.input_video_fps.setEnabled(False)

        self.input_video_bitrate = QComboBox()
        setup_combobox_popup(self.input_video_bitrate)
        self.input_video_bitrate.setEditable(True)
        self.input_video_bitrate.addItems(["500", "1000", "2000", "4000", "8000"])
        self.input_video_bitrate.setCurrentText("2000")
        self.input_video_bitrate.setEnabled(False)

        self.check_mirror = QCheckBox("镜像翻转画面 (左右反转)")
        self.check_mirror.setStyleSheet("QCheckBox { color: #D4D4D8; }")
        self.check_mirror.setChecked(True)
        self.check_mirror.stateChanged.connect(self.on_mirror_toggled)

        # 音频设备
        from PySide6.QtMultimedia import QMediaDevices as _QMD
        self.audio_devices = _QMD.audioInputs()
        audio_names = ["【默认麦克风】"] + [f"【麦克风】{dev.description()}" for dev in self.audio_devices]
        self.check_audio = QCheckBox("开启音频推流 (G.711 A-Law)")
        self.check_audio.setStyleSheet("QCheckBox { color: #D4D4D8; }")
        self.check_audio.setChecked(False)
        self.check_audio.stateChanged.connect(self.on_audio_toggled)

        self.input_audio = QComboBox()
        setup_combobox_popup(self.input_audio)
        self.input_audio.addItems(audio_names)
        self.input_audio.setEnabled(False)

        basic_layout.addRow("服务器 IP:", self.input_server_ip)
        basic_layout.addRow("服务器端口:", self.input_server_port)
        basic_layout.addRow("服务器 ID / 域:", self.input_server_id)
        basic_layout.addRow("本地 IP:", self.input_local_ip)
        basic_layout.addRow("本地端口:", self.input_local_port)
        basic_layout.addRow("设备国标 ID:", self.input_device_id)
        basic_layout.addRow("通道国标 ID:", self.input_channel_id)
        basic_layout.addRow("注册密码:", self.pwd_container)
        basic_layout.addRow("视频源摄像头:", self.input_camera)
        basic_layout.addRow("", self.custom_url_container)
        basic_layout.addRow("快速配置模板:", self.combo_preset)
        basic_layout.addRow("推流分辨率:", self.input_video_resolution)
        basic_layout.addRow("推流帧率 (fps):", self.input_video_fps)
        basic_layout.addRow("推流码率 (kbps):", self.input_video_bitrate)
        basic_layout.addRow("", self.check_mirror)
        basic_layout.addRow("", self.check_audio)
        basic_layout.addRow("麦克风设备:", self.input_audio)

        left_layout.addWidget(basic_frame)

        # 高级配置（可折叠）
        self.adv_section = CollapsibleSection("更多配置（设备信息 / 注册参数）")
        self.input_device_name = QLineEdit("lanccj")
        self.input_manufacturer = QLineEdit("lanccj")
        self.input_model = QLineEdit("MacSimulator")
        self.input_civil_code = QLineEdit("3402000000")
        self.input_address = QLineEdit("实验室")
        self.input_owner = QLineEdit("Owner")
        self.input_heartbeat_interval = QLineEdit("60")
        self.input_expire_time = QLineEdit("3600")

        self.adv_section.add_row("设备名称:", self.input_device_name)
        self.adv_section.add_row("设备厂商:", self.input_manufacturer)
        self.adv_section.add_row("设备型号:", self.input_model)
        self.adv_section.add_row("行政区划代码:", self.input_civil_code)
        self.adv_section.add_row("安装地址:", self.input_address)
        self.adv_section.add_row("所属者:", self.input_owner)
        self.adv_section.add_row("心跳周期(秒):", self.input_heartbeat_interval)
        self.adv_section.add_row("注册有效期(秒):", self.input_expire_time)
        left_layout.addWidget(self.adv_section)

        # 配置预设
        self.preset_section = CollapsibleSection("配置预设（保存 / 加载模拟场景）")
        preset_box = QWidget()
        preset_layout = QGridLayout(preset_box)
        preset_layout.setContentsMargins(0, 0, 0, 0)
        preset_layout.setSpacing(6)
        self.combo_saved_presets = QComboBox()
        setup_combobox_popup(self.combo_saved_presets)
        self.btn_load_preset = QPushButton("加载")
        self.btn_save_preset = QPushButton("保存当前")
        self.btn_delete_preset = QPushButton("删除")
        for btn in [self.btn_load_preset, self.btn_save_preset, self.btn_delete_preset]:
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet("QPushButton { background-color:#3F3F46; color:#F4F4F5; border-radius:5px; padding:5px 8px; font-weight:bold; } QPushButton:hover { background-color:#52525B; }")
        self.btn_load_preset.clicked.connect(self.load_selected_preset)
        self.btn_save_preset.clicked.connect(self.save_current_preset)
        self.btn_delete_preset.clicked.connect(self.delete_selected_preset)
        preset_layout.addWidget(QLabel("预设:"), 0, 0)
        preset_layout.addWidget(self.combo_saved_presets, 0, 1, 1, 3)
        preset_layout.addWidget(self.btn_load_preset, 1, 1)
        preset_layout.addWidget(self.btn_save_preset, 1, 2)
        preset_layout.addWidget(self.btn_delete_preset, 1, 3)
        self.preset_section.content_layout.addRow(preset_box)
        left_layout.addWidget(self.preset_section)

        # 多通道配置
        self.channel_section = CollapsibleSection("多通道模拟（Catalog 通道列表 / 每通道来源）")
        channel_box = QWidget()
        channel_layout = QGridLayout(channel_box)
        channel_layout.setContentsMargins(0, 0, 0, 0)
        channel_layout.setSpacing(6)
        self.combo_channels = QComboBox()
        setup_combobox_popup(self.combo_channels)
        self.combo_channels.currentIndexChanged.connect(self.on_channel_selected)
        self.btn_add_channel = QPushButton("新增")
        self.btn_copy_channel = QPushButton("复制首通道")
        self.btn_remove_channel = QPushButton("删除")
        for btn in [self.btn_add_channel, self.btn_copy_channel, self.btn_remove_channel]:
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet("QPushButton { background-color:#27272A; color:#F4F4F5; border:1px solid #3F3F46; border-radius:5px; padding:5px 8px; } QPushButton:hover { background-color:#3F3F46; }")
        self.btn_add_channel.clicked.connect(self.add_channel)
        self.btn_copy_channel.clicked.connect(self.copy_first_channel_to_current)
        self.btn_remove_channel.clicked.connect(self.remove_current_channel)

        self.input_channel_name = QLineEdit("通道1")
        self.input_channel_name.textChanged.connect(self.update_current_channel_from_ui)
        self.input_channel_source = QComboBox()
        setup_combobox_popup(self.input_channel_source)
        self.input_channel_source.addItems(camera_names)
        self.input_channel_source.currentIndexChanged.connect(self.on_channel_source_changed)
        self.input_channel_custom_url = QLineEdit("rtsp://127.0.0.1:8554/live")
        self.input_channel_custom_url.setPlaceholderText("仅自定义源需要：rtsp/rtmp/http 或本地文件路径")
        self.input_channel_custom_url.textChanged.connect(self.update_current_channel_from_ui)
        self.btn_channel_select_file = QPushButton("文件…")
        self.btn_channel_select_file.setCursor(Qt.PointingHandCursor)
        self.btn_channel_select_file.clicked.connect(self.select_channel_local_file)
        self.lbl_channel_hint = QLabel("提示：新增通道默认复制通道1；只有修改后才形成独立来源。")
        self.lbl_channel_hint.setStyleSheet("color:#A1A1AA; font-size:11px;")

        channel_layout.addWidget(QLabel("通道:"), 0, 0)
        channel_layout.addWidget(self.combo_channels, 0, 1, 1, 3)
        channel_layout.addWidget(self.btn_add_channel, 1, 1)
        channel_layout.addWidget(self.btn_copy_channel, 1, 2)
        channel_layout.addWidget(self.btn_remove_channel, 1, 3)
        channel_layout.addWidget(QLabel("名称:"), 2, 0)
        channel_layout.addWidget(self.input_channel_name, 2, 1, 1, 3)
        channel_layout.addWidget(QLabel("来源:"), 3, 0)
        channel_layout.addWidget(self.input_channel_source, 3, 1, 1, 3)
        channel_layout.addWidget(QLabel("URL/文件:"), 4, 0)
        channel_layout.addWidget(self.input_channel_custom_url, 4, 1, 1, 2)
        channel_layout.addWidget(self.btn_channel_select_file, 4, 3)
        channel_layout.addWidget(self.lbl_channel_hint, 5, 0, 1, 4)
        self.channel_section.content_layout.addRow(channel_box)
        left_layout.addWidget(self.channel_section)

        # 动作按钮组
        btn_container = QWidget()
        btn_layout = QGridLayout(btn_container)
        btn_layout.setContentsMargins(0, 5, 0, 5)
        btn_layout.setSpacing(8)

        self.btn_start = QPushButton("设备注册")
        self.btn_start.setCursor(Qt.PointingHandCursor)
        self.btn_start.setStyleSheet("""
            QPushButton { background-color: #4F46E5; color: white; font-weight: bold; border-radius: 6px; padding: 8px 16px; }
            QPushButton:hover { background-color: #4338CA; }
            QPushButton:disabled { background-color: #4B5563; color: #9CA3AF; }
        """)
        self.btn_start.clicked.connect(self.start_device)

        self.btn_stop = QPushButton("设备注销")
        self.btn_stop.setEnabled(False)
        self.btn_stop.setCursor(Qt.PointingHandCursor)
        self.btn_stop.setStyleSheet("""
            QPushButton { background-color: #DC2626; color: white; font-weight: bold; border-radius: 6px; padding: 8px 16px; }
            QPushButton:hover { background-color: #B91C1C; }
            QPushButton:disabled { background-color: #4B5563; color: #9CA3AF; }
        """)
        self.btn_stop.clicked.connect(self.stop_device)

        self.btn_preview = QPushButton("本地预览")
        self.btn_preview.setCursor(Qt.PointingHandCursor)
        self.btn_preview.setStyleSheet("""
            QPushButton { background-color: #059669; color: white; font-weight: bold; border-radius: 6px; padding: 8px 16px; }
            QPushButton:hover { background-color: #047857; }
            QPushButton:disabled { background-color: #4B5563; color: #9CA3AF; }
        """)
        self.btn_preview.clicked.connect(self.toggle_preview)

        self.btn_test_stream = QPushButton("测试推流")
        self.btn_test_stream.setCursor(Qt.PointingHandCursor)
        self.btn_test_stream.setStyleSheet("""
            QPushButton { background-color: #D97706; color: white; font-weight: bold; border-radius: 6px; padding: 8px 16px; }
            QPushButton:hover { background-color: #B45309; }
            QPushButton:disabled { background-color: #4B5563; color: #9CA3AF; }
        """)
        self.btn_test_stream.clicked.connect(self.toggle_manual_push)

        btn_layout.addWidget(self.btn_start, 0, 0)
        btn_layout.addWidget(self.btn_stop, 0, 1)
        btn_layout.addWidget(self.btn_preview, 1, 0)
        btn_layout.addWidget(self.btn_test_stream, 1, 1)
        left_layout.addWidget(btn_container)

        left_layout.addStretch()

        # 作者及版权信息
        author_layout = QVBoxLayout()
        author_layout.setContentsMargins(0, 10, 0, 0)
        author_layout.setSpacing(4)
        
        lbl_author = QLabel("作者: LancCJ")
        lbl_author.setStyleSheet("color: #71717A; font-size: 11px;")
        lbl_author.setAlignment(Qt.AlignCenter)
        
        lbl_address = QLabel("软件地址: <a href='https://softhub.lanccj.cn/addons/softhub' style='color: #818CF8; text-decoration: none;'>softhub.lanccj.cn</a>")
        lbl_address.setOpenExternalLinks(True)
        lbl_address.setStyleSheet("font-size: 11px;")
        lbl_address.setAlignment(Qt.AlignCenter)
        
        author_layout.addWidget(lbl_author)
        author_layout.addWidget(lbl_address)
        left_layout.addLayout(author_layout)

        # ─── 右面板：推流预览视频卡片 ───
        self.video_container = QFrame()
        self.video_container.setObjectName("video_container")
        self.video_container.setStyleSheet("""
            QFrame#video_container {
                background-color: #121214;
                border: 1px solid #27272A;
                border-radius: 10px;
            }
        """)
        vl = QVBoxLayout(self.video_container)
        vl.setContentsMargins(10, 10, 10, 10)
        
        self.graphics_scene = QGraphicsScene()
        self.video_item = QGraphicsVideoItem()
        self.video_item.videoSink().videoFrameChanged.connect(
            self.on_preview_video_frame
        )
        self.graphics_scene.addItem(self.video_item)
        self.video_view = ResizableVideoView(self.graphics_scene, self.video_item)
        self.video_view.setStyleSheet("border-radius: 6px; background-color: #121214; border: none;")
        
        self.video_placeholder = QLabel("本地预览/国标推流画面未开启\n\n开启预览或在平台发起点播，将显示流视频")
        self.video_placeholder.setAlignment(Qt.AlignCenter)
        self.video_placeholder.setStyleSheet("color: #71717A; font-size: 14px; font-weight: bold;")
        
        vl.addWidget(self.video_view)
        vl.addWidget(self.video_placeholder)
        self.video_view.hide()

        self.preview_progress_container = QWidget()
        preview_progress_layout = QHBoxLayout(self.preview_progress_container)
        preview_progress_layout.setContentsMargins(2, 4, 2, 0)
        preview_progress_layout.setSpacing(10)
        self.preview_progress_slider = QSlider(Qt.Horizontal)
        self.preview_progress_slider.setRange(0, 0)
        self.preview_progress_slider.setStyleSheet("""
            QSlider::groove:horizontal {
                height: 5px;
                background: #3F3F46;
                border-radius: 2px;
            }
            QSlider::sub-page:horizontal {
                background: #6366F1;
                border-radius: 2px;
            }
            QSlider::handle:horizontal {
                width: 14px;
                height: 14px;
                margin: -5px 0;
                background: #FFFFFF;
                border: 2px solid #6366F1;
                border-radius: 7px;
            }
        """)
        self.preview_progress_slider.sliderMoved.connect(
            self.on_preview_slider_moved
        )
        self.preview_progress_slider.sliderReleased.connect(
            self.seek_preview_position
        )
        self.preview_time_label = QLabel("00:00 / 00:00")
        self.preview_time_label.setMinimumWidth(105)
        self.preview_time_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.preview_time_label.setStyleSheet(
            "color: #D4D4D8; font-family: Menlo, monospace; font-size: 12px;"
        )
        preview_progress_layout.addWidget(self.preview_progress_slider, 1)
        preview_progress_layout.addWidget(self.preview_time_label)
        vl.addWidget(self.preview_progress_container)
        self.preview_progress_container.hide()

        self.ptz_panel = QFrame()
        self.ptz_panel.setObjectName("ptz_panel")
        self.ptz_panel.setStyleSheet("""
            QFrame#ptz_panel {
                background-color: #18181B;
                border: 1px solid #27272A;
                border-radius: 8px;
            }
            QPushButton {
                background-color: #27272A;
                color: #F4F4F5;
                border: 1px solid #3F3F46;
                border-radius: 6px;
                padding: 6px 10px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #3F3F46; border-color: #6366F1; }
            QPushButton:pressed { background-color: #4F46E5; }
            QLabel { color: #D4D4D8; font-size: 12px; }
        """)
        ptz_layout = QGridLayout(self.ptz_panel)
        ptz_layout.setContentsMargins(8, 8, 8, 8)
        ptz_layout.setSpacing(6)

        self.btn_ptz_up = QPushButton("▲")
        self.btn_ptz_down = QPushButton("▼")
        self.btn_ptz_left = QPushButton("◀")
        self.btn_ptz_right = QPushButton("▶")
        self.btn_ptz_zoom_in = QPushButton("放大 +")
        self.btn_ptz_zoom_out = QPushButton("缩小 -")
        self.btn_ptz_stop = QPushButton("停止")
        self.btn_ptz_reset = QPushButton("重置")
        self.lbl_ptz_state = QLabel("PTZ：居中 / 1.00x")
        self.lbl_ptz_state.setAlignment(Qt.AlignCenter)

        for btn in [
            self.btn_ptz_up, self.btn_ptz_down, self.btn_ptz_left,
            self.btn_ptz_right, self.btn_ptz_zoom_in, self.btn_ptz_zoom_out,
            self.btn_ptz_stop, self.btn_ptz_reset
        ]:
            btn.setCursor(Qt.PointingHandCursor)

        self.btn_ptz_up.clicked.connect(lambda: self.apply_local_ptz_action("up"))
        self.btn_ptz_down.clicked.connect(lambda: self.apply_local_ptz_action("down"))
        self.btn_ptz_left.clicked.connect(lambda: self.apply_local_ptz_action("left"))
        self.btn_ptz_right.clicked.connect(lambda: self.apply_local_ptz_action("right"))
        self.btn_ptz_zoom_in.clicked.connect(lambda: self.apply_local_ptz_action("zoom_in"))
        self.btn_ptz_zoom_out.clicked.connect(lambda: self.apply_local_ptz_action("zoom_out"))
        self.btn_ptz_stop.clicked.connect(lambda: self.apply_local_ptz_action("stop"))
        self.btn_ptz_reset.clicked.connect(lambda: self.apply_local_ptz_action("reset"))

        ptz_layout.addWidget(QLabel("模拟 PTZ"), 0, 0, 1, 1)
        ptz_layout.addWidget(self.lbl_ptz_state, 0, 1, 1, 3)
        ptz_layout.addWidget(self.btn_ptz_up, 1, 1)
        ptz_layout.addWidget(self.btn_ptz_left, 2, 0)
        ptz_layout.addWidget(self.btn_ptz_stop, 2, 1)
        ptz_layout.addWidget(self.btn_ptz_right, 2, 2)
        ptz_layout.addWidget(self.btn_ptz_down, 3, 1)
        ptz_layout.addWidget(self.btn_ptz_zoom_in, 1, 3)
        ptz_layout.addWidget(self.btn_ptz_zoom_out, 2, 3)
        ptz_layout.addWidget(self.btn_ptz_reset, 3, 3)
        vl.addWidget(self.ptz_panel)

        # 分割栏加入两侧
        top_splitter.addWidget(left_scroll)
        top_splitter.addWidget(self.video_container)
        top_splitter.setSizes([450, 550])

        # ─── 下面板：日志面板容器 ───
        log_panel = QWidget()
        log_panel.setObjectName("log_panel")
        log_panel_layout = QVBoxLayout(log_panel)
        log_panel_layout.setContentsMargins(0, 5, 0, 0)
        log_panel_layout.setSpacing(6)

        # 3. 日志筛选工具栏
        filter_layout = QHBoxLayout()
        filter_layout.setSpacing(8)

        self.combo_filter = QComboBox()
        setup_combobox_popup(self.combo_filter)
        self.combo_filter.addItems(["全部日志", "国标交互", "SIP信令", "媒体推流", "系统通知", "错误警告"])
        self.combo_filter.currentIndexChanged.connect(self.filter_logs)
        self.combo_filter.setStyleSheet("QComboBox { min-width: 100px; }")

        # 是否显示原始信令报文
        self.check_raw_sip = QCheckBox("显示原始信令（SIP报文）")
        self.check_raw_sip.setChecked(True)
        self.check_raw_sip.setStyleSheet("QCheckBox { color: #A1A1AA; font-size: 12px; }")
        self.check_raw_sip.stateChanged.connect(self.filter_logs)

        # 搜索输入框
        self.input_search = QLineEdit()
        self.input_search.setPlaceholderText("🔍 输入关键字过滤高亮...")
        self.input_search.textChanged.connect(self.filter_logs)

        # 清除日志
        self.btn_clear_logs = QPushButton("清空")
        self.btn_clear_logs.setCursor(Qt.PointingHandCursor)
        self.btn_clear_logs.setStyleSheet("""
            QPushButton { background-color: #3F3F46; color: #F4F4F5; font-weight: bold; border-radius: 6px; padding: 5px 12px; }
            QPushButton:hover { background-color: #52525B; }
            QPushButton:disabled { background-color: #4B5563; color: #9CA3AF; }
        """)
        self.btn_clear_logs.clicked.connect(self.clear_logs)

        filter_layout.addWidget(QLabel("日志类型:"))
        filter_layout.addWidget(self.combo_filter)
        filter_layout.addWidget(self.check_raw_sip)
        filter_layout.addWidget(self.input_search, 1)
        filter_layout.addWidget(self.btn_clear_logs)
        log_panel_layout.addLayout(filter_layout)

        # 4. 底部日志框
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setPlaceholderText("SIP 信令与流媒体日志将在这里滚动输出...")
        self.log_text.setStyleSheet("""
            QTextEdit {
                background-color: #09090B;
                color: #D4D4D8;
                font-family: Menlo, Monaco, 'Courier New', monospace;
                font-size: 12px;
                border: 1px solid #27272A;
                border-radius: 8px;
            }
        """)
        self.log_text.setMinimumHeight(150)
        log_panel_layout.addWidget(self.log_text)

        # 创建主选项卡容器 (单设备精细调试 vs 多设备模拟集群)
        self.main_tab_widget = QTabWidget()
        self.main_tab_widget.addTab(top_splitter, "📱 单设备精细调试")
        self.multi_device_widget = self.create_multi_device_tab()
        self.main_tab_widget.addTab(self.multi_device_widget, "🚀 多设备模拟集群")

        # 把主选项卡和底部日志面板放入主垂直分割器
        main_splitter.addWidget(self.main_tab_widget)
        main_splitter.addWidget(log_panel)
        main_splitter.setSizes([550, 200])

        main_layout.addWidget(main_splitter, 1)

        self.all_input_widgets = [
            self.input_server_ip, self.input_server_port, self.input_server_id,
            self.input_local_ip, self.input_local_port, self.input_device_id,
            self.input_channel_id, self.input_password, self.input_camera,
            self.input_custom_url, self.btn_select_local_file,
            self.combo_preset,
            self.input_video_resolution, self.input_video_fps,
            self.input_video_bitrate,
            self.check_mirror,
            self.check_audio, self.input_audio,
            self.input_device_name, self.input_manufacturer, self.input_model,
            self.input_civil_code, self.input_address, self.input_owner,
            self.input_heartbeat_interval, self.input_expire_time,
            self.combo_saved_presets, self.btn_load_preset, self.btn_save_preset, self.btn_delete_preset,
            self.combo_channels, self.btn_add_channel, self.btn_copy_channel, self.btn_remove_channel,
            self.input_channel_name, self.input_channel_source, self.input_channel_custom_url,
            self.btn_channel_select_file,
        ]

        log_signaler.log_signal.connect(self.append_log)
        self.init_default_channels()
        self.load_presets()

        self.stream_health_timer = QTimer(self)
        self.stream_health_timer.setInterval(5000)
        self.stream_health_timer.timeout.connect(self.check_stream_health)
        self.stream_health_timer.start()

    # ---- 辅助方法 ----

    def current_base_channel(self):
        return {
            "channel_id": self.input_channel_id.text().strip() or "34020000001320000002",
            "name": self.input_channel_name.text().strip() if hasattr(self, "input_channel_name") else "通道1",
            "camera_source_text": self.input_camera.currentText(),
            "custom_url": self.input_custom_url.text().strip(),
        }

    def _sync_first_channel_source(self):
        if self._loading_channel_ui or not getattr(self, "channel_configs", None):
            return
        self.channel_configs[0].update({
            "channel_id": self.input_channel_id.text().strip(),
            "camera_source_text": self.input_camera.currentText(),
            "custom_url": self.input_custom_url.text().strip(),
        })
        if self.current_channel_index == 0 and hasattr(self, "input_channel_source"):
            self.input_channel_source.setCurrentText(self.input_camera.currentText())
            self.input_channel_custom_url.setText(self.input_custom_url.text().strip())
        self.refresh_channel_combo()

    def init_default_channels(self):
        self.channel_configs = [normalize_channel_config(self.current_base_channel(), index=0)]
        self.current_channel_index = 0
        self.refresh_channel_combo()
        self.load_channel_to_ui(0)

    def refresh_channel_combo(self):
        current = self.current_channel_index
        self.combo_channels.blockSignals(True)
        self.combo_channels.clear()
        for idx, channel in enumerate(self.channel_configs):
            name = channel.get("name") or f"通道{idx + 1}"
            cid = channel.get("channel_id") or ""
            self.combo_channels.addItem(f"{idx + 1}. {name} ({cid})")
        self.combo_channels.setCurrentIndex(max(0, min(current, len(self.channel_configs) - 1)))
        self.combo_channels.blockSignals(False)

    def load_channel_to_ui(self, index):
        if not self.channel_configs:
            return
        self._loading_channel_ui = True
        self.current_channel_index = max(0, min(index, len(self.channel_configs) - 1))
        channel = self.channel_configs[self.current_channel_index]
        self.input_channel_id.setText(channel.get("channel_id", ""))
        self.input_channel_name.setText(channel.get("name", f"通道{self.current_channel_index + 1}"))
        source = channel.get("camera_source_text", self.input_camera.currentText())
        source_idx = self.input_channel_source.findText(source)
        if source_idx >= 0:
            self.input_channel_source.setCurrentIndex(source_idx)
        self.input_channel_custom_url.setText(channel.get("custom_url", self.input_custom_url.text().strip()))
        self.input_camera.setCurrentText(source)
        self.input_custom_url.setText(channel.get("custom_url", self.input_custom_url.text().strip()))
        self.on_channel_source_changed(self.input_channel_source.currentIndex())
        self._loading_channel_ui = False

    def update_current_channel_from_ui(self):
        if self._loading_channel_ui or not self.channel_configs:
            return
        idx = max(0, min(self.current_channel_index, len(self.channel_configs) - 1))
        self.channel_configs[idx].update({
            "channel_id": self.input_channel_id.text().strip(),
            "name": self.input_channel_name.text().strip() or f"通道{idx + 1}",
            "camera_source_text": self.input_channel_source.currentText(),
            "custom_url": self.input_channel_custom_url.text().strip(),
        })
        if idx == 0:
            self.input_camera.setCurrentText(self.channel_configs[idx]["camera_source_text"])
            self.input_custom_url.setText(self.channel_configs[idx]["custom_url"])
        self.refresh_channel_combo()

    def sync_current_channel_from_main(self):
        if not self.channel_configs:
            self.init_default_channels()
        idx = max(0, min(self.current_channel_index, len(self.channel_configs) - 1))
        self.channel_configs[idx].update({
            "channel_id": self.input_channel_id.text().strip(),
            "name": self.input_channel_name.text().strip() or f"通道{idx + 1}",
            "camera_source_text": self.input_camera.currentText(),
            "custom_url": self.input_custom_url.text().strip(),
        })
        self.refresh_channel_combo()

    def on_channel_selected(self, index):
        if index < 0 or not self.channel_configs:
            return
        self.sync_current_channel_from_main()
        self.load_channel_to_ui(index)

    def on_channel_source_changed(self, index):
        selected_text = self.input_channel_source.currentText()
        enabled = "【自定义源】" in selected_text
        self.input_channel_custom_url.setEnabled(enabled)
        self.btn_channel_select_file.setEnabled(enabled)
        self.update_current_channel_from_ui()

    def next_channel_id(self):
        base = self.channel_configs[0].get("channel_id", self.input_channel_id.text().strip())
        try:
            return str(int(base) + len(self.channel_configs)).zfill(len(base))
        except Exception:
            return f"{base}_{len(self.channel_configs) + 1}"

    def add_channel(self):
        self.sync_current_channel_from_main()
        base = dict(self.channel_configs[0]) if self.channel_configs else self.current_base_channel()
        base["channel_id"] = self.next_channel_id()
        base["name"] = f"通道{len(self.channel_configs) + 1}"
        self.channel_configs.append(normalize_channel_config(base, self.channel_configs[0], len(self.channel_configs)))
        self.current_channel_index = len(self.channel_configs) - 1
        self.refresh_channel_combo()
        self.load_channel_to_ui(self.current_channel_index)
        self.append_log(f"[系统] 已新增通道: {base['name']} / {base['channel_id']}")

    def copy_first_channel_to_current(self):
        if len(self.channel_configs) < 2:
            return
        idx = self.current_channel_index
        first = dict(self.channel_configs[0])
        first["channel_id"] = self.channel_configs[idx].get("channel_id")
        first["name"] = self.channel_configs[idx].get("name")
        self.channel_configs[idx].update(first)
        self.load_channel_to_ui(idx)
        self.append_log(f"[系统] 已将通道1来源复制到当前通道: {first['channel_id']}")

    def remove_current_channel(self):
        if len(self.channel_configs) <= 1:
            QMessageBox.information(self, "提示", "至少保留一个通道。")
            return
        removed = self.channel_configs.pop(self.current_channel_index)
        self.current_channel_index = max(0, self.current_channel_index - 1)
        self.refresh_channel_combo()
        self.load_channel_to_ui(self.current_channel_index)
        self.append_log(f"[系统] 已删除通道: {removed.get('name')} / {removed.get('channel_id')}")

    def select_channel_local_file(self):
        current_value = self.input_channel_custom_url.text().strip()
        initial_dir = ""
        if current_value and not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", current_value):
            initial_dir = current_value if os.path.isdir(current_value) else os.path.dirname(current_value)
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择当前通道本地视频文件",
            initial_dir,
            "视频文件 (*.mp4 *.mov *.mkv *.avi *.flv *.ts *.m2ts *.webm *.mpeg *.mpg);;所有文件 (*)",
        )
        if file_path:
            self.input_channel_custom_url.setText(file_path)
            self.append_log(f"[系统] 当前通道已选择本地视频文件: {file_path}")

    def load_presets(self):
        try:
            with open(get_preset_file_path(), "r", encoding="utf-8") as f:
                self.preset_data = json.load(f)
        except Exception:
            self.preset_data = {}
        self.combo_saved_presets.clear()
        self.combo_saved_presets.addItems(sorted(self.preset_data.keys()))

    def save_current_preset(self):
        self.sync_current_channel_from_main()
        name, ok = QInputDialog.getText(self, "保存配置预设", "预设名称：")
        if not ok or not name.strip():
            return
        self.preset_data[name.strip()] = self._build_config_data()
        with open(get_preset_file_path(), "w", encoding="utf-8") as f:
            json.dump(self.preset_data, f, ensure_ascii=False, indent=2)
        self.load_presets()
        self.combo_saved_presets.setCurrentText(name.strip())
        self.append_log(f"[系统] 已保存配置预设: {name.strip()}")

    def load_selected_preset(self):
        name = self.combo_saved_presets.currentText()
        if not name or name not in self.preset_data:
            return
        self.apply_config_data(self.preset_data[name])
        self.append_log(f"[系统] 已加载配置预设: {name}")

    def delete_selected_preset(self):
        name = self.combo_saved_presets.currentText()
        if not name or name not in self.preset_data:
            return
        if QMessageBox.question(self, "删除预设", f"确认删除预设“{name}”？") != QMessageBox.Yes:
            return
        del self.preset_data[name]
        with open(get_preset_file_path(), "w", encoding="utf-8") as f:
            json.dump(self.preset_data, f, ensure_ascii=False, indent=2)
        self.load_presets()
        self.append_log(f"[系统] 已删除配置预设: {name}")

    def apply_config_data(self, data):
        self.input_server_ip.setEditText(data.get("server_ip", self.input_server_ip.currentText()))
        self.input_server_port.setText(str(data.get("server_port", self.input_server_port.text())))
        self.input_server_id.setText(data.get("server_id", self.input_server_id.text()))
        self.input_local_ip.setEditText(data.get("local_ip", self.input_local_ip.currentText()))
        self.input_local_port.setText(str(data.get("local_port", self.input_local_port.text())))
        self.input_device_id.setText(data.get("device_id", self.input_device_id.text()))
        self.input_password.setText(data.get("password", self.input_password.text()))
        self.input_heartbeat_interval.setText(str(data.get("heartbeat_interval", self.input_heartbeat_interval.text())))
        self.input_expire_time.setText(str(data.get("expire_time", self.input_expire_time.text())))
        self.input_video_resolution.setCurrentText(data.get("video_resolution", self.input_video_resolution.currentText()))
        self.input_video_fps.setCurrentText(str(data.get("video_fps", self.input_video_fps.currentText())))
        self.input_video_bitrate.setCurrentText(str(data.get("video_bitrate", self.input_video_bitrate.currentText())))
        self.check_mirror.setChecked(bool(data.get("video_mirror", self.check_mirror.isChecked())))
        self.check_audio.setChecked(bool(data.get("audio_enabled", self.check_audio.isChecked())))
        self.input_device_name.setText(data.get("device_name", self.input_device_name.text()))
        self.input_manufacturer.setText(data.get("manufacturer", self.input_manufacturer.text()))
        self.input_model.setText(data.get("model", self.input_model.text()))
        self.input_civil_code.setText(data.get("civil_code", self.input_civil_code.text()))
        self.input_address.setText(data.get("address", self.input_address.text()))
        self.input_owner.setText(data.get("owner", self.input_owner.text()))
        fallback = {
            "channel_id": data.get("channel_id", self.input_channel_id.text()),
            "name": "通道1",
            "camera_source_text": data.get("camera_source_text", self.input_camera.currentText()),
            "custom_url": data.get("custom_url", self.input_custom_url.text()),
        }
        channels = data.get("channels") or [fallback]
        self.channel_configs = [
            normalize_channel_config(channel, fallback, idx)
            for idx, channel in enumerate(channels)
        ]
        self.current_channel_index = 0
        self.refresh_channel_combo()
        self.load_channel_to_ui(0)

    def on_camera_source_changed(self, index):
        selected_text = self.input_camera.currentText()
        if "【自定义源】" in selected_text:
            self.custom_url_container.show()
        else:
            self.custom_url_container.hide()
        if not self._loading_channel_ui and self.channel_configs:
            self.channel_configs[0]["camera_source_text"] = self.input_camera.currentText()

    def on_preset_changed(self, index):
        selected_text = self.combo_preset.currentText()
        if "超清" in selected_text:
            self.input_video_resolution.setCurrentText("1920x1080")
            self.input_video_fps.setCurrentText("30")
            self.input_video_bitrate.setCurrentText("4000")
            self.input_video_resolution.setEnabled(False)
            self.input_video_fps.setEnabled(False)
            self.input_video_bitrate.setEnabled(False)
        elif "高清" in selected_text:
            self.input_video_resolution.setCurrentText("1280x720")
            self.input_video_fps.setCurrentText("30")
            self.input_video_bitrate.setCurrentText("2000")
            self.input_video_resolution.setEnabled(False)
            self.input_video_fps.setEnabled(False)
            self.input_video_bitrate.setEnabled(False)
        elif "流畅" in selected_text:
            self.input_video_resolution.setCurrentText("640x360")
            self.input_video_fps.setCurrentText("15")
            self.input_video_bitrate.setCurrentText("800")
            self.input_video_resolution.setEnabled(False)
            self.input_video_fps.setEnabled(False)
            self.input_video_bitrate.setEnabled(False)
        else: # 自定义设置
            self.input_video_resolution.setEnabled(True)
            self.input_video_fps.setEnabled(True)
            self.input_video_bitrate.setEnabled(True)

    def on_mirror_toggled(self, state):
        if self.is_previewing:
            self.video_view.setMirrored(self.check_mirror.isChecked())

    def on_audio_toggled(self, state):
        self.input_audio.setEnabled(self.check_audio.isChecked())

    def auto_select_local_ip(self, server_ip):
        best = best_local_ip_for_server(server_ip.strip(), self.local_ips)
        idx = self.input_local_ip.findText(best)
        if idx >= 0:
            self.input_local_ip.setCurrentIndex(idx)
        else:
            self.input_local_ip.setEditText(best)

    def on_camera_source_changed(self, index):
        selected_text = self.input_camera.currentText()
        if "【自定义源】" in selected_text:
            self.custom_url_container.show()
        else:
            self.custom_url_container.hide()

    def select_local_video_file(self):
        current_value = self.input_custom_url.text().strip()
        initial_dir = ""
        if current_value and not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", current_value):
            import os
            initial_dir = current_value if os.path.isdir(current_value) else os.path.dirname(current_value)

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择本地视频文件",
            initial_dir,
            "视频文件 (*.mp4 *.mov *.mkv *.avi *.flv *.ts *.m2ts *.webm *.mpeg *.mpg);;所有文件 (*)",
        )
        if file_path:
            self.input_custom_url.setText(file_path)
            self.append_log(f"[系统] 已选择本地视频文件: {file_path}")

    def toggle_password_visibility(self):
        if self.input_password.echoMode() == QLineEdit.Password:
            self.input_password.setEchoMode(QLineEdit.Normal)
            self.btn_toggle_pwd.setText("🔒")
        else:
            self.input_password.setEchoMode(QLineEdit.Password)
            self.btn_toggle_pwd.setText("👁️")

    # ---- 日志解析与过滤 ----

    def append_log(self, text):
        log_type = "System"
        
        # 判断报文类型与原始信令分类
        if "[RAW_SIP_IN]" in text or "[RAW_SIP_OUT]" in text:
            log_type = "RawSIP"
        elif "[GB28181]" in text or "[PTZ]" in text:
            log_type = "GB28181"
        elif "[SIP]" in text:
            log_type = "SIP"
        elif "[Media]" in text:
            log_type = "Media"
        elif "[系统]" in text or "[System]" in text:
            log_type = "System"
        elif "[错误]" in text or "[注册失败]" in text or "[Error]" in text or "[SIP Error]" in text or "[Media Error]" in text:
            log_type = "Error"

        self.logs_archive.append((log_type, text))
        print(text, flush=True)
        self.filter_logs()

        # 注册成功绿灯提示
        if "Registration Successful!" in text:
            self.status_label.setText("● 已注册")
            self.status_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #10B981;")

        # 推流状态兜底：即使跨线程状态信号没有及时刷新，日志也能驱动右上角实时状态。
        if "Received INVITE" in text or "Instructed to stream" in text:
            if self.stream_state not in ("STREAMING", "MANUAL"):
                self.set_stream_status("STARTING", "平台已触发点播，正在启动推流")
        elif "推流 FFmpeg 已启动" in text or "平台点播推流中" in text:
            if self.stream_state != "MANUAL":
                self.set_stream_status("STREAMING", "平台点播推流中")
        elif "Received BYE" in text or "推流已停止" in text or "设备已停止" in text:
            if self.stream_state != "MANUAL":
                self.set_stream_status("IDLE", "当前未推流")
        elif "推流中断" in text or "启动推流失败" in text or "FFmpeg 启动失败" in text:
            self.set_stream_status("ERROR", text)

    def clear_logs(self):
        self.logs_archive = []
        self.log_text.clear()

    def filter_logs(self):
        selected = self.combo_filter.currentText()
        show_raw = self.check_raw_sip.isChecked()
        keyword = self.input_search.text().strip()
        
        self.log_text.clear()
        
        type_map = {
            "国标交互": "GB28181",
            "SIP信令": "SIP",
            "媒体推流": "Media",
            "系统通知": "System",
            "错误警告": "Error",
        }
        target = type_map.get(selected, None)
        
        # 逐条过滤显示
        for log_type, text in self.logs_archive:
            # 1. 过滤原始信令报文
            if log_type == "RawSIP" and not show_raw:
                continue
                
            # 2. 根据选中的日志大类过滤
            if target:
                if target == "SIP" and log_type not in ["SIP", "RawSIP", "GB28181"]:
                    continue
                elif target != "SIP" and log_type != target:
                    continue
            
            # 3. 关键字模糊搜索
            if keyword and keyword.lower() not in text.lower():
                continue

            show = html.escape(text)
            # 4. 关键字背景黄色高亮
            if keyword:
                escaped_keyword = html.escape(keyword)
                show = re.compile(re.escape(keyword), re.IGNORECASE).sub(
                    lambda m: f"<span style='background-color:#F59E0B;color:#000;font-weight:bold'>{m.group(0)}</span>",
                    show
                ) if escaped_keyword == keyword else re.compile(re.escape(escaped_keyword), re.IGNORECASE).sub(
                    lambda m: f"<span style='background-color:#F59E0B;color:#000;font-weight:bold'>{m.group(0)}</span>",
                    show
                )

            # 5. 信令报文自动折行显示
            if log_type == "RawSIP":
                show = show.replace("\n", "<br>").replace(" ", "&nbsp;")
            
            # 6. 配置配色样式
            color = {
                "SIP": "#38BDF8", 
                "RawSIP": "#94A3B8", 
                "GB28181": "#FBBF24",
                "Media": "#C084FC", 
                "System": "#34D399", 
                "Error": "#F87171"
            }.get(log_type, "#D4D4D8")
            
            self.log_text.append(f"<font color='{color}'>{show}</font>")

    # ---- 设备管理 ----

    def _build_config_data(self):
        self.sync_current_channel_from_main()
        primary_channel = self.channel_configs[0] if self.channel_configs else self.current_base_channel()
        return {
            'server_ip': self.input_server_ip.currentText().strip(),
            'server_port': self.input_server_port.text().strip(),
            'server_id': self.input_server_id.text().strip(),
            'local_ip': self.input_local_ip.currentText().strip(),
            'local_port': self.input_local_port.text().strip(),
            'device_id': self.input_device_id.text().strip(),
            'channel_id': primary_channel.get('channel_id', self.input_channel_id.text().strip()),
            'password': self.input_password.text().strip(),
            'heartbeat_interval': self.input_heartbeat_interval.text().strip() or '60',
            'expire_time': self.input_expire_time.text().strip() or '3600',
            'camera_index': self.input_camera.currentIndex(),
            'camera_source_text': primary_channel.get('camera_source_text', self.input_camera.currentText()),
            'camera_devices_list': [cam.description() for cam in self.camera_devices],
            'custom_url': primary_channel.get('custom_url', self.input_custom_url.text().strip()),
            'channels': self.channel_configs,
            'video_resolution': self.input_video_resolution.currentText().strip() or '640x480',
            'video_fps': self.input_video_fps.currentText().strip() or '30',
            'video_bitrate': self.input_video_bitrate.currentText().strip() or '1000',
            'video_mirror': self.check_mirror.isChecked(),
            'audio_enabled': self.check_audio.isChecked(),
            'audio_source_idx': self.input_audio.currentIndex(),
            'audio_devices_list': [dev.description() for dev in self.audio_devices],
            'local_preview_port': 23000,
            'ptz_pan': self.ptz_pan,
            'ptz_tilt': self.ptz_tilt,
            'ptz_zoom': self.ptz_zoom,
            'manufacturer': self.input_manufacturer.text().strip() or 'lanccj',
            'model': self.input_model.text().strip() or 'MacSimulator',
            'device_name': self.input_device_name.text().strip() or 'lanccj',
            'civil_code': self.input_civil_code.text().strip() or '3402000000',
            'address': self.input_address.text().strip() or '实验室',
            'owner': self.input_owner.text().strip() or 'Owner',
        }

    def _set_inputs_enabled(self, enabled: bool):
        for w in self.all_input_widgets + [self.btn_toggle_pwd]:
            if w in [self.input_video_resolution, self.input_video_fps, self.input_video_bitrate]:
                w.setEnabled(enabled and ("自定义" in self.combo_preset.currentText()))
            else:
                w.setEnabled(enabled)

    def set_stream_status(self, state, message=""):
        self.stream_state = state
        styles = {
            "IDLE": ("● 未推流", "#71717A"),
            "STARTING": ("● 启动中", "#F59E0B"),
            "STREAMING": ("● 推流中", "#10B981"),
            "MANUAL": ("● 手动推流中", "#10B981"),
            "ERROR": ("● 推流异常", "#EF4444"),
        }
        text, color = styles.get(state, (f"● {state}", "#A1A1AA"))
        self.stream_status_label.setText(text)
        self.stream_status_label.setToolTip(message or text)
        self.stream_status_label.setStyleSheet(
            f"font-size: 14px; font-weight: bold; color: {color};"
        )

    def on_media_state_changed(self, state, message):
        if state in ("IDLE", "ERROR"):
            self._restore_camera_preview_after_stream()
        self.set_stream_status(state, message)
        if message:
            self.append_log(f"[系统] 推流状态：{message}")

    def check_stream_health(self):
        now = time.monotonic()
        elapsed = now - self.last_health_check_ts
        self.last_health_check_ts = now
        woke_from_sleep = elapsed > 20

        if self.is_manual_pushing:
            running = (
                self.manual_media_ctrl is not None
                and self.manual_media_ctrl.is_streaming()
            )
            if woke_from_sleep or not running:
                reason = (
                    "检测到电脑休眠/唤醒，手动推流已失效，请重新测试推流。"
                    if woke_from_sleep else
                    "手动推流 FFmpeg 进程已退出，当前未检测到本机推流。"
                )
                if self.manual_media_ctrl:
                    self.manual_media_ctrl.stop_stream()
                    self.manual_media_ctrl = None
                self.is_manual_pushing = False
                self.btn_test_stream.setText("测试推流")
                self.btn_test_stream.setStyleSheet("""
                    QPushButton { background-color: #D97706; color: white; font-weight: bold; border-radius: 6px; padding: 8px 16px; }
                    QPushButton:hover { background-color: #B45309; }
                """)
                self.set_stream_status("ERROR", reason)
                self.append_log(f"[错误] {reason}")
                return

        device = self.thread.device if self.thread and self.thread.device else None
        if device and getattr(device, "media_state", "IDLE") in ("STARTING", "STREAMING"):
            running = device.media_ctrl.is_streaming()
            if woke_from_sleep or not running:
                reason = (
                    "检测到电脑休眠/唤醒，平台点播推流已中断，请在平台重新点播。"
                    if woke_from_sleep else
                    "平台点播推流 FFmpeg 进程已退出，当前未检测到本机推流。"
                )
                device.mark_media_interrupted(reason)

    def start_device(self):
        if self.is_manual_pushing:
            self.stop_manual_push()
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self._set_inputs_enabled(False)
        self.status_label.setText("● 正在注册...")
        self.status_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #F59E0B;")

        self.thread = DeviceThread(self._build_config_data())
        self.thread.registration_state_changed.connect(self.on_registration_state_changed)
        self.thread.ptz_state_changed.connect(self.on_external_ptz_state_changed)
        self.thread.media_state_changed.connect(self.on_media_state_changed)
        self.thread.media_preview_changed.connect(self.on_stream_preview_changed)
        self.thread.finished.connect(self.on_device_thread_finished)
        self.thread.start()

    def on_registration_state_changed(self, state, message):
        if state == "REGISTERING":
            self.status_label.setText("● 正在注册...")
            self.status_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #F59E0B;")
        elif state == "AUTHENTICATING":
            self.status_label.setText("● 正在认证...")
            self.status_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #F59E0B;")
        elif state == "REGISTERED":
            self.status_label.setText("● 已注册")
            self.status_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #10B981;")
        elif state == "FAILED":
            self.status_label.setText("● 注册失败")
            self.status_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #EF4444;")
            QMessageBox.warning(self, "设备注册失败", message)

    def stop_device(self):
        if self.thread:
            try:
                self.thread.finished.disconnect(self.on_device_thread_finished)
            except Exception:
                pass
            self.thread.stop()
            self.thread = None
        MediaController.kill_bundled_ffmpeg_processes(log_signaler.log_signal.emit)

        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self._set_inputs_enabled(True)
        self.status_label.setText("● 未连接")
        self.status_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #EF4444;")
        self.set_stream_status("IDLE", "设备已停止，当前未推流")
        self.append_log("[系统] 设备已停止。")

    def on_device_thread_finished(self):
        failed = self.thread is not None and self.thread.last_registration_state == "FAILED"
        failure_message = self.thread.last_registration_message if failed else ""
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self._set_inputs_enabled(True)
        if failed:
            self.status_label.setText("● 注册失败")
            self.status_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #EF4444;")
            self.append_log(f"[错误] 注册已终止，可修改配置后重新注册：{failure_message}")
        else:
            self.status_label.setText("● 未连接")
            self.status_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #EF4444;")
            self.append_log("[系统] 设备线程已退出，控制已重置。")
        self.set_stream_status("IDLE", "设备线程已退出，当前未推流")
        self.thread = None

    # ---- 视频源本地预览 ----

    def clamp_ptz_state(self, pan=None, tilt=None, zoom=None):
        pan = self.ptz_pan if pan is None else pan
        tilt = self.ptz_tilt if tilt is None else tilt
        zoom = self.ptz_zoom if zoom is None else zoom
        zoom = max(1.0, min(4.0, float(zoom)))
        pan_limit = 0.0 if zoom <= 1.01 else 1.0
        tilt_limit = 0.0 if zoom <= 1.01 else 1.0
        return (
            max(-pan_limit, min(pan_limit, float(pan))),
            max(-tilt_limit, min(tilt_limit, float(tilt))),
            zoom,
        )

    def set_ptz_state(self, pan=None, tilt=None, zoom=None, source="界面", propagate=True):
        self.ptz_pan, self.ptz_tilt, self.ptz_zoom = self.clamp_ptz_state(pan, tilt, zoom)
        self.video_view.setPtzState(self.ptz_pan, self.ptz_tilt, self.ptz_zoom)
        self.lbl_ptz_state.setText(
            f"PTZ：X {self.ptz_pan:+.2f} / Y {self.ptz_tilt:+.2f} / {self.ptz_zoom:.2f}x"
        )

        if propagate:
            if self.manual_media_ctrl:
                self.manual_media_ctrl.set_ptz_state(
                    self.ptz_pan, self.ptz_tilt, self.ptz_zoom
                )
            if self.thread and self.thread.device:
                self.thread.device.set_ptz_state(
                    self.ptz_pan, self.ptz_tilt, self.ptz_zoom, source=source
                )

    def apply_local_ptz_action(self, action):
        action_names = {
            "up": "上移",
            "down": "下移",
            "left": "左移",
            "right": "右移",
            "zoom_in": "放大",
            "zoom_out": "缩小",
            "stop": "停止",
            "reset": "重置",
        }
        self.append_log(f"[PTZ][本机界面] 点击按钮: {action_names.get(action, action)}")
        self.apply_ptz_action(action, source="本机界面")

    def apply_ptz_action(self, action, speed=1.0, source="界面"):
        step = self.ptz_step * max(0.2, min(3.0, float(speed)))
        zoom_step = self.ptz_zoom_step * max(0.2, min(3.0, float(speed)))

        pan, tilt, zoom = self.ptz_pan, self.ptz_tilt, self.ptz_zoom
        if action == "left":
            zoom = max(zoom, 1.5)
            pan -= step
        elif action == "right":
            zoom = max(zoom, 1.5)
            pan += step
        elif action == "up":
            zoom = max(zoom, 1.5)
            tilt -= step
        elif action == "down":
            zoom = max(zoom, 1.5)
            tilt += step
        elif action == "zoom_in":
            zoom += zoom_step
        elif action == "zoom_out":
            zoom -= zoom_step
        elif action == "reset":
            pan, tilt, zoom = 0.0, 0.0, 1.0
        elif action == "stop":
            self.append_log(f"[PTZ] {source}停止移动，保持当前模拟云台位置。")
            return
        else:
            return

        self.set_ptz_state(pan, tilt, zoom, source=source)
        self.append_log(
            f"[PTZ] {source}控制: {action} -> pan={self.ptz_pan:.2f}, "
            f"tilt={self.ptz_tilt:.2f}, zoom={self.ptz_zoom:.2f}x"
        )

    def on_external_ptz_state_changed(self, pan, tilt, zoom, source):
        self.set_ptz_state(pan, tilt, zoom, source=source, propagate=False)
        self.append_log(
            f"[PTZ] {source}控制已同步到本地预览: pan={pan:.2f}, tilt={tilt:.2f}, zoom={zoom:.2f}x"
        )

    def toggle_preview(self):
        if self.is_previewing:
            self.stop_preview()
            return

        selected_text = self.input_camera.currentText()
        if "【摄像头】" in selected_text:
            self.start_camera_preview(selected_text)
        elif "【自定义源】" in selected_text:
            self.start_custom_source_preview()
        else:
            self.append_log(f"[系统] 该视频源 ({selected_text}) 暂不支持本地预览。")

    def start_camera_preview(self, selected_text):
        permission = QCameraPermission()
        permission_status = QApplication.instance().checkPermission(permission)
        if permission_status == Qt.PermissionStatus.Undetermined:
            self.append_log("[系统] 正在请求 macOS 相机访问权限...")
            QApplication.instance().requestPermission(
                permission,
                self,
                lambda result: self.on_camera_permission_result(
                    result, selected_text
                ),
            )
            return
        if permission_status == Qt.PermissionStatus.Denied:
            self.append_log(
                "[错误] macOS 未授权相机访问。请在“系统设置 → 隐私与安全性 → 相机”中允许 OpenGBD。"
            )
            return

        self._start_camera_preview(selected_text)

    def on_camera_permission_result(self, permission, selected_text):
        if permission.status() == Qt.PermissionStatus.Granted:
            self.append_log("[系统] 相机权限已授权。")
            self._start_camera_preview(selected_text)
        else:
            self.append_log(
                "[错误] 相机权限被拒绝。请在“系统设置 → 隐私与安全性 → 相机”中允许 OpenGBD。"
            )

    def _start_camera_preview(self, selected_text):
        clean_desc = selected_text.replace("【摄像头】", "").strip()
        dev = next(
            (cam for cam in self.camera_devices if cam.description() == clean_desc),
            None,
        )
        if not dev:
            self.append_log("[错误] 未找到对应的物理摄像头设备！")
            return

        self.append_log(f"[系统] 初始化摄像头 [{dev.description()}] 预览...")
        try:
            self.preview_requested = True
            self.preview_mode = "camera"
            self.camera_frame_received = False
            self.camera = QCamera(dev)
            self.camera.errorOccurred.connect(self.on_camera_error)
            self.capture_session = QMediaCaptureSession()
            self.capture_session.setCamera(self.camera)
            self.capture_session.setVideoOutput(self.video_item)
            self._show_preview_ui()
            self.camera.start()
            QTimer.singleShot(3000, self.check_camera_preview_frames)
        except Exception as e:
            self.append_log(f"[错误] 摄像头初始化失败: {e}")
            self.stop_preview()

    def on_camera_error(self, error, error_string):
        if error != QCamera.NoError:
            self.append_log(f"[错误] 摄像头启动失败: {error_string}")
            self.stop_preview()

    def on_preview_video_frame(self, frame):
        if self.camera and frame.isValid():
            self.camera_frame_received = True

    def check_camera_preview_frames(self):
        if self.camera and self.camera.isActive() and not self.camera_frame_received:
            self.append_log(
                "[错误] 摄像头已启动但未收到画面，请检查 macOS 相机权限或是否被其他应用占用。"
            )

    def start_custom_source_preview(self):
        import os

        source = self.input_custom_url.text().strip()
        if not source:
            self.append_log("[错误] 请先输入网络流 URL 或选择本地视频文件。")
            return

        is_file_url = source.lower().startswith("file://")
        is_network_source = bool(
            re.match(r"^(rtsp|rtmp|https?)://", source, re.IGNORECASE)
        )
        local_path = QUrl(source).toLocalFile() if is_file_url else source
        is_local_file = is_file_url or not is_network_source
        if is_local_file and not os.path.isfile(local_path):
            self.append_log(f"[错误] 本地视频文件不存在: {source}")
            return

        media_url = (
            QUrl(source) if is_network_source or is_file_url
            else QUrl.fromLocalFile(source)
        )
        self.append_log(f"[系统] 正在打开自定义视频源: {source}")
        try:
            self.preview_requested = True
            self.preview_mode = "custom"
            self.media_player = QMediaPlayer(self)
            self.preview_audio_output = QAudioOutput(self)
            self.preview_audio_output.setMuted(True)
            self.media_player.setAudioOutput(self.preview_audio_output)
            self.media_player.setVideoOutput(self.video_item)
            self.media_player.errorOccurred.connect(self.on_preview_player_error)
            self.media_player.positionChanged.connect(
                self.on_preview_position_changed
            )
            self.media_player.durationChanged.connect(
                self.on_preview_duration_changed
            )
            self.media_player.setLoops(
                QMediaPlayer.Infinite if is_local_file else QMediaPlayer.Once
            )
            self.media_player.setSource(media_url)
            self._show_preview_ui(show_progress=is_local_file)
            self.media_player.play()
        except Exception as e:
            self.append_log(f"[错误] 自定义视频源预览失败: {e}")
            self.stop_preview()

    def _show_preview_ui(self, show_progress=False):
        self.video_placeholder.hide()
        self.video_view.show()
        self.video_view.setMirrored(self.check_mirror.isChecked())
        self.video_view.setPtzState(self.ptz_pan, self.ptz_tilt, self.ptz_zoom)
        self.preview_progress_container.setVisible(show_progress)
        if show_progress:
            self.preview_duration_ms = 0
            self.preview_progress_slider.setRange(0, 0)
            self.preview_progress_slider.setValue(0)
            self.preview_time_label.setText("00:00 / 00:00")
        self.is_previewing = True
        self.preview_requested = True
        self.btn_preview.setText("关闭预览")
        self.btn_preview.setStyleSheet("""
            QPushButton { background-color: #DC2626; color: white; font-weight: bold; border-radius: 6px; padding: 8px 16px; }
            QPushButton:hover { background-color: #B91C1C; }
        """)

    def on_preview_player_error(self, error, error_string):
        if error != QMediaPlayer.NoError:
            self.append_log(f"[错误] 视频预览播放失败: {error_string}")
            if self.preview_mode == "stream" and self.stream_state in ("STARTING", "STREAMING", "MANUAL"):
                retry_url = self.stream_preview_url
                self._release_preview_resources()
                self.preview_mode = "switching"
                self.video_view.hide()
                self.video_placeholder.setText("共享预览暂未收到视频，正在等待推流数据...")
                self.video_placeholder.show()
                if retry_url and self.stream_preview_retry_count < 3:
                    self.stream_preview_retry_count += 1
                    QTimer.singleShot(
                        700,
                        lambda: self._start_stream_preview(retry_url)
                        if self.preview_requested and self.stream_preview_url == retry_url else None,
                    )
            else:
                self.stop_preview()

    @staticmethod
    def format_preview_time(milliseconds):
        total_seconds = max(0, int(milliseconds)) // 1000
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    def update_preview_time_label(self, position_ms):
        current = self.format_preview_time(position_ms)
        total = self.format_preview_time(self.preview_duration_ms)
        self.preview_time_label.setText(f"{current} / {total}")

    def on_preview_duration_changed(self, duration_ms):
        self.preview_duration_ms = max(0, int(duration_ms))
        self.preview_progress_slider.setRange(0, self.preview_duration_ms)
        self.update_preview_time_label(
            self.media_player.position() if self.media_player else 0
        )

    def on_preview_position_changed(self, position_ms):
        if not self.preview_progress_slider.isSliderDown():
            self.preview_progress_slider.setValue(int(position_ms))
            self.update_preview_time_label(position_ms)

    def on_preview_slider_moved(self, position_ms):
        self.update_preview_time_label(position_ms)

    def seek_preview_position(self):
        if self.media_player:
            self.media_player.setPosition(self.preview_progress_slider.value())

    def _release_preview_resources(self):
        if self.media_player:
            try:
                self.media_player.stop()
                self.media_player.setVideoOutput(None)
            except Exception:
                pass
            self.media_player.deleteLater()
            self.media_player = None
            self.preview_audio_output = None

        if self.camera:
            try:
                self.camera.stop()
            except Exception:
                pass
            self.camera = None
            self.capture_session = None

    def stop_preview(self):
        self.preview_requested = False
        self._release_preview_resources()

        self.video_view.hide()
        self.video_placeholder.show()
        self.preview_progress_container.hide()
        self.preview_duration_ms = 0
        self.preview_progress_slider.setRange(0, 0)
        self.preview_time_label.setText("00:00 / 00:00")
        self.is_previewing = False
        self.preview_mode = None
        self.btn_preview.setText("本地预览")
        self.btn_preview.setStyleSheet("""
            QPushButton { background-color: #059669; color: white; font-weight: bold; border-radius: 6px; padding: 8px 16px; }
            QPushButton:hover { background-color: #047857; }
        """)
        self.append_log("[系统] 本地预览已停止。")

    def _suspend_camera_preview_for_stream(self):
        if not self.is_previewing or self.preview_mode != "camera":
            return
        self.preview_requested = True
        self._release_preview_resources()
        self.preview_mode = "switching"
        self.video_view.hide()
        self.video_placeholder.setText("正在切换到与国标推流同源的本地预览...")
        self.video_placeholder.show()
        self.append_log("[系统] 已释放QCamera，正在切换到FFmpeg单采集共享预览。")

    def on_stream_preview_changed(self, url):
        self.stream_preview_url = url or ""
        if self.stream_preview_url:
            self.stream_preview_retry_count = 0
        if self.stream_preview_url and self.preview_requested and self.preview_mode in ("camera", "switching", "stream"):
            self._start_stream_preview(self.stream_preview_url)
        elif not self.stream_preview_url and self.preview_mode == "stream":
            self._release_preview_resources()
            self.preview_mode = "switching"
            self.video_view.hide()
            self.video_placeholder.setText("推流已停止，正在恢复摄像头预览...")
            self.video_placeholder.show()

    def _start_stream_preview(self, url):
        self._release_preview_resources()
        self.media_player = QMediaPlayer(self)
        self.preview_audio_output = QAudioOutput(self)
        self.preview_audio_output.setMuted(True)
        self.media_player.setAudioOutput(self.preview_audio_output)
        self.media_player.setVideoOutput(self.video_item)
        self.media_player.errorOccurred.connect(self.on_preview_player_error)
        self.media_player.setSource(QUrl(url))
        self.preview_mode = "stream"
        self._show_preview_ui(show_progress=False)
        self.media_player.play()
        self.append_log(f"[系统] 本地预览已切换为FFmpeg共享输出: {url}")

    def _restore_camera_preview_after_stream(self):
        if not self.preview_requested or self.preview_mode not in ("stream", "switching"):
            return
        self._release_preview_resources()
        self.stream_preview_url = ""
        self.stream_preview_retry_count = 0
        selected_text = self.input_camera.currentText()
        if "【摄像头】" not in selected_text:
            self.stop_preview()
            return
        self.preview_mode = "switching"
        self.video_view.hide()
        self.video_placeholder.setText("正在恢复摄像头本地预览...")
        self.video_placeholder.show()
        QTimer.singleShot(350, lambda: self._start_camera_preview(selected_text) if self.preview_requested else None)

    def toggle_manual_push(self):
        if not self.is_manual_pushing:
            default_config = get_backend_default_config()
            # 传递当前的通道 ID 给 default_config 做流 ID 默认值
            default_config['channel_id'] = self.input_channel_id.text().strip()
            
            dialog = TestPushDialog(default_config, self)
            if dialog.exec() == QDialog.Accepted:
                target_ip, target_port, protocol, ssrc, stream_id = dialog.get_values()
                if not target_ip or not target_port:
                    self.append_log("[错误] 目标 IP 和端口不能为空！")
                    return
                
                try:
                    port_val = int(target_port)
                except ValueError:
                    self.append_log("[错误] 目标端口必须为数字！")
                    return
                
                self.append_log(f"[系统] 开始手动测试推流到 {target_ip}:{target_port} ({protocol}, SSRC: {ssrc}, 流 ID: {stream_id})...")
                
                # 记录这组推流用于后续释放关闭
                zlm_base_url = default_config.get('zlm_base_url', f"http://{target_ip}:80")
                zlm_secret = default_config.get('zlm_secret', '')
                
                self.current_manual_zlm_url = zlm_base_url
                self.current_manual_zlm_secret = zlm_secret
                self.current_manual_stream_id = stream_id
                
                if zlm_secret:
                    self.append_log(f"[系统] 正在向 ZLM 请求开启 RTP 端口 {port_val}...")
                    
                    import threading
                    def zlm_setup():
                        res = call_zlm_open_rtp_server(zlm_base_url, zlm_secret, port_val, stream_id)
                        if res.get('code') == 0:
                            log_signaler.log_signal.emit(f"[系统] ZLM RTP 接收端口已成功开辟: {port_val}，推流可以正常被 ZLM 解析 (流 ID: {stream_id})")
                        else:
                            log_signaler.log_signal.emit(f"[系统] 提示: 尝试通过 ZLM REST API 开启 RTP 服务失败: {res.get('msg')}。将直接启动推流进程。")
                    
                    threading.Thread(target=zlm_setup, daemon=True).start()
                
                config_data = self._build_config_data()
                config_obj = ConfigMock(config_data)
                
                if self.is_previewing:
                    self.append_log("[系统] 本地预览保持开启，将与推流同步运行。")
                
                self.manual_media_ctrl = MediaController(config_obj)
                self.manual_media_ctrl.media_ctrl_print = log_signaler.log_signal.emit
                self.manual_media_ctrl.preview_url_callback = self.on_stream_preview_changed
                
                if self.manual_media_ctrl.start_stream(target_ip, port_val, protocol, ssrc):
                    self.is_manual_pushing = True
                    self.set_stream_status(
                        "MANUAL",
                        f"手动测试推流中：{target_ip}:{port_val} / {protocol} / SSRC {ssrc}"
                    )
                else:
                    self.manual_media_ctrl = None
                    self.set_stream_status("ERROR", "手动测试推流 FFmpeg 启动失败")
                    return
                
                self.btn_test_stream.setText("停止推流")
                self.btn_test_stream.setStyleSheet("""
                    QPushButton { background-color: #DC2626; color: white; font-weight: bold; border-radius: 6px; padding: 8px 16px; }
                    QPushButton:hover { background-color: #B91C1C; }
                """)
        else:
            self.stop_manual_push()

    def stop_manual_push(self):
        # 尝试通过 ZLM API 关闭开辟 of 端口
        if hasattr(self, 'current_manual_zlm_secret') and self.current_manual_zlm_secret and self.current_manual_stream_id:
            zlm_base_url = self.current_manual_zlm_url
            zlm_secret = self.current_manual_zlm_secret
            stream_id = self.current_manual_stream_id
            
            import threading
            def zlm_teardown():
                res = call_zlm_close_rtp_server(zlm_base_url, zlm_secret, stream_id)
                if res.get('code') == 0:
                    log_signaler.log_signal.emit(f"[系统] ZLM 已成功释放并关闭 RTP 端口 (流 ID: {stream_id})")
            
            threading.Thread(target=zlm_teardown, daemon=True).start()
            self.current_manual_zlm_secret = None
            self.current_manual_stream_id = None
            
        if self.manual_media_ctrl:
            self.manual_media_ctrl.stop_stream()
            self.manual_media_ctrl = None
        MediaController.kill_bundled_ffmpeg_processes(log_signaler.log_signal.emit)
        self.is_manual_pushing = False
        self.set_stream_status("IDLE", "手动测试推流已停止，当前未推流")
        self._restore_camera_preview_after_stream()
        self.btn_test_stream.setText("测试推流")
        self.btn_test_stream.setStyleSheet("""
            QPushButton { background-color: #D97706; color: white; font-weight: bold; border-radius: 6px; padding: 8px 16px; }
            QPushButton:hover { background-color: #B45309; }
        """)
        self.append_log("[系统] 手动测试推流已停止。")

    # ──────────────────────── 多设备集群控制模块 ────────────────────────

    def create_multi_device_tab(self):
        tab_widget = QWidget()
        tab_layout = QVBoxLayout(tab_widget)
        tab_layout.setContentsMargins(8, 8, 8, 8)
        tab_layout.setSpacing(10)

        # ── 1. 顶部配置与批量生成卡片 ──
        batch_frame = QFrame()
        batch_frame.setObjectName("basic_frame")
        batch_layout = QVBoxLayout(batch_frame)
        batch_layout.setContentsMargins(12, 12, 12, 12)
        batch_layout.setSpacing(10)

        # 第一行：平台公共连接配置
        platform_row = QHBoxLayout()
        platform_row.setSpacing(8)

        platform_row.addWidget(QLabel("平台 SIP IP:"))
        server_ip_default = self.input_server_ip.currentText().strip() if hasattr(self, 'input_server_ip') else "192.168.1.38"
        self.multi_input_server_ip = QLineEdit(server_ip_default or "192.168.1.38")
        self.multi_input_server_ip.setFixedWidth(130)
        platform_row.addWidget(self.multi_input_server_ip)

        platform_row.addWidget(QLabel("端口:"))
        server_port_default = self.input_server_port.text().strip() if hasattr(self, 'input_server_port') else "5060"
        self.multi_input_server_port = QLineEdit(server_port_default or "5060")
        self.multi_input_server_port.setFixedWidth(60)
        platform_row.addWidget(self.multi_input_server_port)

        platform_row.addWidget(QLabel("平台国标 ID:"))
        server_id_default = self.input_server_id.text().strip() if hasattr(self, 'input_server_id') else "34020000002000000001"
        self.multi_input_server_id = QLineEdit(server_id_default or "34020000002000000001")
        self.multi_input_server_id.setFixedWidth(170)
        platform_row.addWidget(self.multi_input_server_id)

        platform_row.addWidget(QLabel("密码:"))
        pwd_default = self.input_password.text().strip() if hasattr(self, 'input_password') else "12345"
        self.multi_input_password = QLineEdit(pwd_default or "12345")
        self.multi_input_password.setFixedWidth(90)
        platform_row.addWidget(self.multi_input_password)

        platform_row.addStretch()
        batch_layout.addLayout(platform_row)

        # 第二行：批量设备参数生成
        gen_row = QHBoxLayout()
        gen_row.setSpacing(8)

        gen_row.addWidget(QLabel("起始端口:"))
        self.multi_input_start_port = QSpinBox()
        self.multi_input_start_port.setRange(1024, 65535)
        self.multi_input_start_port.setValue(50601)
        self.multi_input_start_port.setFixedWidth(85)
        gen_row.addWidget(self.multi_input_start_port)

        gen_row.addWidget(QLabel("起始设备 ID:"))
        self.multi_input_start_device_id = QLineEdit("34020000001110000001")
        self.multi_input_start_device_id.setFixedWidth(170)
        gen_row.addWidget(self.multi_input_start_device_id)

        gen_row.addWidget(QLabel("起始通道 ID:"))
        self.multi_input_start_channel_id = QLineEdit("34020000001320000001")
        self.multi_input_start_channel_id.setFixedWidth(170)
        gen_row.addWidget(self.multi_input_start_channel_id)

        gen_row.addWidget(QLabel("生成数量:"))
        self.multi_input_count = QSpinBox()
        self.multi_input_count.setRange(1, 100)
        self.multi_input_count.setValue(5)
        self.multi_input_count.setFixedWidth(65)
        gen_row.addWidget(self.multi_input_count)

        gen_row.addWidget(QLabel("推流源:"))
        self.multi_combo_source = QComboBox()
        self.multi_combo_source.addItems([
            "【虚拟源】测试彩条信号 (推荐)",
            "【自定义源】RTSP/MP4文件",
            "【摄像头】本机默认摄像头"
        ])
        gen_row.addWidget(self.multi_combo_source)

        gen_row.addStretch()
        batch_layout.addLayout(gen_row)

        # 第三行：操作按钮
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self.btn_multi_batch_generate = QPushButton("➕ 批量生成并添加到列表")
        self.btn_multi_batch_generate.setStyleSheet("""
            QPushButton { background-color: #4F46E5; color: white; font-weight: bold; border-radius: 6px; padding: 7px 16px; font-size: 13px; }
            QPushButton:hover { background-color: #6366F1; }
        """)
        self.btn_multi_batch_generate.clicked.connect(self.on_multi_batch_generate)
        btn_row.addWidget(self.btn_multi_batch_generate)

        self.btn_multi_add_single = QPushButton("➕ 添加单台")
        self.btn_multi_add_single.setStyleSheet("""
            QPushButton { background-color: #27272A; border: 1px solid #3F3F46; color: white; border-radius: 6px; padding: 7px 14px; font-size: 13px; }
            QPushButton:hover { background-color: #3F3F46; }
        """)
        self.btn_multi_add_single.clicked.connect(self.on_multi_add_single)
        btn_row.addWidget(self.btn_multi_add_single)

        self.btn_multi_clear_all = QPushButton("🧹 清空列表")
        self.btn_multi_clear_all.setStyleSheet("""
            QPushButton { background-color: #27272A; border: 1px solid #3F3F46; color: #A1A1AA; border-radius: 6px; padding: 7px 14px; font-size: 13px; }
            QPushButton:hover { background-color: #3F3F46; color: #FAFAFA; }
        """)
        self.btn_multi_clear_all.clicked.connect(self.on_multi_clear_all)
        btn_row.addWidget(self.btn_multi_clear_all)

        btn_row.addStretch()

        # 集群全局启动/停止按钮
        self.btn_multi_start_all = QPushButton("🚀 一键启动全部设备")
        self.btn_multi_start_all.setStyleSheet("""
            QPushButton { background-color: #059669; color: white; font-weight: bold; border-radius: 6px; padding: 7px 18px; font-size: 13px; }
            QPushButton:hover { background-color: #10B981; }
        """)
        self.btn_multi_start_all.clicked.connect(self.on_multi_start_all)
        btn_row.addWidget(self.btn_multi_start_all)

        self.btn_multi_stop_all = QPushButton("🛑 一键停止全部设备")
        self.btn_multi_stop_all.setStyleSheet("""
            QPushButton { background-color: #DC2626; color: white; font-weight: bold; border-radius: 6px; padding: 7px 18px; font-size: 13px; }
            QPushButton:hover { background-color: #EF4444; }
        """)
        self.btn_multi_stop_all.clicked.connect(self.on_multi_stop_all)
        btn_row.addWidget(self.btn_multi_stop_all)

        batch_layout.addLayout(btn_row)
        tab_layout.addWidget(batch_frame)

        # ── 2. 集群状态指标与统计栏 ──
        kpi_frame = QFrame()
        kpi_frame.setStyleSheet("""
            QFrame {
                background-color: #18181B;
                border: 1px solid #27272A;
                border-radius: 6px;
                padding: 4px 12px;
            }
            QLabel { font-size: 13px; font-weight: bold; }
        """)
        kpi_layout = QHBoxLayout(kpi_frame)
        kpi_layout.setContentsMargins(8, 6, 8, 6)
        
        self.lbl_multi_kpi_total = QLabel("总设备: 0 台")
        self.lbl_multi_kpi_total.setStyleSheet("color: #E4E4E7;")
        
        self.lbl_multi_kpi_online = QLabel("● 在线注册: 0 台")
        self.lbl_multi_kpi_online.setStyleSheet("color: #10B981;")

        self.lbl_multi_kpi_streaming = QLabel("● 正在推流: 0 台")
        self.lbl_multi_kpi_streaming.setStyleSheet("color: #38BDF8;")

        self.lbl_multi_kpi_idle = QLabel("● 离线/空闲: 0 台")
        self.lbl_multi_kpi_idle.setStyleSheet("color: #71717A;")

        kpi_layout.addWidget(QLabel("📊 集群概览:"))
        kpi_layout.addSpacing(10)
        kpi_layout.addWidget(self.lbl_multi_kpi_total)
        kpi_layout.addSpacing(14)
        kpi_layout.addWidget(self.lbl_multi_kpi_online)
        kpi_layout.addSpacing(14)
        kpi_layout.addWidget(self.lbl_multi_kpi_streaming)
        kpi_layout.addSpacing(14)
        kpi_layout.addWidget(self.lbl_multi_kpi_idle)
        kpi_layout.addStretch()

        tab_layout.addWidget(kpi_frame)

        # ── 3. 多设备矩阵表格 ──
        self.multi_table = QTableWidget()
        self.multi_table.setColumnCount(9)
        self.multi_table.setHorizontalHeaderLabels([
            "#", "设备名称", "本地端口", "设备国标ID", "通道国标ID", "推流视频源", "注册状态", "推流状态", "操作"
        ])
        self.multi_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.multi_table.horizontalHeader().setStretchLastSection(True)
        self.multi_table.verticalHeader().setVisible(False)
        self.multi_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.multi_table.setAlternatingRowColors(True)
        self.multi_table.setStyleSheet("""
            QTableWidget {
                background-color: #18181B;
                alternate-background-color: #111113;
                color: #FAFAFA;
                border: 1px solid #27272A;
                border-radius: 6px;
                gridline-color: #27272A;
            }
            QTableWidget::item {
                padding: 4px;
            }
        """)
        self.multi_table.setColumnWidth(0, 35)
        self.multi_table.setColumnWidth(1, 115)
        self.multi_table.setColumnWidth(2, 75)
        self.multi_table.setColumnWidth(3, 175)
        self.multi_table.setColumnWidth(4, 175)
        self.multi_table.setColumnWidth(5, 140)
        self.multi_table.setColumnWidth(6, 95)
        self.multi_table.setColumnWidth(7, 95)
        self.multi_table.setColumnWidth(8, 130)

        tab_layout.addWidget(self.multi_table, 1)

        return tab_widget

    def _increment_gb_code(self, code_str, offset):
        """递增国标编码最后几位数字"""
        if not code_str:
            return code_str
        match = re.match(r'^(.*?)(\d+)$', code_str)
        if match:
            prefix, num_part = match.group(1), match.group(2)
            new_num = int(num_part) + offset
            return f"{prefix}{new_num:0{len(num_part)}d}"
        return f"{code_str}_{offset}"

    def on_multi_batch_generate(self):
        start_port = self.multi_input_start_port.value()
        start_dev_id = self.multi_input_start_device_id.text().strip() or "34020000001110000001"
        start_ch_id = self.multi_input_start_channel_id.text().strip() or "34020000001320000001"
        count = self.multi_input_count.value()
        source_text = self.multi_combo_source.currentText()
        
        custom_url = self.input_custom_url.text().strip() if hasattr(self, 'input_custom_url') else "rtsp://127.0.0.1:8554/live"

        existing_ports = {d["port"] for d in self.multi_devices}

        for i in range(count):
            port = start_port + i
            while port in existing_ports:
                port += 1
            existing_ports.add(port)

            self.multi_device_counter += 1
            idx_name = f"模拟设备-{self.multi_device_counter:02d}"
            dev_id = self._increment_gb_code(start_dev_id, i)
            ch_id = self._increment_gb_code(start_ch_id, i)

            dev_dict = {
                "uid": f"dev_{self.multi_device_counter}_{int(time.time()*1000)}",
                "name": idx_name,
                "port": port,
                "device_id": dev_id,
                "channel_id": ch_id,
                "source": source_text,
                "custom_url": custom_url,
                "thread": None,
                "reg_state": "IDLE",
                "media_state": "IDLE"
            }
            self.multi_devices.append(dev_dict)

        self.refresh_multi_device_table()
        self.update_multi_kpi_summary()
        self.append_log(f"[集群] 成功批量生成 {count} 台模拟设备，当前集群共 {len(self.multi_devices)} 台。")

    def on_multi_add_single(self):
        start_port = self.multi_input_start_port.value()
        existing_ports = {d["port"] for d in self.multi_devices}
        port = start_port + len(self.multi_devices)
        while port in existing_ports:
            port += 1

        self.multi_device_counter += 1
        idx = len(self.multi_devices)
        start_dev_id = self.multi_input_start_device_id.text().strip() or "34020000001110000001"
        start_ch_id = self.multi_input_start_channel_id.text().strip() or "34020000001320000001"

        dev_dict = {
            "uid": f"dev_{self.multi_device_counter}_{int(time.time()*1000)}",
            "name": f"模拟设备-{self.multi_device_counter:02d}",
            "port": port,
            "device_id": self._increment_gb_code(start_dev_id, idx),
            "channel_id": self._increment_gb_code(start_ch_id, idx),
            "source": self.multi_combo_source.currentText(),
            "custom_url": self.input_custom_url.text().strip() if hasattr(self, 'input_custom_url') else "rtsp://127.0.0.1:8554/live",
            "thread": None,
            "reg_state": "IDLE",
            "media_state": "IDLE"
        }
        self.multi_devices.append(dev_dict)
        self.refresh_multi_device_table()
        self.update_multi_kpi_summary()
        self.append_log(f"[集群] 已添加设备 [{dev_dict['name']}] (Port: {port}, ID: {dev_dict['device_id']})")

    def on_multi_clear_all(self):
        self.on_multi_stop_all()
        self.multi_devices.clear()
        self.refresh_multi_device_table()
        self.update_multi_kpi_summary()
        self.append_log("[集群] 设备列表已清空。")

    def refresh_multi_device_table(self):
        self.multi_table.setRowCount(len(self.multi_devices))
        for row_idx, dev in enumerate(self.multi_devices):
            self._render_multi_device_row(row_idx, dev)

    def _render_multi_device_row(self, row_idx, dev):
        # 0. 序号
        item_no = QTableWidgetItem(str(row_idx + 1))
        item_no.setTextAlignment(Qt.AlignCenter)
        self.multi_table.setItem(row_idx, 0, item_no)

        # 1. 设备名称
        item_name = QTableWidgetItem(dev["name"])
        self.multi_table.setItem(row_idx, 1, item_name)

        # 2. 本地端口
        item_port = QTableWidgetItem(str(dev["port"]))
        item_port.setTextAlignment(Qt.AlignCenter)
        self.multi_table.setItem(row_idx, 2, item_port)

        # 3. 设备国标ID
        item_did = QTableWidgetItem(dev["device_id"])
        self.multi_table.setItem(row_idx, 3, item_did)

        # 4. 通道国标ID
        item_cid = QTableWidgetItem(dev["channel_id"])
        self.multi_table.setItem(row_idx, 4, item_cid)

        # 5. 推流源
        src_label = dev["source"]
        if "彩条" in src_label:
            src_label = "🌈 彩条测试图"
        elif "摄像头" in src_label:
            src_label = "📷 本地摄像头"
        else:
            src_label = "🎬 自定义流/文件"
        item_src = QTableWidgetItem(src_label)
        self.multi_table.setItem(row_idx, 5, item_src)

        # 6. 注册状态
        lbl_reg = QLabel()
        lbl_reg.setAlignment(Qt.AlignCenter)
        reg_st = dev["reg_state"]
        if reg_st == "REGISTERED":
            lbl_reg.setText("● 已注册")
            lbl_reg.setStyleSheet("color: #10B981; font-weight: bold; font-size: 12px;")
        elif reg_st in ("REGISTERING", "AUTHENTICATING"):
            lbl_reg.setText("● 正在注册...")
            lbl_reg.setStyleSheet("color: #F59E0B; font-weight: bold; font-size: 12px;")
        elif reg_st == "FAILED":
            lbl_reg.setText("● 注册失败")
            lbl_reg.setStyleSheet("color: #EF4444; font-weight: bold; font-size: 12px;")
        else:
            lbl_reg.setText("● 未连接")
            lbl_reg.setStyleSheet("color: #71717A; font-size: 12px;")
        self.multi_table.setCellWidget(row_idx, 6, lbl_reg)

        # 7. 推流状态
        lbl_media = QLabel()
        lbl_media.setAlignment(Qt.AlignCenter)
        media_st = dev["media_state"]
        if media_st in ("STREAMING", "MANUAL"):
            lbl_media.setText("● 正在推流")
            lbl_media.setStyleSheet("color: #38BDF8; font-weight: bold; font-size: 12px;")
        elif media_st == "STARTING":
            lbl_media.setText("● 正在建流...")
            lbl_media.setStyleSheet("color: #F59E0B; font-weight: bold; font-size: 12px;")
        elif media_st == "ERROR":
            lbl_media.setText("● 推流异常")
            lbl_media.setStyleSheet("color: #EF4444; font-weight: bold; font-size: 12px;")
        else:
            lbl_media.setText("● 空闲")
            lbl_media.setStyleSheet("color: #71717A; font-size: 12px;")
        self.multi_table.setCellWidget(row_idx, 7, lbl_media)

        # 8. 操作按钮列
        op_widget = QWidget()
        op_layout = QHBoxLayout(op_widget)
        op_layout.setContentsMargins(4, 2, 4, 2)
        op_layout.setSpacing(6)

        is_running = dev["thread"] is not None

        btn_toggle = QPushButton("停止" if is_running else "启动")
        if is_running:
            btn_toggle.setStyleSheet("QPushButton { background-color: #DC2626; color: white; border-radius: 4px; padding: 3px 8px; font-size: 11px; } QPushButton:hover { background-color: #EF4444; }")
        else:
            btn_toggle.setStyleSheet("QPushButton { background-color: #059669; color: white; border-radius: 4px; padding: 3px 8px; font-size: 11px; } QPushButton:hover { background-color: #10B981; }")
        
        uid = dev["uid"]
        btn_toggle.clicked.connect(lambda _, u=uid: self.toggle_single_multi_device(u))
        op_layout.addWidget(btn_toggle)

        btn_delete = QPushButton("删除")
        btn_delete.setEnabled(not is_running)
        btn_delete.setStyleSheet("QPushButton { background-color: #27272A; border: 1px solid #3F3F46; color: #A1A1AA; border-radius: 4px; padding: 3px 8px; font-size: 11px; } QPushButton:hover { background-color: #3F3F46; color: #FAFAFA; }")
        btn_delete.clicked.connect(lambda _, u=uid: self.remove_single_multi_device(u))
        op_layout.addWidget(btn_delete)

        self.multi_table.setCellWidget(row_idx, 8, op_widget)

    def _build_multi_device_config(self, dev):
        local_ips = get_all_local_ips()
        server_ip = self.multi_input_server_ip.text().strip() or "127.0.0.1"
        local_ip = best_local_ip_for_server(server_ip, local_ips)

        return {
            "SIP_SERVER_IP": server_ip,
            "SIP_SERVER_PORT": int(self.multi_input_server_port.text().strip() or "5060"),
            "SIP_SERVER_ID": self.multi_input_server_id.text().strip() or "34020000002000000001",
            "LOCAL_IP": local_ip,
            "LOCAL_PORT": int(dev["port"]),
            "DEVICE_ID": dev["device_id"],
            "CHANNEL_ID": dev["channel_id"],
            "PASSWORD": self.multi_input_password.text().strip() or "12345",
            "camera_source_text": dev["source"],
            "custom_url": dev.get("custom_url", ""),
            "video_resolution": "1920x1080",
            "video_fps": "25",
            "video_bitrate": "2048",
            "enable_audio": False,
            "audio_source_text": "默认音频输入",
            "device_name": dev["name"],
            "manufacturer": "AntigravitySim",
            "model": "GB28181-SimCluster",
            "civil_code": "310115",
            "address": "Shanghai-Lab",
            "owner": "COMAC",
            "HEARTBEAT_INTERVAL": 15,
            "EXPIRE_TIME": 3600,
            "channels": [{
                "channel_id": dev["channel_id"],
                "name": dev["name"],
                "camera_source_text": dev["source"],
                "custom_url": dev.get("custom_url", "")
            }]
        }

    def start_single_multi_device(self, uid):
        dev = next((d for d in self.multi_devices if d["uid"] == uid), None)
        if not dev or dev["thread"] is not None:
            return

        cfg_dict = self._build_multi_device_config(dev)
        dev["reg_state"] = "REGISTERING"
        dev["media_state"] = "IDLE"

        thread = DeviceThread(cfg_dict, log_prefix=dev["name"])
        thread.registration_state_changed.connect(lambda st, msg, u=uid: self.on_multi_reg_state_changed(u, st, msg))
        thread.media_state_changed.connect(lambda st, msg, u=uid: self.on_multi_media_state_changed(u, st, msg))
        thread.finished.connect(lambda u=uid: self.on_multi_thread_finished(u))
        
        dev["thread"] = thread
        thread.start()
        
        self.refresh_multi_device_table()
        self.update_multi_kpi_summary()

    def stop_single_multi_device(self, uid):
        dev = next((d for d in self.multi_devices if d["uid"] == uid), None)
        if not dev or dev["thread"] is None:
            return

        thread = dev["thread"]
        dev["thread"] = None
        dev["reg_state"] = "IDLE"
        dev["media_state"] = "IDLE"
        
        try:
            thread.stop()
        except Exception:
            pass

        self.refresh_multi_device_table()
        self.update_multi_kpi_summary()
        self.append_log(f"[集群] 设备 [{dev['name']}] 已停止。")

    def toggle_single_multi_device(self, uid):
        dev = next((d for d in self.multi_devices if d["uid"] == uid), None)
        if not dev:
            return
        if dev["thread"] is not None:
            self.stop_single_multi_device(uid)
        else:
            self.start_single_multi_device(uid)

    def remove_single_multi_device(self, uid):
        self.stop_single_multi_device(uid)
        self.multi_devices = [d for d in self.multi_devices if d["uid"] != uid]
        self.refresh_multi_device_table()
        self.update_multi_kpi_summary()

    def on_multi_start_all(self):
        self.append_log(f"[集群] 正在一键启动集群全部 {len(self.multi_devices)} 台设备...")
        for dev in self.multi_devices:
            if dev["thread"] is None:
                self.start_single_multi_device(dev["uid"])
                time.sleep(0.08)  # 平滑错开注册，防止并发洪峰

    def on_multi_stop_all(self):
        self.append_log("[集群] 正在停止集群中所有设备...")
        for dev in self.multi_devices:
            if dev["thread"] is not None:
                self.stop_single_multi_device(dev["uid"])
        MediaController.kill_bundled_ffmpeg_processes(log_signaler.log_signal.emit)

    def on_multi_reg_state_changed(self, uid, state, msg):
        dev = next((d for d in self.multi_devices if d["uid"] == uid), None)
        if dev:
            dev["reg_state"] = state
            self.refresh_multi_device_table()
            self.update_multi_kpi_summary()

    def on_multi_media_state_changed(self, uid, state, msg):
        dev = next((d for d in self.multi_devices if d["uid"] == uid), None)
        if dev:
            dev["media_state"] = state
            self.refresh_multi_device_table()
            self.update_multi_kpi_summary()

    def on_multi_thread_finished(self, uid):
        dev = next((d for d in self.multi_devices if d["uid"] == uid), None)
        if dev and dev["thread"] is not None:
            dev["thread"] = None
            dev["reg_state"] = "IDLE"
            dev["media_state"] = "IDLE"
            self.refresh_multi_device_table()
            self.update_multi_kpi_summary()

    def update_multi_kpi_summary(self):
        total = len(self.multi_devices)
        online = sum(1 for d in self.multi_devices if d["reg_state"] == "REGISTERED")
        streaming = sum(1 for d in self.multi_devices if d["media_state"] in ("STREAMING", "MANUAL"))
        idle = total - online

        self.lbl_multi_kpi_total.setText(f"总设备: {total} 台")
        self.lbl_multi_kpi_online.setText(f"● 在线注册: {online} 台")
        self.lbl_multi_kpi_streaming.setText(f"● 正在推流: {streaming} 台")
        self.lbl_multi_kpi_idle.setText(f"● 离线/空闲: {max(0, idle)} 台")

    def closeEvent(self, event):
        self.stop_device()
        self.stop_preview()
        self.stop_manual_push()
        self.on_multi_stop_all()
        MediaController.kill_bundled_ffmpeg_processes(log_signaler.log_signal.emit)
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    gui = EasyGBDMacGUI()
    gui.show()
    sys.exit(app.exec())
