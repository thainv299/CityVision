(function () {
    function buildHeaders(options) {
        const headers = new Headers(options.headers || {});
        if (!(options.body instanceof FormData) && !headers.has("Content-Type")) {
            headers.set("Content-Type", "application/json");
        }
        return headers;
    }

    async function request(url, options = {}) {
        const response = await fetch(url, {
            credentials: "same-origin",
            ...options,
            headers: buildHeaders(options),
        });

        const isJson = response.headers.get("content-type")?.includes("application/json");
        const payload = isJson ? await response.json() : null;

        if (!response.ok) {
            throw new Error(payload?.error || `Yêu cầu thất bại (${response.status})`);
        }

        return payload;
    }

    function showNotice(target, message, tone = "info") {
        if (!target) {
            return;
        }
        target.innerHTML = message
            ? `<div class="notice ${tone}">${message}</div>`
            : "";
    }

    function pillText(enabled, yesText = "Bật", noText = "Tắt") {
        return enabled ? yesText : noText;
    }

    function showToast(message, type = 'info', title = '', duration = 4500) {
        const icons = {
            success: '✓',
            error: '✕',
            warning: '⚠',
            info: 'ℹ'
        };

        const container = document.getElementById('notificationContainer');
        if (!container) return;

        const toast = document.createElement('div');
        toast.className = `toast-notification ${type}`;

        toast.innerHTML = `
            <div class="toast-icon">${icons[type] || '●'}</div>
            <div class="toast-content">
                ${title ? `<div class="toast-title">${title}</div>` : ''}
                <div class="toast-message">${message}</div>
            </div>
            <button class="toast-close" type="button">✕</button>
        `;

        container.appendChild(toast);

        const closeBtn = toast.querySelector('.toast-close');
        const removeToast = () => {
            toast.classList.add('hide');
            setTimeout(() => toast.remove(), 300);
        };

        closeBtn.addEventListener('click', removeToast);

        if (duration > 0) {
            setTimeout(removeToast, duration);
        }

        return toast;
    }

    function readJsonFileToInput(fileInput, targetElementId) {
        const file = fileInput.files[0];
        if (!file) return;
        const target = document.getElementById(targetElementId) || document.querySelector(`textarea[name="${targetElementId}"]`);
        if (!target) return;

        const reader = new FileReader();
        reader.onload = function (e) {
            try {
                let content = JSON.parse(e.target.result);
                // Thích ứng với chuẩn JSON format Tkinter: {"points": [[x,y],...]}
                if (content && content.points) {
                    target.value = JSON.stringify(content.points);
                } else {
                    target.value = JSON.stringify(content);
                }
                showToast('Đã tải cấu hình vùng thành công', 'success');
            } catch (err) {
                console.error(err);
                showToast('Tệp JSON không hợp lệ!', 'error', 'Lỗi');
            }
            // Reset chuỗi để có thể nạp lại file đó
            fileInput.value = '';
        };
        reader.readAsText(file);
    }

    function submitFormWithProgress(url, formData, onProgress) {
        return new Promise((resolve, reject) => {
            const xhr = new XMLHttpRequest();
            xhr.open("POST", url);
            xhr.withCredentials = true; // equivalent to credentials: "same-origin"

            if (xhr.upload && onProgress) {
                xhr.upload.addEventListener("progress", (event) => {
                    if (event.lengthComputable) {
                        const percent = Math.round((event.loaded / event.total) * 100);
                        onProgress(percent, event.loaded, event.total);
                    }
                });
            }

            xhr.onload = () => {
                let payload = null;
                const contentType = xhr.getResponseHeader("content-type");
                if (contentType && contentType.includes("application/json")) {
                    try {
                        payload = JSON.parse(xhr.responseText);
                    } catch (e) {
                        // ignore
                    }
                }

                if (xhr.status >= 200 && xhr.status < 300) {
                    resolve(payload);
                } else {
                    reject(new Error(payload?.error || `Yêu cầu thất bại (${xhr.status})`));
                }
            };

            xhr.onerror = () => {
                reject(new Error("Lỗi mạng hoặc kết nối bị từ chối."));
            };

            xhr.send(formData);
        });
    }

    async function submitFormChunked(url, formData, onProgress, chunkSize = 20 * 1024 * 1024) {
        let file = null;
        let fileKey = null;

        for (const [key, value] of formData.entries()) {
            if (value instanceof File && value.name) {
                file = value;
                fileKey = key;
                break;
            }
        }

        if (!file || file.size <= chunkSize) {
            return submitFormWithProgress(url, formData, onProgress);
        }

        const uploadId = "upl_" + Date.now() + "_" + Math.random().toString(36).substring(2, 9);
        const totalChunks = Math.ceil(file.size / chunkSize);
        let uploadedBytes = 0;

        for (let i = 0; i < totalChunks; i++) {
            const start = i * chunkSize;
            const end = Math.min(start + chunkSize, file.size);
            const chunk = file.slice(start, end);

            const chunkFormData = new FormData();
            chunkFormData.append("upload_id", uploadId);
            chunkFormData.append("chunk_index", i);
            chunkFormData.append("total_chunks", totalChunks);
            chunkFormData.append("file_data", chunk, file.name);

            await new Promise((resolve, reject) => {
                const xhr = new XMLHttpRequest();
                xhr.open("POST", "/api/upload-chunk");
                xhr.withCredentials = true;

                if (xhr.upload && onProgress) {
                    xhr.upload.addEventListener("progress", (event) => {
                        if (event.lengthComputable) {
                            const currentTotalLoaded = uploadedBytes + event.loaded;
                            const percent = Math.min(99, Math.round((currentTotalLoaded / file.size) * 100));
                            onProgress(percent, currentTotalLoaded, file.size);
                        }
                    });
                }

                xhr.onload = () => {
                    let payload = null;
                    try { payload = JSON.parse(xhr.responseText); } catch (e) { }
                    if (xhr.status >= 200 && xhr.status < 300) {
                        uploadedBytes += chunk.size;
                        resolve();
                    } else {
                        reject(new Error(payload?.error || `Tải lên chunk ${i} thất bại (${xhr.status})`));
                    }
                };

                xhr.onerror = () => reject(new Error("Lỗi mạng khi tải lên."));
                xhr.send(chunkFormData);
            });
        }

        if (onProgress) {
            onProgress(100, file.size, file.size);
        }

        formData.delete(fileKey);
        formData.append("upload_id", uploadId);
        formData.append("original_filename", file.name);

        return window.portalApi.submitForm(url, formData);
    }

    function formatVietnameseDateTime(dateString) {
        if (!dateString) return 'N/A';
        try {
            // SQLite và Python lưu thời gian theo giờ local (datetime.now()), 
            // nên không thêm chữ 'Z' để tránh trình duyệt tự động cộng thêm 7 tiếng.
            let formattedStr = dateString;
            if (typeof dateString === 'string') {
                // Loại bỏ đuôi 'Z' hoặc '+00:00' nếu có để giữ nguyên giờ local gốc của server
                if (formattedStr.endsWith('Z')) {
                    formattedStr = formattedStr.slice(0, -1);
                }
                // Thay thế khoảng trắng ' ' bằng 'T' để API Date() của trình duyệt hoạt động đồng nhất
                if (formattedStr.includes(' ') && !formattedStr.includes('T')) {
                    formattedStr = formattedStr.replace(' ', 'T');
                }
            }
            const date = new Date(formattedStr);
            if (isNaN(date.getTime())) return dateString;
            const days = ['Chủ Nhật', 'Thứ 2', 'Thứ 3', 'Thứ 4', 'Thứ 5', 'Thứ 6', 'Thứ 7'];
            const dayName = days[date.getDay()];
            const d = String(date.getDate()).padStart(2, '0');
            const m = String(date.getMonth() + 1).padStart(2, '0');
            const hr = String(date.getHours()).padStart(2, '0');
            const min = String(date.getMinutes()).padStart(2, '0');
            return `${dayName} - ${d} - ${m} - ${hr}:${min}`;
        } catch (e) {
            return dateString;
        }
    }

    window.portalApi = {
        get: (url) => request(url, { method: "GET" }),
        post: (url, body) => request(url, { method: "POST", body: JSON.stringify(body) }),
        put: (url, body) => request(url, { method: "PUT", body: JSON.stringify(body) }),
        delete: (url) => request(url, { method: "DELETE" }),
        submitForm: (url, formData) => request(url, { method: "POST", body: formData }),
        submitFormWithProgress,
        submitFormChunked,
        showNotice,
        pillText,
        showToast,
        readJsonFileToInput,
        formatVietnameseDateTime,
    };

    // Sidebar Toggle Logic
    document.addEventListener('DOMContentLoaded', function () {
        const toggleBtn = document.getElementById('sidebar-toggle-btn');
        const shell = document.querySelector('.portal-shell');
        if (toggleBtn && shell) {
            toggleBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                shell.classList.toggle('sidebar-active');
            });

            // Đóng sidebar khi click ra ngoài (trên mobile)
            document.addEventListener('click', (e) => {
                if (shell.classList.contains('sidebar-active')) {
                    const sidebar = document.querySelector('.sidebar');
                    if (sidebar && !sidebar.contains(e.target) && !toggleBtn.contains(e.target)) {
                        shell.classList.remove('sidebar-active');
                    }
                }
            });
        }
    });

})();
// Logic thông báo
let notificationPollingInterval = null;
let shownNotificationIds = null;

