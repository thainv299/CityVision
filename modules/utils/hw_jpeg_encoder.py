import cv2
import numpy as np
import time

try:
    import gi
    gi.require_version('Gst', '1.0')
    gi.require_version('GstApp', '1.0')
    from gi.repository import Gst, GstApp, GLib
    Gst.init(None)
    GSTREAMER_AVAILABLE = True
except ImportError:
    GSTREAMER_AVAILABLE = False
    print("[HW-JPEG] ⚠️ Thư viện gi.repository không khả dụng. Fallback về cv2.imencode (CPU).")

class HardwareJPEGEncoder:
    """
    Sử dụng GStreamer nvjpegenc để nén ảnh sang JPEG bằng phần cứng trên Jetson.
    Pipeline mới: appsrc(RGBA, full-res) → nvvidconv(resize + I420, VIC HW) → nvjpegenc(HW)
    Tự động fallback về cv2.imencode (CPU) nếu chạy trên Windows hoặc môi trường không hỗ trợ.
    """
    def __init__(self):
        self.pipeline = None
        self.appsrc = None
        self.appsink = None
        
        # Kích thước đầu vào (frame gốc từ camera)
        self.in_width = 0
        self.in_height = 0
        # Kích thước đầu ra (preview gửi lên web)
        self.out_width = 0
        self.out_height = 0
        self.quality = 0
        self.is_hardware = GSTREAMER_AVAILABLE
        
    def _build_pipeline(self, in_w, in_h, out_w, out_h, q):
        if self.pipeline:
            self.pipeline.set_state(Gst.State.NULL)
            self.pipeline = None
            
        print(f"[HW-JPEG] Khởi tạo GStreamer Pipeline: {in_w}x{in_h} → {out_w}x{out_h} (Quality: {q})")
        # appsrc nhận ảnh RGBA ở KÍCH THƯỚC GỐC (1080p)
        # nvvidconv đảm nhiệm RESIZE + chuyển đổi màu trên phần cứng VIC
        # → Loại bỏ hoàn toàn cv2.resize khỏi CPU
        pipeline_str = (
            f"appsrc name=src format=TIME is-live=true do-timestamp=true ! "
            f"video/x-raw,format=RGBA,width={in_w},height={in_h},framerate=30/1 ! "
            f"nvvidconv ! video/x-raw(memory:NVMM),format=I420,width={out_w},height={out_h} ! "
            f"nvjpegenc quality={q} ! "
            f"appsink name=sink emit-signals=true sync=false max-buffers=1 drop=true"
        )
        
        try:
            self.pipeline = Gst.parse_launch(pipeline_str)
            self.appsrc = self.pipeline.get_by_name("src")
            self.appsink = self.pipeline.get_by_name("sink")
            
            # Cấu hình appsrc
            self.appsrc.set_property("format", Gst.Format.TIME)
            self.appsrc.set_property("is-live", True)
            
            self.pipeline.set_state(Gst.State.PLAYING)
        except Exception as e:
            print(f"[HW-JPEG] Lỗi khởi tạo pipeline: {e}. Tự động fallback về CPU.")
            self.is_hardware = False
            self.pipeline = None

    def encode(self, frame: np.ndarray, preview_w: int = 0, preview_h: int = 0, quality: int = 75):
        """Mã hóa frame sang JPEG. Resize được thực hiện trên phần cứng VIC (không dùng CPU)."""
        if frame is None or frame.size == 0:
            return None
        
        in_h, in_w = frame.shape[:2]
        out_w = preview_w if preview_w > 0 else in_w
        out_h = preview_h if preview_h > 0 else in_h

        # FALLBACK: Nếu không có Hardware hoặc khởi tạo lỗi
        if not self.is_hardware:
            if out_w != in_w or out_h != in_h:
                process_frame = cv2.resize(frame, (out_w, out_h), interpolation=cv2.INTER_NEAREST)
            else:
                process_frame = frame
            success, encoded = cv2.imencode('.jpg', process_frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
            if success:
                return encoded.tobytes()
            return None
            
        # DYNAMIC REBUILD: Kiểm tra nếu kích thước hoặc chất lượng thay đổi
        need_rebuild = (
            in_w != self.in_width or in_h != self.in_height or
            out_w != self.out_width or out_h != self.out_height or
            quality != self.quality
        )
        if need_rebuild:
            self._build_pipeline(in_w, in_h, out_w, out_h, quality)
            self.in_width, self.in_height = in_w, in_h
            self.out_width, self.out_height = out_w, out_h
            self.quality = quality
            
        if not self.is_hardware:
            # Fallback nếu rebuild thất bại
            if out_w != in_w or out_h != in_h:
                process_frame = cv2.resize(frame, (out_w, out_h), interpolation=cv2.INTER_NEAREST)
            else:
                process_frame = frame
            success, encoded = cv2.imencode('.jpg', process_frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
            return encoded.tobytes() if success else None
            
        # Convert BGR → RGBA cho GStreamer appsrc (CPU SIMD, nhẹ hơn resize rất nhiều)
        # nvvidconv sẽ đảm nhiệm phần RESIZE nặng nề trên phần cứng VIC
        rgba_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGBA)
        data = rgba_frame.tobytes()
        
        # Push buffer
        buf = Gst.Buffer.new_allocate(None, len(data), None)
        buf.fill(0, data)
        
        ret = self.appsrc.emit("push-buffer", buf)
        if ret != Gst.FlowReturn.OK:
            return None
            
        # Pull JPEG
        sample = self.appsink.emit("pull-sample")
        if sample:
            sink_buf = sample.get_buffer()
            success, map_info = sink_buf.map(Gst.MapFlags.READ)
            if success:
                jpeg_bytes = map_info.data
                sink_buf.unmap(map_info)
                return jpeg_bytes
                
        return None
        
    def __del__(self):
        if self.pipeline:
            self.pipeline.set_state(Gst.State.NULL)
