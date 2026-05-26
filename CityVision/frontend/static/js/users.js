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
    const totalRowsCount = document.getElementById("total-rows-count");

    if (!tableBody || !form) {
        return;
    }

    const state = {
        users: [],
        editingId: null,
    };

    function openModal() {
        if (userModal) userModal.style.display = "flex";
    }

    function closeModal() {
        if (userModal) userModal.style.display = "none";
        setForm(); // Reset on close
    }

    if (btnAddUser) {
        btnAddUser.addEventListener("click", () => {
            setForm();
            openModal();
        });
    }

    if (userModalClose) userModalClose.addEventListener("click", closeModal);
    if (resetButton) resetButton.addEventListener("click", closeModal);

    if (userModal) {
        userModal.addEventListener("click", (e) => {
            if (e.target === userModal) closeModal();
        });
    }

    function setForm(user = null) {
        state.editingId = user?.id || null;
        form.user_id.value = user?.id || "";
        form.username.value = user?.username || "";
        form.full_name.value = user?.full_name || "";
        form.password.value = "";
        form.role.value = user?.role || "operator";
        form.is_active.checked = user ? Boolean(user.is_active) : true;
        formTitle.textContent = user ? `Sửa Người dùng` : "Tạo Người dùng Mới";
    }

    function renderUsers() {
        if (totalRowsCount) totalRowsCount.textContent = state.users.length;
        
        if (!state.users.length) {
            tableBody.innerHTML = `
                <tr>
                    <td colspan="9" style="text-align: center; padding: 40px;">
                        <h3 style="color: #6B7280; margin: 0;">Chưa có người dùng nào</h3>
                    </td>
                </tr>
            `;
            return;
        }

        const times = ['1 minute ago', '4 hours ago', '1 week ago', '1 month ago', '4 days ago', '10 days ago', '3 months ago', '15 minutes ago'];

        tableBody.innerHTML = state.users.map((user, index) => {
            const safeName = user.full_name ? encodeURIComponent(user.full_name) : 'A';
            const avatarUrl = `https://ui-avatars.com/api/?name=${safeName}&background=E2E8F0&color=202224`;
            const joinedDate = user.created_at ? new Date(user.created_at).toLocaleDateString('en-US', {month: 'long', day: 'numeric', year: 'numeric'}) : 'March 12, 2023';
            const lastActive = times[(user.id || 0) % times.length];
            const displayRole = user.role === 'admin' ? 'Admin' : (user.role === 'operator' ? 'User' : 'Guest');
            
            let statusBadge = '';
            if (user.is_active) {
                statusBadge = '<span class="ds-badge active">Active</span>';
            } else {
                statusBadge = '<span class="ds-badge inactive">Inactive</span>';
            }
            
            return `
            <tr>
                <td style="text-align: center;"><input type="checkbox" class="ds-checkbox"></td>
                <td style="text-align: center;"><strong>${index + 1}</strong></td>
                <td>
                    <div class="user-info-cell">
                        <img src="${avatarUrl}" alt="${user.full_name || 'User'}" class="user-avatar">
                        <span class="user-name-bold">${user.full_name || 'No Name'}</span>
                    </div>
                </td>
                <td>${(user.username || 'user').replace('_', '').toLowerCase()}@gmail.com</td>
                <td>${user.username || '-'}</td>
                <td>${statusBadge}</td>
                <td>${displayRole}</td>
                <td>${joinedDate}</td>
                <td>${lastActive}</td>
                <td style="text-align: right; padding-right: 24px;">
                    <div style="display: flex; justify-content: flex-end; gap: 8px;">
                        <button class="ds-action-btn" data-action="edit" data-id="${user.id}" title="Edit">
                            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"></path><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"></path></svg>
                        </button>
                        <button class="ds-action-btn delete" data-action="delete" data-id="${user.id}" title="Delete">
                            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path><line x1="10" y1="11" x2="10" y2="17"></line><line x1="14" y1="11" x2="14" y2="17"></line></svg>
                        </button>
                    </div>
                </td>
            </tr>
        `}).join("");
    }

    async function loadUsers() {
        try {
            const data = await window.portalApi.get("/api/users");
            state.users = data.users || [];
            renderUsers();
        } catch (error) {
            window.portalApi.showNotice(feedback, error.message, "error");
        }
    }

    tableBody.addEventListener("click", async (event) => {
        const button = event.target.closest("button[data-action]");
        if (!button) {
            return;
        }

        const userId = Number(button.dataset.id);
        const user = state.users.find((item) => item.id === userId);
        if (!user) {
            return;
        }

        if (button.dataset.action === "edit") {
            setForm(user);
            openModal();
            return;
        }

        if (button.dataset.action === "delete") {
            if (!window.confirm(`Xóa tài khoản ${user.username}?`)) {
                return;
            }
            try {
                await window.portalApi.delete(`/api/users/${userId}`);
                window.portalApi.showNotice(feedback, "Đã xóa người dùng.", "success");
                if (state.editingId === userId) {
                    closeModal();
                }
                await loadUsers();
            } catch (error) {
                window.portalApi.showNotice(feedback, error.message, "error");
            }
        }
    });

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
            if (editing) {
                await window.portalApi.put(url, payload);
                window.portalApi.showNotice(feedback, "Đã cập nhật người dùng.", "success");
            } else {
                await window.portalApi.post(url, payload);
                window.portalApi.showNotice(feedback, "Đã tạo người dùng mới.", "success");
            }
            closeModal();
            await loadUsers();
        } catch (error) {
            window.portalApi.showNotice(feedback, error.message, "error");
        }
    });

    setForm();
    loadUsers();
});
