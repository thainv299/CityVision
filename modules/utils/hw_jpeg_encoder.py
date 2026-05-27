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
    Tự động fallback về cv2.imencode (CPU) nếu chạy trên Windows hoặc môi trường không hỗ trợ.
    """
    def __init__(self):
        self.pipeline = None
        self.appsrc = None
        self.appsink = None
        
        self.width = 0
        self.height = 0
        self.quality = 0
        self.is_hardware = GSTREAMER_AVAILABLE
        
    def _build_pipeline(self, w, h, q):
        if self.pipeline:
            self.pipeline.set_state(Gst.State.NULL)
            self.pipeline = None
            
        print(f"[HW-JPEG] Khởi tạo GStreamer Pipeline: {w}x{h} (Quality: {q})")
        # Sử dụng NVJPEGENC để nén bằng phần cứng
        # appsrc nhận ảnh RGBA (vì nvvidconv hỗ trợ tốt RGBA sang I420)
        pipeline_str = (
            f"appsrc name=src format=TIME is-live=true do-timestamp=true ! "
            f"video/x-raw,format=RGBA,width={w},height={h},framerate=30/1 ! "
            f"nvvidconv ! video/x-raw(memory:NVMM),format=I420 ! "
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
        """Mã hóa frame sang JPEG. Tự động resize nếu có yêu cầu."""
        if frame is None or frame.size == 0:
            return None
            
        # Resize frame nếu có yêu cầu
        if preview_w > 0 and preview_h > 0 and (frame.shape[1] != preview_w or frame.shape[0] != preview_h):
            process_frame = cv2.resize(frame, (preview_w, preview_h), interpolation=cv2.INTER_NEAREST)
            h, w = preview_h, preview_w
        else:
            process_frame = frame
            h, w = frame.shape[:2]

        # FALLBACK: Nếu không có Hardware hoặc khởi tạo lỗi
        if not self.is_hardware:
            success, encoded = cv2.imencode('.jpg', process_frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
            if success:
                return encoded.tobytes()
            return None
            
        # DYNAMIC REBUILD: Kiểm tra nếu kích thước hoặc chất lượng thay đổi
        if w != self.width or h != self.height or quality != self.quality:
            self._build_pipeline(w, h, quality)
            self.width, self.height, self.quality = w, h, quality
            
        if not self.is_hardware:
            # Fallback nếu rebuild thất bại
            success, encoded = cv2.imencode('.jpg', process_frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
            return encoded.tobytes() if success else None
            
        # Convert BGR to RGBA cho GStreamer appsrc
        rgba_frame = cv2.cvtColor(process_frame, cv2.COLOR_BGR2RGBA)
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
