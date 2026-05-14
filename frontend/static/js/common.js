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
        reader.onload = function(e) {
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
                    try { payload = JSON.parse(xhr.responseText); } catch (e) {}
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
    };

    // Sidebar Toggle Logic
    document.addEventListener('DOMContentLoaded', function() {
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

    // Notification Polling Logic
    let notificationPollingInterval = null;

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
            const timeObj = new Date(n.time);
            const timeStr = timeObj.toLocaleString('vi-VN', {hour: '2-digit', minute:'2-digit', day:'2-digit', month:'2-digit'});
            
            item.innerHTML = `
                <div style="display: flex; gap: 10px;">
                    ${n.image ? `<img src="/${n.image}" style="width: 50px; height: 35px; border-radius: 4px; object-fit: cover; flex-shrink: 0; background: #eee;">` : ''}
                    <div style="flex: 1;">
                        <div style="font-size: 12px; font-weight: 600; color: ${color};">${icon} ${titleText}</div>
                        <div style="font-size: 11px; color: #64748B; margin-top: 2px;">${n.type === 'violation' ? 'BKS: ' + n.title : ''}</div>
                        <div style="font-size: 10px; color: #94A3B8; margin-top: 4px;">${timeStr}</div>
                    </div>
                </div>
            `;
            
            item.onclick = async function() {
                try {
                    await window.portalApi.post('/api/notifications/' + n.type + '/' + n.id + '/read', {});
                    window.location.href = urlPrefix + n.id;
                } catch(e) {
                    console.error("Lỗi đọc thông báo", e);
                }
            };
            listContainer.appendChild(item);
        });
    }

    window.portalApi.fetchNotifications = fetchNotifications;
    
    document.addEventListener('DOMContentLoaded', function() {
        if (document.getElementById('bell-icon')) {
            fetchNotifications();
            notificationPollingInterval = setInterval(fetchNotifications, 10000); // 10s
        }
    });
})();