// Khởi tạo trạng thái âm thanh từ localStorage (mặc định là bật)
let isAudioEnabled = localStorage.getItem('isAudioEnabled') !== 'false';

// Hàm cập nhật trạng thái icon âm thanh ở thanh Header
function updateAudioIcon() {
    const audioIcon = document.getElementById('audio-icon');
    const audioBtn = document.getElementById('audio-toggle-btn');
    if (!audioIcon) return;

    if (isAudioEnabled) {
        audioIcon.className = 'fas fa-volume-up';
        audioIcon.style.color = '#3B82F6'; // Màu xanh active
        if (audioBtn) audioBtn.title = 'Tắt âm thanh cảnh báo';
    } else {
        audioIcon.className = 'fas fa-volume-mute';
        audioIcon.style.color = '#94A3B8'; // Màu xám mute
        if (audioBtn) audioBtn.title = 'Bật âm thanh cảnh báo';
    }
}

// Khởi tạo hàng đợi âm thanh để tránh phát đè lên nhau khi có nhiều cảnh báo cùng lúc
let audioQueue = [];
let isAudioPlaying = false;

// Hàm điều phối hàng đợi âm thanh tuần tự
function processAudioQueue() {
    if (isAudioPlaying || audioQueue.length === 0) return;

    isAudioPlaying = true;
    const currentSrc = audioQueue.shift();
    const audio = new Audio(currentSrc);

    audio.play()
        .then(() => {
            // Khi âm thanh hiện tại phát xong, tiếp tục phát âm thanh tiếp theo trong hàng đợi
            audio.onended = () => {
                isAudioPlaying = false;
                processAudioQueue();
            };
        })
        .catch(err => {
            console.warn("Autoplay bị chặn hoặc lỗi phát nhạc:", err);
            isAudioPlaying = false;
            // Nếu lỗi phát (ví dụ do chặn autoplay), đợi 500ms rồi tiếp tục xử lý hàng đợi
            setTimeout(processAudioQueue, 500);
        });
}

