import socket
import threading
import time
import re
import random

from core import sip_stack
from core.media_control import MediaController

def get_local_ip_for_target(target_ip):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect((target_ip, 80))
        local_ip = s.getsockname()[0]
        s.close()
        return local_ip
    except Exception:
        return None

class GB28181Device:
    REGISTRATION_TIMEOUT_SECONDS = 10

    def __init__(self, config_obj, log_callback=None, state_callback=None, ptz_callback=None,
                 media_state_callback=None, media_preview_callback=None):
        self.config = config_obj
        self.log_callback = log_callback
        self.state_callback = state_callback
        self.ptz_callback = ptz_callback
        self.media_state_callback = media_state_callback
        
        self.sock = None
        self.server_addr = (self.config.SIP_SERVER_IP, int(self.config.SIP_SERVER_PORT))
        
        self.cseq = 1
        self.is_registered = False
        self.running = False
        self.media_ctrl = MediaController(self.config)
        self.media_ctrl.media_ctrl_print = self.log
        self.media_ctrl.preview_url_callback = media_preview_callback
        self.call_id = self.config.generate_call_id()
        self._active_invite_callid = None  # 防止 INVITE 重传轰炸
        self.registration_state = "IDLE"
        self.registration_error = ""
        self._registration_deadline = None
        self._auth_attempted = False
        self._auth_realm = None
        self._auth_nonce = None
        self._stopping = False
        
        # 异常重试与断线重连控制
        self.max_registration_retries = int(getattr(self.config, 'MAX_REGISTRATION_RETRIES', 5))  # 默认最多重试5次，0为无限重试
        self.retry_count = 0
        self._retry_timer_deadline = None
        
        # 心跳丢失检测与租期刷新注册
        self._missed_heartbeats = 0
        self.MAX_MISSED_HEARTBEATS = 3
        self._last_register_time = 0.0

        self.ptz_pan = float(getattr(self.config, 'ptz_pan', 0.0))
        self.ptz_tilt = float(getattr(self.config, 'ptz_tilt', 0.0))
        self.ptz_zoom = float(getattr(self.config, 'ptz_zoom', 1.0))
        self.ptz_step = 0.18
        self.ptz_zoom_step = 0.25
        self.media_state = "IDLE"

    def log(self, msg):
        if self.log_callback:
            self.log_callback(str(msg))
        else:
            print(msg)

    def _set_registration_state(self, state, message):
        self.registration_state = state
        self.registration_error = message if state in ("FAILED", "RETRYING") else ""
        if self.state_callback:
            self.state_callback(state, message)

    def _set_media_state(self, state, message):
        self.media_state = state
        if self.media_state_callback:
            self.media_state_callback(state, message)

    @staticmethod
    def _xml_value(body, tag, default=""):
        match = re.search(rf'<{tag}>\s*([^<]*)\s*</{tag}>', body, re.IGNORECASE)
        return match.group(1).strip() if match else default

    @staticmethod
    def _invite_target_id(first_line):
        match = re.match(r'INVITE\s+sip:([^@;\s>]+)', first_line, re.IGNORECASE)
        return match.group(1).strip() if match else ""

    def mark_media_interrupted(self, reason):
        if self.media_state not in ("STARTING", "STREAMING"):
            return
        self.log(f"[Media Error] 推流中断: {reason}")
        self.media_ctrl.stop_stream()
        MediaController.kill_bundled_ffmpeg_processes(self.log)
        self._active_invite_callid = None
        self._set_media_state("ERROR", reason)

    def _registration_fatal_error(self, message):
        """不可恢复的致命注册错误（凭据错误/平台明确拒绝/端口冲突），终止运行"""
        self.is_registered = False
        self._registration_deadline = None
        self._retry_timer_deadline = None
        self.retry_count = 0
        self.log(f"[注册失败] 致命错误终止: {message}")
        self._set_registration_state("FAILED", message)
        self.running = False

    def _schedule_registration_retry(self, reason):
        """可恢复网络或平台异常（超时、500/503、心跳断开），触发退避重试"""
        if not self.running or self._stopping:
            return
        self.is_registered = False
        self._registration_deadline = None

        if self.max_registration_retries > 0 and self.retry_count >= self.max_registration_retries:
            self._registration_fatal_error(f"{reason}（已达最大重试上限 {self.max_registration_retries} 次）")
            return

        self.retry_count += 1
        # 指数退避：3s -> 5s -> 8s -> 12s -> 最大15s
        delay = min(15.0, 3.0 * (1.4 ** (self.retry_count - 1)))
        self._retry_timer_deadline = time.monotonic() + delay
        retry_msg = f"{reason}，将在 {int(delay)} 秒后自动重试（第 {self.retry_count} 次）"
        self.log(f"[注册异常] {retry_msg}")
        self._set_registration_state("RETRYING", retry_msg)

    def _bind_socket(self):
        """尝试绑定 UDP 套接字，失败则记录错误并返回 False"""
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            self.sock.bind(('0.0.0.0', int(self.config.LOCAL_PORT)))
            return True
        except Exception as e:
            self.log(f"[错误] 本地端口 {self.config.LOCAL_PORT} 绑定失败: {e}")
            if self.sock:
                try:
                    self.sock.close()
                except:
                    pass
                self.sock = None
            return False

    def send_msg(self, msg):
        if not self.sock:
            return False
        try:
            self.log(f"[RAW_SIP_OUT]\n{msg.strip()}")
            if 'encoding="GB2312"' in msg or "encoding='GB2312'" in msg:
                payload = msg.encode('gbk')
            else:
                payload = msg.encode('utf-8')
            self.sock.sendto(payload, self.server_addr)
            return True
        except Exception as e:
            self.log(f"[SIP Error] Failed to send message: {e}")
            return False

    def _send_device_info(self):
        """注册成功后主动推送 DeviceInfo 给平台，更新厂商/型号/设备名称"""
        self.log(f"[SIP] Pushing DeviceInfo: name={getattr(self.config,'DEVICE_NAME','')}, "
                 f"manufacturer={getattr(self.config,'MANUFACTURER','')}, "
                 f"model={getattr(self.config,'MODEL','')}")
        msg = sip_stack.build_device_info_msg(
            self.config, self.cseq, self.config.generate_call_id()
        )
        self.send_msg(msg)
        self.cseq += 1

    def register(self, is_retry=False):
        if is_retry:
            self.log(f"[SIP] Sending Retry REGISTER (第 {self.retry_count} 次)...")
            self.call_id = self.config.generate_call_id()
        else:
            self.log("[SIP] Sending Initial REGISTER...")
        self._auth_attempted = False
        self._registration_deadline = time.monotonic() + self.REGISTRATION_TIMEOUT_SECONDS
        self._retry_timer_deadline = None
        state_text = f"正在向上级平台发送注册请求{' (第' + str(self.retry_count) + '次重试)' if is_retry else ''}"
        self._set_registration_state("REGISTERING", state_text)
        msg = sip_stack.build_register_msg(self.config, self.cseq, self.call_id)
        if not self.send_msg(msg):
            self._schedule_registration_retry("注册请求发送失败，本地网络或UDP端口异常")
        self.cseq += 1

    def handle_auth(self, parsed_msg):
        self.log("[SIP] Handling 401 Unauthorized (Digest Auth)...")
        auth_header = sip_stack.get_header(parsed_msg, 'WWW-Authenticate')
        if not auth_header:
            self._registration_fatal_error("平台返回401，但未携带WWW-Authenticate认证参数")
            return
        
        realm_match = re.search(r'realm="([^"]+)"', auth_header)
        nonce_match = re.search(r'nonce="([^"]+)"', auth_header)
        
        if realm_match and nonce_match:
            realm = realm_match.group(1)
            nonce = nonce_match.group(1)
            self._auth_realm = realm
            self._auth_nonce = nonce
            
            uri = f"sip:{self.config.SIP_SERVER_DOMAIN}@{self.config.SIP_SERVER_IP}:{self.config.SIP_SERVER_PORT}"
            response = sip_stack.generate_auth_response(self.config.DEVICE_ID, self.config.PASSWORD, realm, nonce, uri)
            
            auth_response = f'Digest username="{self.config.DEVICE_ID}", realm="{realm}", nonce="{nonce}", uri="{uri}", response="{response}", algorithm=MD5'
            
            msg = sip_stack.build_register_msg(self.config, self.cseq, self.call_id, auth_header=auth_response)
            self._auth_attempted = True
            self._registration_deadline = time.monotonic() + self.REGISTRATION_TIMEOUT_SECONDS
            self._set_registration_state("AUTHENTICATING", f"平台要求Digest认证（Realm: {realm}）")
            if not self.send_msg(msg):
                self._schedule_registration_retry("认证注册请求发送失败，请检查网络连接")
            self.cseq += 1
        else:
            self._registration_fatal_error("平台返回的Digest认证参数不完整，缺少realm或nonce")

    def _handle_register_response(self, parsed_msg, status_code, reason):
        if status_code == 401:
            if self._auth_attempted:
                self._registration_fatal_error("认证失败（401）：请检查设备国标ID、注册密码和认证域")
            else:
                self.handle_auth(parsed_msg)
            return

        if 200 <= status_code < 300:
            self._registration_deadline = None
            self._retry_timer_deadline = None
            self.retry_count = 0
            self._missed_heartbeats = 0
            self._last_register_time = time.monotonic()
            if not self.is_registered:
                self.log("[SIP] Registration Successful!")
                self.is_registered = True
                self._set_registration_state("REGISTERED", "设备注册成功")
                self._send_device_info()
            else:
                self.log("[SIP] Refresh Registration Successful (租期已续签)")
            return

        fatal_messages = {
            400: "注册请求格式错误（400），请检查SIP报文参数",
            403: "平台拒绝注册（403）：设备可能未获准接入或已被拉黑",
            404: f"平台未找到设备（404）：请确认国标ID {self.config.DEVICE_ID} 已在平台配置或通过审核",
            423: "注册有效期过短（423），请按平台Min-Expires要求调整",
        }
        if status_code in fatal_messages:
            self._registration_fatal_error(fatal_messages[status_code])
            return

        retry_messages = {
            408: "平台注册处理超时（408）",
            500: "平台内部错误（500）",
            502: "平台网关错误（502）",
            503: "平台服务暂不可用（503）",
            504: "平台网关超时（504）",
        }
        detail = retry_messages.get(status_code, f"平台响应异常（SIP {status_code} {reason or 'Unknown'}）")
        self._schedule_registration_retry(detail)

    def _check_registration_timers(self):
        # 1. 注册应答超时检测
        if (not self.is_registered and self._registration_deadline is not None
                and time.monotonic() >= self._registration_deadline):
            self._schedule_registration_retry(
                f"注册超时：{self.REGISTRATION_TIMEOUT_SECONDS}秒内未收到平台响应"
            )

        # 2. 退避重试定时器触发
        if (not self.is_registered and self._retry_timer_deadline is not None
                and time.monotonic() >= self._retry_timer_deadline):
            self.register(is_retry=True)

    def _clamp_ptz_state(self, pan=None, tilt=None, zoom=None):
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

    def set_ptz_state(self, pan, tilt, zoom, source="平台"):
        self.ptz_pan, self.ptz_tilt, self.ptz_zoom = self._clamp_ptz_state(
            pan, tilt, zoom
        )
        self.config.ptz_pan = self.ptz_pan
        self.config.ptz_tilt = self.ptz_tilt
        self.config.ptz_zoom = self.ptz_zoom
        self.media_ctrl.set_ptz_state(self.ptz_pan, self.ptz_tilt, self.ptz_zoom)
        if self.ptz_callback:
            self.ptz_callback(self.ptz_pan, self.ptz_tilt, self.ptz_zoom, source)

    def apply_ptz_action(self, action, speed=1.0, source="平台"):
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
            self.log("[PTZ] 收到停止命令，保持当前模拟云台位置。")
            return
        else:
            return

        self.set_ptz_state(pan, tilt, zoom, source=source)
        self.log(
            f"[PTZ] {source}控制: {action} -> pan={self.ptz_pan:.2f}, "
            f"tilt={self.ptz_tilt:.2f}, zoom={self.ptz_zoom:.2f}x"
        )

    def _parse_ptz_cmd(self, body):
        match = re.search(r'<PTZCmd>\s*([^<\s]+)\s*</PTZCmd>', body, re.IGNORECASE)
        if not match:
            return None, 1.0

        hex_text = re.sub(r'[^0-9A-Fa-f]', '', match.group(1))
        if len(hex_text) < 8:
            return None, 1.0

        try:
            data = bytes.fromhex(hex_text)
        except ValueError:
            return None, 1.0

        # GB28181 PTZCmd 常见格式：A5 0F Addr Cmd HSpeed VSpeed ZSpeed Checksum
        # Cmd 位：0x01右 / 0x02左 / 0x04下 / 0x08上 / 0x10缩小 / 0x20放大
        cmd = data[3] if len(data) > 3 else 0
        h_speed = data[4] if len(data) > 4 else 0
        v_speed = data[5] if len(data) > 5 else 0
        z_speed = data[6] if len(data) > 6 else 0
        speed = max(h_speed, v_speed, z_speed, 1) / 128.0
        speed = max(0.4, min(2.5, speed))

        if cmd == 0:
            return "stop", speed
        if cmd & 0x20:
            return "zoom_in", speed
        if cmd & 0x10:
            return "zoom_out", speed
        if cmd & 0x02:
            return "left", speed
        if cmd & 0x01:
            return "right", speed
        if cmd & 0x08:
            return "up", speed
        if cmd & 0x04:
            return "down", speed
        return "stop", speed

    def handle_device_control(self, body):
        if "PTZCmd" not in body:
            self.log("[SIP] Received DeviceControl, but no PTZCmd found.")
            return
        raw_match = re.search(r'<PTZCmd>\s*([^<\s]+)\s*</PTZCmd>', body, re.IGNORECASE)
        if raw_match:
            self.log(f"[PTZ] 平台 PTZCmd 原始值: {raw_match.group(1).strip()}")
        action, speed = self._parse_ptz_cmd(body)
        if not action:
            self.log("[PTZ] 无法解析平台 PTZCmd，已忽略。")
            return
        self.log(f"[PTZ] 收到平台 PTZCmd，解析动作: {action}, speed={speed:.2f}")
        self.apply_ptz_action(action, speed=speed, source="平台")

    def send_keepalive(self):
        last_heartbeat_time = 0.0
        while self.running:
            now = time.monotonic()
            interval = getattr(self.config, 'HEARTBEAT_INTERVAL', 15)
            expire_time = getattr(self.config, 'EXPIRE_TIME', 3600)
            refresh_interval = max(60, int(expire_time * 0.7))

            if self.is_registered:
                # 1. 检测连续心跳丢失（连续3次未收到200 OK响应判定掉线）
                if self._missed_heartbeats >= self.MAX_MISSED_HEARTBEATS:
                    self.log(
                        f"[GB28181] 警告：连续 {self._missed_heartbeats} 次心跳未收到平台响应，判定与平台失联！准备重新注册..."
                    )
                    self._schedule_registration_retry("心跳超时，平台失联")
                    time.sleep(1)
                    continue

                # 2. 定时发送心跳
                if now - last_heartbeat_time >= interval:
                    self._missed_heartbeats += 1
                    self.log(
                        f"[SIP] Sending Keepalive (CSeq: {self.cseq}, 待应答心跳: {self._missed_heartbeats})"
                    )
                    msg = sip_stack.build_keepalive_msg(
                        self.config, self.cseq, self.config.generate_call_id()
                    )
                    self.send_msg(msg)
                    self.cseq += 1
                    last_heartbeat_time = now

                # 3. 租期即将到期前自动刷新注册 (Refresh REGISTER)
                if self._last_register_time > 0 and (now - self._last_register_time >= refresh_interval):
                    self.log(
                        f"[SIP] 注册租期过半 (Expires: {expire_time}s)，自动发送刷新注册维持在线..."
                    )
                    self._last_register_time = now
                    msg = sip_stack.build_register_msg(self.config, self.cseq, self.call_id)
                    self.send_msg(msg)
                    self.cseq += 1

            time.sleep(1)

    def handle_invite(self, parsed_msg):
        call_id = parsed_msg['headers'].get('Call-ID', '')

        # 防止 INVITE 重传轰炸：同一个 Call-ID 只处理一次
        if call_id == self._active_invite_callid:
            self.log("[SIP] Ignoring retransmitted INVITE (same Call-ID)")
            return
        self._active_invite_callid = call_id

        self.log("[SIP] Received INVITE, starting media...")
        self._set_media_state("STARTING", "平台已发起点播，正在启动推流")
        ip, port, protocol, ssrc = self.media_ctrl.parse_sdp(parsed_msg['body'])
        requested_channel_id = self._invite_target_id(parsed_msg.get('first_line', ''))
        channel = self.config.get_channel(requested_channel_id)
        self.config.apply_channel_config(channel)
        self.log(
            f"[GB28181] 平台点播通道: requested={requested_channel_id or '-'}, "
            f"using={self.config.CHANNEL_ID}, name={getattr(self.config, 'channel_name', '')}, "
            f"source={getattr(self.config, 'camera_source_text', '')}"
        )
        
        cseq_val = parsed_msg['headers'].get('CSeq', '')
        via = parsed_msg['headers'].get('Via', '')
        from_hdr = parsed_msg['headers'].get('From', '')
        to_hdr = parsed_msg['headers'].get('To', '')
        
        # 生成纯随机 tag（不含 @）
        import string, random
        to_tag = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))

        # 动态探测连接流媒体目标 IP 时，Mac 使用的本地网卡 IP 接口，保证 SDP 里的 c= 属性值正确，以便 ZLM 正常接收 UDP 包
        media_local_ip = self.config.LOCAL_IP
        if ip:
            detected_ip = get_local_ip_for_target(ip)
            if detected_ip:
                if detected_ip != media_local_ip:
                    self.log(f"[SIP] 提示: 模拟器自动将 SDP 媒体连接 IP 从 {media_local_ip} 替换为真实局域网 IP {detected_ip}，以适配目标流媒体服务器 {ip}")
                media_local_ip = detected_ip

        contact_uri = f"sip:{self.config.DEVICE_ID}@{self.config.LOCAL_IP}:{self.config.LOCAL_PORT}"
        
        sdp = f"""v=0
o={self.config.DEVICE_ID} 0 0 IN IP4 {media_local_ip}
s=Play
c=IN IP4 {media_local_ip}
t=0 0
m=video {self.config.LOCAL_PORT} RTP/AVP 96
a=sendonly
a=rtpmap:96 PS/90000
y={ssrc}
"""
        
        resp = f"SIP/2.0 200 OK\r\n"
        resp += f"Via: {via}\r\n"
        resp += f"From: {from_hdr}\r\n"
        if ";tag=" not in to_hdr:
            resp += f"To: {to_hdr};tag={to_tag}\r\n"
        else:
            resp += f"To: {to_hdr}\r\n"
        resp += f"Call-ID: {call_id}\r\n"
        resp += f"CSeq: {cseq_val}\r\n"
        resp += f"Contact: <{contact_uri}>\r\n"
        resp += "Content-Type: application/sdp\r\n"
        resp += f"Content-Length: {len(sdp)}\r\n\r\n"
        resp += sdp
        
        self.send_msg(resp)
        
        if ip and port:
            self.log(f"[Media] Instructed to stream to {ip}:{port} via {protocol}")
            # 先让 GUI 释放 QCamera，再由 FFmpeg 独占摄像头并输出同源预览。
            time.sleep(0.35)
            if self.media_ctrl.start_stream(ip, port, protocol, ssrc):
                self._set_media_state(
                    "STREAMING",
                    f"平台点播推流中：{ip}:{port} / {protocol} / SSRC {ssrc}"
                )
            else:
                self._active_invite_callid = None
                self._set_media_state("ERROR", "FFmpeg 启动失败，平台点播未形成有效推流")
        else:
            self._set_media_state("ERROR", "平台点播 SDP 中缺少媒体地址或端口")

    def handle_bye(self, parsed_msg):
        self.log("[SIP] Received BYE, stopping media...")
        self.media_ctrl.stop_stream()
        MediaController.kill_bundled_ffmpeg_processes(self.log)
        self._set_media_state("IDLE", "平台已停止点播，当前未推流")
        self._active_invite_callid = None  # 允许后续新的 INVITE
        
        via = parsed_msg['headers'].get('Via', '')
        from_hdr = parsed_msg['headers'].get('From', '')
        to_hdr = parsed_msg['headers'].get('To', '')
        call_id = parsed_msg['headers'].get('Call-ID', '')
        cseq_val = parsed_msg['headers'].get('CSeq', '')
        
        resp = f"SIP/2.0 200 OK\r\n"
        resp += f"Via: {via}\r\n"
        resp += f"From: {from_hdr}\r\n"
        resp += f"To: {to_hdr}\r\n"
        resp += f"Call-ID: {call_id}\r\n"
        resp += f"CSeq: {cseq_val}\r\n"
        resp += "Content-Length: 0\r\n\r\n"
        
        self.send_msg(resp)

    def receive_loop(self):
        self.sock.settimeout(1.0)
        while self.running:
            try:
                data, addr = self.sock.recvfrom(65535)
                try:
                    msg_text = data.decode('utf-8')
                except UnicodeDecodeError:
                    msg_text = data.decode('gbk', errors='ignore')
                if msg_text.strip():
                    self.log(f"[RAW_SIP_IN]\n{msg_text.strip()}")
                parsed = sip_stack.parse_sip_msg(msg_text)
                
                if not parsed:
                    continue
                    
                first_line = parsed['first_line']
                
                response_match = re.match(r'^SIP/2\.0\s+(\d{3})(?:\s+(.*))?$', first_line, re.IGNORECASE)
                if response_match:
                    status_code = int(response_match.group(1))
                    reason = (response_match.group(2) or '').strip()
                    cseq = sip_stack.get_header(parsed, 'CSeq')
                    if 'REGISTER' in cseq.upper():
                        if self._stopping:
                            self.log(f"[SIP] Deregistration response: {status_code} {reason}".rstrip())
                        else:
                            self._handle_register_response(parsed, status_code, reason)
                    elif 200 <= status_code < 300 and 'MESSAGE' in cseq.upper():
                        self.log(f"[GB28181] MESSAGE事务响应: SIP {status_code} {reason or 'OK'} / CSeq: {cseq}")
                        # 收到心跳响应，清零丢失计数
                        self._missed_heartbeats = 0
                elif first_line.startswith("INVITE"):
                    self.handle_invite(parsed)
                elif first_line.startswith("BYE"):
                    self.handle_bye(parsed)
                elif first_line.startswith("MESSAGE"):
                    body = parsed.get('body', '')
                    cmd_type = self._xml_value(body, "CmdType", "Unknown")
                    sn = self._xml_value(body, "SN", "-")
                    device_id = self._xml_value(body, "DeviceID", "-")
                    self.log(
                        f"[GB28181] 收到平台MESSAGE: CmdType={cmd_type}, SN={sn}, DeviceID={device_id}"
                    )
                    via = parsed['headers'].get('Via', '')
                    from_hdr = parsed['headers'].get('From', '')
                    to_hdr = parsed['headers'].get('To', '')
                    call_id = parsed['headers'].get('Call-ID', '')
                    cseq_val = parsed['headers'].get('CSeq', '')
                    
                    resp = f"SIP/2.0 200 OK\r\n"
                    resp += f"Via: {via}\r\n"
                    resp += f"From: {from_hdr}\r\n"
                    resp += f"To: {to_hdr}\r\n"
                    resp += f"Call-ID: {call_id}\r\n"
                    resp += f"CSeq: {cseq_val}\r\n"
                    resp += "Content-Length: 0\r\n\r\n"
                    self.send_msg(resp)
                    self.log(f"[GB28181] 已回复MESSAGE确认: 200 OK / CSeq: {cseq_val}")

                    if "Catalog" in body:
                        sn = sn if sn != "-" else "1"
                        self.log(
                            f"[GB28181] 同步通道/Catalog查询: SN={sn}，准备返回 {len(getattr(self.config, 'channels', []))} 个通道"
                        )
                        cat_msg = sip_stack.build_catalog_response_msg(
                            self.config, sn, self.cseq, self.config.generate_call_id()
                        )
                        self.send_msg(cat_msg)
                        self.log(
                            f"[GB28181] 已发送通道列表Catalog响应: {', '.join([c.get('channel_id', '') for c in getattr(self.config, 'channels', [])])}"
                        )
                        self.cseq += 1
                    elif "DeviceInfo" in body and "<Query>" in body:
                        sn = sn if sn != "-" else "1"
                        self.log(
                            f"[GB28181] 设备信息DeviceInfo查询: SN={sn}，准备返回设备信息"
                        )
                        dev_info_msg = sip_stack.build_device_info_msg(
                            self.config, self.cseq, self.config.generate_call_id(), sn=sn
                        )
                        self.send_msg(dev_info_msg)
                        self.log(
                            f"[GB28181] 已发送DeviceInfo响应: name={getattr(self.config, 'DEVICE_NAME', '')}, model={getattr(self.config, 'MODEL', '')}"
                        )
                        self.cseq += 1
                    elif "DeviceControl" in body:
                        self.log("[GB28181] 收到设备控制DeviceControl消息，进入控制处理。")
                        self.handle_device_control(body)
                    else:
                        self.log(f"[GB28181] 暂未实现的MESSAGE类型: CmdType={cmd_type}")
            except socket.timeout:
                self._check_registration_timers()
                continue
            except Exception as e:
                if self.running:
                    self.log(f"[SIP] Receive loop error: {e}")

    def start(self):
        """启动设备：绑定端口后开始注册和接收循环"""
        # 绑定 UDP 端口（在 start 里做，不在构造函数里，避免多次 start/stop 时的端口残留问题）
        if not self._bind_socket():
            message = f"无法绑定本地UDP端口 {self.config.LOCAL_PORT}，请检查端口是否被其他程序占用"
            self.log(f"[错误] {message}")
            self._registration_fatal_error(message)
            return
        
        self.running = True
        self._stopping = False
        self.log(f"[System] Starting Device {self.config.DEVICE_ID} on {self.config.LOCAL_IP}:{self.config.LOCAL_PORT}")
        threading.Thread(target=self.receive_loop, daemon=True).start()
        threading.Thread(target=self.send_keepalive, daemon=True).start()
        self.register()
        
        # 主循环：保持线程存活
        while self.running:
            time.sleep(0.1)

        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None

    def stop(self):
        self.log("[System] Stopping Device...")
        self._stopping = True
        if self.is_registered:
            self.log("[SIP] Sending deregistration (Expires: 0) to server...")
            auth_header = None
            if self._auth_realm and self._auth_nonce:
                uri = f"sip:{self.config.SIP_SERVER_DOMAIN}@{self.config.SIP_SERVER_IP}:{self.config.SIP_SERVER_PORT}"
                response = sip_stack.generate_auth_response(
                    self.config.DEVICE_ID, self.config.PASSWORD,
                    self._auth_realm, self._auth_nonce, uri
                )
                auth_header = (
                    f'Digest username="{self.config.DEVICE_ID}", realm="{self._auth_realm}", '
                    f'nonce="{self._auth_nonce}", uri="{uri}", response="{response}", algorithm=MD5'
                )
            msg = sip_stack.build_register_msg(
                self.config, self.cseq, self.call_id,
                auth_header=auth_header, expires=0
            )
            self.send_msg(msg)
            # Give a brief moment for unregistration message to leave socket
            time.sleep(0.3)
            
        self.running = False
        self.is_registered = False
        self._registration_deadline = None
        self._retry_timer_deadline = None
        self.retry_count = 0
        self._set_registration_state("STOPPED", "设备已停止")
        self.media_ctrl.stop_stream()
        MediaController.kill_bundled_ffmpeg_processes(self.log)
        self._set_media_state("IDLE", "设备已停止，当前未推流")
        if self.sock:
            try:
                self.sock.close()
            except:
                pass
            self.sock = None
        self.log("[System] Stopped.")
