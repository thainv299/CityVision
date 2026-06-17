// ═══════════════════════════════════════════════════════════════
// CityVision AI — Multi-view Monitoring Controller
// ═══════════════════════════════════════════════════════════════

async function startWebRTCPlayer(videoElement, cameraId) {
    const streamPath = `live_camera_${cameraId}`;
    const hostname = window.location.hostname || "localhost";
    const isLocal = hostname === "localhost" || hostname === "127.0.0.1";
    const url = isLocal ? `http://${hostname}:8889/${streamPath}/whep` : `/api/webrtc/whep/${cameraId}`;

    console.log("[WebRTC] Khởi động trình phát WebRTC tại:", url);
    const pc = new RTCPeerConnection({
        iceServers: [{ urls: "stun:stun.l.google.com:19302" }]
    });

    const remoteStream = new MediaStream();
    videoElement.srcObject = remoteStream;

    pc.ontrack = (event) => {
        console.log("[WebRTC] Đã nhận được track hình ảnh:", event.track);
        if (event.track) {
            remoteStream.addTrack(event.track);
            videoElement.play().catch(err => {
                console.warn("[WebRTC] Playback failed or was blocked by browser autoplay policy:", err);
            });
        }
    };

    pc.addTransceiver("video", { direction: "recvonly" });

    // Tạo Promise đợi kết nối ICE thành công thực tế để đảm bảo không bị đen màn hình
    return new Promise(async (resolve, reject) => {
        let isSettled = false;

        const cleanup = () => {
            pc.oniceconnectionstatechange = null;
        };

        pc.oniceconnectionstatechange = () => {
            console.log("[WebRTC] ICE Connection State:", pc.iceConnectionState);
            if (pc.iceConnectionState === "connected" || pc.iceConnectionState === "completed") {
                if (!isSettled) {
                    isSettled = true;
                    cleanup();
                    resolve(pc);
                }
            } else if (pc.iceConnectionState === "failed") {
                if (!isSettled) {
                    isSettled = true;
                    cleanup();
                    pc.close();
                    reject(new Error("Kết nối ICE WebRTC thất bại (mDNS/UDP bị chặn)"));
                }
            }
        };

        // Giới hạn thời gian kết nối tối đa 2 giây (Timeout cho mạng local/localhost cực nhanh)
        const timeoutId = setTimeout(() => {
            if (!isSettled) {
                isSettled = true;
                cleanup();
                pc.close();
                reject(new Error("Timeout kết nối ICE WebRTC (2s)"));
            }
        }, 2000);

        try {
            const offer = await pc.createOffer();
            await pc.setLocalDescription(offer);

            const response = await fetch(url, {
                method: "POST",
                headers: {
                    "Content-Type": "application/sdp"
                },
                body: offer.sdp
            });

            if (!response.ok) {
                throw new Error(`WHEP endpoint returned error: ${response.status}`);
            }

            const answerSdp = await response.text();
            await pc.setRemoteDescription(new RTCSessionDescription({
                type: "answer",
                sdp: answerSdp
            }));

            console.log("[WebRTC] SDP Offer/Answer thành công, đang đợi bắt tay ICE...");
        } catch (e) {
            clearTimeout(timeoutId);
            if (!isSettled) {
                isSettled = true;
                cleanup();
                pc.close();
                reject(e);
            }
        }
    });
}


