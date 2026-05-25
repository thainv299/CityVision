document.addEventListener("DOMContentLoaded", () => {
    const tableBody = document.getElementById("users-table-body");
    const form = document.getElementById("user-form");
    const feedback = document.getElementById("users-feedback");
    const resetButton = document.getElementById("user-form-reset");
    const formTitle = document.getElementById("user-form-title");

    // Modal elements
    const userModal = document.getElementById("user-modal");
    const btnAddUser = document.getElementById("btn-add-user");
    const userModalClose = document.getElementById("user-modal-close");

    // Search & Filter elements
    const searchInput = document.getElementById("users-search-input");
    const roleFilterBtn = document.getElementById("role-filter-btn");
    const roleFilterMenu = document.getElementById("role-filter-menu");
    const statusFilterBtn = document.getElementById("status-filter-btn");
    const statusFilterMenu = document.getElementById("status-filter-menu");
    const exportBtn = document.getElementById("btn-export-users");

    // Pagination
    const paginationContainer = document.getElementById("pagination-container");

    // Select all
    const selectAllCb = document.getElementById("select-all-checkbox");

    if (!tableBody || !form) return;

    // ─── STATE ──────────────────────────────────────────
    const state = {
        users: [],
        filteredUsers: [],
        editingId: null,
        searchQuery: "",
        roleFilter: "all",     // all | admin | operator
        statusFilter: "all",   // all | active | inactive
        sortField: null,       // full_name | username | created_at
        sortDir: "asc",
        currentPage: 1,
        rowsPerPage: 10,
        loading: true,
    };

    // ─── AVATAR GRADIENTS ───────────────────────────────
    const AVATAR_GRADIENTS = [
        "linear-gradient(135deg, #667eea, #764ba2)",
        "linear-gradient(135deg, #f093fb, #f5576c)",
        "linear-gradient(135deg, #4facfe, #00f2fe)",
        "linear-gradient(135deg, #43e97b, #38f9d7)",
        "linear-gradient(135deg, #fa709a, #fee140)",
        "linear-gradient(135deg, #a18cd1, #fbc2eb)",
        "linear-gradient(135deg, #fccb90, #d57eeb)",
        "linear-gradient(135deg, #e0c3fc, #8ec5fc)",
        "linear-gradient(135deg, #f5576c, #ff6a88)",
        "linear-gradient(135deg, #667eea, #4facfe)",
    ];

    function getInitials(name) {
        if (!name) return "?";
        const parts = name.trim().split(/\s+/);
        if (parts.length >= 2) {
            return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
        }
        return parts[0].substring(0, 2).toUpperCase();
    }

    function getAvatarGradient(id) {
        return AVATAR_GRADIENTS[(id || 0) % AVATAR_GRADIENTS.length];
    }

    // ─── MODAL ──────────────────────────────────────────
    function openModal() {
        if (userModal) userModal.style.display = "flex";
    }

    function closeModal() {
        if (userModal) userModal.style.display = "none";
        setForm();
    }

    if (btnAddUser) btnAddUser.addEventListener("click", () => { setForm(); openModal(); });
    if (userModalClose) userModalClose.addEventListener("click", closeModal);
    if (resetButton) resetButton.addEventListener("click", closeModal);
    if (userModal) userModal.addEventListener("click", (e) => { if (e.target === userModal) closeModal(); });

    // ─── CAMERA SECTION ─────────────────────────────────
    function toggleCameraSection(role) {
        const camSection = document.getElementById("camera-access-section");
        if (camSection) camSection.style.display = role === "operator" ? "block" : "none";
    }

    form.role.addEventListener("change", (e) => toggleCameraSection(e.target.value));

    const btnSelectAll = document.getElementById("btn-select-all-cams");
    const btnDeselectAll = document.getElementById("btn-deselect-all-cams");
    if (btnSelectAll) btnSelectAll.addEventListener("click", () => {
        document.querySelectorAll(".camera-access-cb").forEach(cb => cb.checked = true);
    });
    if (btnDeselectAll) btnDeselectAll.addEventListener("click", () => {
        document.querySelectorAll(".camera-access-cb").forEach(cb => cb.checked = false);
    });

    // ─── SET FORM ───────────────────────────────────────
    async function setForm(user = null) {
        state.editingId = user?.id || null;
        form.user_id.value = user?.id || "";
        form.username.value = user?.username || "";
        form.full_name.value = user?.full_name || "";
        form.password.value = "";
        const role = user?.role || "operator";
        form.role.value = role;
        form.is_active.checked = user ? Boolean(user.is_active) : true;
        formTitle.textContent = user ? "Sửa Người dùng" : "Tạo Người dùng Mới";

        toggleCameraSection(role);
        document.querySelectorAll(".camera-access-cb").forEach(cb => cb.checked = false);

        if (user && role === "operator") {
            try {
                const res = await window.portalApi.get(`/api/users/${user.id}/camera-access`);
                if (res.ok && res.camera_ids) {
                    res.camera_ids.forEach(camId => {
                        const cb = document.querySelector(`.camera-access-cb[value="${camId}"]`);
                        if (cb) cb.checked = true;
                    });
                }
            } catch (err) {
                console.error("Lỗi khi load quyền camera:", err);
            }
        }
    }

    // ─── FILTER & SEARCH ────────────────────────────────
    function applyFilters() {
        let result = [...state.users];

        // Search
        if (state.searchQuery) {
            const removeAccents = (str) => {
                if (!str) return "";
                return str.normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/đ/g, "d").replace(/Đ/g, "D");
            };
            const q = removeAccents(state.searchQuery.toLowerCase());
            result = result.filter(u => {
                const name = removeAccents(u.full_name || "").toLowerCase();
                const uname = removeAccents(u.username || "").toLowerCase();
                return name.includes(q) || uname.includes(q);
            });
        }

        // Role filter
        if (state.roleFilter !== "all") {
            result = result.filter(u => u.role === state.roleFilter);
        }

        // Status filter
        if (state.statusFilter !== "all") {
            const wantActive = state.statusFilter === "active";
            result = result.filter(u => Boolean(u.is_active) === wantActive);
        }

        // Sort
        if (state.sortField) {
            result.sort((a, b) => {
                let va = a[state.sortField] || "";
                let vb = b[state.sortField] || "";
                if (typeof va === "string") va = va.toLowerCase();
                if (typeof vb === "string") vb = vb.toLowerCase();
                if (va < vb) return state.sortDir === "asc" ? -1 : 1;
                if (va > vb) return state.sortDir === "asc" ? 1 : -1;
                return 0;
            });
        }

        state.filteredUsers = result;
        state.currentPage = 1;
        renderAll();
    }

    // Debounced search
    let searchTimeout;
    const searchBtn = document.getElementById("users-search-btn");

    function triggerSearch() {
        if (searchInput) {
            state.searchQuery = searchInput.value.trim();
            applyFilters();
        }
    }

    if (searchInput) {
        searchInput.addEventListener("input", () => {
            clearTimeout(searchTimeout);
            searchTimeout = setTimeout(triggerSearch, 300);
        });

        searchInput.addEventListener("keypress", (e) => {
            if (e.key === "Enter") {
                clearTimeout(searchTimeout);
                triggerSearch();
            }
        });
    }

    if (searchBtn) {
        searchBtn.addEventListener("click", () => {
            clearTimeout(searchTimeout);
            triggerSearch();
        });
    }

    // ─── FILTER DROPDOWNS ───────────────────────────────
    function setupFilterDropdown(btn, menu, filterKey, applyFn) {
        if (!btn || !menu) return;

        btn.addEventListener("click", (e) => {
            e.stopPropagation();
            // Close all other dropdowns
            document.querySelectorAll(".filter-dropdown-menu.show").forEach(m => {
                if (m !== menu) m.classList.remove("show");
            });
            menu.classList.toggle("show");
        });

        menu.querySelectorAll(".filter-dropdown-item").forEach(item => {
            item.addEventListener("click", () => {
                const value = item.dataset.value;
                state[filterKey] = value;
                // Update active state
                menu.querySelectorAll(".filter-dropdown-item").forEach(i => i.classList.remove("active"));
                item.classList.add("active");
                // Update button appearance
                btn.classList.toggle("filter-active", value !== "all");
                menu.classList.remove("show");
                applyFn();
            });
        });
    }

    setupFilterDropdown(roleFilterBtn, roleFilterMenu, "roleFilter", applyFilters);
    setupFilterDropdown(statusFilterBtn, statusFilterMenu, "statusFilter", applyFilters);

    // Close dropdowns on outside click
    document.addEventListener("click", () => {
        document.querySelectorAll(".filter-dropdown-menu.show").forEach(m => m.classList.remove("show"));
    });

    // ─── SORT ───────────────────────────────────────────
    document.querySelectorAll(".data-table thead th[data-sort]").forEach(th => {
        th.addEventListener("click", () => {
            const field = th.dataset.sort;
            if (state.sortField === field) {
                state.sortDir = state.sortDir === "asc" ? "desc" : "asc";
            } else {
                state.sortField = field;
                state.sortDir = "asc";
            }
            // Update UI
            document.querySelectorAll(".data-table thead th[data-sort]").forEach(h => {
                h.classList.remove("sort-active");
                const icon = h.querySelector(".sort-icon");
                if (icon) icon.textContent = "↕";
            });
            th.classList.add("sort-active");
            const icon = th.querySelector(".sort-icon");
            if (icon) icon.textContent = state.sortDir === "asc" ? "↑" : "↓";
            applyFilters();
        });
    });

    // ─── SELECT ALL ─────────────────────────────────────
    if (selectAllCb) {
        selectAllCb.addEventListener("change", () => {
            const checked = selectAllCb.checked;
            tableBody.querySelectorAll(".ds-checkbox").forEach(cb => cb.checked = checked);
        });
    }

    // ─── LOADING SKELETON ───────────────────────────────
    function renderSkeleton() {
        const cols = 9;
        let html = "";
        for (let i = 0; i < 5; i++) {
            html += `<tr class="skeleton-row" style="animation-delay: ${i * 0.08}s">
                <td style="text-align: center;"><div class="skeleton-cell" style="width:18px; height:18px; border-radius:5px; margin: 0 auto;"></div></td>
                <td style="text-align: center;"><div class="skeleton-cell" style="width:20px; margin: 0 auto;"></div></td>
                <td><div style="display:flex;align-items:center;gap:14px"><div class="skeleton-avatar"></div><div class="skeleton-cell" style="width:120px;"></div></div></td>
                <td><div class="skeleton-cell" style="width:110px;"></div></td>
                <td><div class="skeleton-cell" style="width:70px;"></div></td>
                <td><div class="skeleton-cell" style="width:60px;"></div></td>
                <td><div class="skeleton-cell" style="width:100px;"></div></td>
                <td><div class="skeleton-cell" style="width:80px;"></div></td>
                <td><div class="skeleton-cell" style="width:50px; margin-left: auto;"></div></td>
            </tr>`;
        }
        tableBody.innerHTML = html;
    }

    // ─── RENDER USERS TABLE ─────────────────────────────
    function renderUsers() {
        const { filteredUsers, currentPage, rowsPerPage } = state;
        const totalRows = filteredUsers.length;
        const start = (currentPage - 1) * rowsPerPage;
        const end = Math.min(start + rowsPerPage, totalRows);
        const pageUsers = filteredUsers.slice(start, end);

        if (selectAllCb) selectAllCb.checked = false;

        if (totalRows === 0) {
            tableBody.innerHTML = `
                <tr>
                    <td colspan="9">
                        <div class="users-empty-state">
                            <div class="empty-icon-wrapper">
                                <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                                    <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path>
                                    <circle cx="12" cy="7" r="4"></circle>
                                </svg>
                            </div>
                            <h3>${state.searchQuery || state.roleFilter !== "all" || state.statusFilter !== "all"
                                ? "Không tìm thấy người dùng phù hợp"
                                : "Chưa có người dùng nào"}</h3>
                            <p>${state.searchQuery || state.roleFilter !== "all" || state.statusFilter !== "all"
                                ? "Thử thay đổi bộ lọc hoặc từ khóa tìm kiếm"
                                : "Nhấn '+ Thêm Người dùng' để bắt đầu"}</p>
                        </div>
                    </td>
                </tr>`;
            return;
        }

        tableBody.innerHTML = pageUsers.map((user, idx) => {
            const globalIdx = start + idx + 1;
            const initials = getInitials(user.full_name);
            const gradient = getAvatarGradient(user.id);
            const joinedDate = user.created_at
                ? new Date(user.created_at).toLocaleDateString('vi-VN', { day: '2-digit', month: '2-digit', year: 'numeric' })
                : '—';
            const displayRole = user.role === 'admin' ? 'Admin' : 'Operator';
            const roleClass = user.role === 'admin' ? 'admin' : 'operator';

            const statusBadge = user.is_active
                ? '<span class="ds-badge active">Hoạt động</span>'
                : '<span class="ds-badge inactive">Tắt</span>';

            let camCount;
            if (user.role === 'admin') {
                camCount = '<span class="cam-count-chip all">Tất cả</span>';
            } else {
                const total = user.camera_access_ids ? user.camera_access_ids.length : 0;
                camCount = `<span class="cam-count-chip">${total} camera</span>`;
            }

            return `
            <tr style="animation-delay: ${idx * 0.04}s">
                <td style="text-align: center;"><input type="checkbox" class="ds-checkbox" data-id="${user.id}"></td>
                <td style="text-align: center;"><strong>${globalIdx}</strong></td>
                <td>
                    <div class="user-info-cell">
                        <div class="user-avatar" style="background: ${gradient}">${initials}</div>
                        <span class="user-name-bold">${user.full_name || 'Chưa đặt tên'}</span>
                    </div>
                </td>
                <td>${user.username || '—'}</td>
                <td>${statusBadge}</td>
                <td><span class="ds-role-badge ${roleClass}">${displayRole}</span></td>
                <td>${joinedDate}</td>
                <td>${camCount}</td>
                <td style="text-align: right; padding-right: 24px;">
                    <div style="display: flex; justify-content: flex-end; gap: 6px;">
                        <button class="ds-action-btn" data-action="edit" data-id="${user.id}" title="Chỉnh sửa">
                            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"></path><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"></path></svg>
                        </button>
                        <button class="ds-action-btn delete" data-action="delete" data-id="${user.id}" title="Xóa">
                            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path><line x1="10" y1="11" x2="10" y2="17"></line><line x1="14" y1="11" x2="14" y2="17"></line></svg>
                        </button>
                    </div>
                </td>
            </tr>`;
        }).join("");
    }

    // ─── PAGINATION ─────────────────────────────────────
    function renderPagination() {
        if (!paginationContainer) return;

        const total = state.filteredUsers.length;
        const totalPages = Math.max(1, Math.ceil(total / state.rowsPerPage));
        const { currentPage } = state;
        const start = Math.min((currentPage - 1) * state.rowsPerPage + 1, total);
        const end = Math.min(currentPage * state.rowsPerPage, total);

        let pagesHtml = "";

        // Generate page buttons with smart ellipsis
        const maxVisible = 5;
        let pages = [];

        if (totalPages <= maxVisible + 2) {
            for (let i = 1; i <= totalPages; i++) pages.push(i);
        } else {
            pages.push(1);
            if (currentPage > 3) pages.push("...");
            const rangeStart = Math.max(2, currentPage - 1);
            const rangeEnd = Math.min(totalPages - 1, currentPage + 1);
            for (let i = rangeStart; i <= rangeEnd; i++) pages.push(i);
            if (currentPage < totalPages - 2) pages.push("...");
            pages.push(totalPages);
        }

        pages.forEach(p => {
            if (p === "...") {
                pagesHtml += `<span class="page-ellipsis">…</span>`;
            } else {
                pagesHtml += `<button class="page-btn ${p === currentPage ? 'active' : ''}" data-page="${p}">${p}</button>`;
            }
        });

        paginationContainer.innerHTML = `
            <div class="pagination-info">
                Hiển thị <strong>${total > 0 ? start : 0}–${end}</strong> trong <strong>${total}</strong> người dùng
                <span style="margin-left: 12px;">Số hàng:</span>
                <select id="rows-per-page-select">
                    <option value="10" ${state.rowsPerPage === 10 ? 'selected' : ''}>10</option>
                    <option value="25" ${state.rowsPerPage === 25 ? 'selected' : ''}>25</option>
                    <option value="50" ${state.rowsPerPage === 50 ? 'selected' : ''}>50</option>
                </select>
            </div>
            <div class="pagination-buttons">
                <button class="page-btn" data-page="prev" ${currentPage <= 1 ? 'disabled' : ''}>‹</button>
                ${pagesHtml}
                <button class="page-btn" data-page="next" ${currentPage >= totalPages ? 'disabled' : ''}>›</button>
            </div>
        `;

        // Rows per page change
        const rppSelect = document.getElementById("rows-per-page-select");
        if (rppSelect) {
            rppSelect.addEventListener("change", (e) => {
                state.rowsPerPage = parseInt(e.target.value, 10);
                state.currentPage = 1;
                renderAll();
            });
        }

        // Page button clicks
        paginationContainer.querySelectorAll(".page-btn[data-page]").forEach(btn => {
            btn.addEventListener("click", () => {
                const val = btn.dataset.page;
                if (val === "prev") {
                    if (state.currentPage > 1) state.currentPage--;
                } else if (val === "next") {
                    const totalPages = Math.ceil(state.filteredUsers.length / state.rowsPerPage);
                    if (state.currentPage < totalPages) state.currentPage++;
                } else {
                    state.currentPage = parseInt(val, 10);
                }
                renderAll();
            });
        });
    }

    // ─── RENDER ALL ─────────────────────────────────────
    function renderAll() {
        renderUsers();
        renderPagination();
    }

    // ─── CONFIRM DIALOG ─────────────────────────────────
    function showConfirmDialog(title, message, onConfirm) {
        const overlay = document.createElement("div");
        overlay.className = "confirm-modal-overlay";
        overlay.innerHTML = `
            <div class="confirm-modal">
                <div class="confirm-modal-icon">
                    <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <polyline points="3 6 5 6 21 6"></polyline>
                        <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
                    </svg>
                </div>
                <h3>${title}</h3>
                <p>${message}</p>
                <div class="confirm-modal-actions">
                    <button class="button secondary" id="confirm-cancel">Hủy bỏ</button>
                    <button class="button danger" id="confirm-ok">Xóa</button>
                </div>
            </div>
        `;
        document.body.appendChild(overlay);

        overlay.querySelector("#confirm-cancel").addEventListener("click", () => overlay.remove());
        overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.remove(); });
        overlay.querySelector("#confirm-ok").addEventListener("click", () => {
            overlay.remove();
            onConfirm();
        });
    }

    // ─── TABLE ACTIONS ──────────────────────────────────
    tableBody.addEventListener("click", async (event) => {
        const button = event.target.closest("button[data-action]");
        if (!button) return;

        const userId = Number(button.dataset.id);
        const user = state.users.find(item => item.id === userId);
        if (!user) return;

        if (button.dataset.action === "edit") {
            await setForm(user);
            openModal();
            return;
        }

        if (button.dataset.action === "delete") {
            showConfirmDialog(
                "Xóa người dùng",
                `Bạn có chắc muốn xóa tài khoản <strong>${user.full_name || user.username}</strong>? Hành động này không thể hoàn tác.`,
                async () => {
                    try {
                        await window.portalApi.delete(`/api/users/${userId}`);
                        window.portalApi.showNotice(feedback, "Đã xóa người dùng.", "success");
                        if (state.editingId === userId) closeModal();
                        await loadUsers();
                    } catch (error) {
                        window.portalApi.showNotice(feedback, error.message, "error");
                    }
                }
            );
        }
    });

    // ─── FORM SUBMIT ────────────────────────────────────
    form.addEventListener("submit", async (event) => {
        event.preventDefault();

        const payload = {
            username: form.username.value.trim(),
            full_name: form.full_name.value.trim(),
            password: form.password.value,
            role: form.role.value,
            is_active: form.is_active.checked,
        };

        const editing = Boolean(state.editingId);
        const url = editing ? `/api/users/${state.editingId}` : "/api/users";

        try {
            let userId = state.editingId;
            if (editing) {
                await window.portalApi.put(url, payload);
                window.portalApi.showNotice(feedback, "Đã cập nhật người dùng.", "success");
            } else {
                const res = await window.portalApi.post(url, payload);
                userId = res.user.id;
                window.portalApi.showNotice(feedback, "Đã tạo người dùng mới.", "success");
            }

            // Cập nhật quyền camera cho operator
            if (payload.role === "operator" && userId) {
                const cameraIds = Array.from(document.querySelectorAll(".camera-access-cb:checked")).map(cb => parseInt(cb.value, 10));
                try {
                    await window.portalApi.put(`/api/users/${userId}/camera-access`, { camera_ids: cameraIds });
                } catch (camErr) {
                    console.error("Lỗi cập nhật quyền camera:", camErr);
                }
            }

            closeModal();
            await loadUsers();
        } catch (error) {
            window.portalApi.showNotice(feedback, error.message, "error");
        }
    });

    // ─── EXPORT CSV ─────────────────────────────────────
    if (exportBtn) {
        exportBtn.addEventListener("click", () => {
            const users = state.filteredUsers;
            if (!users.length) {
                window.portalApi.showNotice(feedback, "Không có dữ liệu để xuất.", "error");
                return;
            }

            const BOM = "\uFEFF";
            const headers = ["STT", "Họ và tên", "Tên đăng nhập", "Vai trò", "Trạng thái", "Ngày tham gia", "Số camera"];
            const rows = users.map((u, i) => [
                i + 1,
                u.full_name || "",
                u.username || "",
                u.role === "admin" ? "Admin" : "Operator",
                u.is_active ? "Hoạt động" : "Tắt",
                u.created_at ? new Date(u.created_at).toLocaleDateString("vi-VN") : "",
                u.role === "admin" ? "Tất cả" : (u.camera_access_ids ? u.camera_access_ids.length : 0),
            ]);

            const csvContent = BOM + [headers, ...rows].map(r => r.map(c => `"${String(c).replace(/"/g, '""')}"`).join(",")).join("\n");
            const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
            const url = URL.createObjectURL(blob);
            const link = document.createElement("a");
            link.href = url;
            link.download = `nguoi_dung_${new Date().toISOString().slice(0, 10)}.csv`;
            link.click();
            URL.revokeObjectURL(url);

            window.portalApi.showNotice(feedback, `Đã xuất ${users.length} người dùng.`, "success");
        });
    }

    // ─── LOAD USERS ─────────────────────────────────────
    async function loadUsers() {
        try {
            renderSkeleton();
            const data = await window.portalApi.get("/api/users");
            state.users = data.users || [];
            state.loading = false;
            applyFilters();
        } catch (error) {
            state.loading = false;
            window.portalApi.showNotice(feedback, error.message, "error");
        }
    }

    // ─── INIT ───────────────────────────────────────────
    setForm();
    loadUsers();
});