// Phát âm thanh tương ứng với loại vi phạm hoặc mức độ ùn tắc (Hỗ trợ hàng đợi tuần tự)
function playNotificationSound(type, level = '') {
    if (!isAudioEnabled) return;

    let audioSrc = '';
    if (type === 'violation') {
        audioSrc = '/static/audio/violation.mp3';
    } else if (type === 'congestion') {
        if (level === '1') {
            audioSrc = '/static/audio/congestion_1.mp3';
        } else if (level === '2') {
            audioSrc = '/static/audio/congestion_2.mp3';
        } else if (level === '3') {
            audioSrc = '/static/audio/congestion_3.mp3';
        } else {
            audioSrc = '/static/audio/congestion.mp3';
        }
    }

    if (audioSrc) {
        // Tối ưu chống spam: Nếu tệp âm thanh này đã có trong hàng đợi chờ phát, bỏ qua để tránh lặp từ khóa liên tục
        if (audioQueue.includes(audioSrc)) {
            return;
        }

        // Tối ưu thời gian thực: Giới hạn tối đa 3 âm thanh chờ phát để thông báo luôn bám sát thực tế, không bị trễ quá lâu
        if (audioQueue.length >= 3) {
            return;
        }

        audioQueue.push(audioSrc);
        processAudioQueue();
    }
}

