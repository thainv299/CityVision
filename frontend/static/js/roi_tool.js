// ROI Drawing Tool - Separate file to avoid syntax issues


window.roiDrawingTool = {
    modal: null,
    canvas: null,
    targetField: null,
    points: [],
    firstFrame: null,
    isDirty: false,
    keydownHandler: null,
    canvasClickHandler: null,
    canvasContextMenuHandler: null,
    originalWidth: 1,
    originalHeight: 1,

    init() {
        this.modal = document.getElementById("roi-drawing-modal");
        this.canvas = document.getElementById("roi-canvas");

        if (!this.modal || !this.canvas) {
            console.error("❌ Không tìm thấy Modal hoặc Canvas trong DOM!");
            return;
        }
    },

    openModal(targetFieldId, firstFrameData) {


        this.targetField = targetFieldId;
        this.points = [];
        this.firstFrame = firstFrameData;
        this.isDirty = false;

        // Set canvas size
        const img = new Image();
        img.onload = () => {
            this.originalWidth = img.naturalWidth || img.width;
            this.originalHeight = img.naturalHeight || img.height;

            const maxWidth = Math.min(800, window.innerWidth - 40);
            this.canvas.width = maxWidth;
            this.canvas.height = (maxWidth / this.originalWidth) * this.originalHeight;
            this.drawCanvas();
        };
        img.onerror = () => {
            console.error("❌ Lỗi tải ảnh!");
        };
        img.src = firstFrameData;

        this.modal.style.display = "flex";
        this.setupCanvasListeners();
    },

    closeModal() {
        this.modal.style.display = "none";
        this.points = [];
        this.firstFrame = null;
        this.removeCanvasListeners();
    },

    confirmPoints() {
        if (this.points.length < 3) {
            alert("Cần vẽ ít nhất 3 điểm!");
            return;
        }

        // Gửi tọa độ kèm kích thước tham chiếu (Reference Resolution)
        const resultData = {
            units: "reference",
            ref_width: this.canvas.width,
            ref_height: this.canvas.height,
            points: this.points
        };

        const el = document.getElementById(this.targetField);
        if (el) {
            el.value = JSON.stringify(resultData);
        }
        this.closeModal();
    },

    setupCanvasListeners() {

        this.canvasClickHandler = (e) => {
            const rect = this.canvas.getBoundingClientRect();
            const style = window.getComputedStyle(this.canvas);
            const borderLeft = parseFloat(style.borderLeftWidth) || 0;
            const borderTop = parseFloat(style.borderTopWidth) || 0;

            // Tọa độ thực tế bên trong canvas drawing area
            const clientX = e.clientX - rect.left - borderLeft;
            const clientY = e.clientY - rect.top - borderTop;

            // Quy đổi sang tọa độ pixel trên canvas resolution
            const canvasX = clientX * (this.canvas.width / (rect.width - borderLeft * 2));
            const canvasY = clientY * (this.canvas.height / (rect.height - borderTop * 2));

            // Lưu tọa độ trực tiếp trên canvas tham chiếu
            const refX = Math.round(canvasX);
            const refY = Math.round(canvasY);

            this.points.push([refX, refY]);
            this.drawCanvas();
        };

        this.canvasContextMenuHandler = (e) => {
            e.preventDefault();
            this.points = [];
            this.drawCanvas();
        };

        this.keydownHandler = (e) => {
            if (e.key === "Enter") {
                this.confirmPoints();
            } else if (e.key === "Escape") {
                this.closeModal();
            } else if (e.ctrlKey && e.key === "z") {
                e.preventDefault();
                if (this.points.length > 0) {
                    this.points.pop();
                    this.drawCanvas();
                }
            }
        };

        this.canvas.addEventListener("mousedown", this.canvasClickHandler);
        this.canvas.addEventListener("contextmenu", this.canvasContextMenuHandler);
        document.addEventListener("keydown", this.keydownHandler);
    },

    removeCanvasListeners() {
        if (this.canvasClickHandler) {
            this.canvas.removeEventListener("mousedown", this.canvasClickHandler);
            this.canvasClickHandler = null;
        }
        if (this.canvasContextMenuHandler) {
            this.canvas.removeEventListener("contextmenu", this.canvasContextMenuHandler);
            this.canvasContextMenuHandler = null;
        }
        if (this.keydownHandler) {
            document.removeEventListener("keydown", this.keydownHandler);
            this.keydownHandler = null;
        }
    },

    drawCanvas() {
        const ctx = this.canvas.getContext("2d");
        ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);

        // Draw background image
        if (this.firstFrame) {
            const img = new Image();
            img.onload = () => {
                ctx.drawImage(img, 0, 0, this.canvas.width, this.canvas.height);
                this.drawOverlay(ctx);
            };
            img.src = this.firstFrame;
        } else {
            this.drawOverlay(ctx);
        }
    },

    drawOverlay(ctx) {
        // Draw polygon
        if (this.points.length > 0) {
            ctx.fillStyle = "rgba(0, 255, 0, 0.2)";
            ctx.strokeStyle = "#00ff00";
            ctx.lineWidth = 2;
            ctx.beginPath();

            // Quy đổi tọa độ gốc về tọa độ canvas để vẽ
            const getCanvasPoint = (pt) => {
                // pt đã là tọa độ trên canvas tham chiếu nên không cần quy đổi nữa
                return pt;
            };

            const startPt = getCanvasPoint(this.points[0]);
            ctx.moveTo(startPt[0], startPt[1]);

            for (let i = 1; i < this.points.length; i++) {
                const pt = getCanvasPoint(this.points[i]);
                ctx.lineTo(pt[0], pt[1]);
            }
            ctx.closePath();
            ctx.stroke();
            ctx.fill();

            // Draw points
            ctx.fillStyle = "#ff0000";
            for (let point of this.points) {
                const pt = getCanvasPoint(point);
                ctx.beginPath();
                ctx.arc(pt[0], pt[1], 5, 0, 2 * Math.PI);
                ctx.fill();
            }

            // Draw point count
            ctx.fillStyle = "#fff";
            ctx.font = "14px Arial";
            ctx.fillText("Điểm: " + this.points.length, 10, 25);
        }
    }
};

// Initialize when DOM is ready
if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => window.roiDrawingTool.init());
} else {
    window.roiDrawingTool.init();
}