function initMonitoringForm() {
    // ── DOM ELEMENTS ────────────────────────────────────────
    const feedback = document.getElementById("test-job-feedback");
    const statusPanel = document.getElementById("job-status-panel");
    const viewerPanel = document.getElementById("viewer-panel");
    const activeCameraName = document.getElementById("active-camera-name");
    const resultSummary = document.getElementById("result-summary");
    const previewGrid = document.getElementById("camera-preview-grid");
    const refreshGridBtn = document.getElementById("cameras-refresh-grid");
    const multiviewGrid = document.getElementById("multiview-grid");
    const singleViewInfo = document.getElementById("single-view-info");

    // ── STATE ───────────────────────────────────────────────
    let allCameras = [];
    let refreshTimer = null;
    let activeCameraConfig = null;

    // Multi-view state
    let currentLayout = '1x1'; // '1x1' | '2x2' | '3x3'
    const LAYOUT_SIZES = { '1x1': 1, '2x2': 4, '3x3': 9 };

    // Each slot: { index, cameraId, camera, jobId, pollingHandle, streamUrl, state: 'empty'|'loading'|'streaming'|'error' }
    let slots = [];
    let pickerTargetSlot = null; // Which slot index the picker is targeting

    // ── SLOT MANAGEMENT ────────────────────────────────────
    function getSlotCount() {
        return LAYOUT_SIZES[currentLayout] || 1;
    }

    function getActiveSlots() {
        return slots.filter(s => s.state === 'streaming' || s.state === 'loading');
    }

    function getUsedCameraIds() {
        return slots.filter(s => s.cameraId).map(s => s.cameraId);
    }

    function findFirstEmptySlot() {
        return slots.find(s => s.state === 'empty');
    }

    function updateStreamCounter() {
        const counter = document.getElementById('mv-stream-counter');
        const countEl = document.getElementById('mv-active-count');
        const activeCount = getActiveSlots().length;
        if (counter && countEl) {
            countEl.textContent = activeCount;
            counter.style.display = activeCount > 0 ? 'inline-flex' : 'none';
        }
    }

    // ── RENDER SLOTS ───────────────────────────────────────
    function renderSlots() {
        if (!multiviewGrid) return;
        const count = getSlotCount();

        // Update grid class
        multiviewGrid.className = `multiview-grid layout-${currentLayout}`;

        // Ensure slots array matches count
        while (slots.length < count) {
            slots.push({ index: slots.length, cameraId: null, camera: null, jobId: null, pollingHandle: null, streamUrl: null, state: 'empty', domElement: null });
        }

        // Build HTML for each slot
        const activeDomElements = [];

        for (let i = 0; i < count; i++) {
            const slot = slots[i];
            let slotEl = slot.domElement;
            let stateChanged = false;

            if (!slotEl) {
                slotEl = document.createElement('div');
                slot.domElement = slotEl;
                stateChanged = true;
            }

            // Check if class state has changed
            const expectedClass = `mv-slot ${slot.state}`;
            if (slotEl.className !== expectedClass) {
                slotEl.className = expectedClass;
                stateChanged = true;
            }
            slotEl.dataset.slotIndex = i;

            if (stateChanged) {
                if (slot.state === 'empty') {
                    slotEl.innerHTML = `
                        <div class="mv-slot-empty">
                            <div class="mv-plus-icon">+</div>
                            <span>Chọn camera</span>
                        </div>
                    `;
                    // Clone node to remove old click event listeners cleanly
                    const newSlotEl = slotEl.cloneNode(true);
                    if (slotEl.parentNode) {
                        slotEl.parentNode.replaceChild(newSlotEl, slotEl);
                    }
                    slotEl = newSlotEl;
                    slot.domElement = slotEl;
                    slotEl.addEventListener('click', () => openCameraPicker(i));
                } else if (slot.state === 'loading') {
                    slotEl.innerHTML = `
                        <div class="mv-slot-loader">
                            <div class="mv-spinner"></div>
                            <p>Đang kết nối...</p>
                        </div>
                        <div class="mv-slot-status">
                            <span class="mv-cam-label">${slot.camera ? slot.camera.name : 'Camera'}</span>
                        </div>
                    `;
                } else if (slot.state === 'streaming') {
                    slotEl.innerHTML = `
                        <video class="mv-stream-video" autoplay playsinline muted style="width: 100%; height: 100%; object-fit: contain;"></video>
                        <img class="mv-stream-img" data-src="${slot.streamUrl}" alt="${slot.camera ? slot.camera.name : 'Stream'}" style="display: none; width: 100%; height: 100%; object-fit: contain;">
                        <div class="mv-slot-overlay">
                            <div class="mv-slot-cam-name">
                                <span class="mv-live-dot"></span>
                                ${slot.camera ? slot.camera.name : 'Camera'}
                            </div>
                            <div class="mv-slot-actions">
                                <button type="button" class="mv-slot-btn close-btn" data-action="close" data-slot="${i}" title="Đóng">✕</button>
                            </div>
                        </div>
                        <div class="mv-slot-status">
                            <span class="mv-cam-label">${slot.camera ? slot.camera.name : 'Camera'}</span>
                            <span class="mv-slot-badge live" style="background: #2563EB;">WebRTC</span>
                        </div>
                    `;

                    const videoEl = slotEl.querySelector('video');
                    const imgEl = slotEl.querySelector('img');
                    const badgeEl = slotEl.querySelector('.mv-slot-badge');

                    // Try WebRTC with retries to give MediaMTX time to ingest the new stream
                    let retryCount = 0;
                    const maxRetries = 4;
                    const attemptWebRTC = () => {
                        startWebRTCPlayer(videoEl, slot.cameraId).then(pc => {
                            slot.rtcPeerConnection = pc;
                            if (badgeEl) {
                                badgeEl.textContent = 'WebRTC';
                                badgeEl.style.background = '#2563EB';
                            }
                        }).catch(err => {
                            const isIceError = err.message && (err.message.includes("ICE") || err.message.includes("Timeout"));

                            if (!isIceError && retryCount < maxRetries) {
                                retryCount++;
                                console.log(`[WebRTC] Luồng chưa sẵn sàng, đang thử lại (Lần ${retryCount}/${maxRetries})...`);
                                setTimeout(attemptWebRTC, 1500); // Thử lại sau 1.5s
                            } else {
                                console.log(`[WebRTC] Gặp lỗi: ${err.message}. Dự phòng về MJPEG Stream...`);
                                if (videoEl) videoEl.style.display = 'none';
                                if (imgEl) {
                                    imgEl.src = imgEl.dataset.src; // Nạp luồng MJPEG thực tế khi thực sự có nhu cầu fallback
                                    imgEl.style.display = 'block';
                                }
                                if (badgeEl) {
                                    badgeEl.textContent = 'MJPEG';
                                    badgeEl.style.background = '#EF4444';
                                }
                            }
                        });
                    };
                    attemptWebRTC();

                    // Click-to-close and action buttons
                    const closeBtn = slotEl.querySelector('[data-action="close"]');
                    if (closeBtn) {
                        closeBtn.addEventListener('click', (e) => {
                            e.stopPropagation();
                            removeSlot(i);
                        });
                    }
                } else if (slot.state === 'error') {
                    slotEl.innerHTML = `
                        <div class="mv-slot-loader" style="background: rgba(239,68,68,0.1);">
                            <p style="color: #f87171;">⚠ Lỗi kết nối</p>
                        </div>
                        <div class="mv-slot-status">
                            <span class="mv-cam-label">${slot.camera ? slot.camera.name : 'Camera'}</span>
                        </div>
                    `;
                    const newSlotEl = slotEl.cloneNode(true);
                    if (slotEl.parentNode) {
                        slotEl.parentNode.replaceChild(newSlotEl, slotEl);
                    }
                    slotEl = newSlotEl;
                    slot.domElement = slotEl;
                    slotEl.addEventListener('click', () => {
                        removeSlot(i);
                        openCameraPicker(i);
                    });
                }
            }

            activeDomElements.push(slotEl);
        }

        // Clean re-append and sort nodes in grid without resetting existing player states
        multiviewGrid.innerHTML = '';
        activeDomElements.forEach(el => multiviewGrid.appendChild(el));

        // Show/hide single-view info panel (only in 1x1)
        if (singleViewInfo) {
            singleViewInfo.style.display = currentLayout === '1x1' ? '' : 'none';
        }

        updateStreamCounter();
    }

    // ── ASSIGN CAMERA TO SLOT ──────────────────────────────
    async function assignCameraToSlot(slotIndex, camera) {
        const index = parseInt(slotIndex);
        if (isNaN(index) || index >= slots.length) {
            console.warn("[CityVision] Invalid slot index or out of bounds:", slotIndex);
            return;
        }

        const slot = slots[index];
        if (!slot) {
            console.warn("[CityVision] Slot at index is undefined:", index);
            return;
        }

        // Clean up existing job in this slot
        if (slot.jobId) {
            await stopSlotJob(index);
        }

        slot.cameraId = camera.id;
        slot.camera = camera;
        slot.state = 'loading';
        renderSlots();

        // Build payload
        const payload = {
            camera_id: camera.id,
            roi_points: camera.roi_points ? JSON.stringify({ points: camera.roi_points, ...(camera.roi_meta || {}) }) : "",
            no_parking_points: camera.no_parking_points ? JSON.stringify({ points: camera.no_parking_points, ...(camera.no_park_meta || {}) }) : "",
            enable_congestion: camera.enable_congestion ? "on" : "off",
            enable_illegal_parking: camera.enable_illegal_parking ? "on" : "off",
            enable_license_plate: camera.enable_license_plate ? "on" : "off",
            enable_ai: camera.enable_ai ? "on" : "off",
            model_path: camera.model_path || "",
            show_roi_surveillance: "on",
            show_roi_parking: "on",
            show_fps: "on",
            show_box_person: "on",
            show_box_bicycle: "on",
            show_box_motorcycle: "on",
            show_box_car: "on",
            show_box_bus: "on",
            show_box_truck: "on",
            show_box_plate: "on",
            show_label: "on"
        };

        try {
            const fd = new FormData();
            for (const key in payload) fd.append(key, payload[key]);

            const data = await window.portalApi.submitForm("/api/test-jobs", fd);
            const job = data.job;
            slot.jobId = job.id;

            // Start polling (tần suất 1s/lần thay vì 3s/lần để hiển thị luồng tức thì)
            slot.pollingHandle = setInterval(() => pollSlotJob(index), 1000);
            pollSlotJob(index);

            // In 1x1 mode, update the info panel
            if (currentLayout === '1x1') {
                activeCameraConfig = camera;
                activeCameraName.textContent = `Camera: ${camera.name}`;
                renderActiveFeatures(camera);
            }

            // Auto-adjust quality for multi-view
            if (currentLayout !== '1x1') {
                const quality = currentLayout === '2x2' ? 'medium' : 'low';
                setTimeout(async () => {
                    try {
                        await window.portalApi.post(`/api/test-jobs/${job.id}/quality`, { quality });
                    } catch (e) { /* ignore */ }
                }, 2000);
            }
        } catch (error) {
            slot.state = 'error';
            renderSlots();
            if (window.portalApi.showToast) {
                window.portalApi.showToast(`Lỗi khởi tạo camera: ${error.message}`, 'error');
            }
        }
    }

    async function pollSlotJob(slotIndex) {
        const slot = slots[slotIndex];
        if (!slot || !slot.jobId) return;

        try {
            const data = await window.portalApi.get(`/api/test-jobs/${slot.jobId}`);
            const job = data.job;

            // Chỉ hiển thị 'streaming' khi luồng thực tế đã bắt đầu xử lý ảnh và đẩy RTSP thành công lên MediaMTX
            const isProcessing = job.progress && (job.progress.phase === 'running_detection' || job.progress.processed_frames > 0);

            if (job.stream_url && isProcessing && slot.state !== 'streaming') {
                slot.streamUrl = job.stream_url;
                slot.state = 'streaming';
                renderSlots();
            }

            // In 1x1 mode, also update status panel
            if (currentLayout === '1x1' && slotIndex === 0) {
                renderStatus(job);
            }

            if (job.status !== 'queued' && job.status !== 'running') {
                clearInterval(slot.pollingHandle);
                slot.pollingHandle = null;

                if (job.status === 'completed' && currentLayout === '1x1') {
                    renderSummary(job.summary || {});
                } else if (job.status === 'failed') {
                    slot.state = 'error';
                    renderSlots();
                }
            }
        } catch (error) {
            clearInterval(slot.pollingHandle);
            slot.pollingHandle = null;
            slot.state = 'error';
            renderSlots();
        }
    }

    async function stopSlotJob(slotIndex) {
        const slot = slots[slotIndex];
        if (!slot) return;

        if (slot.rtcPeerConnection) {
            try {
                slot.rtcPeerConnection.close();
                console.log("[WebRTC] Đã giải phóng kết nối cho slot:", slotIndex);
            } catch (e) {
                console.error("[WebRTC] Lỗi đóng kết nối:", e);
            }
            slot.rtcPeerConnection = null;
        }

        if (slot.pollingHandle) {
            clearInterval(slot.pollingHandle);
            slot.pollingHandle = null;
        }

        if (slot.jobId) {
            try {
                await window.portalApi.post(`/api/test-jobs/${slot.jobId}/stop`);
            } catch (e) { /* ignore */ }
        }
    }

    function removeSlot(slotIndex) {
        const slot = slots[slotIndex];
        if (!slot) return;

        stopSlotJob(slotIndex);

        slot.cameraId = null;
        slot.camera = null;
        slot.jobId = null;
        slot.streamUrl = null;
        slot.state = 'empty';
        renderSlots();
    }

    // ── STOP ALL ───────────────────────────────────────────
    async function stopAllSlots() {
        const promises = slots.map((slot, i) => {
            if (slot.state === 'streaming' || slot.state === 'loading') {
                return stopSlotJob(i).then(() => {
                    slot.cameraId = null;
                    slot.camera = null;
                    slot.jobId = null;
                    slot.streamUrl = null;
                    slot.state = 'empty';
                });
            }
            return Promise.resolve();
        });
        await Promise.all(promises);
        renderSlots();
    }

    const stopAllBtn = document.getElementById('mv-stop-all-btn');
    if (stopAllBtn) {
        stopAllBtn.addEventListener('click', stopAllSlots);
    }

    // ── LAYOUT SWITCHING ───────────────────────────────────
    function setLayout(layout) {
        if (layout === currentLayout) return;
        const oldCount = getSlotCount();
        currentLayout = layout;
        const newCount = getSlotCount();

        // Update toolbar button states
        document.querySelectorAll('.mv-layout-btn').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.layout === layout);
        });

        // If shrinking, stop jobs in excess slots
        if (newCount < oldCount) {
            for (let i = newCount; i < oldCount; i++) {
                if (slots[i] && (slots[i].state === 'streaming' || slots[i].state === 'loading')) {
                    removeSlot(i);
                }
            }
            slots.length = newCount;
        }

        // Auto-adjust quality for existing streams
        const quality = layout === '1x1' ? 'high' : (layout === '2x2' ? 'medium' : 'low');
        slots.forEach(slot => {
            if (slot.jobId && slot.state === 'streaming') {
                window.portalApi.post(`/api/test-jobs/${slot.jobId}/quality`, { quality }).catch(() => { });
            }
        });

        // Update header text
        const labels = { '1x1': 'Giám sát Camera', '2x2': 'Multi-view 2×2', '3x3': 'Multi-view 3×3' };
        if (activeCameraName && currentLayout !== '1x1') {
            activeCameraName.textContent = labels[layout];
        }

        renderSlots();
    }

    // Bind layout buttons
    document.querySelectorAll('.mv-layout-btn').forEach(btn => {
        btn.addEventListener('click', () => setLayout(btn.dataset.layout));
    });

    // ── CAMERA PICKER ──────────────────────────────────────
    const pickerOverlay = document.getElementById('mv-camera-picker-overlay');
    const pickerList = document.getElementById('mv-picker-list');
    const pickerSearchInput = document.getElementById('mv-picker-search-input');
    const pickerCloseBtn = document.getElementById('mv-picker-close');

    function openCameraPicker(slotIndex) {
        pickerTargetSlot = slotIndex;
        renderPickerList('');
        if (pickerOverlay) pickerOverlay.style.display = 'flex';
        if (pickerSearchInput) {
            pickerSearchInput.value = '';
            setTimeout(() => pickerSearchInput.focus(), 100);
        }
    }

    function closeCameraPicker() {
        if (pickerOverlay) pickerOverlay.style.display = 'none';
        pickerTargetSlot = null;
    }

    function renderPickerList(searchQuery) {
        if (!pickerList) return;
        const usedIds = getUsedCameraIds();
        const query = searchQuery.toLowerCase().trim();

        let filtered = allCameras;
        if (query) {
            filtered = allCameras.filter(c =>
                c.name.toLowerCase().includes(query) ||
                String(c.id).includes(query)
            );
        }

        if (filtered.length === 0) {
            pickerList.innerHTML = '<div class="mv-picker-empty">Không tìm thấy camera nào.</div>';
            return;
        }

        pickerList.innerHTML = filtered.map(camera => {
            const isUsed = usedIds.includes(camera.id);
            return `
                <div class="mv-picker-item ${isUsed ? 'disabled' : ''}" data-camera-id="${camera.id}">
                    <div class="mv-picker-item-thumb">
                        <img src="/api/cameras/${camera.id}/snapshot?ts=${Date.now()}" alt="${camera.name}" onerror="this.style.display='none'">
                    </div>
                    <div class="mv-picker-item-info">
                        <div class="mv-picker-name">${camera.name}</div>
                        <div class="mv-picker-detail">
                            <span class="mv-picker-status-dot ${camera.is_active ? 'online' : 'offline'}"></span>
                            ${camera.is_active ? 'Đang hoạt động' : 'Không hoạt động'}
                            · ID: ${camera.id}
                        </div>
                    </div>
                    ${isUsed ? '<span class="mv-picker-used-badge">Đang dùng</span>' : ''}
                </div>
            `;
        }).join('');

        // Bind click events
        pickerList.querySelectorAll('.mv-picker-item:not(.disabled)').forEach(item => {
            item.addEventListener('click', () => {
                const cameraId = parseInt(item.dataset.cameraId);
                const camera = allCameras.find(c => c.id === cameraId);
                console.log("[CityVision] Picker selected camera ID:", cameraId, "found camera:", camera, "target slot:", pickerTargetSlot);
                if (camera && pickerTargetSlot !== null) {
                    const targetSlot = pickerTargetSlot;
                    closeCameraPicker();
                    assignCameraToSlot(targetSlot, camera);
                } else {
                    console.warn("[CityVision] Cannot assign slot. Camera:", camera, "target slot:", pickerTargetSlot);
                }
            });
        });
    }

    if (pickerCloseBtn) {
        pickerCloseBtn.addEventListener('click', closeCameraPicker);
    }
    if (pickerOverlay) {
        pickerOverlay.addEventListener('click', (e) => {
            if (e.target === pickerOverlay) closeCameraPicker();
        });
    }
    if (pickerSearchInput) {
        pickerSearchInput.addEventListener('input', () => {
            renderPickerList(pickerSearchInput.value);
        });
    }

    // ESC to close picker
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && pickerOverlay && pickerOverlay.style.display === 'flex') {
            closeCameraPicker();
        }
    });

    // ── START MONITORING (backward compat + multi-view) ────
    function startMonitoring(camera) {
        viewerPanel.hidden = false;
        viewerPanel.scrollIntoView({ behavior: "smooth" });

        if (currentLayout === '1x1') {
            // Clear all existing slots first
            slots.forEach((s, i) => {
                if (s.state !== 'empty') removeSlot(i);
            });
            slots = [{ index: 0, cameraId: null, camera: null, jobId: null, pollingHandle: null, streamUrl: null, state: 'empty' }];
            renderSlots();
            assignCameraToSlot(0, camera);
        } else {
            // Multi-view: assign to first empty slot
            const emptySlot = findFirstEmptySlot();
            if (emptySlot) {
                assignCameraToSlot(emptySlot.index, camera);
            } else {
                window.portalApi.showToast(`Tất cả ô đã đầy. Đóng 1 camera trước hoặc chuyển sang layout lớn hơn.`, 'warning');
            }
        }
    }

    // ── RENDER HELPERS (from old code) ─────────────────────
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

    // ── DISPLAY SETTINGS ─────────
    const setupDisplaySettings = () => {
        const gearBtn = document.getElementById("quality-gear-btn");
        const menu = document.getElementById("quality-menu");

        // Toggle menu
        if (gearBtn && menu) {
            gearBtn.addEventListener("click", (e) => {
                e.stopPropagation();
                menu.style.display = menu.style.display === "none" ? "block" : "none";
            });

            // Close menu when clicking outside
            document.addEventListener("click", (e) => {
                if (menu.style.display === "block" && !menu.contains(e.target) && e.target !== gearBtn) {
                    menu.style.display = "none";
                }
            });
        }

        // Resolution options
        const qualityOptions = document.querySelectorAll(".quality-option");
        qualityOptions.forEach(btn => {
            btn.addEventListener("click", async () => {
                // Update inline styles to reflect active state
                qualityOptions.forEach(b => {
                    b.classList.remove("active");
                    b.style.background = "rgba(255,255,255,0.05)";
                    b.style.color = "#E2E8F0";
                });
                btn.classList.add("active");
                btn.style.background = "#2563EB";
                btn.style.color = "#fff";

                const quality = btn.dataset.quality;
                const activeSlots = getActiveSlots();
                for (const slot of activeSlots) {
                    if (slot.jobId) {
                        try {
                            await window.portalApi.post(`/api/test-jobs/${slot.jobId}/quality`, { quality });

                            // Kết nối lại WebRTC nếu đang phát bằng WebRTC để nhận luồng độ phân giải mới
                            const videoEl = slot.domElement ? slot.domElement.querySelector('video') : null;
                            if (videoEl && slot.rtcPeerConnection) {
                                console.log(`[Quality] Đang kết nối lại WebRTC cho camera ${slot.cameraId} với độ phân giải: ${quality}`);
                                try {
                                    slot.rtcPeerConnection.close();
                                } catch (err) { }
                                slot.rtcPeerConnection = null;

                                // Đợi 1 giây để FFmpeg khởi động lại và đẩy luồng mới lên MediaMTX ổn định
                                setTimeout(() => {
                                    startWebRTCPlayer(videoEl, slot.cameraId).then(pc => {
                                        slot.rtcPeerConnection = pc;
                                    }).catch(err => {
                                        console.error("[Quality] Lỗi kết nối lại WebRTC sau khi đổi chất lượng:", err);
                                    });
                                }, 1000);
                            }

                            // Làm mới luồng ảnh nếu đang dùng MJPEG fallback
                            const imgEl = slot.domElement ? slot.domElement.querySelector('.mv-stream-img') : null;
                            if (imgEl && imgEl.style.display !== 'none') {
                                const oldSrc = imgEl.src;
                                imgEl.src = '';
                                setTimeout(() => {
                                    imgEl.src = oldSrc;
                                }, 300);
                            }
                        } catch (e) {
                            console.error("Lỗi cập nhật phân giải:", e);
                        }
                    }
                }

                if (window.portalApi.showToast) {
                    window.portalApi.showToast(`Đã đổi chất lượng hiển thị sang ${btn.textContent.trim()}`, "success");
                }
            });
        });

        // Checkboxes
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
            { id: "show-box-plate-chk", key: "show_box_plate" },
            { id: "show-label-chk", key: "show_label" }
        ];

        checkBoxes.forEach(item => {
            const el = document.getElementById(item.id);
            if (el) {
                el.checked = true;
                el.addEventListener("change", async () => {
                    // Apply to all active slots
                    const activeSlots = getActiveSlots();
                    for (const slot of activeSlots) {
                        if (slot.jobId) {
                            try {
                                const payload = {};
                                payload[item.key] = el.checked;
                                await window.portalApi.post(`/api/test-jobs/${slot.jobId}/settings`, payload);
                            } catch (error) {
                                console.error("Lỗi cập nhật cấu hình hiển thị:", error);
                            }
                        }
                    }

                    if (window.portalApi.showToast) {
                        const status = el.checked ? "Bật" : "Tắt";
                        const labelText = el.parentElement.textContent.trim();
                        window.portalApi.showToast(`Đã ${status} ${labelText}`, "success");
                    }
                });
            }
        });
    };

    setupDisplaySettings();

    // ── CAMERA DASHBOARD GRID ──────────────────────────────
    function renderPreviewGrid() {
        if (!previewGrid) return;

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

        let payload = { ...camera, [feature]: value };
        if (feature === "enable_ai" && !value) {
            payload.enable_congestion = false;
            payload.enable_illegal_parking = false;
            payload.enable_license_plate = false;
        }

        try {
            await window.portalApi.put(`/api/cameras/${cameraId}`, payload);
            await loadAllCameras();

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

    // ── FULLSCREEN LOGIC ───────────────────────────────────
    const fsContainer = document.getElementById("multiview-wrapper");
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

    // ── INIT ───────────────────────────────────────────────
    // Initialize with 1 empty slot
    slots = [{ index: 0, cameraId: null, camera: null, jobId: null, pollingHandle: null, streamUrl: null, state: 'empty' }];
    renderSlots();

    loadAllCameras();
    refreshTimer = setInterval(refreshSnapshots, 10000);

    // Dừng tất cả các slot đang chạy khi người dùng đóng tab hoặc chuyển trang khác
    const stoppedJobs = new Set();
    function stopAllActiveJobsBeacon() {
        slots.forEach(slot => {
            if (slot && slot.jobId && !stoppedJobs.has(slot.jobId)) {
                stoppedJobs.add(slot.jobId);
                const url = `${window.location.origin}/api/test-jobs/${slot.jobId}/stop`;
                if (navigator.sendBeacon) {
                    navigator.sendBeacon(url);
                } else {
                    fetch(url, {
                        method: 'POST',
                        keepalive: true,
                        credentials: 'same-origin'
                    }).catch(() => {});
                }
            }
        });
    }

    window.addEventListener('beforeunload', stopAllActiveJobsBeacon);
    window.addEventListener('pagehide', stopAllActiveJobsBeacon);
    window.addEventListener('unload', stopAllActiveJobsBeacon);
}

document.addEventListener('DOMContentLoaded', initMonitoringForm);