function showNotificationToast(n) {
    // Phát âm thanh cảnh báo tương ứng
    playNotificationSound(n.type, n.title);

    let type = n.type === 'violation' ? 'error' : 'warning';
    let titleText = n.type === 'violation' ? 'Xe đỗ sai quy định' : 'Cảnh báo ùn tắc (Mức ' + n.title + ')';
    let messageText = n.type === 'violation' ? 'Biển số xe: ' + n.title : (n.noi_dung || 'Đã phát hiện ùn tắc tại khu vực giám sát');

    // Tạo toast thông báo (Hiển thị 8 giây cho người dùng dễ theo dõi)
    const toast = window.portalApi.showToast(messageText, type, titleText, 8000);
    if (!toast) return;

    // Thêm hình ảnh preview vào toast nếu có để tăng độ trực quan
    if (n.image) {
        const toastContent = toast.querySelector('.toast-content');
        if (toastContent) {
            const cleanImgPath = n.image.replace(/^\/+/, '').replace(/\\/g, '/');
            const imgDiv = document.createElement('div');
            imgDiv.style.marginTop = '8px';
            imgDiv.innerHTML = `<img src="/${cleanImgPath}" style="width: 100%; height: 80px; border-radius: 6px; object-fit: cover; border: 1px solid rgba(0,0,0,0.05);">`;
            toastContent.appendChild(imgDiv);
        }
    }

    // Nhấp vào thanh thông báo để đọc, đánh dấu là đã đọc và điều hướng
    toast.style.cursor = 'pointer';
    toast.addEventListener('click', async (e) => {
        // Nếu click trúng nút đóng toast-close thì chỉ đóng toast, không điều hướng
        if (e.target.closest('.toast-close')) return;

        try {
            // Đánh dấu là đã đọc
            await window.portalApi.post('/api/notifications/' + n.type + '/' + n.id + '/read', {});
            // Cập nhật lại giao diện thông báo của chuông lập tức
            fetchNotifications();
            // Đóng toast
            toast.classList.add('hide');
            setTimeout(() => toast.remove(), 300);

            // Điều hướng đến trang chi tiết
            let urlPrefix = n.type === 'violation' ? '/violations?id=' : '/congestion?id=';
            window.location.href = urlPrefix + n.id;
        } catch (err) {
            console.error("Lỗi xử lý click thông báo toast:", err);
        }
    });
}

async function fetchNotifications() {
    try {
        const data = await window.portalApi.get('/api/notifications/unread');
        if (data && data.ok) {
            updateNotificationUI(data.unread_count, data.notifications);
        }
    } catch (err) {
        console.error("Lỗi lấy thông báo:", err);
        const listContainer = document.getElementById('notif-list-content');
        if (listContainer && listContainer.innerHTML.trim() === '') {
            listContainer.innerHTML = '<div style="padding: 20px; text-align: center; color: #EF4444; font-size: 13px;">Không thể kết nối đến máy chủ. Vui lòng khởi động lại server.</div>';
        }
    }
}

function updateNotificationUI(count, notifications) {
    const badge = document.querySelector('.icon-badge');
    const listContainer = document.getElementById('notif-list-content');

    if (!badge || !listContainer) return;

    // Kiểm tra và hiển thị toast cho các thông báo mới
    if (shownNotificationIds === null) {
        // Lần đầu tải trang: Chỉ khởi tạo danh sách đã có để tránh spam thông báo cũ
        shownNotificationIds = new Set(notifications.map(n => `${n.type}_${n.id}`));
    } else {
        // Các lần quét sau: Tìm thông báo mới và hiển thị dưới dạng toast
        notifications.forEach(n => {
            const key = `${n.type}_${n.id}`;
            if (!shownNotificationIds.has(key)) {
                shownNotificationIds.add(key);
                showNotificationToast(n);
            }
        });
    }

    // Update badge
    if (count > 0) {
        badge.textContent = count;
        badge.style.display = 'flex';
    } else {
        badge.style.display = 'none';
    }

    // Update list
    listContainer.innerHTML = '';
    if (notifications.length === 0) {
        listContainer.innerHTML = '<div style="padding: 20px; text-align: center; color: #94A3B8; font-size: 13px;">Không có thông báo mới</div>';
        return;
    }

    notifications.forEach(n => {
        const item = document.createElement('div');
        item.className = 'notif-item';
        item.style.padding = '10px 15px';
        item.style.borderBottom = '1px solid #F8FAFC';
        item.style.cursor = 'pointer';
        item.onmouseover = () => item.style.background = '#F8FAFC';
        item.onmouseout = () => item.style.background = 'white';

        let color, icon, titleText, urlPrefix;
        if (n.type === 'violation') {
            color = '#EF4444';
            icon = '⚠️';
            titleText = 'Xe đỗ sai quy định';
            urlPrefix = '/violations?id=';
        } else {
            color = '#F59E0B';
            icon = '🟠';
            titleText = 'Cảnh báo ùn tắc (Mức ' + n.title + ')';
            urlPrefix = '/congestion?id=';
        }

        // Format time string
        const timeStr = window.portalApi.formatVietnameseDateTime(n.time);

        const cleanImgPath = n.image ? n.image.replace(/^\/+/, '').replace(/\\/g, '/') : '';
        item.innerHTML = `
                <div style="display: flex; gap: 10px;">
                    ${cleanImgPath ? `<img src="/${cleanImgPath}" style="width: 50px; height: 35px; border-radius: 4px; object-fit: cover; flex-shrink: 0; background: #eee;">` : ''}
                    <div style="flex: 1;">
                        <div style="font-size: 12px; font-weight: 600; color: ${color};">${icon} ${titleText}</div>
                        <div style="font-size: 11px; color: #64748B; margin-top: 2px;">${n.type === 'violation' ? 'BKS: ' + n.title : (n.noi_dung || 'Phát hiện ùn tắc')}</div>
                        <div style="font-size: 10px; color: #94A3B8; margin-top: 4px;">${timeStr}</div>
                    </div>
                </div>
            `;

        item.onclick = async function () {
            try {
                await window.portalApi.post('/api/notifications/' + n.type + '/' + n.id + '/read', {});
                window.location.href = urlPrefix + n.id;
            } catch (e) {
                console.error("Lỗi đọc thông báo", e);
            }
        };
        listContainer.appendChild(item);
    });
}

