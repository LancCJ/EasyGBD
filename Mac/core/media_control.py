# media_control.py
import re
import os
import subprocess
import threading
import time
import signal
import tempfile

class MediaController:
    def __init__(self, config):
        self.config = config
        self.current_process = None
        self.last_stream_args = None
        self.preview_url_callback = None
        self.current_preview_url = None
        self.current_process_pid = None
        # Talk 会话独立于视频点播：一个进程播放平台下行语音，另一个采集麦克风回传。
        self.talk_receive_process = None
        self.talk_send_process = None
        self.talk_sdp_path = None
        self.talk_session = None

    @staticmethod
    def kill_bundled_ffmpeg_processes(log_callback=None):
        """兜底清理本软件启动的 FFmpeg，避免设备注销/退出后残留推流。"""
        patterns = (
            "/OpenGBD.app/Contents/Frameworks/bin/ffmpeg_arm64",
            "/OpenGBD.app/Contents/Frameworks/bin/ffmpeg_x86",
            "/Mac/bin/ffmpeg_arm64",
            "/Mac/bin/ffmpeg_x86",
        )
        try:
            output = subprocess.check_output(["ps", "ax", "-o", "pid=,command="], text=True)
        except Exception as exc:
            if log_callback:
                log_callback(f"[Media Error] 检查残留 FFmpeg 失败: {exc}")
            return

        targets = []
        for line in output.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            pid_text, _, command = stripped.partition(" ")
            if not pid_text.isdigit():
                continue
            if any(pattern in command for pattern in patterns):
                targets.append(int(pid_text))

        for pid in targets:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                continue
            except Exception as exc:
                if log_callback:
                    log_callback(f"[Media Error] 停止残留 FFmpeg PID {pid} 失败: {exc}")

        if targets:
            time.sleep(0.5)
            for pid in targets:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    continue
                except Exception:
                    continue
                try:
                    os.kill(pid, signal.SIGKILL)
                except Exception:
                    pass
            if log_callback:
                log_callback(f"[Media] 已清理残留推流 FFmpeg 进程: {', '.join(map(str, targets))}")

    @staticmethod
    def _parse_resolution(resolution):
        match = re.match(r"^\s*(\d+)x(\d+)\s*$", str(resolution), re.IGNORECASE)
        if not match:
            return 640, 480
        return max(1, int(match.group(1))), max(1, int(match.group(2)))

    @staticmethod
    def _camera_capture_resolution(video_resolution):
        """Mac 摄像头输入尺寸要使用 avfoundation 支持的模式；输出尺寸交给滤镜统一缩放/裁剪。"""
        width, height = MediaController._parse_resolution(video_resolution)
        supported = {
            (640, 480),
            (1280, 720),
            (1920, 1080),
            (1760, 1328),
            (1328, 1760),
            (1552, 1552),
            (1080, 1920),
        }
        if (width, height) in supported:
            return video_resolution
        if width <= 640 and height <= 480:
            return "640x480"
        if width <= 1280 and height <= 720:
            return "1280x720"
        return "1920x1080"

    def _build_video_filters(self, video_resolution, video_mirror):
        width, height = self._parse_resolution(video_resolution)
        pan = max(-1.0, min(1.0, float(getattr(self.config, 'ptz_pan', 0.0))))
        tilt = max(-1.0, min(1.0, float(getattr(self.config, 'ptz_tilt', 0.0))))
        zoom = max(1.0, min(4.0, float(getattr(self.config, 'ptz_zoom', 1.0))))
        filters = [
            f"scale={width}:{height}:force_original_aspect_ratio=increase",
            f"crop={width}:{height}",
        ]

        if zoom > 1.01:
            crop_w = max(2, int(width / zoom))
            crop_h = max(2, int(height / zoom))
            crop_w -= crop_w % 2
            crop_h -= crop_h % 2
            max_x = max(0, width - crop_w)
            max_y = max(0, height - crop_h)
            x = int(max_x * (pan + 1.0) / 2.0)
            y = int(max_y * (tilt + 1.0) / 2.0)
            x -= x % 2
            y -= y % 2
            filters.append(f"crop={crop_w}:{crop_h}:{x}:{y}")
            filters.append(f"scale={width}:{height}")

        if video_mirror:
            filters.append("hflip")

        return ",".join(filters)

    def _append_video_filters(self, ffmpeg_cmd, video_resolution, video_mirror):
        vf = self._build_video_filters(video_resolution, video_mirror)
        if vf:
            ffmpeg_cmd.extend(["-vf", vf])

    @staticmethod
    def _normalize_video_codec(codec):
        return "H265" if "265" in str(codec).upper() or "HEVC" in str(codec).upper() else "H264"

    @staticmethod
    def _normalize_audio_codec(codec):
        value = str(codec).upper().replace("-", "").replace("_", "")
        if "AAC" in value:
            return "AAC"
        if "723" in value:
            return "G723"
        if "711U" in value or "MULAW" in value:
            return "G711U"
        return "G711A"

    @staticmethod
    def _talk_codec_profile(codec):
        """返回 RTP 对讲音频档案；当前开放互通性最佳的 G.711 A/μ-law。"""
        normalized = MediaController._normalize_audio_codec(codec)
        if normalized == "G711U":
            return {
                "codec": "G711U", "payload_type": "0", "rtpmap": "PCMU/8000",
                "encoder_args": ["-c:a", "pcm_mulaw", "-ar", "8000", "-ac", "1"],
            }
        return {
            "codec": "G711A", "payload_type": "8", "rtpmap": "PCMA/8000",
            "encoder_args": ["-c:a", "pcm_alaw", "-ar", "8000", "-ac", "1"],
        }

    @staticmethod
    def _terminate_process(process):
        if not process or process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=2)
        except Exception:
            try:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=1)
            except Exception:
                pass

    @staticmethod
    def _read_process_errors(process, label, callback):
        if not process or not process.stderr or not callback:
            return
        for line in process.stderr:
            lowered = line.lower()
            if any(token in lowered for token in ("error", "fail", "could not", "invalid", "permission")):
                callback(f"[Media Error] {label}: {line.strip()}")

    @staticmethod
    def _resolve_ffmpeg_path():
        """定位开发环境或 PyInstaller App 内携带的 FFmpeg。"""
        import platform
        import sys

        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        arch = platform.machine().lower()
        ffmpeg_path = os.path.join(
            base_dir, "bin", "ffmpeg_arm64" if "arm" in arch else "ffmpeg_x86"
        )
        if os.path.exists(ffmpeg_path):
            return ffmpeg_path
        if hasattr(sys, "_MEIPASS"):
            return os.path.join(sys._MEIPASS, "ffmpeg")
        fallback = os.path.join(base_dir, "bin", "ffmpeg")
        return fallback if os.path.exists(fallback) else "ffmpeg"

    def _video_encoder_params(self, codec, bitrate, maxrate, bufsize, fps):
        """返回按配置实际生效的低延迟视频编码参数。"""
        if self._normalize_video_codec(codec) == "H265":
            # 2022 国标平台常用 H.265；关闭 B 帧和前瞻，以维持与 H.264 相同的低延迟特性。
            x265_params = "bframes=0:rc-lookahead=0:scenecut=0:repeat-headers=1:log-level=error"
            return [
                "-c:v", "libx265",
                "-profile:v", "main",
                "-preset", "ultrafast",
                "-tune", "zerolatency",
                "-x265-params", x265_params,
                "-b:v", bitrate,
                "-maxrate", maxrate,
                "-bufsize", bufsize,
                "-r", fps,
                "-pix_fmt", "yuv420p",
                "-g", "15",
            ]

        x264_params = "no-mbtree=1:sync-lookahead=0:rc-lookahead=0:no-scenecut=1:bframes=0:nal-hrd=cbr"
        return [
            "-c:v", "libx264",
            "-profile:v", "baseline",
            "-level:v", "3.1",
            "-preset", "ultrafast",
            "-tune", "zerolatency",
            "-x264-params", x264_params,
            "-b:v", bitrate,
            "-maxrate", maxrate,
            "-bufsize", bufsize,
            "-r", fps,
            "-pix_fmt", "yuv420p",
            "-g", "15",
        ]

    def _audio_encoder_params(self, enabled, codec):
        """返回音频编码参数；禁用音频时明确移除音轨。"""
        if not enabled:
            return ["-an"]

        codec = self._normalize_audio_codec(codec)
        if codec == "G711U":
            return ["-c:a", "pcm_mulaw", "-ar", "8000", "-ac", "1"]
        if codec == "G723":
            return ["-c:a", "g723_1", "-b:a", "6.3k", "-ar", "8000", "-ac", "1"]
        if codec == "AAC":
            return ["-c:a", "aac", "-profile:a", "aac_low", "-b:a", "64k", "-ar", "16000", "-ac", "1"]
        return ["-c:a", "pcm_alaw", "-ar", "8000", "-ac", "1"]

    def set_ptz_state(self, pan, tilt, zoom):
        self.config.ptz_pan = max(-1.0, min(1.0, float(pan)))
        self.config.ptz_tilt = max(-1.0, min(1.0, float(tilt)))
        self.config.ptz_zoom = max(1.0, min(4.0, float(zoom)))
        if hasattr(self, 'media_ctrl_print'):
            self.media_ctrl_print(
                f"[PTZ] 推流画面应用模拟 PTZ: pan={self.config.ptz_pan:.2f}, "
                f"tilt={self.config.ptz_tilt:.2f}, zoom={self.config.ptz_zoom:.2f}x"
            )
        if self.current_process and self.last_stream_args:
            target_ip, target_port, protocol, ssrc, video_source = self.last_stream_args
            if hasattr(self, 'media_ctrl_print'):
                self.media_ctrl_print("[PTZ] 正在重启 FFmpeg 以同步平台侧 PTZ 画面...")
            self.start_stream(target_ip, target_port, protocol, ssrc, video_source)

    def is_streaming(self):
        return self.current_process is not None and self.current_process.poll() is None

    def get_process_status(self):
        if not self.current_process:
            return "STOPPED", None
        code = self.current_process.poll()
        if code is None:
            return "RUNNING", self.current_process.pid
        return f"EXITED({code})", self.current_process.pid

    def parse_sdp(self, sdp_body):
        # Extract IP and Port from SDP
        # c=IN IP4 192.168.1.100
        # m=video 6000 RTP/AVP 96
        # y=0111000000
        ip = None
        port = None
        ssrc = None
        protocol = "UDP"

        for line in sdp_body.split('\n'):
            line = line.strip()
            if line.startswith('c=IN IP4 '):
                ip = line.split(' ')[2]
            elif line.startswith('m=video '):
                parts = line.split(' ')
                port = parts[1]
                if 'TCP' in parts[2]:
                    protocol = "TCP"
            elif line.startswith('y='):
                ssrc = line.split('=')[1]

        return ip, port, protocol, ssrc

    def parse_talk_sdp(self, sdp_body):
        """解析语音 Talk/Broadcast SDP，返回平台 RTP 目标与协商编码。"""
        ip = None
        port = None
        transport = "UDP"
        payload_types = []
        rtpmap = {}

        for raw_line in sdp_body.splitlines():
            line = raw_line.strip()
            if line.startswith("c=IN IP4 "):
                parts = line.split()
                if len(parts) >= 3:
                    ip = parts[2]
            elif line.startswith("m=audio "):
                parts = line.split()
                if len(parts) >= 4:
                    port = parts[1]
                    transport = "TCP" if "TCP" in parts[2].upper() else "UDP"
                    payload_types = parts[3:]
            elif line.lower().startswith("a=rtpmap:"):
                match = re.match(r"a=rtpmap:(\d+)\s+([^/\s]+)(?:/(\d+))?", line, re.IGNORECASE)
                if match:
                    rtpmap[match.group(1)] = (match.group(2).upper(), match.group(3) or "8000")

        static_codecs = {"0": ("PCMU", "8000"), "8": ("PCMA", "8000")}
        offered_codecs = []
        for payload_type in payload_types:
            encoding, sample_rate = rtpmap.get(payload_type, static_codecs.get(payload_type, ("", "")))
            codec = {"PCMA": "G711A", "PCMU": "G711U"}.get(encoding)
            if codec:
                offered_codecs.append({
                    "payload_type": payload_type,
                    "encoding": encoding,
                    "sample_rate": sample_rate,
                    "codec": codec,
                })
        selected = offered_codecs[0] if offered_codecs else {}
        return {
            "ip": ip,
            "port": port,
            "transport": transport,
            "payload_type": selected.get("payload_type"),
            "encoding": selected.get("encoding", ""),
            "sample_rate": selected.get("sample_rate", ""),
            "codec": selected.get("codec"),
            "offered_codecs": offered_codecs,
        }

    @staticmethod
    def build_talk_receive_sdp(local_ip, local_port, codec):
        """构造供 FFmpeg 接收 RTP 音频的最小 SDP。"""
        profile = MediaController._talk_codec_profile(codec)
        return (
            "v=0\n"
            "o=OpenGBD 0 0 IN IP4 {ip}\n"
            "s=Talk\n"
            "c=IN IP4 {ip}\n"
            "t=0 0\n"
            "m=audio {port} RTP/AVP {payload}\n"
            "a=rtpmap:{payload} {rtpmap}\n"
            "a=recvonly\n"
        ).format(
            ip=local_ip, port=local_port, payload=profile["payload_type"], rtpmap=profile["rtpmap"]
        )

    def is_talking(self):
        processes = (self.talk_receive_process, self.talk_send_process)
        return any(process and process.poll() is None for process in processes)

    def start_talk_session(self, remote_ip, remote_port, local_ip, local_port, codec, audio_device=0):
        """启动基础双向语音对讲：播放平台下行 RTP，同时回传麦克风 RTP。"""
        profile = self._talk_codec_profile(codec)
        self.stop_talk_session()
        ffmpeg_path = self._resolve_ffmpeg_path()
        sdp_text = self.build_talk_receive_sdp(local_ip, local_port, profile["codec"])

        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", suffix=".sdp", prefix="opengbd-talk-", delete=False
            ) as sdp_file:
                sdp_file.write(sdp_text)
                self.talk_sdp_path = sdp_file.name

            receive_cmd = [
                ffmpeg_path, "-nostdin", "-hide_banner", "-loglevel", "warning",
                "-protocol_whitelist", "file,udp,rtp", "-fflags", "nobuffer", "-flags", "low_delay",
                "-i", self.talk_sdp_path, "-vn", "-ac", "1", "-ar", "8000",
                "-f", "audiotoolbox", "default",
            ]
            send_cmd = [
                ffmpeg_path, "-nostdin", "-hide_banner", "-loglevel", "warning",
                "-f", "avfoundation", "-i", f"none:{audio_device}", "-vn",
                *profile["encoder_args"], "-f", "rtp", f"rtp://{remote_ip}:{remote_port}",
            ]
            self.talk_receive_process = subprocess.Popen(
                receive_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True
            )
            self.talk_send_process = subprocess.Popen(
                send_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True
            )
            # 麦克风不可用、FFmpeg 缺失等通常会在刚启动时立刻退出；此时不要向平台虚报 200 OK。
            time.sleep(0.2)
            exited = [
                (label, process) for label, process in (
                    ("对讲下行", self.talk_receive_process),
                    ("对讲上行", self.talk_send_process),
                ) if process.poll() is not None
            ]
            if exited:
                details = []
                for label, process in exited:
                    try:
                        details.append(f"{label}: {(process.stderr.read() or '').strip()}")
                    except Exception:
                        details.append(label)
                raise RuntimeError("；".join(details))
            self.talk_session = {
                "remote_ip": remote_ip, "remote_port": str(remote_port),
                "local_ip": local_ip, "local_port": str(local_port), "codec": profile["codec"],
            }
            if hasattr(self, "media_ctrl_print"):
                self.media_ctrl_print(
                    f"[Talk] 已启动双向语音对讲：{profile['codec']} / 本地 RTP {local_port} / 平台 RTP {remote_ip}:{remote_port}"
                )
                for process, label in (
                    (self.talk_receive_process, "对讲下行"),
                    (self.talk_send_process, "对讲上行"),
                ):
                    threading.Thread(
                        target=self._read_process_errors,
                        args=(process, label, self.media_ctrl_print), daemon=True
                    ).start()
            return True
        except Exception as exc:
            if hasattr(self, "media_ctrl_print"):
                self.media_ctrl_print(f"[Talk Error] 启动语音对讲失败: {exc}")
            self.stop_talk_session()
            return False

    def stop_talk_session(self):
        active = self.is_talking()
        self._terminate_process(self.talk_receive_process)
        self._terminate_process(self.talk_send_process)
        self.talk_receive_process = None
        self.talk_send_process = None
        self.talk_session = None
        if self.talk_sdp_path:
            try:
                os.unlink(self.talk_sdp_path)
            except FileNotFoundError:
                pass
            except Exception:
                pass
            self.talk_sdp_path = None
        if active and hasattr(self, "media_ctrl_print"):
            self.media_ctrl_print("[Talk] 语音对讲已停止。")

    def start_stream(self, target_ip, target_port, protocol, ssrc, video_source="mac_camera"):
        self.last_stream_args = (target_ip, target_port, protocol, ssrc, video_source)
        if self.current_process:
            self.stop_stream()

        ffmpeg_path = self._resolve_ffmpeg_path()

        print(f"[Media] Selected FFmpeg binary path: {ffmpeg_path}")
        if hasattr(self, 'media_ctrl_print'):
            self.media_ctrl_print(f"[Media] 使用独立推流核心: {ffmpeg_path}")

        print(f"[Media] Starting stream to {target_ip}:{target_port} over {protocol} with SSRC {ssrc}")

        # 使用 rtp_mpegts 格式，这能被 ZLM / WVP 等国标流媒体服务器完美作为 PS/TS 流解析
        source_text = getattr(self.config, 'camera_source_text', '')
        custom_url = getattr(self.config, 'custom_url', '')
        video_bitrate = int(getattr(self.config, 'VIDEO_BITRATE', 1000))
        video_resolution = getattr(self.config, 'VIDEO_RESOLUTION', '640x480')
        video_fps = str(getattr(self.config, 'VIDEO_FPS', 30))
        video_mirror = getattr(self.config, 'video_mirror', False)
        video_codec = self._normalize_video_codec(getattr(self.config, 'VIDEO_CODEC', 'H264'))
        audio_enabled = getattr(self.config, 'audio_enabled', False)
        audio_codec = self._normalize_audio_codec(getattr(self.config, 'AUDIO_CODEC', 'G711A'))
        audio_source_idx = int(getattr(self.config, 'audio_source_idx', 0))
        b_v = f"{video_bitrate}k"
        maxrate = f"{int(video_bitrate * 1.2)}k"  # 最大码率 = 1.2x，避免突发帧打爆缓冲
        bufsize = f"{video_bitrate}k"               # bufsize = 1x码率 = 1秒缓冲，让码率控制更严格
        video_params = self._video_encoder_params(video_codec, b_v, maxrate, bufsize, video_fps)
        audio_params = self._audio_encoder_params(audio_enabled, audio_codec)

        audio_device = str(getattr(self.config, 'audio_source_idx', 0))
        if hasattr(self, 'media_ctrl_print'):
            audio_desc = audio_codec if audio_enabled else '关闭'
            self.media_ctrl_print(
                f"[Media] 编码配置: 视频 {video_codec}，音频 {audio_desc}，"
                f"国标 GB/T 28181-{getattr(self.config, 'GB28181_VERSION', '2016')}"
            )

        if "【自定义源】" in source_text:
            # 自定义流（RTSP/RTMP）或本地视频文件
            # 如果为空则兜底使用虚拟源
            input_src = custom_url.strip() if custom_url.strip() else f"testsrc=size={video_resolution}:rate={video_fps}"
            if input_src.lower().startswith("file://"):
                try:
                    from PySide6.QtCore import QUrl
                    input_src = QUrl(input_src).toLocalFile()
                except Exception:
                    input_src = input_src[7:]
            
            ffmpeg_cmd = [
                ffmpeg_path,
            ]
            
            is_network_source = input_src.startswith(
                ("rtsp://", "rtmp://", "http://", "https://", "rtp://")
            )
            is_local_file = not is_network_source and os.path.isfile(input_src)

            # 本地文件循环并按实时帧率读取，避免文件结束后推流中断
            if is_local_file:
                ffmpeg_cmd.extend(["-stream_loop", "-1"])
            if not is_network_source:
                ffmpeg_cmd.append("-re")
                
            ffmpeg_cmd.extend([
                "-i", input_src,
            ])
            
            self._append_video_filters(ffmpeg_cmd, video_resolution, video_mirror)

            # 若自定义源自身不含音频，加入 -map 容错，避免缺少音频流时报错
            stream_maps = ["-map", "0:v:0"]
            if audio_enabled:
                stream_maps.extend(["-map", "0:a?"])
                
            ffmpeg_cmd.extend(stream_maps + video_params + audio_params + [
                "-f", "rtp_mpegts",
            ])
        elif "【桌面捕获】" in source_text:
            # 屏幕录像抓取
            screen_name = "Capture screen 0"
            if "Capture screen 1" in source_text:
                screen_name = "Capture screen 1"
                
            ffmpeg_cmd = [
                ffmpeg_path,
                "-f", "avfoundation",
                "-framerate", video_fps,
                "-video_size", video_resolution,
                "-i", f"{screen_name}:none",
            ]

            stream_maps = ["-map", "0:v:0"]
            if audio_enabled:
                # 桌面捕获同时抓取麦克风音频作为输入 1
                ffmpeg_cmd.extend([
                    "-f", "avfoundation",
                    "-i", f"none:{audio_device}",
                ])
                stream_maps.extend(["-map", "1:a:0"])
            
            self._append_video_filters(ffmpeg_cmd, video_resolution, video_mirror)
                
            ffmpeg_cmd.extend(stream_maps + video_params + audio_params + [
                "-f", "rtp_mpegts",
            ])
        elif "【摄像头】" in source_text:
            # 物理摄像头
            actual_idx = 0
            clean_desc = source_text.replace("【摄像头】", "").strip()
            for idx, cam_desc in enumerate(getattr(self.config, 'camera_devices_list', [])):
                if cam_desc == clean_desc:
                    actual_idx = idx
                    break
            capture_resolution = self._camera_capture_resolution(video_resolution)
            capture_fps = "30"
            if capture_resolution != video_resolution and hasattr(self, 'media_ctrl_print'):
                self.media_ctrl_print(
                    f"[Media] 摄像头不直接采集 {video_resolution}，改用 {capture_resolution} 采集后缩放为 {video_resolution}"
                )
            if video_fps != capture_fps and hasattr(self, 'media_ctrl_print'):
                self.media_ctrl_print(
                    f"[Media] Mac 摄像头固定以 {capture_fps}fps 采集，再编码输出为 {video_fps}fps，避免 avfoundation 低帧率打开失败"
                )

            # avfoundation 支持单会话同时指定音视频输入 "<video>:<audio>"
            video_input = clean_desc or actual_idx
            audio_input = audio_device if audio_enabled else "none"
            
            ffmpeg_cmd = [
                ffmpeg_path,
                "-f", "avfoundation",
                "-framerate", capture_fps,
                "-video_size", capture_resolution,
                "-i", f"{video_input}:{audio_input}",
            ]

            self._append_video_filters(ffmpeg_cmd, video_resolution, video_mirror)

            ffmpeg_cmd.extend(video_params + audio_params + [
                "-f", "rtp_mpegts",
            ])
        else:
            # 兜底：虚拟源
            ffmpeg_cmd = [
                ffmpeg_path,
                "-f", "lavfi",
                "-i", f"testsrc=size={video_resolution}:rate={video_fps}",
            ]

            stream_maps = ["-map", "0:v:0"]
            if audio_enabled:
                # 虚拟视频源结合麦克风音频作为输入 1
                ffmpeg_cmd.extend([
                    "-f", "avfoundation",
                    "-i", f"none:{audio_device}",
                ])
                stream_maps.extend(["-map", "1:a:0"])

            self._append_video_filters(ffmpeg_cmd, video_resolution, video_mirror)

            ffmpeg_cmd.extend(stream_maps + video_params + audio_params + [
                "-f", "rtp_mpegts",
            ])

        if protocol == "TCP":
            rtp_url = f"rtp://{target_ip}:{target_port}?tcp=1"
        else:
            rtp_url = f"rtp://{target_ip}:{target_port}"

        # 平台点播链路只输出一路 RTP，避免 tee 二次封装/本地 UDP 分流导致部分平台解码花屏。
        self.current_preview_url = None
        if self.preview_url_callback:
            self.preview_url_callback("")
        ffmpeg_cmd.append(rtp_url)

        print("[Media] Executing FFmpeg command:", " ".join(ffmpeg_cmd))
        if hasattr(self, 'media_ctrl_print'):
            self.media_ctrl_print(f"[Media] Executing FFmpeg: {' '.join(ffmpeg_cmd)}")
        
        try:
            # 捕获 stderr 并开启异步读取线程输出到日志区，方便看到 FFmpeg 实时错误
            self.current_process = subprocess.Popen(
                ffmpeg_cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True
            )
            self.current_process_pid = self.current_process.pid
            print("[Media] FFmpeg started with PID", self.current_process.pid)
            if hasattr(self, 'media_ctrl_print'):
                self.media_ctrl_print(f"[Media] 推流 FFmpeg 已启动 (PID: {self.current_process.pid})")

            # 无效输入、被占用的摄像头或不可用编码器通常会立即退出。确认进程存活后，
            # 上层才应当向平台确认点播会话，避免产生“收到 200 OK 但没有 RTP”的假成功。
            time.sleep(0.2)
            if self.current_process.poll() is not None:
                try:
                    detail = (self.current_process.stderr.read() or "").strip()
                except Exception:
                    detail = ""
                raise RuntimeError(detail or f"FFmpeg 启动后立即退出（退出码 {self.current_process.returncode}）")
            
            # 开启后台线程读取 FFmpeg 的 stderr
            def log_reader(pipe, callback):
                for line in pipe:
                    if "Error" in line or "error" in line or "fail" in line or "Could not" in line:
                        callback(f"[Media Error] {line.strip()}")
            
            if hasattr(self, 'media_ctrl_print'):
                t = threading.Thread(target=log_reader, args=(self.current_process.stderr, self.media_ctrl_print), daemon=True)
                t.start()
            return True
        except Exception as e:
            print("[Media] Error starting FFmpeg:", e)
            if hasattr(self, 'media_ctrl_print'):
                self.media_ctrl_print(f"[Media Error] 启动推流失败: {e}")
            self.current_process = None
            self.current_process_pid = None
            self.current_preview_url = None
            if self.preview_url_callback:
                self.preview_url_callback("")
            return False

    def stop_stream(self):
        process = self.current_process
        pid = self.current_process_pid or (process.pid if process else None)
        if process or pid:
            print("[Media] Stopping current stream...")
            if hasattr(self, 'media_ctrl_print'):
                self.media_ctrl_print("[Media] 停止推流中...")
            try:
                if process and process.poll() is None:
                    process.terminate()
                    process.wait(timeout=2)
            except Exception:
                try:
                    if process and process.poll() is None:
                        process.kill()
                        process.wait(timeout=1)
                except Exception:
                    pass
            if pid:
                try:
                    os.kill(pid, 0)
                    os.kill(pid, signal.SIGTERM)
                    time.sleep(0.2)
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except Exception:
                    pass
            self.current_process = None
            self.current_process_pid = None
            self.current_preview_url = None
            if self.preview_url_callback:
                self.preview_url_callback("")
            print("[Media] Stream stopped.")
            if hasattr(self, 'media_ctrl_print'):
                self.media_ctrl_print("[Media] 推流已停止。")
