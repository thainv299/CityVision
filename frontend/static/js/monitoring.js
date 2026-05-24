// Initialize immediately since script loads at end of page (after DOM is ready)
function initMonitoringForm() {
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
    let activeCameraConfig = null;

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

    function renderActiveFeatures(camera) {
        if (!resultSummary) return;
        activeCameraConfig = camera;

        const buildStatusBadge = (enabled) => enabled
            ? `<span style="background: rgba(16, 185, 129, 0.1); color: #10B981; border: 1px solid rgba(16, 185, 129, 0.2); padding: 4px 10px; border-radius: 9999px; font-weight: 700; font-size: 0.75rem;">BẬT</span>`
            : `<span style="background: rgba(244, 67, 54, 0.1); color: #F44336; border: 1px solid rgba(244, 67, 54, 0.2); padding: 4px 10px; border-radius: 9999px; font-weight: 700; font-size: 0.75rem;">TẮT</span>`;

        const isAiEnabled = Boolean(camera.enable_ai);

        resultSummary.innerHTML = `
            <div style="display: flex; justify-content: space-between; align-items: center; padding: 10px 14px; background: var(--bg-main, #f8fafc); border: 1px solid var(--border, #e2e8f0); border-radius: 10px; box-shadow: 0 1px 2px rgba(0,0,0,0.02);">
                <span style="font-weight: 700; font-size: 0.85rem; color: var(--text-main, #0f172a);">Xử lý AI</span>
                ${buildStatusBadge(camera.enable_ai)}
            </div>
            <div style="display: flex; justify-content: space-between; align-items: center; padding: 10px 14px; background: var(--bg-main, #f8fafc); border: 1px solid var(--border, #e2e8f0); border-radius: 10px; box-shadow: 0 1px 2px rgba(0,0,0,0.02); ${!isAiEnabled ? 'opacity: 0.45;' : ''}">
                <span style="font-weight: 600; font-size: 0.8rem; color: #475569;">Phát hiện Tắc nghẽn</span>
                ${buildStatusBadge(isAiEnabled && camera.enable_congestion)}
            </div>
            <div style="display: flex; justify-content: space-between; align-items: center; padding: 10px 14px; background: var(--bg-main, #f8fafc); border: 1px solid var(--border, #e2e8f0); border-radius: 10px; box-shadow: 0 1px 2px rgba(0,0,0,0.02); ${!isAiEnabled ? 'opacity: 0.45;' : ''}">
                <span style="font-weight: 600; font-size: 0.8rem; color: #475569;">Phát hiện Đỗ trái phép</span>
                ${buildStatusBadge(isAiEnabled && camera.enable_illegal_parking)}
            </div>
            <div style="display: flex; justify-content: space-between; align-items: center; padding: 10px 14px; background: var(--bg-main, #f8fafc); border: 1px solid var(--border, #e2e8f0); border-radius: 10px; box-shadow: 0 1px 2px rgba(0,0,0,0.02); ${!isAiEnabled ? 'opacity: 0.45;' : ''}">
                <span style="font-weight: 600; font-size: 0.8rem; color: #475569;">Nhận diện Biển số xe</span>
                ${buildStatusBadge(isAiEnabled && camera.enable_license_plate)}
            </div>
        `;
    }

    function renderStatus(job) {
        if (!statusPanel) return;
        const colorClass = job.status === "running" ? "success" : (job.status === "failed" ? "error" : "warning");
        const statusText = job.status === "running" ? "ĐANG HOẠT ĐỘNG" : (job.status === "failed" ? "THẤT BẠI" : "ĐANG CHỜ");

        statusPanel.innerHTML = `
            <div style="display: flex; align-items: center; gap: 12px;">
                <div class="status-badge ${colorClass}" style="display: inline-flex; align-items: center; gap: 8px; padding: 6px 14px; border-radius: 8px; font-weight: 700; font-size: 0.85rem; box-shadow: 0 2px 4px rgba(0,0,0,0.05);">
                    <span class="dot"></span>
                    ${statusText}
                </div>
                <span style="font-size: 0.9rem; color: var(--text-muted, #64748b); font-weight: 500;">
                    ${job.message || 'Hệ thống đang chạy ổn định.'}
                </span>
            </div>
        `;
    }

    function renderSummary(summary) {
        if (!resultSummary) return;

        if (activeCameraConfig) {
            renderActiveFeatures(activeCameraConfig);
        }

        const statsHtml = `
            <div style="grid-column: 1 / -1; margin-top: 12px; padding-top: 12px; border-top: 1px dashed var(--border, #e2e8f0); display: grid; grid-template-columns: 1fr 1fr; gap: 12px;">
                <div style="padding: 10px 14px; background: var(--bg-main, #f8fafc); border: 1px solid var(--border, #e2e8f0); border-radius: 10px; text-align: center;">
                    <span style="display: block; font-size: 0.75rem; color: #64748b; font-weight: 600; text-transform: uppercase;">Lượt xe qua</span>
                    <strong style="font-size: 1.4rem; color: var(--brand-main, #2563eb); font-weight: 800;">${summary.unique_passed_count ?? "0"}</strong>
                </div>
                <div style="padding: 10px 14px; background: var(--bg-main, #f8fafc); border: 1px solid var(--border, #e2e8f0); border-radius: 10px; text-align: center;">
                    <span style="display: block; font-size: 0.75rem; color: #64748b; font-weight: 600; text-transform: uppercase;">Vi phạm đỗ xe</span>
                    <strong style="font-size: 1.4rem; color: #ef4444; font-weight: 800;">${summary.parking_violation_count ?? "0"}</strong>
                </div>
            </div>
        `;
        resultSummary.insertAdjacentHTML('beforeend', statsHtml);
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

        renderActiveFeatures(camera);

        // Reset tất cả ô checkbox khi khởi động (True)
        const checkBoxes = [
            "show-roi-surveillance-chk",
            "show-roi-parking-chk",
            "show-fps-chk",
            "show-box-person-chk",
            "show-box-bicycle-chk",
            "show-box-motorcycle-chk",
            "show-box-car-chk",
            "show-box-bus-chk",
            "show-box-truck-chk",
            "show-box-plate-chk"
        ];
        checkBoxes.forEach(id => {
            const el = document.getElementById(id);
            if (el) el.checked = true;
        });

        const payload = {
            camera_id: camera.id,
            roi_points: camera.roi_points ? JSON.stringify({ points: camera.roi_points, ...(camera.roi_meta || {}) }) : "",
            no_parking_points: camera.no_parking_points ? JSON.stringify({ points: camera.no_parking_points, ...(camera.no_park_meta || {}) }) : "",
            enable_congestion: camera.enable_congestion ? "on" : "off",
            enable_illegal_parking: camera.enable_illegal_parking ? "on" : "off",
            enable_license_plate: camera.enable_license_plate ? "on" : "off",
            enable_ai: camera.enable_ai ? "on" : "off",
            model_path: camera.model_path || "",
            // Luôn mặc định hiển thị toàn bộ khi mới khởi động
            show_roi_surveillance: "on",
            show_roi_parking: "on",
            show_fps: "on",
            show_box_person: "on",
            show_box_bicycle: "on",
            show_box_motorcycle: "on",
            show_box_car: "on",
            show_box_bus: "on",
            show_box_truck: "on",
            show_box_plate: "on"
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

                    // Update UI (Ghi đè thuộc tính CSS inline để di chuyển nền xanh sang tab được chọn)
                    qualityOptions.forEach(o => {
                        o.classList.remove("active");
                        o.style.background = "rgba(255, 255, 255, 0.05)";
                        o.style.color = "#E2E8F0";
                    });
                    opt.classList.add("active");
                    opt.style.background = "#2563EB";
                    opt.style.color = "#fff";
                    qualityMenu.style.display = "none";

                    window.portalApi.showToast(`Đã chuyển sang chất lượng ${opt.textContent}`, "success");
                } catch (error) {
                    console.error("Lỗi đổi chất lượng:", error);
                    window.portalApi.showToast("Không thể đổi chất lượng lúc này", "error");
                }
            });
        });
    };

    // ── DISPLAY & OVERLAY SETTINGS ──────────────────────────
    const setupDisplaySettings = () => {
        const checkBoxes = [
            { id: "show-roi-surveillance-chk", key: "show_roi_surveillance" },
            { id: "show-roi-parking-chk", key: "show_roi_parking" },
            { id: "show-fps-chk", key: "show_fps" },
            { id: "show-box-person-chk", key: "show_box_person" },
            { id: "show-box-bicycle-chk", key: "show_box_bicycle" },
            { id: "show-box-motorcycle-chk", key: "show_box_motorcycle" },
            { id: "show-box-car-chk", key: "show_box_car" },
            { id: "show-box-bus-chk", key: "show_box_bus" },
            { id: "show-box-truck-chk", key: "show_box_truck" },
            { id: "show-box-plate-chk", key: "show_box_plate" }
        ];

        // 1. Đặt tất cả các checkbox mặc định là checked (True) khi khởi tạo giao diện
        checkBoxes.forEach(item => {
            const el = document.getElementById(item.id);
            if (el) {
                el.checked = true;
            }
        });

        // 2. Đăng ký bộ lắng nghe sự kiện thay đổi của từng checkbox (Không lưu bộ nhớ trình duyệt)
        checkBoxes.forEach(item => {
            const el = document.getElementById(item.id);
            if (el) {
                el.addEventListener("change", async () => {
                    // Nếu có job đang chạy, gửi cập nhật in-memory lập tức sang backend
                    if (currentJobId) {
                        try {
                            const payload = {};
                            payload[item.key] = el.checked;

                            await window.portalApi.post(`/api/test-jobs/${currentJobId}/settings`, payload);
                        } catch (error) {
                            console.error("Lỗi cập nhật cấu hình hiển thị:", error);
                        }
                    }
                });
            }
        });
    };

    setupQualitySettings();
    setupDisplaySettings();

    // ── CAMERA DASHBOARD GRID ──────────────────────────────
    function renderPreviewGrid() {
        if (!previewGrid) return;

        // Hiển thị Skeleton Loading nếu đang tải
        if (allCameras.length === 0 && !previewGrid.dataset.loaded) {
            previewGrid.innerHTML = Array(6).fill(0).map(() => `
                <div class="skeleton" style="height: 380px; border-radius: 16px; width: 100%;"></div>
            `).join("");
            return;
        }

        if (!allCameras.length) {
            previewGrid.innerHTML = `<div class="empty-state">Chưa có camera nào để hiển thị.</div>`;
            return;
        }

        previewGrid.dataset.loaded = "true";

        previewGrid.innerHTML = allCameras.map((camera, index) => {
            const isAiEnabled = Boolean(camera.enable_ai);

            const createToggle = (feature, label, isChecked, isMaster = false) => {
                const isDisabled = !isMaster && !isAiEnabled;
                const checkedStr = (isChecked && (isMaster || isAiEnabled)) ? "checked" : "";
                const disabledStr = isDisabled ? "disabled" : "";
                const opacityStyle = isDisabled ? "opacity: 0.45; pointer-events: none;" : "";
                const highlightBorder = isMaster ? "border-bottom: 2px solid rgba(37, 99, 235, 0.15); margin-bottom: 4px; padding-bottom: 6px;" : "";

                return `
                    <div class="feature-toggle-row" style="display: flex; justify-content: space-between; align-items: center; padding: 6px 4px; border-bottom: 1px solid rgba(0,0,0,0.05); ${highlightBorder} ${opacityStyle}">
                        <span style="font-size: 0.85rem; font-weight: ${isMaster ? '700' : '500'}; color: ${isMaster ? 'var(--brand-main, #2563eb)' : '#475569'};">${label}</span>
                        <label class="switch">
                            <input type="checkbox" data-action="toggle" data-feature="${feature}" data-id="${camera.id}" ${checkedStr} ${disabledStr}>
                            <span class="slider"></span>
                        </label>
                    </div>
                `;
            };

            return `
                <article class="camera-preview-card staggered-item" data-id="${camera.id}" style="border: 1px solid #E2E8F0; border-radius: 16px; overflow: hidden; background: #fff; transition: all 0.3s ease; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05); max-width: 400px; animation-delay: ${index * 0.1}s;">
                    <div class="preview-container" style="position: relative; height: 180px; background: #000; overflow: hidden;">
                        <img src="/api/cameras/${camera.id}/snapshot?ts=${Date.now()}" alt="${camera.name}" class="camera-preview-image" data-camera-id="${camera.id}" style="width: 100%; height: 100%; object-fit: cover; transition: transform 0.5s ease;">
                        <div class="status-overlay" style="position: absolute; top: 12px; left: 12px; z-index: 2;">
                            <span class="badge ${camera.is_active ? "success" : "muted"}" style="box-shadow: 0 4px 12px rgba(0,0,0,0.2); backdrop-filter: blur(8px); padding: 6px 12px; font-weight: 700; font-size: 11px; letter-spacing: 0.05em; display: flex; align-items: center; gap: 4px;">
                                ${camera.is_active ? '<span class="status-live"></span> LIVE' : '● OFFLINE'}
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
                            <div style="display: flex; align-items: center; gap: 6px;">
                                <h3 style="margin: 0; font-size: 1.15rem; font-weight: 800; color: #0F172A;">${camera.name}</h3>
                                ${!isAiEnabled ? '<span style="font-size: 0.65rem; padding: 2px 6px; background: rgba(100, 116, 139, 0.1); border: 1px solid rgba(100, 116, 139, 0.2); border-radius: 4px; color: #64748B; font-weight: 600;">RAW</span>' : ''}
                            </div>
                            <span style="font-size: 10px; color: #94A3B8; font-weight: 700; background: #F1F5F9; padding: 2px 8px; border-radius: 4px;">ID: ${camera.id}</span>
                        </div>
                        
                        <div class="toggles-area" style="background: #F8FAFC; padding: 14px; border-radius: 14px; border: 1px solid #F1F5F9;">
                            ${createToggle("enable_ai", "Xử lý AI", camera.enable_ai, true)}
                            ${createToggle("enable_congestion", "Tắc nghẽn", camera.enable_congestion)}
                            ${createToggle("enable_illegal_parking", "Đỗ trái phép", camera.enable_illegal_parking)}
                            ${createToggle("enable_license_plate", "Biển số xe", camera.enable_license_plate)}
                        </div>
                        
                        <div style="margin-top: 16px; padding-top: 16px; border-top: 1px solid #F1F5F9; display: flex; justify-content: space-between; align-items: center;">
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

        // Tắt hết các tính năng khác khi tắt AI
        let payload = { ...camera, [feature]: value };
        if (feature === "enable_ai" && !value) {
            payload.enable_congestion = false;
            payload.enable_illegal_parking = false;
            payload.enable_license_plate = false;
        }

        try {
            await window.portalApi.put(`/api/cameras/${cameraId}`, payload);
            await loadAllCameras();

            // Hiển thị cấu hình đang bật
            if (activeCameraConfig && activeCameraConfig.id === cameraId) {
                activeCameraConfig = { ...activeCameraConfig, ...payload };
                renderActiveFeatures(activeCameraConfig);
            }
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

document.addEventListener('DOMContentLoaded', initMonitoringForm);