// Thiết lập Server-Sent Events (SSE) để nhận thông báo tức thời mà không cần polling liên tục
function setupNotificationSSE() {
    if (!document.getElementById('bell-icon')) return;

    const eventSource = new EventSource('/api/notifications/stream');

    eventSource.onmessage = function (event) {
        try {
            const n = JSON.parse(event.data);
            if (n && n.id) {
                if (shownNotificationIds === null) {
                    shownNotificationIds = new Set();
                }
                const key = `${n.type}_${n.id}`;
                if (!shownNotificationIds.has(key)) {
                    shownNotificationIds.add(key);
                    // Hiển thị thông báo Toast nền trắng lập tức khi camera phát hiện
                    showNotificationToast(n);
                    // Cập nhật biểu tượng Chuông và danh sách dropdown tức thì
                    fetchNotifications();
                }
            }
        } catch (err) {
            console.error("Lỗi phân tích dữ liệu SSE:", err);
        }
    };

    eventSource.onerror = function (err) {
        // EventSource tự động kết nối lại khi kết nối gián đoạn
        console.warn("Mất kết nối dòng sự kiện SSE thông báo. Đang chờ tự động kết nối lại...");
    };
}

window.portalApi.fetchNotifications = fetchNotifications;

document.addEventListener('DOMContentLoaded', function () {
    // Khởi tạo nút bật/tắt âm thanh cảnh báo
    const audioBtn = document.getElementById('audio-toggle-btn');
    if (audioBtn) {
        updateAudioIcon();
        audioBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            isAudioEnabled = !isAudioEnabled;
            localStorage.setItem('isAudioEnabled', isAudioEnabled);
            updateAudioIcon();

            // Hiển thị toast thông báo trạng thái
            if (isAudioEnabled) {
                window.portalApi.showToast('Đã bật âm thanh cảnh báo', 'success', 'Âm thanh');
                // Phát thử một âm thanh ngắn để xin quyền autoplay
                playNotificationSound('congestion', '1');
            } else {
                window.portalApi.showToast('Đã tắt âm thanh cảnh báo', 'warning', 'Âm thanh');
            }
        });
    }

    if (document.getElementById('bell-icon')) {
        fetchNotifications();
        setupNotificationSSE();
        // Giữ fallback polling ở mức 15s làm kênh dự phòng dự phòng an toàn
        notificationPollingInterval = setInterval(fetchNotifications, 15000);
    }

    // Tự động định dạng các thẻ hiển thị thời gian
    document.querySelectorAll('.vietnamese-datetime').forEach(el => {
        const timeVal = el.getAttribute('data-time');
        if (timeVal) {
            el.textContent = window.portalApi.formatVietnameseDateTime(timeVal);
        }
    });
});