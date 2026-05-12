// Initialize immediately since script loads at end of page (after DOM is ready)
function initTestVideoForm() {
    const feedback = document.getElementById("test-job-feedback");
    const statusPanel = document.getElementById("job-status-panel");
    const viewerPanel = document.getElementById("viewer-panel");
    const activeCameraName = document.getElementById("active-camera-name");
    const streamOutput = document.getElementById("stream-output");
    const streamOutputNote = document.getElementById("stream-output-note");
    const resultSummary = document.getElementById("result-summary");
    const stopButton = document.getElementById("stop-test-job");
    const previewGrid = document.getElementById("camera-preview-grid");
    const refreshGridBtn = document.getElementById("cameras-refresh-grid");

    let currentJobId = null;
    let pollingHandle = null;
    let allCameras = [];
    let refreshTimer = null;

    // ── JOB MANAGEMENT ──────────────────────────────────────
    function stopPolling() {
        if (pollingHandle) {
            clearInterval(pollingHandle);
            pollingHandle = null;
        }
    }

    function clearStream() {
        if (streamOutput) {
            streamOutput.removeAttribute("src");
            delete streamOutput.dataset.jobId;
        }
        const loader = document.getElementById('stream-loader');
        if (loader) {
            loader.style.display = 'flex';
            const spinner = loader.querySelector('.loader-spinner');
            if (spinner) spinner.style.display = 'block';
        }
    }

    function startStream(jobId, streamUrl) {
        if (!streamOutput || !streamUrl) return;
        streamOutput.dataset.jobId = jobId;
        streamOutput.src = streamUrl;
        const loader = document.getElementById('stream-loader');
        if (loader) loader.style.display = 'none';
    }

    function renderStatus(job) {
        if (!statusPanel) return;
        const colorClass = job.status === "running" ? "success" : (job.status === "failed" ? "error" : "warning");
        statusPanel.innerHTML = `
            <div class="status-badge ${colorClass}" style="margin-bottom: 8px; display: inline-flex; align-items: center; gap: 8px; padding: 6px 12px; border-radius: 6px; font-weight: 600; font-size: 0.9rem;">
                <span class="dot"></span>
                Trạng thái: ${job.status.toUpperCase()}
            </div>
            <p style="margin: 0; font-size: 0.95rem; line-height: 1.5; color: var(--text-main);">${job.message || 'Hệ thống đang hoạt động ổn định.'}</p>
        `;
    }

    function renderSummary(summary) {
        resultSummary.innerHTML = `
            <div style="display: grid; grid-template-columns: 1fr; gap: 8px;">
                <article class="summary-card" style="display: flex; justify-content: space-between; padding: 12px; background: var(--bg-main); border-radius: 8px;">
                    <span class="small">Lượt xe qua</span>
                    <strong>${summary.unique_passed_count ?? "0"}</strong>
                </article>
                <article class="summary-card" style="display: flex; justify-content: space-between; padding: 12px; background: var(--bg-main); border-radius: 8px;">
                    <span class="small">Vi phạm đỗ xe</span>
                    <strong>${summary.parking_violation_count ?? "0"}</strong>
                </article>
            </div>
        `;
    }

    async function pollJob(jobId) {
        try {
            const data = await window.portalApi.get(`/api/test-jobs/${jobId}`);
            const job = data.job;

            if (job.stream_url && streamOutput.dataset.jobId !== job.id) {
                startStream(job.id, job.stream_url);
            }

            // Cập nhật text loading nếu đang chờ
            const loaderText = document.getElementById('stream-loader-text');
            if (loaderText) {
                if (job.status === "queued") loaderText.textContent = "Đang chờ đến lượt xử lý AI...";
                else if (job.status === "running" && !job.stream_url) loaderText.textContent = "Đang khởi tạo mô hình & luồng dữ liệu...";
            }

            renderStatus(job);

            if (job.status !== "queued" && job.status !== "running") {
                stopPolling();
                if (job.status === "completed") {
                    renderSummary(job.summary || {});
                } else if (job.status === "failed") {
                    const loader = document.getElementById('stream-loader');
                    if (loader) {
                        loader.style.display = 'flex';
                        const spinner = loader.querySelector('.loader-spinner');
                        if (spinner) spinner.style.display = 'none';
                        if (loaderText) loaderText.textContent = "Lỗi: " + (job.error || "Không thể kết nối");
                    }
                }
            }
        } catch (error) {
            stopPolling();
        }
    }

    async function startMonitoring(camera) {
        stopPolling();
        clearStream();

        viewerPanel.hidden = false;
        viewerPanel.scrollIntoView({ behavior: "smooth" });
        activeCameraName.textContent = `Camera: ${camera.name}`;
        
        const loader = document.getElementById('stream-loader');
        const loaderText = document.getElementById('stream-loader-text');
        if (loader) loader.style.display = 'flex';
        if (loaderText) loaderText.textContent = "Đang kết nối luồng AI...";
        
        resultSummary.innerHTML = "";

        const payload = {
            camera_id: camera.id,
            roi_points: camera.roi_points ? JSON.stringify({ points: camera.roi_points, ...(camera.roi_meta || {}) }) : "",
            no_parking_points: camera.no_parking_points ? JSON.stringify({ points: camera.no_parking_points, ...(camera.no_park_meta || {}) }) : "",
            enable_congestion: camera.enable_congestion ? "on" : "off",
            enable_illegal_parking: camera.enable_illegal_parking ? "on" : "off",
            enable_license_plate: camera.enable_license_plate ? "on" : "off",
            model_path: camera.model_path || ""
        };

        try {
            const fd = new FormData();
            for (const key in payload) fd.append(key, payload[key]);

            const data = await window.portalApi.submitForm("/api/test-jobs", fd);
            const job = data.job;
            currentJobId = job.id;

            pollingHandle = setInterval(() => pollJob(job.id), 3000);
            pollJob(job.id);
        } catch (error) {
            window.portalApi.showNotice(feedback, error.message, "error");
        }
    }

    if (stopButton) {
        stopButton.addEventListener("click", async () => {
            if (!currentJobId) return;
            stopButton.disabled = true;
            try {
                await window.portalApi.post(`/api/test-jobs/${currentJobId}/stop`);
                stopPolling();
                const loader = document.getElementById('stream-loader');
                const loaderText = document.getElementById('stream-loader-text');
                const spinner = document.querySelector('.loader-spinner');
                if (loader) loader.style.display = 'flex';
                if (spinner) spinner.style.display = 'none';
                if (loaderText) loaderText.textContent = "Đã dừng giám sát.";
                streamOutput.src = "";
            } catch (error) {
                console.error("Lỗi dừng job:", error);
            } finally {
                stopButton.disabled = false;
            }
        });
    }

    // ── QUALITY SETTINGS ────────────────────────────────────
    const setupQualitySettings = () => {
        const qualityGearBtn = document.getElementById("quality-gear-btn");
        const qualityMenu = document.getElementById("quality-menu");
        const qualityOptions = document.querySelectorAll(".quality-option");

        if (!qualityGearBtn || !qualityMenu) {
            console.warn("[UI] Không tìm thấy nút cài đặt chất lượng.");
            return;
        }

        qualityGearBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            const isHidden = qualityMenu.style.display === "none";
            qualityMenu.style.display = isHidden ? "flex" : "none";
        });

        // Đóng menu khi click ra ngoài
        document.addEventListener("click", () => {
            if (qualityMenu) qualityMenu.style.display = "none";
        });

        qualityMenu.addEventListener("click", (e) => e.stopPropagation());

        qualityOptions.forEach(opt => {
            opt.addEventListener("click", async () => {
                if (!currentJobId) {
                    window.portalApi.showToast("Vui lòng khởi động camera trước", "warning");
                    return;
                }
                const quality = opt.dataset.quality;
                
                try {
                    await window.portalApi.post(`/api/test-jobs/${currentJobId}/quality`, { quality });
                    
                    // Update UI
                    qualityOptions.forEach(o => o.classList.remove("active"));
                    opt.classList.add("active");
                    qualityMenu.style.display = "none";
                    
                    window.portalApi.showToast(`Đã chuyển sang chất lượng ${opt.textContent}`, "success");
                } catch (error) {
                    console.error("Lỗi đổi chất lượng:", error);
                    window.portalApi.showToast("Không thể đổi chất lượng lúc này", "error");
                }
            });
        });
    };

    setupQualitySettings();

    // ── CAMERA DASHBOARD GRID ──────────────────────────────
    function renderPreviewGrid() {
        if (!previewGrid) return;
        if (!allCameras.length) {
            previewGrid.innerHTML = `<div class="empty-state">Chưa có camera nào để hiển thị.</div>`;
            return;
        }

        previewGrid.innerHTML = allCameras.map((camera) => {
            const createToggle = (feature, label, isChecked) => `
                <div class="feature-toggle-row" style="padding: 8px 4px; border-bottom: 1px solid rgba(0,0,0,0.05);">
                    <span style="font-size: 0.85rem; font-weight: 500; color: #475569;">${label}</span>
                    <label class="switch">
                        <input type="checkbox" data-action="toggle" data-feature="${feature}" data-id="${camera.id}" ${isChecked ? "checked" : ""}>
                        <span class="slider"></span>
                    </label>
                </div>
            `;

            return `
                <article class="camera-preview-card" data-id="${camera.id}" style="border: 1px solid #E2E8F0; border-radius: 16px; overflow: hidden; background: #fff; transition: all 0.3s ease; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05); max-width: 400px;">
                    <div class="preview-container" style="position: relative; height: 180px; background: #000; overflow: hidden;">
                        <img src="/api/cameras/${camera.id}/snapshot?ts=${Date.now()}" alt="${camera.name}" class="camera-preview-image" data-camera-id="${camera.id}" style="width: 100%; height: 100%; object-fit: cover; transition: transform 0.5s ease;">
                        <div class="status-overlay" style="position: absolute; top: 12px; left: 12px; z-index: 2;">
                            <span class="badge ${camera.is_active ? "success" : "muted"}" style="box-shadow: 0 4px 12px rgba(0,0,0,0.2); backdrop-filter: blur(8px); padding: 6px 12px; font-weight: 700; font-size: 11px; letter-spacing: 0.05em;">
                                ${camera.is_active ? "● LIVE" : "● OFFLINE"}
                            </span>
                        </div>
                        <div class="model-badge" style="position: absolute; bottom: 12px; right: 12px; background: rgba(15, 23, 42, 0.7); color: #fff; padding: 4px 10px; border-radius: 6px; font-size: 10px; font-weight: 700; backdrop-filter: blur(6px); border: 1px solid rgba(255,255,255,0.1);">
                            ${camera.model_path ? camera.model_path.split(/[\\/]/).pop() : "YOLO26"}
                        </div>
                        <div class="play-hint" style="position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); width: 56px; height: 56px; background: var(--brand-blue); color: #fff; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 1.4rem; opacity: 0; transition: all 0.4s cubic-bezier(0.4, 0, 0.2, 1); box-shadow: 0 0 30px rgba(37, 99, 235, 0.5);">
                            ▶
                        </div>
                    </div>
                    <div class="camera-body" style="padding: 20px;">
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
                            <h3 style="margin: 0; font-size: 1.15rem; font-weight: 800; color: #0F172A;">${camera.name}</h3>
                            <span style="font-size: 10px; color: #94A3B8; font-weight: 700; background: #F1F5F9; padding: 2px 8px; border-radius: 4px;">ID: ${camera.id}</span>
                        </div>
                        
                        <div class="toggles-area" style="background: #F8FAFC; padding: 14px; border-radius: 14px; border: 1px solid #F1F5F9;">
                            ${createToggle("enable_congestion", "Tắc nghẽn", camera.enable_congestion)}
                            ${createToggle("enable_illegal_parking", "Đỗ trái phép", camera.enable_illegal_parking)}
                            ${createToggle("enable_license_plate", "Biển số xe", camera.enable_license_plate)}
                        </div>
                        
                        <div style="margin-top: 16px; padding-top: 16px; border-top: 1px solid #F1F5F9; display: flex; justify-content: space-between; align-items: center;">
                             <span style="font-size: 11px; color: #64748B; font-weight: 500;">Bấm vào ảnh để xem chi tiết</span>
                             <div class="switch-row" style="display: flex; align-items: center; gap: 10px;">
                                <span style="font-size: 11px; font-weight: 800; color: ${camera.is_active ? '#10B981' : '#94A3B8'}">
                                    ${camera.is_active ? 'KÍCH HOẠT' : 'TẠM TẮT'}
                                </span>
                                <label class="switch">
                                    <input type="checkbox" data-action="toggle" data-feature="is_active" data-id="${camera.id}" ${camera.is_active ? "checked" : ""}>
                                    <span class="slider"></span>
                                </label>
                             </div>
                        </div>
                    </div>
                </article>
            `;
        }).join("");
    }

    async function loadAllCameras() {
        try {
            const data = await window.portalApi.get("/api/cameras");
            allCameras = data.cameras || [];
            renderPreviewGrid();
        } catch (error) {
            console.error("Lỗi tải camera grid:", error);
        }
    }

    async function updateCameraFeature(cameraId, feature, value) {
        const camera = allCameras.find(c => c.id === cameraId);
        if (!camera) return;
        const payload = { ...camera, [feature]: value };
        try {
            await window.portalApi.put(`/api/cameras/${cameraId}`, payload);
            await loadAllCameras();
        } catch (error) {
            window.portalApi.showNotice(feedback, "Lỗi cập nhật camera: " + error.message, "error");
        }
    }

    if (previewGrid) {
        previewGrid.addEventListener("change", async (e) => {
            const toggle = e.target.closest("input[data-action='toggle']");
            if (toggle) {
                const id = parseInt(toggle.dataset.id);
                const feature = toggle.dataset.feature;
                const value = toggle.checked;
                await updateCameraFeature(id, feature, value);
            }
        });

        previewGrid.addEventListener("click", async (e) => {
            // Nếu click vào switch hoặc slider thì bỏ qua (để 'change' xử lý)
            if (e.target.closest(".switch") || e.target.closest(".slider")) {
                return;
            }

            const card = e.target.closest(".camera-preview-card");
            if (card) {
                const id = parseInt(card.dataset.id);
                const camera = allCameras.find(c => c.id === id);
                if (camera) {
                    startMonitoring(camera);
                }
            }
        });
    }

    if (refreshGridBtn) {
        refreshGridBtn.addEventListener("click", loadAllCameras);
    }

    function refreshSnapshots() {
        if (!previewGrid) return;
        previewGrid.querySelectorAll("img[data-camera-id]").forEach(img => {
            img.src = `/api/cameras/${img.dataset.cameraId}/snapshot?ts=${Date.now()}`;
        });
    }

    // Fullscreen logic
    const fsContainer = document.getElementById("stream-viewer-wrapper");
    const fsEnterBtn = document.getElementById("fullscreen-btn");
    const fsExitBtn = document.getElementById("fullscreen-exit-btn");

    if (fsEnterBtn && fsContainer) {
        fsEnterBtn.addEventListener("click", () => {
            if (fsContainer.requestFullscreen) {
                fsContainer.requestFullscreen();
            } else if (fsContainer.webkitRequestFullscreen) {
                fsContainer.webkitRequestFullscreen();
            } else if (fsContainer.mozRequestFullScreen) {
                fsContainer.mozRequestFullScreen();
            } else if (fsContainer.msRequestFullscreen) {
                fsContainer.msRequestFullscreen();
            }
        });
    }
    if (fsExitBtn) {
        fsExitBtn.addEventListener("click", () => {
            if (document.exitFullscreen) {
                document.exitFullscreen();
            } else if (document.webkitExitFullscreen) {
                document.webkitExitFullscreen();
            } else if (document.mozCancelFullScreen) {
                document.mozCancelFullScreen();
            }
        });
    }

    // Khởi động
    loadAllCameras();
    refreshTimer = setInterval(refreshSnapshots, 10000); // 10s refresh for snapshots
}

document.addEventListener('DOMContentLoaded', initTestVideoForm);
