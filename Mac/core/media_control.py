# media_control.py
import re
import os
import subprocess
import threading
import time
import signal

class MediaController:
    def __init__(self, config):
        self.config = config
        self.current_process = None
        self.last_stream_args = None
        self.preview_url_callback = None
        self.current_preview_url = None
        self.current_process_pid = None

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

    def start_stream(self, target_ip, target_port, protocol, ssrc, video_source="mac_camera"):
        self.last_stream_args = (target_ip, target_port, protocol, ssrc, video_source)
        if self.current_process:
            self.stop_stream()

        # 动态探测内置的 ffmpeg 二进制路径
        import platform
        import sys

        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        arch = platform.machine().lower() # 'arm64' or 'x86_64'
        
        # 默认找 bin 目录下的对应架构独立程序
        if "arm" in arch:
            ffmpeg_path = os.path.join(base_dir, "bin", "ffmpeg_arm64")
        else:
            ffmpeg_path = os.path.join(base_dir, "bin", "ffmpeg_x86")

        # 兜底：如果是 pyinstaller 打包后的单文件/App 结构
        if not os.path.exists(ffmpeg_path):
            if hasattr(sys, '_MEIPASS'):
                ffmpeg_path = os.path.join(sys._MEIPASS, "ffmpeg")
            else:
                # 再次兜底检查同名 ffmpeg 
                fallback = os.path.join(base_dir, "bin", "ffmpeg")
                if os.path.exists(fallback):
                    ffmpeg_path = fallback
                else:
                    ffmpeg_path = "ffmpeg" # 使用环境变量系统的

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
        audio_enabled = getattr(self.config, 'audio_enabled', False)
        audio_source_idx = int(getattr(self.config, 'audio_source_idx', 0))
        b_v = f"{video_bitrate}k"
        maxrate = f"{int(video_bitrate * 1.2)}k"  # 最大码率 = 1.2x，避免突发帧打爆缓冲
        bufsize = f"{video_bitrate}k"               # bufsize = 1x码率 = 1秒缓冲，让码率控制更严格
        # x264 低延时参数串：关闭 b-frames, 低延迟模式
        x264_params = "no-mbtree=1:sync-lookahead=0:rc-lookahead=0:no-scenecut=1:bframes=0:nal-hrd=cbr"
        # 音频编码参数：G.711 A-Law 8kHz，国标 GB28181 标准格式
        audio_params = ["-acodec", "pcm_alaw", "-ar", "8000", "-ac", "1"] if audio_enabled else ["-an"]

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
                
            ffmpeg_cmd.extend([
                "-vcodec", "libx264",
                "-profile:v", "baseline",
                "-level", "3.1",
                "-preset", "ultrafast",
                "-tune", "zerolatency",
                "-x264-params", x264_params,
                "-b:v", b_v,
                "-maxrate", maxrate,
                "-bufsize", bufsize,
                "-r", video_fps,
                "-pix_fmt", "yuv420p",
                "-g", "15",
            ] + audio_params + [
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
            
            self._append_video_filters(ffmpeg_cmd, video_resolution, video_mirror)
                
            ffmpeg_cmd.extend([
                "-vcodec", "libx264",
                "-profile:v", "baseline",
                "-level", "3.1",
                "-preset", "ultrafast",
                "-tune", "zerolatency",
                "-x264-params", x264_params,
                "-b:v", b_v,
                "-maxrate", maxrate,
                "-bufsize", bufsize,
                "-r", video_fps,
                "-pix_fmt", "yuv420p",
                "-g", "15",
            ] + audio_params + [
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
                    
            ffmpeg_cmd = [
                ffmpeg_path,
                "-f", "avfoundation",
                "-framerate", capture_fps,
                "-video_size", capture_resolution,
                "-i", f"{clean_desc or actual_idx}:none",
            ]

            if audio_enabled:
                # 摄像头和麦克风同时输入：用多路 -i
                audio_device = str(audio_source_idx)
                ffmpeg_cmd.extend([
                    "-f", "avfoundation",
                    "-i", f"none:{audio_device}",
                ])

            self._append_video_filters(ffmpeg_cmd, video_resolution, video_mirror)

            ffmpeg_cmd.extend([
                "-vcodec", "libx264",
                "-profile:v", "baseline",
                "-level", "3.1",
                "-preset", "ultrafast",
                "-tune", "zerolatency",
                "-x264-params", x264_params,
                "-b:v", b_v,
                "-maxrate", maxrate,
                "-bufsize", bufsize,
                "-r", video_fps,
                "-pix_fmt", "yuv420p",
                "-g", "15",
            ] + audio_params + [
                "-f", "rtp_mpegts",
            ])
        else:
            # 兜底：虚拟源
            ffmpeg_cmd = [
                ffmpeg_path,
                "-f", "lavfi",
                "-i", f"testsrc=size={video_resolution}:rate={video_fps}",
            ]

            if audio_enabled:
                # 虚拟源使用麦克风抓取音频
                audio_device = str(audio_source_idx)
                ffmpeg_cmd.extend([
                    "-f", "avfoundation",
                    "-i", f"none:{audio_device}",
                ])

            self._append_video_filters(ffmpeg_cmd, video_resolution, video_mirror)

            ffmpeg_cmd.extend([
                "-vcodec", "libx264",
                "-profile:v", "baseline",
                "-level", "3.1",
                "-preset", "ultrafast",
                "-tune", "zerolatency",
                "-x264-params", x264_params,
                "-b:v", b_v,
                "-maxrate", maxrate,
                "-bufsize", bufsize,
                "-r", video_fps,
                "-pix_fmt", "yuv420p",
                "-g", "15",
            ] + audio_params + [
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
