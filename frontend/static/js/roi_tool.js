// ROI Drawing Tool - Hỗ trợ cả Single ROI và Multi-Zone Parking ROI

window.roiDrawingTool = {
    modal: null,
    canvas: null,
    targetField: null,
    points: [],
    zones: [],
    currentZoneIndex: 0,
    isMultiZone: false,
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
        this.firstFrame = firstFrameData;
        this.isDirty = false;
        this.isMultiZone = (targetFieldId === "no_parking_points");

        const modalTitle = document.getElementById("roi-modal-title");
        const multiZoneToolbar = document.getElementById("roi-multi-zone-toolbar");

        if (this.isMultiZone) {
            if (modalTitle) modalTitle.textContent = "Vẽ Vùng Cấm Đỗ Xe (Đa vùng độc lập)";
            if (multiZoneToolbar) multiZoneToolbar.style.display = "block";
            this.loadExistingZones();
        } else {
            if (modalTitle) modalTitle.textContent = "Vẽ Vùng Quan Sát Giám Sát (ROI)";
            if (multiZoneToolbar) multiZoneToolbar.style.display = "none";
            this.loadExistingSingleRoi();
        }

        // Set canvas size
        const img = new Image();
        img.onload = () => {
            this.originalWidth = img.naturalWidth || img.width;
            this.originalHeight = img.naturalHeight || img.height;

            const maxWidth = Math.min(800, window.innerWidth - 60);
            const maxHeight = window.innerHeight * 0.65;

            const scale = Math.min(maxWidth / this.originalWidth, maxHeight / this.originalHeight);

            this.canvas.width = this.originalWidth * scale;
            this.canvas.height = this.originalHeight * scale;
            this.drawCanvas();
        };
        img.onerror = () => {
            console.error("❌ Lỗi tải ảnh!");
        };
        img.src = firstFrameData;

        this.modal.style.display = "flex";
        this.setupCanvasListeners();
    },

    loadExistingSingleRoi() {
        this.points = [];
        const el = document.getElementById(this.targetField);
        if (el && el.value.trim()) {
            try {
                const data = JSON.parse(el.value.trim());
                const pts = Array.isArray(data) ? data : (data.points || []);
                if (Array.isArray(pts)) {
                    this.points = pts.map(p => [Math.round(p[0]), Math.round(p[1])]);
                }
            } catch (e) {
                this.points = [];
            }
        }
    },

    loadExistingZones() {
        this.zones = [];
        this.currentZoneIndex = 0;
        const el = document.getElementById(this.targetField);
        let loaded = false;

        if (el && el.value.trim()) {
            try {
                const data = JSON.parse(el.value.trim());
                if (data.zones && Array.isArray(data.zones) && data.zones.length > 0) {
                    this.zones = data.zones.map((z, idx) => ({
                        id: z.id || (idx + 1),
                        name: z.name || `Vùng cấm ${idx + 1}`,
                        timeout: z.timeout !== undefined ? z.timeout : 30,
                        points: Array.isArray(z.points) ? z.points.map(p => [Math.round(p[0]), Math.round(p[1])]) : []
                    }));
                    loaded = true;
                } else if (data.points && Array.isArray(data.points) && data.points.length > 0) {
                    this.zones = [{
                        id: 1,
                        name: "Vùng cấm 1",
                        timeout: 30,
                        points: data.points.map(p => [Math.round(p[0]), Math.round(p[1])])
                    }];
                    loaded = true;
                }
            } catch (e) {
                loaded = false;
            }
        }

        if (!loaded || this.zones.length === 0) {
            this.zones = [{
                id: 1,
                name: "Vùng cấm 1",
                timeout: 30,
                points: []
            }];
        }

        this.updateZoneToolbar();
    },

    updateZoneToolbar() {
        const tabsContainer = document.getElementById("roi-zone-tabs");
        const nameInput = document.getElementById("roi-zone-name");
        const timeoutInput = document.getElementById("roi-zone-timeout");
        const deleteBtn = document.getElementById("roi-zone-delete-btn");

        if (!tabsContainer || this.zones.length === 0) return;

        const curZone = this.zones[this.currentZoneIndex] || this.zones[0];

        // Render Tabs
        tabsContainer.innerHTML = this.zones.map((z, idx) => {
            const isActive = idx === this.currentZoneIndex;
            const ptsCount = z.points.length;
            const bg = isActive ? "#2563EB" : "#F1F5F9";
            const color = isActive ? "#FFFFFF" : "#475569";
            const border = isActive ? "1px solid #1D4ED8" : "1px solid #CBD5E1";
            return `
                <button type="button" onclick="roiDrawingTool.switchZone(${idx})"
                    style="background: ${bg}; color: ${color}; border: ${border}; padding: 4px 10px; border-radius: 6px; font-size: 0.8rem; font-weight: 600; cursor: pointer; transition: all 0.15s;">
                    ${z.name} (${ptsCount}đ)
                </button>
            `;
        }).join("");

        if (nameInput) nameInput.value = curZone.name;
        if (timeoutInput) timeoutInput.value = curZone.timeout !== null && curZone.timeout !== undefined ? curZone.timeout : 30;
        if (deleteBtn) {
            deleteBtn.style.display = this.zones.length > 1 ? "inline-block" : "none";
        }
    },

    addNewZone() {
        const curZone = this.zones[this.currentZoneIndex];
        if (curZone && curZone.points.length > 0 && curZone.points.length < 3) {
            alert(`Vùng "${curZone.name}" mới chỉ có ${curZone.points.length} điểm. Vui lòng chấm ít nhất 3 điểm trước khi thêm vùng mới!`);
            return;
        }

        const newId = this.zones.length + 1;
        this.zones.push({
            id: newId,
            name: `Vùng cấm ${newId}`,
            timeout: 30,
            points: []
        });
        this.currentZoneIndex = this.zones.length - 1;
        this.updateZoneToolbar();
        this.drawCanvas();
    },

    deleteCurrentZone() {
        if (this.zones.length <= 1) {
            this.zones[0].points = [];
        } else {
            this.zones.splice(this.currentZoneIndex, 1);
            this.currentZoneIndex = Math.max(0, this.currentZoneIndex - 1);
        }
        this.updateZoneToolbar();
        this.drawCanvas();
    },

    switchZone(idx) {
        if (idx >= 0 && idx < this.zones.length) {
            this.currentZoneIndex = idx;
            this.updateZoneToolbar();
            this.drawCanvas();
        }
    },

    onZoneNameChange(val) {
        if (this.zones[this.currentZoneIndex]) {
            this.zones[this.currentZoneIndex].name = val.trim() || `Vùng cấm ${this.currentZoneIndex + 1}`;
            this.updateZoneToolbar();
        }
    },

    onZoneTimeoutChange(val) {
        if (this.zones[this.currentZoneIndex]) {
            const num = parseInt(val);
            this.zones[this.currentZoneIndex].timeout = !isNaN(num) && num > 0 ? num : 30;
        }
    },

    closeModal() {
        this.modal.style.display = "none";
        this.points = [];
        this.zones = [];
        this.firstFrame = null;
        this.removeCanvasListeners();
    },

    confirmPoints() {
        let resultData = null;

        if (this.isMultiZone) {
            const validZones = this.zones.filter(z => z.points && z.points.length >= 3);
            if (validZones.length === 0) {
                alert("Cần vẽ ít nhất một vùng cấm có tối thiểu 3 điểm!");
                return;
            }

            resultData = {
                units: "reference",
                ref_width: this.canvas.width,
                ref_height: this.canvas.height,
                zones: validZones,
                points: validZones[0].points
            };
        } else {
            if (this.points.length < 3) {
                alert("Cần vẽ ít nhất 3 điểm!");
                return;
            }

            resultData = {
                units: "reference",
                ref_width: this.canvas.width,
                ref_height: this.canvas.height,
                points: this.points
            };
        }

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

            const clientX = e.clientX - rect.left - borderLeft;
            const clientY = e.clientY - rect.top - borderTop;

            const canvasX = clientX * (this.canvas.width / (rect.width - borderLeft * 2));
            const canvasY = clientY * (this.canvas.height / (rect.height - borderTop * 2));

            const refX = Math.round(canvasX);
            const refY = Math.round(canvasY);

            if (this.isMultiZone) {
                if (!this.zones[this.currentZoneIndex]) {
                    this.zones[this.currentZoneIndex] = { id: 1, name: "Vùng cấm 1", timeout: 30, points: [] };
                }
                this.zones[this.currentZoneIndex].points.push([refX, refY]);
                this.updateZoneToolbar();
            } else {
                this.points.push([refX, refY]);
            }
            this.drawCanvas();
        };

        this.canvasContextMenuHandler = (e) => {
            e.preventDefault();
            if (this.isMultiZone) {
                if (this.zones[this.currentZoneIndex]) {
                    this.zones[this.currentZoneIndex].points = [];
                    this.updateZoneToolbar();
                }
            } else {
                this.points = [];
            }
            this.drawCanvas();
        };

        this.keydownHandler = (e) => {
            if (e.key === "Enter") {
                this.confirmPoints();
            } else if (e.key === "Escape") {
                this.closeModal();
            } else if (e.ctrlKey && e.key === "z") {
                e.preventDefault();
                if (this.isMultiZone) {
                    const cur = this.zones[this.currentZoneIndex];
                    if (cur && cur.points.length > 0) {
                        cur.points.pop();
                        this.updateZoneToolbar();
                        this.drawCanvas();
                    }
                } else {
                    if (this.points.length > 0) {
                        this.points.pop();
                        this.drawCanvas();
                    }
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
        if (this.isMultiZone) {
            this.drawMultiZoneOverlay(ctx);
        } else {
            this.drawSingleRoiOverlay(ctx);
        }
    },

    drawSingleRoiOverlay(ctx) {
        if (this.points.length === 0) return;

        ctx.fillStyle = "rgba(0, 255, 0, 0.2)";
        ctx.strokeStyle = "#00ff00";
        ctx.lineWidth = 2;
        ctx.beginPath();

        const startPt = this.points[0];
        ctx.moveTo(startPt[0], startPt[1]);

        for (let i = 1; i < this.points.length; i++) {
            ctx.lineTo(this.points[i][0], this.points[i][1]);
        }
        ctx.closePath();
        ctx.stroke();
        ctx.fill();

        // Draw points
        ctx.fillStyle = "#ff0000";
        for (let point of this.points) {
            ctx.beginPath();
            ctx.arc(point[0], point[1], 5, 0, 2 * Math.PI);
            ctx.fill();
        }

        // Draw point count
        ctx.fillStyle = "#fff";
        ctx.font = "14px Arial";
        ctx.fillText("Điểm: " + this.points.length, 10, 25);
    },

    drawMultiZoneOverlay(ctx) {
        // Vẽ các vùng khác trước (Màu đỏ mờ)
        this.zones.forEach((zone, idx) => {
            const pts = zone.points;
            if (!pts || pts.length === 0) return;

            const isCurrent = (idx === this.currentZoneIndex);
            const strokeColor = isCurrent ? "#22C55E" : "#EF4444";
            const fillColor = isCurrent ? "rgba(34, 197, 94, 0.25)" : "rgba(239, 68, 68, 0.2)";
            const pointColor = isCurrent ? "#16A34A" : "#DC2626";

            ctx.fillStyle = fillColor;
            ctx.strokeStyle = strokeColor;
            ctx.lineWidth = isCurrent ? 2.5 : 1.5;
            ctx.beginPath();

            ctx.moveTo(pts[0][0], pts[0][1]);
            for (let i = 1; i < pts.length; i++) {
                ctx.lineTo(pts[i][0], pts[i][1]);
            }
            if (pts.length >= 3) {
                ctx.closePath();
                ctx.fill();
            }
            ctx.stroke();

            // Vẽ các đỉnh
            ctx.fillStyle = pointColor;
            for (let pt of pts) {
                ctx.beginPath();
                ctx.arc(pt[0], pt[1], isCurrent ? 5 : 3.5, 0, 2 * Math.PI);
                ctx.fill();
            }

            // Nhãn vùng
            if (pts.length >= 1) {
                const lx = pts[0][0];
                const ly = Math.max(16, pts[0][1] - 8);
                ctx.font = isCurrent ? "bold 13px Arial" : "11px Arial";
                ctx.fillStyle = "#000000";
                const label = `${zone.name} (${zone.timeout || 30}s)`;
                ctx.fillText(label, lx + 1, ly + 1);
                ctx.fillStyle = isCurrent ? "#22C55E" : "#EF4444";
                ctx.fillText(label, lx, ly);
            }
        });

        // Info box
        const curZone = this.zones[this.currentZoneIndex];
        const curPts = curZone ? curZone.points.length : 0;
        ctx.fillStyle = "rgba(0, 0, 0, 0.65)";
        ctx.fillRect(8, 8, 220, 30);
        ctx.fillStyle = "#FFFFFF";
        ctx.font = "13px Arial";
        ctx.fillText(`Đang vẽ: ${curZone ? curZone.name : ''} (${curPts} điểm)`, 16, 28);
    }
};

// Initialize when DOM is ready
if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => window.roiDrawingTool.init());
} else {
    window.roiDrawingTool.init();
}
